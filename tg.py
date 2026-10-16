#!/usr/bin/env python3
"""Слой Telegram: вход профиля по ссылке и чтение приватных каналов.

Зачем он отдельным файлом: без входа проект работает ровно как раньше, публичные
каналы читаются по-прежнему через t.me/s/<имя>, и Telethon даже не импортируется.
Пока нет профиля с подключённым Telegram, сюда никто не заходит.

Sessия — это готовый вход в аккаунт, а не пароль. Поэтому она лежит файлом в томе
данных с правами 0600 и в базу не пишется: в бэкап SQLite она не попадает.

Чего модуль пока не умеет: медиа из приватных каналов. Telegram не отдаёт для них
публичную ссылку на файл — картинку надо тянуть через MTProto и отдавать самому
через отдельный эндпоинт с проверкой профиля. Пока приходят только текстовые посты
со ссылкой на оригинал.
"""
import asyncio
import hashlib
import os
import secrets
import threading
import time

DATA_DIR = os.environ.get("DATA_DIR", "/data")
TG_DIR = os.path.join(DATA_DIR, "tg")

# Официальные ключи Telegram Desktop: вход по ссылке без регистрации приложения
# на my.telegram.org. Свои api_id/api_hash можно задать переменными TG_API_ID и
# TG_API_HASH, если появятся причины их разделить.
API_ID = int(os.environ.get("TG_API_ID", "2040"))
API_HASH = os.environ.get("TG_API_HASH", "b18441a1ff607e10a989891a5462e627")

POSTS_PER_CHANNEL = 40
PREFIX = "private:"


class TelegramMissing(Exception):
    """Telethon не установлен в образе."""


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
    client = TelegramClient(path, API_ID, API_HASH)
    return client


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
    await client.connect()
    if await client.is_user_authorized():
        me = await client.get_me()
        _tighten(session_path(profile))
        return {"state": "done", "account": _account(me)}
    qr = await client.qr_login()
    _tighten(session_path(profile))
    return {"state": "waiting", "client": client, "qr": qr}


def start_link(profile):
    """Начинает вход и отдаёт ссылку, которую надо открыть в Telegram."""
    if not installed():
        raise TelegramMissing("Telethon не установлен в образе")

    async def go():
        return await _start(profile)

    state = run(go())
    if state.get("state") == "done":
        return state
    token = secrets.token_urlsafe(12)
    _pending[token] = {"profile": profile, "client": state["client"], "qr": state["qr"],
                       "started": time.time()}
    return {"state": "waiting", "token": token, "url": state["qr"].url}


async def _finish(entry):
    qr, client = entry["qr"], entry["client"]
    try:
        await asyncio.wait_for(qr.wait(), timeout=5)
    except asyncio.TimeoutError:
        return {"state": "waiting"}
    me = await client.get_me()
    _tighten(session_path(entry["profile"]))
    return {"state": "done", "account": _account(me)}


def poll_link(token):
    """Проверяет, подтвердил ли человек вход в Telegram."""
    entry = _pending.get(token)
    if not entry:
        return {"state": "unknown"}
    if time.time() - entry["started"] > 600:
        _drop_pending(token)
        return {"state": "expired"}
    result = run(_finish(entry))
    if result["state"] == "done":
        _drop_pending(token)
    return result


def _drop_pending(token):
    entry = _pending.pop(token, None)
    if not entry:
        return

    async def bye():
        try:
            await entry["client"].disconnect()
        except Exception:
            pass

    try:
        run(bye(), timeout=20)
    except Exception:
        pass


def cancel_link(token):
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
    return run(_discover(profile), timeout=180)


async def _fetch(profile, channel, limit):
    """Тянет последние сообщения канала и отдаёт их в нашей форме."""
    client = _client(profile)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise TelegramMissing("профиль не подключён к Telegram")
    entity = await client.get_entity(int(channel.removeprefix(PREFIX)))
    messages = await client.get_messages(entity, limit=limit)
    rows = []
    for m in messages:
        if not m.message:
            continue
        views = None
        if getattr(m, "views", None):
            views = str(getattr(m.views, "count", m.views))
        rows.append({
            "channel": channel,
            "msg_id": m.id,
            # у приватного канала нет имени в адресе, ссылка работает только
            # для участников — ровно то, что нужно
            "url": f"https://t.me/c/{str(entity.id).removeprefix('-100')}/{m.id}",
            "text": m.message,
            "media": None,
            "views": views,
            "ts": int(m.date.timestamp()),
            "reactions": None, "preview": None, "doc": None,
            "fwd": None, "quote": None, "sticker": None,
        })
    await client.disconnect()
    return rows


def fetch(profile, channel, limit=POSTS_PER_CHANNEL):
    return run(_fetch(profile, channel, limit), timeout=180)


def linked(profile):
    if not installed():
        return False
    return os.path.exists(session_path(profile))


def drop_session(profile):
    """Выход: стираем файл сессии, иначе вход в аккаунт остаётся жить."""
    _tighten  # noqa: B018
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
