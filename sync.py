#!/usr/bin/env python3
"""Собирает сообщения из Telegram-каналов в SQLite и отдаёт их как RSS.

Запуск:
    python3 sync.py          # разовая выгрузка
    python3 serve.py         # раздача фида на http://127.0.0.1:8899/feed.xml
"""
import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone

import qrcode
from telethon import TelegramClient, functions
from telethon.errors import RPCError

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "lenta.db")
SESSION = os.path.join(BASE, "session")
CONFIG = os.path.join(BASE, "config.json")
CHANNELS = os.path.join(BASE, "channels.txt")

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    channel_id  INTEGER NOT NULL,
    msg_id      INTEGER NOT NULL,
    channel     TEXT    NOT NULL,
    title       TEXT    NOT NULL,
    url         TEXT,
    body        TEXT,
    ts          INTEGER NOT NULL,
    PRIMARY KEY (channel_id, msg_id)
);
CREATE INDEX IF NOT EXISTS messages_ts ON messages (ts DESC);
"""


# Официальные ключи Telegram Desktop. Позволяют войти по QR-коду
# без регистрации приложения на my.telegram.org.
TDESKTOP_API_ID = 2040
TDESKTOP_API_HASH = "b18441a1ff607e10a989891a5462e627"


def config():
    """api_id/api_hash необязательны: без них берутся ключи Telegram Desktop."""
    cfg = {"api_id": TDESKTOP_API_ID, "api_hash": TDESKTOP_API_HASH}
    if os.path.exists(CONFIG):
        with open(CONFIG) as f:
            cfg.update(json.load(f))
    cfg.setdefault("limit_per_channel", 50)
    return cfg


def wanted_channels():
    """Пустой файл = все каналы из диалогов."""
    if not os.path.exists(CHANNELS):
        return []
    out = []
    with open(CHANNELS) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    return out


def save(db, channel_id, channel_title, username, messages):
    rows = []
    for m in messages:
        if not m.message and not m.media:
            continue
        if username:
            url = f"https://t.me/{username}/{m.id}"
        else:
            # Приватный канал: ссылка работает только для участников.
            url = f"https://t.me/c/{str(channel_id).replace('-100', '')}/{m.id}"
        rows.append(
            (
                channel_id,
                m.id,
                channel_title,
                (m.message or "")[:200].replace("\n", " ").strip(),
                url,
                m.message or "[медиа]",
                int(m.date.replace(tzinfo=timezone.utc).timestamp()),
            )
        )
    db.executemany(
        "INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?)", rows
    )
    db.commit()
    return len(rows)


async def resolve(client, target):
    """Приводит @username / t.me/... / -100...id / t.me/+invite к entity."""
    t = target.strip()
    if t.startswith("-100") and t[4:].isdigit():
        return await client.get_entity(int(t))

    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if t.startswith(prefix):
            t = t[len(prefix):]
            break

    if t.startswith("+") or t.startswith("joinchat/"):
        # Внимание: импорт инвайта ВСТУПЛЯЕТ в канал, если ты ещё не участник.
        invite_hash = t.lstrip("+").removeprefix("joinchat/")
        result = await client(functions.messages.ImportChatInviteRequest(invite_hash))
        chats = getattr(result, "chats", None) or []
        if not chats:
            raise ValueError("инвайт не вернул чат (уже участник?) — укажи -100...id")
        return chats[0]

    return await client.get_entity(t.lstrip("@"))


async def login(client):
    """QR-вход с телефона. tdata Telegram Desktop не переиспользуется: это
    другой формат хранения, поэтому нужен один раз отсканировать код."""
    if not client.is_connected():
        await client.connect()
    if await client.is_user_authorized():
        return

    print("В Telegram на Маке: Settings -> Devices -> Link Desktop Device")
    print("Затем отсканируй QR, который появится ниже. Код обновляется каждые ~20 с.\n")
    qr_login = await client.qr_login()
    while True:
        render_qr(qr_login.url)
        print("Ожидаю сканирование...")
        try:
            # Telegram даёт QR ~25 секунд; ловим протухание и берём новый.
            await asyncio.wait_for(qr_login.wait(), timeout=20)
            break
        except asyncio.TimeoutError:
            await qr_login.recreate()
            print("\nКод протух, беру новый.\n")
    print("Готово.")


def render_qr(url):
    try:
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception:
        # qrcode не обязателен: ссылку можно открыть и вручную.
        print(f"QR не отрисовался, открой ссылку: {url}")


async def main():
    cfg = config()
    limit = int(cfg.get("limit_per_channel", 50))
    client = TelegramClient(SESSION, cfg["api_id"], cfg["api_hash"])
    await login(client)

    db = sqlite3.connect(DB)
    db.executescript(SCHEMA)

    targets = wanted_channels()
    if targets:
        channels = []
        for t in targets:
            try:
                channels.append((t, await resolve(client, t)))
            except (RPCError, ValueError, KeyError) as e:
                print(f"  ! {t}: {e}")
    else:
        channels = [(d.name, d.entity) for d in await client.get_dialogs()]

    total = 0
    for name, entity in channels:
        if not getattr(entity, "broadcast", False):
            continue
        try:
            msgs = await client.get_messages(entity, limit=limit)
        except RPCError as e:
            print(f"  ! {name}: {e}")
            continue
        n = save(db, entity.id, entity.title, entity.username, msgs)
        total += n
        print(f"  {entity.title}: {n}")

    print(f"Итого сохранено: {total} сообщений -> {DB}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
