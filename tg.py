#!/usr/bin/env python3
"""Слой Telegram: вход профиля по ссылке и чтение приватных каналов.

Зачем он отдельным файлом: без входа проект работает ровно как раньше, публичные
каналы читаются по-прежнему через t.me/s/<имя>, и Telethon даже не импортируется.
Пока нет профиля с подключённым Telegram, сюда никто не заходит.

Sessия — это готовый вход в аккаунт, а не пароль. Поэтому она лежит файлом в томе
данных с правами 0600 и в базу не пишется: в бэкап SQLite она не попадает.

Медиа из приватных каналов Telegram не отдаёт публичной ссылкой. Поэтому картинка
или короткое видео тянется через MTProto, когда её просят, и отдаётся через эндпоинт
/api/tg/media с проверкой профиля. Скачанное кэшируется на диске в пределах лимита.

Один файл сессии нельзя открывать двумя клиентами сразу: сервер многопоточный, а
фоновое обновление работает параллельно. Поэтому все обращения к Telegram одного
профиля идут под его замком.
"""
import asyncio
import hashlib
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from html import escape
from urllib.parse import quote

DATA_DIR = os.environ.get("DATA_DIR", "/data")
TG_DIR = os.path.join(DATA_DIR, "tg")

# Официальные ключи Telegram Desktop: вход по ссылке без регистрации приложения
# на my.telegram.org. Свои api_id/api_hash можно задать переменными TG_API_ID и
# TG_API_HASH, если появятся причины их разделить.
API_ID = int(os.environ.get("TG_API_ID", "2040"))
API_HASH = os.environ.get("TG_API_HASH", "b18441a1ff607e10a989891a5462e627")

POSTS_PER_CHANNEL = 40
PREFIX = "private:"
MAX_MEDIA = 15 * 1024 * 1024          # больше не тянем: картинка или короткое видео
MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "video/mp4"}
CACHE_LIMIT = 500 * 1024 * 1024       # весь кэш медиа на диске


class TelegramMissing(Exception):
    """Telethon не установлен в образе."""


class MediaUnavailable(Exception):
    """Файла нет, он слишком большой или его тип не отдаём."""


class ForeignLogin(Exception):
    """Вход начат другим профилем: его токен не опрашиваем и не отменяем."""


MISSING_MESSAGE = "Telethon не установлен в образе"


def installed():
    try:
        import telethon  # noqa: F401
        return True
    except ImportError:
        return False


def session_path(profile):
    """Путь к файлу сессии профиля.

    Имя профиля не годится как имя файла напрямую: это ключ от сессии и в пути
    его показывать не нужно. Поэтому берём короткий хеш — коллизия на практике
    невозможна, а в списке каталога не видно, чей это вход.
    """
    return os.path.join(TG_DIR, hashlib.sha256(profile.encode()).hexdigest()[:24] + ".session")


def _client(profile):
    from telethon import TelegramClient
    os.makedirs(TG_DIR, mode=0o700, exist_ok=True)
    path = session_path(profile)
    # Telethon открывает файл сессии прямо в конструкторе, и он появляется с
    # правами по umask, то есть читаемый всеми. А в файле — готовый вход в
    # аккаунт. Поэтому создаём файл сами и сразу с 0600.
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
    except OSError:
        pass
    client = TelegramClient(path, API_ID, API_HASH)
    return client


# Замок на профиль: Telethon держит сессию в одном файле, и два клиента сразу его портят.
_locks = {}
_locks_guard = threading.Lock()


def _lock(profile):
    with _locks_guard:
        return _locks.setdefault(profile, threading.Lock())


def _tighten(path):
    """Telethon создаёт файл сессии с правами по umask, а тут он может оказаться
    читаемым группой. Вход в аккаунт не должен достаться соседям по машине."""
    for suffix in ("", "-journal", "-shm", "-wal"):
        try:
            os.chmod(path + suffix, 0o600)
        except OSError:
            pass


# --- один event loop на весь процесс -------------------------------------------------
# Сервер многопоточный и синхронный, Telethon асинхронный. Отдельный поток со своим
# циклом избавляет от asyncio.run() в обработчиках запросов: клиенты живут между
# запросами, и логин по QR не должен жить дольше одного вызова.
_loop = None
_loop_ready = threading.Event()


