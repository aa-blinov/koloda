#!/usr/bin/env python3
"""Аудит медиа: сверяет базу с тем, что t.me отдаёт прямо сейчас.

Ловит поломку, которую не видно в тестах на фикстурах: Telegram меняет разметку
превью, и в базе остаются посты, где вместо фото лежит эмодзи, а от альбома
сохранился только первый кадр.

Запуск:
    python3 audit_media.py [база.db]
    docker compose cp feed:/data/feed.db ./feed.db && python3 audit_media.py

Нужен интернет. Служебный инструмент, в образ не попадает и в тестах не гоняется.
"""
import json
import sqlite3
import sys

import app


def media(raw):
    try:
        return json.loads(raw or "[]")
    except ValueError:
        return []


def main(path):
    db = sqlite3.connect(path)
    channels = [r[0] for r in db.execute("SELECT DISTINCT channel FROM posts")]
    posts = db.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    if not channels:
        # sqlite3 создаёт пустой файл молча: без этой проверки аудит рапортовал
        # «0 расхождений» о базе, в которой нет ни одного поста
        print(f"{path}: в базе нет постов, проверять нечего")
        return 2
    print(f"{path}: постов {posts}, каналов {len(channels)}")
    wrong = []
    for channel in channels:
        try:
            _, posts = app.fetch_channel(channel)
        except Exception as exc:  # канал мог закрыться или отдать не то
            print(f"  {channel}: не проверен ({exc})")
            continue
        stored = {msg: media(med) for msg, med in db.execute(
            "SELECT msg_id,media FROM posts WHERE channel=?", (channel,))}
        fresh = {post["msg_id"]: media(post["media"]) for post in posts}
        for msg_id, items in fresh.items():
            if stored.get(msg_id) == items:
                continue
            old = stored.get(msg_id)
            if old is None:
                continue  # поста ещё нет в базе, это не расхождение
            if [m for m in old if "emoji" in m["u"]] and items:
                what = "эмодзи вместо медиа"
            elif items and not old:
                what = "медиа потеряно в базе"
            elif old and not items:
                what = "медиа в базе, на странице нет"
            else:
                what = "адрес или состав устарели"
            wrong.append((channel, msg_id, what))
    print(f"расхождений: {len(wrong)} из {posts}")
    for channel, msg_id, what in wrong[:40]:
        print(f"  {channel}/{msg_id}: {what}")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "feed.db"))