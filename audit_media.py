#!/usr/bin/env python3
"""Аудит медиа: сверяет базу с тем, что t.me отдаёт прямо сейчас.

Ловит поломку, которую не видно в тестах на фикстурах: Telegram меняет разметку
превью, и в базе остаются посты, где вместо фото лежит эмодзи, а от альбома
сохранился только первый кадр.

Запуск:
    python3 audit_media.py [база.db]
    docker compose cp feed:/data/feed.db ./feed.db && python3 audit_media.py

Нужен интернет. Служебный инструмент, в образ не попадает и в тестах не гоняется.

Коды возврата:
    0 — расхождений нет и проверка полная
    1 — есть расхождения
    2 — проверять нечего (в базе нет постов)
    3 — проверка неполная: часть каналов не удалось проверить

Код 3 важен: раньше аудит рапортовал «расхождений: 0» и выходил с нулём именно
тогда, когда парсер переставал видеть посты, — то есть зеленел ровно при той
поломке, ради которой он написан.
"""
import json
import sqlite3
import sys
import time

import app


def media(raw):
    try:
        return json.loads(raw or "[]")
    except ValueError:
        return []


def ident(items):
    # у видео Telegram каждые несколько часов меняет token после вопросительного
    # знака, а путь с именем файла остаётся прежним. По полному адресу сравнивать
    # бессмысленно: аудит светился на каждом ролике, у которого переехал токен
    return [(m.get("k"), (m.get("u") or "").split("?")[0]) for m in items]


AUDIT_MAX_PAGES = 40   # страниц на канал: хватает на всю догруженную историю
AUDIT_PAUSE = 2.0      # секунд между страницами, чтобы не мешать t.me


def fetch_window(channel):
    """Посты канала на всю глубину, а не только на первой странице.

    Медиа старых постов в базе тоже может поехать, а первая страница про него
    ничего не скажет. Страницы идут с паузой, как идёт фон, чтобы не мешать
    t.me.
    """
    posts = []
    before = None
    for page in range(AUDIT_MAX_PAGES):
        _, chunk = app.fetch_channel(channel, before=before)
        if not chunk:
            break
        posts.extend(chunk)
        before = min(chunk, key=lambda p: p["msg_id"])["msg_id"]
        if page + 1 < AUDIT_MAX_PAGES:
            time.sleep(AUDIT_PAUSE)
    return posts


def main(path):
    db = sqlite3.connect(path)
    channels = [r[0] for r in db.execute("SELECT DISTINCT channel FROM posts")]
    total = db.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    if not channels:
        # sqlite3 создаёт пустой файл молча: без этой проверки аудит рапортовал
        # «0 расхождений» о базе, в которой нет ни одного поста
        print(f"{path}: в базе нет постов, проверять нечего")
        return 2
    print(f"{path}: постов {total}, каналов {len(channels)}")
    wrong = []
    skipped = []
    compared = 0
    for channel in channels:
        try:
            fetched = fetch_window(channel)
        except Exception as exc:  # канал мог закрыться или отдать не то
            print(f"  {channel}: не проверен ({exc})")
            skipped.append(channel)
            continue
        stored = {msg: media(med) for msg, med in db.execute(
            "SELECT msg_id,media FROM posts WHERE channel=?", (channel,))}
        fresh = {post["msg_id"]: media(post["media"]) for post in fetched}
        for msg_id, items in fresh.items():
            old = stored.get(msg_id)
            if old is None:
                continue  # поста ещё нет в базе, это не расхождение
            compared += 1
            if ident(old) == ident(items):
                continue
            if [m for m in old if "emoji" in (m.get("u") or "")] and items:
                what = "эмодзи вместо медиа"
            elif items and not old:
                what = "медиа потеряно в базе"
            elif old and not items:
                what = "медиа в базе, на странице нет"
            else:
                what = "адрес или состав устарели"
            wrong.append((channel, msg_id, what))
    print(f"проверено каналов: {len(channels) - len(skipped)} из {len(channels)}, "
          f"сверено постов: {compared}")
    print(f"расхождений: {len(wrong)}")
    for channel, msg_id, what in wrong[:40]:
        print(f"  {channel}/{msg_id}: {what}")
    if wrong:
        return 1
    if skipped:
        # Проверка неполная: зелёный результат здесь означал бы, что мы ничего
        # не увидели, а не то, что всё в порядке.
        print(f"не проверены каналы: {', '.join(skipped)}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "feed.db"))