def _loop_thread():
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _loop_ready.set()
    _loop.run_forever()


def loop():
    global _loop
    if _loop is None:
        t = threading.Thread(target=_loop_thread, daemon=True, name="tg-loop")
        t.start()
        _loop_ready.wait(5)
    return _loop


def run(coro, timeout=120):
    """Отправляет корутину в фоновый цикл и ждёт результат."""
    future = asyncio.run_coroutine_threadsafe(coro, loop())
    return future.result(timeout=timeout)


# --- вход ----------------------------------------------------------------------------
# Ожидающие входы живут в памяти: это короткая сессия между кликом в интерфейсе и
# подтверждением в Telegram. Переживать перезапуск им не нужно.
_pending = {}


async def _start(profile):
    client = _client(profile)
    handed = False
    try:
        await client.connect()
        if await client.is_user_authorized():
            me = await client.get_me()
            _tighten(session_path(profile))
            return {"state": "done", "account": _account(me)}
        qr = await client.qr_login()
        _tighten(session_path(profile))
        handed = True
        return {"state": "waiting", "client": client, "qr": qr}
    finally:
        # Клиент, который не передан ожидающему входу, отключаем: он держит
        # файл сессии открытым, а второй клиент на том же файле его портит.
        if not handed:
            try:
                await client.disconnect()
            except Exception:
                pass


def start_link(profile):
    """Начинает вход и отдаёт ссылку, которую надо открыть в Telegram."""
    if not installed():
        raise TelegramMissing(MISSING_MESSAGE)

    # Замок на профиль: и вход, и чтение приватных каналов ходят в один и тот же
    # файл сессии, а Telethon не терпит двух клиентов на одном файле.
    with _lock(profile):
        # Второе устройство того же человека не должно запускать второй вход в тот
        # же профиль поверх первого: ключ по токену, а не по профилю, так что
        # без этой проверки оба висяли бы с отдельными QR.
        for token, entry in list(_pending.items()):
            if entry.get("profile") == profile:
                return {"state": "waiting", "token": token, "url": entry["qr"].url}

        async def go():
            return await _start(profile)

        state = run(go())
        if state.get("state") == "done":
            return state
        token = secrets.token_urlsafe(12)
        entry = {"profile": profile, "client": state["client"], "qr": state["qr"],
                 "started": time.time()}
        # Ждём подтверждения в фоне: событие об импорте может прийти между опросами
        entry["task"] = asyncio.run_coroutine_threadsafe(_watch(entry), loop())
        _pending[token] = entry
        return {"state": "waiting", "token": token, "url": entry["qr"].url}


async def _watch(entry):
    """Ждёт подтверждения и перевыпускает ссылку, пока её не открыли.

    Токен живёт около 30 секунд, после этого Telegram отвечает, что ссылка устарела,
    и нужен новый токен: recreate. Ожидание идёт всё время, а не в момент опроса,
    иначе подтверждение, пришедшее между опросами, теряется.
    """
    qr = entry["qr"]
    try:
        while time.time() - entry["started"] <= 600:
            left = (qr.expires - datetime.now(timezone.utc)).total_seconds()
            try:
                me = await qr.wait(timeout=max(left - 2, 1))
            except asyncio.TimeoutError:
                await qr.recreate()
                continue
            except Exception as exc:
                # Двухфакторный пароль: при входе по QR Telethon его не спрашивает,
                # и аккаунт с ним так не подключится. Молчаливый «ссылка протухла»
                # отправлял человека по кругу, поэтому говорим прямо.
                if "SessionPasswordNeeded" in type(exc).__name__:
                    entry["failed"] = True
                    entry["error"] = "у аккаунта включён двухфакторный пароль, вход по QR не поддерживается"
                    return
                raise
            entry["account"] = _account(me)
            _tighten(session_path(entry["profile"]))
            return
    except Exception:
        pass
    entry["failed"] = True
    # Вход не состоялся: клиент отключаем сам. Иначе он остаётся подключённым
    # и держит файл сессии открытым, пока человек не нажмёт «отменить».
    try:
        await entry["client"].disconnect()
    except Exception:
        pass


def _own_entry(token, profile):
    """Ожидающий вход, если он начат этим профилем. Чужой токен не опрашиваем и не отменяем."""
    entry = _pending.get(token)
    if entry and entry.get("profile") != profile:
        raise ForeignLogin()
    return entry


def poll_link(token, profile):
    """Проверяет, подтвердил ли человек вход, и отдаёт ссылку, которая ещё открывается."""
    entry = _own_entry(token, profile)
    if not entry:
        return {"state": "unknown"}
    if entry.get("account"):
        _drop_pending(token)
        return {"state": "done", "account": entry["account"]}
    if entry.get("failed") or time.time() - entry["started"] > 600:
        error = entry.get("error")
        _drop_pending(token)
        return {"state": "expired", "error": error} if error else {"state": "expired"}
    return {"state": "waiting", "url": entry["qr"].url}


def _drop_pending(token):
    entry = _pending.pop(token, None)
    if not entry:
        return
    if entry.get("task"):
        entry["task"].cancel()

    async def bye():
        try:
            await entry["client"].disconnect()
        except Exception:
            pass

    try:
        run(bye(), timeout=20)
    except Exception:
        pass


def cancel_link(token, profile):
    _own_entry(token, profile)
    _drop_pending(token)
    return {"ok": True}


def _account(me):
    name = " ".join(x for x in (getattr(me, "first_name", ""),
                                getattr(me, "last_name", "")) if x).strip()
    return {"phone": getattr(me, "phone", "") or "", "title": name or (getattr(me, "username", "") or "")}


# --- каналы --------------------------------------------------------------------------
async def _discover(profile):
    client = _client(profile)
    await client.connect()
    if not await client.is_user_authorized():
        raise TelegramMissing("профиль не подключён к Telegram")
    rows = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if not getattr(entity, "broadcast", False):
            continue
        rows.append({"channel": f"{PREFIX}{entity.id}", "entity_id": entity.id,
                     "title": dialog.name, "private": 1})
    await client.disconnect()
    return rows


def discover(profile):
    with _lock(profile):
        return run(_discover(profile), timeout=180)


def _media_kind(m):
    """i — картинка, v — видео, None — остального не берём."""
    if getattr(m, "photo", None):
        return "i"
    if getattr(m, "video", None):
        return "v"
    return None


async def _fetch(profile, channel, limit):
    """Тянет последние сообщения канала и отдаёт их в нашей форме."""
    client = _client(profile)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise TelegramMissing("профиль не подключён к Telegram")
    entity = await client.get_entity(int(channel.removeprefix(PREFIX)))
    messages = await client.get_messages(entity, limit=limit)
    # Альбом Telegram — это несколько сообщений с одним grouped_id. В ленте он один пост,
    # поэтому сообщения сводим в группы, сохраняя порядок от новых к старым.
    groups = {}
    for m in messages:
        key = ("album", m.grouped_id) if getattr(m, "grouped_id", None) else ("one", m.id)
        groups.setdefault(key, []).append(m)
    rows = []
    for items in groups.values():
        items.sort(key=lambda x: x.id)
        media = [{"k": _media_kind(m), "u": f"/api/tg/media?channel={quote(channel, safe='')}&msg={m.id}"}
                 for m in items if _media_kind(m)]
        text = next((m.message for m in items if m.message), "")
        if not text and not media:
            continue
        first = items[0]
        views = None
        if getattr(first, "views", None):
            views = str(getattr(first.views, "count", first.views))
        rows.append({
            "channel": channel,
            "msg_id": first.id,
            # у приватного канала нет имени в адресе, ссылка работает только
            # для участников — ровно то, что нужно
            "url": f"https://t.me/c/{str(entity.id).removeprefix('-100')}/{first.id}",
            # Текст из Telegram приходит без разметки и вставляется в страницу
            # как есть, поэтому экранируем: иначе любой участник приватного
            # канала выполнит свой код у всех, кто его читает. Публичные посты
            # идут через rich_text() в app.py, там та же гарантия.
            "text": escape(text),
            "media": json.dumps(media, ensure_ascii=False) if media else None,
            "views": views,
            "ts": int(first.date.timestamp()),
            "reactions": None, "preview": None, "doc": None,
            "fwd": None, "quote": None, "sticker": None,
        })
    await client.disconnect()
    return rows


def fetch(profile, channel, limit=POSTS_PER_CHANNEL):
    with _lock(profile):
        return run(_fetch(profile, channel, limit), timeout=180)


async def _download(profile, channel, msg_id):
    """Скачивает картинку или видео одного сообщения и отдаёт байты с типом."""
    client = _client(profile)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise TelegramMissing("профиль не подключён к Telegram")
        entity = await client.get_entity(int(channel.removeprefix(PREFIX)))
        msg = await client.get_messages(entity, ids=msg_id)
        if not msg or not (msg.photo or msg.video):
            raise MediaUnavailable("в сообщении нет картинки или видео")
        mime = (msg.file.mime_type if msg.file else "") or ("image/jpeg" if msg.photo else "video/mp4")
        size = (msg.file.size if msg.file else 0) or 0
        if mime not in MEDIA_TYPES or size > MAX_MEDIA:
            raise MediaUnavailable("файл не отдаём: тип или размер")
        data = await client.download_media(msg, file=bytes)
        if not data or len(data) > MAX_MEDIA:
            raise MediaUnavailable("файл не скачался")
        return data, mime
    finally:
        await client.disconnect()


def _media_path(profile, channel, msg_id):
    base = os.path.join(TG_DIR, "media", hashlib.sha256(profile.encode()).hexdigest()[:24])
    return os.path.join(base, hashlib.sha256(f"{channel}/{msg_id}".encode()).hexdigest()[:32])


def _cache_size():
    total = 0
    for root, _dirs, files in os.walk(os.path.join(TG_DIR, "media")):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def media(profile, channel, msg_id):
    """Картинка или видео из приватного поста: с диска, если уже скачивали, иначе через MTProto."""
    path = _media_path(profile, channel, msg_id)
    bin_path, mime_path = path + ".bin", path + ".mime"
    if os.path.exists(bin_path) and os.path.exists(mime_path):
        try:
            with open(mime_path, encoding="utf-8") as f:
                mime = f.read().strip()
            with open(bin_path, "rb") as f:
                return f.read(), mime
        except OSError:
            # Кэш побит: удаляем его, иначе файл без .mime читался бы вечно.
            for stale in (bin_path, mime_path):
                try:
                    os.remove(stale)
                except OSError:
                    pass
    with _lock(profile):
        data, mime = run(_download(profile, channel, msg_id), timeout=120)
    if _cache_size() + len(data) <= CACHE_LIMIT:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        for suffix, content in ((".bin", data), (".mime", mime.encode())):
            # Пишем во временный файл и переименовываем: прерванная запись
            # больше не оставляет файл, который потом читается как готовый.
            tmp = path + suffix + ".tmp"
            with open(tmp, "wb") as f:
                f.write(content)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path + suffix)
    return data, mime


def linked(profile):
    if not installed():
        return False
    return os.path.exists(session_path(profile))


def drop_session(profile):
    """Выход: стираем файл сессии, иначе вход в аккаунт остаётся жить. Кэш медиа тоже."""
    # Ожидающий вход отменяем: он успел бы снова записать файл сессии сразу
    # после удаления, и «Telegram отключён» было бы неправдой.
    for token, entry in list(_pending.items()):
        if entry.get("profile") == profile:
            _drop_pending(token)
    import shutil
    shutil.rmtree(os.path.join(TG_DIR, "media", hashlib.sha256(profile.encode()).hexdigest()[:24]),
                  ignore_errors=True)
    path = session_path(profile)
    removed = False
    for suffix in ("", "-journal", "-shm", "-wal"):
        try:
            os.remove(path + suffix)
            removed = True
        except OSError:
            pass
    return removed


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps({"installed": installed(), "dir": TG_DIR,
                      "session_exists": os.path.exists(session_path(sys.argv[1] if len(sys.argv) > 1 else "x"))},
                     ensure_ascii=False))
