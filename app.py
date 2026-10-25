#!/usr/bin/env python3
"""Веб-сервис: единая лента из ПУБЛИЧНЫХ Telegram-каналов.

Посты берутся из публичной превью-страницы t.me/s/<name>.
Профили сайта защищены паролем; приватные Telegram-каналы так не читаются.

Запуск: python3 app.py  [порт]
Открыть: http://127.0.0.1:8000
"""
import json
import gzip
import hashlib
import http.cookies
import ipaddress
import smtplib
import os
import queue
import re
import secrets
import sqlite3
import sys
import threading
import time
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape, unescape
from urllib.parse import parse_qs, urlencode, urlparse

import qrsvg  # QR-код ссылки входа для сканирования с компьютера
import tg  # слой Telegram. Без входа профиля он ничего не делает и не ходит в сеть.

BASE = os.path.dirname(os.path.abspath(__file__))
# База в подкаталоге: volume монтируется на /data, чтобы не перекрывать код.
DATA = os.environ.get("DATA_DIR", os.path.join(BASE, "data"))
DB = os.path.join(DATA, "feed.db")
# Порт: аргумент > $PORT (docker) > 8000
try:
    CLI_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else None
except ValueError:
    CLI_PORT = None
PORT = CLI_PORT or int(os.environ.get("PORT", 8000))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    channel    TEXT    NOT NULL,
    msg_id     INTEGER NOT NULL,
    url        TEXT    NOT NULL,
    text       TEXT,
    media      TEXT,
    views      TEXT,
    ts         INTEGER NOT NULL,
    reactions  TEXT,
    preview    TEXT,
    doc        TEXT,
    fwd        TEXT,
    quote      TEXT,
    sticker    TEXT,
    PRIMARY KEY (channel, msg_id)
);
CREATE INDEX IF NOT EXISTS posts_ts ON posts (ts DESC);
CREATE TABLE IF NOT EXISTS saved_posts (
    profile TEXT NOT NULL,
    channel TEXT NOT NULL,
    msg_id INTEGER NOT NULL,
    saved INTEGER NOT NULL,
    PRIMARY KEY (profile, channel, msg_id)
);
CREATE TABLE IF NOT EXISTS backfill (
    channel TEXT PRIMARY KEY,
    next_before INTEGER,              -- с какого msg_id тянуть следующую страницу вглубь
    fetched INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    retry_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cache (
    channel  TEXT PRIMARY KEY,
    title    TEXT,
    fetched  INTEGER
);
-- Фото каналов из og:image страницы t.me/s. Пустой url: фото у канала нет, второй раз его не ищем
CREATE TABLE IF NOT EXISTS avatars (
    channel  TEXT PRIMARY KEY,
    url      TEXT,
    fetched  INTEGER
);
-- Состояние пользователя: подписки и прочитанные посты.
-- Доступ к профилю задаётся паролем и серверной сессией.
CREATE TABLE IF NOT EXISTS profiles (
    code    TEXT PRIMARY KEY,
    created INTEGER,
    settings TEXT,
    initialized INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS user_subs (
    profile TEXT NOT NULL,
    channel TEXT NOT NULL,
    PRIMARY KEY (profile, channel)
);
CREATE TABLE IF NOT EXISTS collections (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    profile TEXT    NOT NULL,
    name    TEXT    NOT NULL,
    created INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS collections_profile ON collections (profile);
CREATE TABLE IF NOT EXISTS collection_channels (
    collection INTEGER NOT NULL,
    channel    TEXT    NOT NULL,
    PRIMARY KEY (collection, channel)
);
CREATE TABLE IF NOT EXISTS user_reads (
    profile TEXT    NOT NULL,
    post    TEXT    NOT NULL,
    PRIMARY KEY (profile, post)
);
CREATE TABLE IF NOT EXISTS accounts (
    username TEXT PRIMARY KEY COLLATE NOCASE,
    profile  TEXT NOT NULL UNIQUE,
    salt     TEXT NOT NULL,
    digest   TEXT NOT NULL,
    created  INTEGER NOT NULL,
    email    TEXT,
    email_verified INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS auth_tokens (
    token_hash TEXT PRIMARY KEY,
    profile    TEXT NOT NULL,
    purpose    TEXT NOT NULL,
    email      TEXT,
    created    INTEGER NOT NULL,
    expires    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_tokens_expiry ON auth_tokens(expires);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    profile    TEXT NOT NULL,
    created    INTEGER NOT NULL,
    expires    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions (expires);
CREATE TABLE IF NOT EXISTS tg_accounts (
    profile    TEXT PRIMARY KEY,
    phone      TEXT,
    title      TEXT,
    linked     INTEGER
);
CREATE TABLE IF NOT EXISTS tg_channels (
    profile    TEXT NOT NULL,
    channel    TEXT NOT NULL,
    entity_id  INTEGER,
    title      TEXT,
    updated    INTEGER,
    PRIMARY KEY (profile, channel)
);
-- Приватные посты лежат отдельно от публичных и привязаны к профилю, который
-- принёс их своим входом в Telegram. Класть их в posts нельзя: регистрация на
-- сайте открыта, и общая корзина отдала бы чужую приватную ленту всем.
CREATE TABLE IF NOT EXISTS private_posts (
    profile    TEXT NOT NULL,
    channel    TEXT NOT NULL,
    msg_id     INTEGER NOT NULL,
    url        TEXT NOT NULL,
    text       TEXT,
    media      TEXT,
    views      TEXT,
    ts         INTEGER NOT NULL,
    PRIMARY KEY (profile, channel, msg_id)
);
CREATE INDEX IF NOT EXISTS private_posts_ts ON private_posts (profile, ts DESC);
"""

# Имя канала попадает в URL t.me, поэтому проверяем форму: без этого
# в локальной сети можно было бы заставить сервис ходить по произвольным адресам.
# fullmatch, а не ^…$: знак $ пропускает перевод строки в конце имени.
CHANNEL_RE = re.compile(r"[A-Za-z0-9_]{3,32}")
# Приватный канал адреса в t.me не имеет, поэтому в ленте он зовется ключом
# private:-100… В URL такие имена не попадают — по ним никто не ходит, — так что
# строгая проверка обычного имени тут неприменима. Цифры только ASCII: \d пропускает
# и арабские, а int() их тоже разбирает.
PRIVATE_RE = re.compile(r"private:-100[0-9]{6,20}")
# Целое из строки запроса или пути: только ASCII-цифры. str.isdigit() пропускает
# «²», которую int() не разбирает, и запрос рвал соединение вместо ответа 400.
DIGITS = re.compile(r"[0-9]+")

# Короткий TTL: t.me отдаёт превью без ограничений, но и обновляется не мгновенно.
TTL = 300
TTL_SPREAD = 60       # TTL канала = TTL ± TTL_SPREAD: каналы, загруженные вместе, не устаревают разом
FETCH_TIMEOUT = 8     # сколько ждём ответа t.me на один канал
REFRESH_TIMEOUT = 8   # сколько запрос ленты ждёт канал, которого ещё нет в кэше
BACKFILL_CAP = 500    # постов истории на канал; дальше только в Telegram
BACKFILL_PAUSE = 4    # секунд между страницами истории: не дёргаем t.me
BACKFILL_IDLE = 5     # секунд ждать, когда догружать нечего: новая подписка начнётся не позже
MEDIA_SWEEP_EVERY = int(os.environ.get("MEDIA_SWEEP_EVERY", 6 * 3600))  # секунд между проходами одного канала
MEDIA_WINDOW_DAYS = 3       # на сколько дней назад освежаем ссылки: старше кадры уже не открываем
MEDIA_SWEEP_MAX_PAGES = 12  # страниц на проход, около двухсот постов; дальше хватит запроса на пост
MEDIA_PAUSE = 3       # секунд между страницами и каналами: не дёргаем t.me
MEDIA_IDLE = 60       # секунд ждать, когда все подписанные каналы свежие
COLLECTION_NAME_MAX = 32       # символов в названии коллекции
COLLECTION_CHANNELS_CAP = 200  # каналов в одной коллекции, как и подписок в ленте
COLLECTIONS_CAP = 50           # коллекций на профиль
FEED_PAGE_SIZE = 80
CHANNEL_PAGE_SIZE = 10   # публикаций канала на экране за одну подзагрузку
MIN_GZIP = 1024        # меньше килобайта сжатие не окупает служебных байт заголовка
# Собранный интерфейс: его делает сборка на node (ui/), сервер раздаёт готовое
UI_DIR = os.path.join(BASE, "ui", "dist")
UI_ASSET_TYPES = {".js": "application/javascript; charset=utf-8",
                   ".css": "text/css; charset=utf-8",
                   ".map": "application/json; charset=utf-8",
                   ".svg": "image/svg+xml",
                   ".png": "image/png",
                   ".ico": "image/x-icon",
                   ".woff2": "font/woff2"}


def connect():
    db = sqlite3.connect(DB)
    # WAL: чтение ленты не ждёт фоновой записи кэша. Режим хранится в самом файле базы,
    # поэтому переключится один раз. synchronous=NORMAL — пара к WAL: быстрее, и при
    # сбое питания теряются последние транзакции, а сама база не портится.
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    # Без этого SQLite ждёт блокировку записи 5 секунд и потом отдаёт
    # "database is locked": фоновый писатель кэша держит её дольше. Таймаут
    # ожидания блокировки отделён от таймаута соединения, поэтому задаётся явно.
    db.execute("PRAGMA busy_timeout=15000")
    db.executescript(SCHEMA)
    columns = {row[1] for row in db.execute("PRAGMA table_info(profiles)")}
    if "initialized" not in columns:
        db.execute("ALTER TABLE profiles ADD COLUMN initialized INTEGER NOT NULL DEFAULT 0")
    account_columns = {row[1] for row in db.execute("PRAGMA table_info(accounts)")}
    if "email" not in account_columns:
        db.execute("ALTER TABLE accounts ADD COLUMN email TEXT")
    if "email_verified" not in account_columns:
        db.execute("ALTER TABLE accounts ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")
    post_columns = {row[1] for row in db.execute("PRAGMA table_info(posts)")}
    if "media" not in post_columns:
        # image и video схлопывались в один кадр, из-за чего альбомы теряли всё,
        # кроме первого медиа. Старые колонки остаются в таблице пустыми.
        db.execute("ALTER TABLE posts ADD COLUMN media TEXT")
    profile_columns = {row[1] for row in db.execute("PRAGMA table_info(profiles)")}
    if "settings" not in profile_columns:
        db.execute("ALTER TABLE profiles ADD COLUMN settings TEXT")
    # Индекс по почте: без него два профиля с одной почтой получили бы
    # одинаковый ключ восстановления. Старые базы могли остаться с пустой почтой
    # или с дублями — тогда создание индекса падало, и сервис не поднимался вовсе.
    # Поэтому перед индексом пустые почты становятся пустыми (NULL), а дубли
    # оставляют адрес только у первого профиля.
    db.execute("UPDATE accounts SET email=NULL WHERE email IS NOT NULL AND TRIM(email)=''")
    dupes = db.execute("SELECT email, MIN(rowid) FROM accounts WHERE email IS NOT NULL "
                       "GROUP BY email COLLATE NOCASE HAVING COUNT(*)>1").fetchall()
    for email, keep in dupes:
        db.execute("UPDATE accounts SET email=NULL WHERE email=? COLLATE NOCASE AND rowid<>?",
                   (email, keep))
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS accounts_email ON accounts(email COLLATE NOCASE) "
               "WHERE email IS NOT NULL")
    db.commit()
    return db


def strip_pagination(html):
    """Убирает хвост «1 • 2 • 3 • More» — это навигация по группе сообщений,
    а не текст поста.

    «More» бывает двух видов: ссылкой (если ты не на этой странице группы) или
    жирным шрифтом (если ты на ней). Берём оба.

    Режем от последнего <br> перед «More» до конца фрагмента, но только если в
    хвосте нет ничего, кроме цифр и разделителей, а все ссылки ведут в Telegram.
    Иначе это настоящий текст поста, и такой пост не трогаем."""
    mi = html.rfind(">More</")
    if mi == -1:
        return html
    cut = html.rfind("<br", 0, mi)
    if cut == -1:
        return html
    # Проверяем только часть до «More»: в ней допустимы цифры и разделители.
    tail = unescape(re.sub(r"<[^>]*>?", "", html[cut:mi])).replace("\xa0", " ")
    if set(tail) - set("1234567890 •· \n\t"):
        return html                                       # в хвосте есть слова — это текст
    links = re.findall(r'href="([^"]+)"', html[cut:])
    if not links or any("t.me/" not in h for h in links):
        return html                                       # ссылки ведут не в Telegram
    return html[:cut].rstrip()


# Теги, которые Telegram использует в теле поста. Всё остальное выбрасываем:
# именно из-за срезания <b> и <i> пост выглядел плоским.
ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
                "code", "pre", "br", "p", "blockquote", "a"}
# Содержимое этих тегов выбрасываем целиком, а не оставляем текстом.
DROP_CONTENT = {"script", "style"}
TAG = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)([^>]*)>")
SAFE_URL = ("http://", "https://", "tg://", "mailto://")


def rich_text(fragment):
    """Тело поста в безопасном HTML: разрешённые теги, никаких атрибутов
    кроме проверенного href. Всё остальное экранируется, поэтому текст из
    канала не может принести разметку."""
    s = strip_pagination(fragment)
    out, pos, stack, skip = [], 0, [], 0
    for m in TAG.finditer(s):
        if skip:
            continue
        # unescape перед escape: иначе "&amp;" превратится в "&amp;amp;"
        out.append(escape(unescape(s[pos:m.start()]), quote=False))
        pos = m.end()
        closing, name, attrs = m.group(1), m.group(2).lower(), m.group(3)
        if name in DROP_CONTENT:
            if not closing:
                end = s.lower().find("</" + name, pos)
                pos = end + len(name) + 3 if end != -1 else len(s)
            continue
        if name not in ALLOWED_TAGS:
            continue                      # тег выкидываем, текст внутри остаётся
        if name == "br":
            out.append("<br>")
        elif name == "a":
            h = re.search(r'href\s*=\s*"([^"]*)"', attrs) or \
                re.search(r"href\s*=\s*'([^']*)'", attrs)
            url = unescape(h.group(1)) if h else ""
            if closing:
                if "a" in stack:
                    while stack:
                        t = stack.pop(); out.append("</" + t + ">")
                        if t == "a": break
            elif url.startswith(SAFE_URL):
                out.append('<a href="' + escape(url, quote=True) +
                           '" target="_blank" rel="noopener">')
                stack.append("a")
        elif closing:
            if name in stack:
                while stack:
                    t = stack.pop(); out.append("</" + t + ">")
                    if t == name: break
        else:
            out.append("<" + name + ">"); stack.append(name)
    if not skip:
        out.append(escape(unescape(s[pos:]), quote=False))
    while stack:
        out.append("</" + stack.pop() + ">")
    return "".join(out)


def strip_html(s):
    s = strip_pagination(s)
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"</p>", "\n\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    # html.unescape знает все именованные сущности (middot, nbsp, lt, amp...),
    # ручной список их всегда упускает.
    return unescape(s).replace("\xa0", " ").strip()


def _bg(chunk, pattern):
    """Достаёт url из style="background-image:url(...)" по заданному классу."""
    m = re.search(r'class="' + pattern + r'[^"]*"[^>]*background-image:url\(([^)]+)\)', chunk)
    return unescape(m.group(1)).strip("'\"") if m else None


def _text(chunk, cls):
    """Видимый текст элемента по классу. Класс в разметке Telegram всегда идёт
    с дополнительными словами ("...title accent_color"), поэтому закрываем кавычку
    через [^"]*, а не сразу после имени. Закрывающим тегом может быть и div,
    и span: имя автора в ответе Telegram лежит в span."""
    m = re.search(r'class="' + cls + r'[^"]*"[^>]*>(.*?)</(?:div|span)>', chunk, re.S)
    if not m:
        return None
    return unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() or None


EMOJI_IMAGE = re.compile(r"/emoji/\d+/([0-9A-Fa-f]+)\.png")


def emoji_from_image(style):
    """Обычный эмодзи приходит картинкой t.me; её имя — код символа в UTF-8 (E29DA4 — ❤)."""
    m = EMOJI_IMAGE.search(style)
    if not m:
        return ""
    try:
        return bytes.fromhex(m.group(1)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ""


def parse_reactions(chunk):
    """Реакции: обычные эмодзи (картинкой, символ в имени файла), звёзды и кастомные.
    У кастомных в разметке только emoji-id, символа нет — выдумывать смайлик нельзя,
    поэтому отдаём пустую метку."""
    box = re.search(r'class="tgme_widget_message_reactions[^"]*"[^>]*>(.*?)</div>', chunk, re.S)
    if not box:
        return None
    out = []
    for r in re.finditer(r'<span class="tgme_reaction[^"]*">(.*?)</span>', box.group(1), re.S):
        inner = r.group(1)
        paid = "stars" in inner
        style = re.search(r'<i class="emoji"[^>]*style="([^"]*)"', inner)
        text = re.search(r"<b>(.*?)</b>", inner, re.S)
        if style:
            emoji = emoji_from_image(style.group(1))
        elif text:
            emoji = unescape(text.group(1))
        else:
            emoji = "⭐" if paid else ""
        # Счётчик — то, что после иконки и символа, иначе в него попадёт сам символ.
        tail = re.sub(r"<i\b.*?</i>|<b>.*?</b>", "", inner, flags=re.S)
        count = re.sub(r"<[^>]+>", "", tail).strip()
        if not count:
            continue
        out.append({"emoji": emoji, "count": count, "paid": paid})
    return out or None


def parse_preview(chunk):
    """Карточка ссылки: сайт, заголовок, описание, картинка."""
    m = re.search(r'<a class="tgme_widget_message_link_preview"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', chunk, re.S)
    if not m:
        return None
    inner = m.group(2)
    return {"url": unescape(m.group(1)), "site": _text(inner, "link_preview_site_name"),
            "title": _text(inner, "link_preview_title"),
            "desc": _text(inner, "link_preview_description"),
            "image": _bg(inner, "link_preview_image") or _bg(inner, "link_preview_right_image")}


def parse_doc(chunk):
    """Файл: имя, размер/длительность, тип иконки (audio/video/voice/...)."""
    m = re.search(r'<a class="tgme_widget_message_document_wrap[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', chunk, re.S)
    if not m:
        return None
    inner = m.group(2)
    icon = re.search(r'class="tgme_widget_message_document_icon[^"]*\s(\w+)', inner)
    return {"url": unescape(m.group(1)), "title": _text(inner, "tgme_widget_message_document_title"),
            "meta": _text(inner, "tgme_widget_message_document_extra"),
            "kind": icon.group(1) if icon else "file"}


def parse_fwd(chunk):
    """Откуда переслали. В живой разметке это span, а не ссылка: имя лежит
    во вложенном span, а адреса у него часто нет вовсе. Раньше разбирался
    только вариант со ссылкой, и пересылка терялась целиком."""
    m = re.search(r'<(a|span) ([^>]*class="tgme_widget_message_forwarded_from_name"[^>]*)>(.*?)</\1>',
                  chunk, re.S)
    if not m:
        return None
    inner = m.group(3)
    name = unescape(re.sub(r"<[^>]+>", "", inner)).strip()
    href = re.search(r'href="([^"]+)"', m.group(2))
    url = unescape(href.group(1)) if href else ""
    if not name and not url:
        return None
    return {"url": url, "name": name}


def parse_quote(chunk):
    """Ответ на публикацию: автор и текст. В живой разметке класс ответа —
    tgme_widget_message_reply (не ..._reply_quote), а имя автора лежит в span."""
    m = re.search(r'<a ([^>]*class="tgme_widget_message_reply(?![_\w-])[^"]*"[^>]*)>', chunk)
    if not m:
        return None
    href = re.search(r'href="([^"]+)"', m.group(1))
    rest = chunk[m.end():]
    # Блок ответа заканчивается перед основным текстом публикации.
    stop = rest.find("js-message_text")
    inner = rest[:stop] if stop != -1 else rest
    return {"url": unescape(href.group(1)) if href else "",
            "author": _text(inner, "tgme_widget_message_author_name"),
            "text": _text(inner, "tgme_widget_message_text")}


def valid_channel(name):
    # isinstance, а не `name or ""`: из браузера прилетает всё, включая список,
    # на котором match() падал с TypeError и рвал соединение без ответа
    return isinstance(name, str) and bool(CHANNEL_RE.fullmatch(name))


def private_channel(name):
    return isinstance(name, str) and bool(PRIVATE_RE.fullmatch(name))


def valid_feed_channel(name):
    """Имя канала в запросе ленты: обычное имя или ключ приватного канала."""
    return valid_channel(name) or private_channel(name)


class ChannelError(ValueError):
    """Свои сообщения о канале («не найден», «t.me попросил подождать»): их можно показать.
    Любое другое исключение наружу не отдаём: в тексте бывают адрес и детали запроса."""


# Новая разметка t.me не отдаёт текст некоторых публикаций: вместо него заглушка «открой в Telegram».
# Без подписи карточка была пустой, поэтому даём короткую строку и ссылку на саму публикацию.
UNSUPPORTED_RE = re.compile(r"message_media_not_supported|text_not_supported")


def unsupported_text(chunk, owner, msg_id):
    if not UNSUPPORTED_RE.search(chunk) or not valid_channel(owner):
        return ""
    return rich_text('<i>Публикация открывается только в Telegram.</i> '
                     f'<a href="https://t.me/{owner}/{msg_id}">Открыть</a>')


def fetch_channel(channel, before=None):
    """Один канал -> (title, [posts]). before — страница старее этого msg_id.
    Бросает ChannelError, если канала нет, и когда t.me попросил подождать (капча)."""
    url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    html = urllib.request.urlopen(req, timeout=FETCH_TIMEOUT).read().decode("utf-8", "replace")

    t = re.search(r'property="og:title" content="([^"]+)"', html) or \
        re.search(r"<title>([^<]+)</title>", html)
    # название приходит с мнемониками (Tom &amp; Jerry), в базе и в ответе нужен текст
    title = unescape(t.group(1)).strip() if t else channel

    posts = []
    for m in re.finditer(r'<div class="tgme_widget_message[^"]*"[^>]*data-post="([^"]+)"', html):
        start = m.start()
        nxt = html.find('data-post="', m.end())
        chunk = html[start:nxt if nxt != -1 else len(html)]

        # data-post отдаёт канонический username, а страницу открыли по псевдониму
        # (tonlive -> tlive). Кладём в базу под именем подписки, иначе выборка по
        # нему не найдёт ни одного поста. В ссылке оставляем каноническое имя.
        owner, _, msg_id = m.group(1).partition("/")
        if not DIGITS.fullmatch(msg_id):
            continue
        txt = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', chunk, re.S)
        # Медиа берём из контейнеров: первый background-image в посте — это обычно
        # кастомный эмодзи из текста, и он перебивал настоящие снимки.
        photos = [unescape(u.strip("'\"")) for u in re.findall(
            r'tgme_widget_message_photo_wrap[^"]*"[^>]*background-image:url\(([^)]+)\)', chunk)]
        # Видео лежит в <video src> — прямая ссылка с токеном, отдаётся как mp4.
        videos = [unescape(u) for u in re.findall(r'<video[^>]+src="([^"]+)"', chunk)]
        videos += [unescape(u) for u in re.findall(r'<video[^>]+src=\'([^\']+)\'', chunk)]
        # t.me отдаёт один и тот же файл в двух <video> поста: в альбоме он стоял дважды
        videos = list(dict.fromkeys(videos))
        # Превью и пропорции кадра лежат рядом с плеером: миниатюра в теге i и
        # padding-top обёртки (высота в процентах от ширины). Без них клиент не знает,
        # какой рамки ждать до загрузки видео. Ключи p и r необязательные.
        video_meta = {}
        for part in re.split(r'(?=<a class="tgme_widget_message_video_player)', chunk)[1:]:
            src = re.search(r'<video[^>]+src="([^"]+)"', part)
            if not src:
                continue
            meta = {}
            poster = _bg(part, "tgme_widget_message_video_thumb")
            ratio = re.search(r'tgme_widget_message_video_wrap"[^>]*padding-top:([\d.]+)%', part)
            if poster:
                meta["p"] = poster
            if ratio:
                meta["r"] = round(float(ratio.group(1)) / 100, 4)
            video_meta.setdefault(unescape(src.group(1)), meta)
        media = [{"k": "i", "u": u} for u in photos] + [
            {"k": "v", "u": u, **video_meta.get(u, {})} for u in videos]
        # Видео без src (круглое, с превью) отдаём обложкой.
        thumb = _bg(chunk, "tgme_widget_message_video_wrap")
        if not media and thumb:
            media = [{"k": "i", "u": thumb}]
        views = re.search(r'tgme_widget_message_views">([^<]+)<', chunk)
        dt = re.search(r'<time datetime="([^"]+)"', chunk)

        ts = 0
        if dt:
            try:
                ts = int(datetime.fromisoformat(dt.group(1)).timestamp())
            except ValueError:
                ts = 0

        # Стикер: настоящая картинка лежит в data-webp, а background-image у того же элемента
        # это заглушка-анимация в base64
        sticker_m = re.search(r'class="tgme_widget_message_sticker[ "][^>]*data-webp="([^"]+)"', chunk)
        sticker = sticker_m.group(1) if sticker_m else None
        posts.append({
            "channel": channel,
            "msg_id": int(msg_id),
            "url": f"https://t.me/{owner}/{msg_id}",
            "text": rich_text(txt.group(1)) if txt else unsupported_text(chunk, owner, msg_id),
            # Список медиа одним JSON: альбомы хранятся целиком.
            "media": J(media),
            "views": views.group(1).strip() if views else None,
            "ts": ts,
            "reactions": J(parse_reactions(chunk)),
            "preview": J(parse_preview(chunk)),
            "doc": J(parse_doc(chunk)),
            "fwd": J(parse_fwd(chunk)),
            "quote": J(parse_quote(chunk)),
            "sticker": unescape(sticker) if sticker else None,
        })

    # t.me на несуществующий канал отдаёт свою заглушку с чужим og:title.
    # Единственный честный признак канала — посты в его ленте. Пустая страница с before
    # — не ошибка, а конец истории: догрузка по ней помечает канал полным.
    if not posts:
        # Признак блокировки смотрим только на странице без постов: там слову «captcha»
        # неоткуда взяться, кроме самой заглушки t.me. Раньше проверка шла по всему
        # html, и канал, в тексте поста которого есть слово «captcha», оказывался
        # недоступен целиком.
        low = html.lower()
        if "captcha" in low or "too many requests" in low:
            raise ChannelError("t.me попросил подождать, попробуем позже")
    if not posts and before is None:
        raise ChannelError(f"канал «{channel}» не найден, закрыт или без постов")
    return title, posts


J = lambda v: json.dumps(v, ensure_ascii=False) if v else None

# Колонки постов в одном месте: копия этого списка в выборке или INSERT
# разъезжается с таблицей после миграции.
POST_FIELDS = ("channel", "msg_id", "url", "text", "media", "views", "ts",
               "reactions", "preview", "doc", "fwd", "quote", "sticker")
POST_INSERT = (f"INSERT OR REPLACE INTO posts ({','.join(POST_FIELDS)}) VALUES "
               f"({','.join(':' + f for f in POST_FIELDS)})")


PRIVATE_FIELDS = ("channel", "msg_id", "url", "text", "media", "views", "ts",
                  "reactions", "preview", "doc", "fwd", "quote", "sticker")


def like_pattern(q):
    """Образец для LIKE ... ESCAPE '\\': % и _ в запросе читаются как буквы, а не как шаблон."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_private_posts(db, q, channels, profile, limit=120, before=None, hide_read=None):
    """То же, что search_posts, но из приватной корзины и только своего профиля.

    Отдельная таблица и обязательный profile: общий запрос по постам отдавал бы
    чужую приватную ленту всем, кто умеет назвать её ключ.
    """
    sql = ("SELECT channel,msg_id,url,text,media,views,ts,"
           " NULL,NULL,NULL,NULL,NULL,NULL FROM private_posts"
           " WHERE profile=? AND 1=1")
    args = [profile]
    if channels:
        sql += " AND channel IN (%s)" % ",".join("?" * len(channels))
        args += channels
    if q:
        sql += " AND text LIKE ? ESCAPE '\\'"
        args.append(like_pattern(q))
    if hide_read:
        sql += (" AND NOT EXISTS (SELECT 1 FROM user_reads r"
                " WHERE r.profile=? AND r.post=private_posts.channel || '/' || private_posts.msg_id)")
        args.append(hide_read)
    if before:
        ts, channel, msg_id = before
        sql += (" AND (ts < ? OR (ts = ? AND (channel > ? OR"
                " (channel = ? AND msg_id < ?))))")
        args.extend((ts, ts, channel, channel, msg_id))
    sql += " ORDER BY ts DESC, channel ASC, msg_id DESC LIMIT ?"
    args.append(limit)
    return db.execute(sql, args).fetchall()


# История канала догружается в фоне: подписанные публичные каналы листаются вглубь
# страницами по BACKFILL_PAUSE секунд. Позиция хранится в таблице backfill, поэтому
# перезапуск её не сбрасывает. Посты пишутся без изменения cache: иначе догрузка
# сбрасывала бы отсчёт обычного обновления канала. Без постов в базе листать не от чего:
# канал, подписанный раньше первой записи ленты, закрылся бы полным с нулём навсегда.
def backfill_candidates(db):
    now = int(time.time())
    rows = db.execute(
        "SELECT DISTINCT s.channel FROM user_subs s "
        "LEFT JOIN backfill b ON b.channel = s.channel "
        "WHERE COALESCE(b.done, 0) = 0 AND COALESCE(b.retry_at, 0) <= ? "
        "AND EXISTS (SELECT 1 FROM posts p WHERE p.channel = s.channel) "
        "ORDER BY COALESCE(b.fetched, 0), s.channel", (now,)).fetchall()
    return public_channels([r[0] for r in rows])


def backfill_step(db):
    """Одна страница истории одного канала. False, если догружать нечего."""
    candidates = backfill_candidates(db)
    if not candidates:
        return False
    channel = candidates[0]
    now = int(time.time())
    row = db.execute("SELECT next_before, fetched, failures FROM backfill WHERE channel=?",
                     (channel,)).fetchone()
    next_before, fetched, failures = row if row else (None, 0, 0)
    if next_before is None:
        next_before = db.execute("SELECT MIN(msg_id) FROM posts WHERE channel=?",
                                 (channel,)).fetchone()[0]
    try:
        _, posts = fetch_channel(channel, before=next_before)
    except Exception:
        failures += 1
        retry_at = now + min(60 * 2 ** (failures - 1), 3600)
        db.execute("INSERT OR REPLACE INTO backfill VALUES (?,?,?,0,?,?)",
                   (channel, next_before, fetched, failures, retry_at))
        return True
    db.executemany(POST_INSERT, posts)
    older = [p["msg_id"] for p in posts if p["msg_id"] < next_before]
    fetched += len(older)
    done = not older or fetched >= BACKFILL_CAP
    db.execute("INSERT OR REPLACE INTO backfill VALUES (?,?,?,?,0,0)",
               (channel, min(older) if older else next_before, fetched, int(done)))
    return True


# t.me меняет адреса файлов в ссылках, старые дают 404, а лента перезаписывает пост
# только когда канал загружен заново. Поэтому битый кадр просит свежие ссылки на пост.
MEDIA_RECHECK = 600     # секунд: один пост в t.me чаще не ходим
_media_checked = {}     # (канал, msg_id) -> когда последний раз ходили в t.me


def store_media(db, channel, posts):
    """Свежие медиа и карточки ссылок всех постов страницы. Пишем только посты, которые уже
    в базе: новые приходят через синхронизацию. Страница стоит одного запроса, поэтому все её
    посты помечаем проверенными, и соседние протухшие кадры не идут в t.me повторно."""
    now = time.time()
    for p in posts:
        _media_checked[(channel, p["msg_id"])] = now
        db.execute("UPDATE posts SET media=?, preview=?, sticker=? WHERE channel=? AND msg_id=?",
                   (p["media"], p["preview"], p["sticker"], channel, p["msg_id"]))
    db.commit()


# Запросы к t.me из пути запроса ограничены: страница с десятком битых кадров
# иначе устраивала десяток параллельных загрузок, а t.me отвечал на это
# блокировкой — и лента гасла у всех сразу.
_ondemand_lock = threading.Lock()
_ondemand_next = 0.0
ONDEMAND_PAUSE = 0.7     # секунд между такими запросами


def _ondemand_slot():
    """Встать в очередь к t.me: один запрос за раз и не чаще раза в ONDEMAND_PAUSE."""
    global _ondemand_next
    with _ondemand_lock:
        wait = _ondemand_next - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _ondemand_next = time.monotonic() + ONDEMAND_PAUSE


def refresh_media(db, channel, msg_id):
    """Свежие медиа и карточка ссылки одного поста или None, если поста нет в базе.
    Страница до msg_id + 1 захватывает сам пост и более старые, поэтому работает и для
    постов, которых нет на первой странице канала. Картинка карточки ссылки мёртвая
    по тем же причинам, что и кадры, поэтому обновляется в той же записи. Всю страницу
    пишем сразу: на ней около двадцати постов, и соседние протухшие кадры не должны
    снова идти в t.me."""
    row = db.execute("SELECT media, preview, sticker FROM posts WHERE channel=? AND msg_id=?",
                     (channel, msg_id)).fetchone()
    if row is None:
        return None
    stored = {"media": json.loads(row[0] or "[]"), "preview": json.loads(row[1] or "null"),
              "sticker": row[2]}
    key = (channel, msg_id)
    if time.time() - _media_checked.get(key, 0) < MEDIA_RECHECK:
        return stored
    if len(_media_checked) > 5000:
        _media_checked.clear()
    _media_checked[key] = time.time()
    _ondemand_slot()
    _, posts = fetch_channel(channel, before=msg_id + 1)
    store_media(db, channel, posts)
    fresh = next((p for p in posts if p["msg_id"] == msg_id), None)
    if fresh is None:  # пост удалили или t.me не отдал его на этой странице
        return stored
    return {"media": json.loads(fresh["media"] or "[]"), "preview": json.loads(fresh["preview"] or "null"),
            "sticker": fresh["sticker"]}


# Фон освежает ссылки подписанных публичных каналов. Без него кадр, который никто не открыл,
# стоит протухшим до клика, а потом идёт в t.me по одному посту. Время последнего прохода
# держим в памяти: после перезапуска каналы пройдут заново, один раз на канал.
_media_swept = {}  # канал -> когда последний раз проходили
_media_delay = {}  # канал -> через сколько секунд проходить снова
MEDIA_RETRY = 15 * 60   # после ошибки t.me повторяем скоро, а не через шесть часов


def media_sweep(db, channel):
    """Страницы канала от свежих к старым, пока не дойдём до окна. Ошибка t.me поднимается наверх."""
    cutoff = time.time() - MEDIA_WINDOW_DAYS * 86400
    before = None
    for _ in range(MEDIA_SWEEP_MAX_PAGES):
        _, posts = fetch_channel(channel, before=before)
        if not posts:
            return
        store_media(db, channel, posts)
        oldest = min(posts, key=lambda p: p["msg_id"])
        if oldest["ts"] < cutoff:
            return
        before = oldest["msg_id"]
        time.sleep(MEDIA_PAUSE)


def media_step(db):
    """Один канал за проход. False, если все подписанные каналы проверены недавно."""
    now = time.time()
    channels = public_channels([r[0] for r in db.execute("SELECT DISTINCT channel FROM user_subs")])
    due = [c for c in channels if now - _media_swept.get(c, 0) >= _media_delay.get(c, MEDIA_SWEEP_EVERY)]
    if not due:
        return False
    channel = min(due, key=lambda c: _media_swept.get(c, 0))
    _media_swept[channel] = now
    try:
        media_sweep(db, channel)
        _media_delay[channel] = MEDIA_SWEEP_EVERY
    except Exception as exc:
        # t.me ответил ошибкой или попросил подождать: повторяем скоро. Раньше
        # канал после одной ошибки ждал полный круг, и битые ссылки в ленте
        # оставались битыми до вечера.
        _media_delay[channel] = MEDIA_RETRY
        print("фон медиа: " + type(exc).__name__, file=sys.stderr, flush=True)
    return True


def _media_loop():
    while True:
        worked = False
        try:
            db = connect()
            try:
                worked = media_step(db)
                db.commit()
            finally:
                db.close()
        except Exception as exc:
            print("фон медиа: " + type(exc).__name__, file=sys.stderr, flush=True)
        time.sleep(MEDIA_PAUSE if worked else MEDIA_IDLE)


# Приватные каналы обновляются в фоне, как публичные: раз в PRIVATE_TTL секунд и только
# те, что стоят в ленте. Ошибка Telegram (нет входа, лимит запросов) не повторяется
# чаще, чем раз в PRIVATE_RETRY секунд.
PRIVATE_TTL = 900
PRIVATE_RETRY = 600
_private_retry = {}   # (профиль, канал) -> когда можно пробовать снова


def store_private_rows(db, cid, channel, rows):
    db.executemany(
        "INSERT OR REPLACE INTO private_posts "
        "(profile,channel,msg_id,url,text,media,views,ts) VALUES (?,?,?,?,?,?,?,?)",
        [(cid, r["channel"], r["msg_id"], r["url"], r["text"], r["media"],
          r["views"], r["ts"]) for r in rows])


def private_sync_step(db):
    """Обновляет один приватный канал из ленты, который давно не обновлялся.
    False, если делать нечего."""
    now = int(time.time())
    due = db.execute(
        "SELECT t.profile, t.channel FROM tg_channels t JOIN user_subs s "
        "ON s.profile=t.profile AND s.channel=t.channel "
        "WHERE COALESCE(t.updated,0) <= ? ORDER BY COALESCE(t.updated,0) LIMIT 20",
        (now - PRIVATE_TTL,)).fetchall()
    for profile, channel in due:
        if _private_retry.get((profile, channel), 0) > now or not tg.linked(profile):
            continue
        try:
            rows = tg.fetch(profile, channel)
        except Exception as exc:
            print("приватный канал не обновился: " + type(exc).__name__, file=sys.stderr, flush=True)
            _private_retry[(profile, channel)] = now + PRIVATE_RETRY
            return True
        store_private_rows(db, profile, channel, rows)
        db.execute("UPDATE tg_channels SET updated=? WHERE profile=? AND channel=?",
                   (now, profile, channel))
        db.commit()
        return True
    return False


def parse_range(header, total):
    """Диапазон из заголовка Range: (начало, конец) включительно или None, если заголовок не про байты.
    ValueError — диапазон вне файла."""
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not m or not (m.group(1) or m.group(2)):
        return None
    if m.group(1):
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else total - 1
    else:  # суффикс: последние N байт
        start = max(0, total - int(m.group(2)))
        end = total - 1
    end = min(end, total - 1)
    if start > end or start >= total:
        raise ValueError("диапазон вне файла")
    return start, end


def _private_loop():
    while True:
        worked = False
        try:
            db = connect()
            try:
                worked = private_sync_step(db)
                db.commit()
            finally:
                db.close()
        except Exception as exc:
            # только тип ошибки: текст может содержать что-то из Telegram, а в журнал такое не пишем
            print("фон приватных каналов: " + type(exc).__name__, file=sys.stderr, flush=True)
        time.sleep(2 if worked else 30)


def _backfill_loop():
    while True:
        worked = False
        try:
            db = connect()
            try:
                worked = backfill_step(db)
                db.commit()
            finally:
                db.close()
        except Exception:
            pass
        time.sleep(BACKFILL_PAUSE if worked else BACKFILL_IDLE)


def history_status(db, profile):
    """Состояние догрузки истории по каждому подписанному публичному каналу.
    waiting — ждёт очереди, loading — догружается, paused — t.me ответил ошибкой и
    ждём повтора, done — история канала кончилась, capped — упёрлись в BACKFILL_CAP."""
    subs = public_channels([r[0] for r in db.execute(
        "SELECT channel FROM user_subs WHERE profile=? ORDER BY channel", (profile,))]) if profile else []
    if not subs:
        return {"loading": False, "fetched": 0, "cap": BACKFILL_CAP, "channels": []}
    marks = ",".join("?" * len(subs))
    rows = {c: (f, d, fa, ra) for c, f, d, fa, ra in db.execute(
        "SELECT channel,fetched,done,failures,retry_at FROM backfill WHERE channel IN (%s)" % marks, subs)}
    titles = dict(db.execute("SELECT channel,title FROM cache WHERE channel IN (%s)" % marks, subs))
    now = int(time.time())
    channels = []
    for c in subs:
        fetched, done, failures, retry_at = rows.get(c, (0, 0, 0, 0))
        if done:
            state = "capped" if fetched >= BACKFILL_CAP else "done"
        elif failures and retry_at > now:
            state = "paused"
        elif c not in rows:
            state = "waiting"
        else:
            state = "loading"
        channels.append({"channel": c, "title": titles.get(c) or c, "state": state, "fetched": fetched})
    return {"loading": any(ch["state"] in ("waiting", "loading", "paused") for ch in channels),
            "fetched": sum(ch["fetched"] for ch in channels),
            "cap": BACKFILL_CAP, "channels": channels}


def public_channels(channels):
    return [c for c in channels if valid_channel(c)]


def search_posts(db, q, channels, limit=120, before=None, hide_read=None):
    """Простой LIKE поверх SQLite — FTS5 тут оверинжиниринг."""
    sql = "SELECT " + ",".join(POST_FIELDS) + " FROM posts WHERE 1=1"
    args = []
    if channels:
        sql += " AND channel IN (%s)" % ",".join("?" * len(channels))
        args += channels
    if q:
        sql += " AND text LIKE ? ESCAPE '\\'"
        args.append(like_pattern(q))
    if hide_read:
        sql += (" AND NOT EXISTS (SELECT 1 FROM user_reads r"
                " WHERE r.profile=? AND r.post=posts.channel || '/' || posts.msg_id)")
        args.append(hide_read)
    if before:
        ts, channel, msg_id = before
        sql += (" AND (ts < ? OR (ts = ? AND (channel > ? OR"
                " (channel = ? AND msg_id < ?))))")
        args.extend((ts, ts, channel, channel, msg_id))
    sql += " ORDER BY ts DESC, channel ASC, msg_id DESC LIMIT ?"
    args.append(limit)
    return db.execute(sql, args).fetchall()


def channel_ttl(channel):
    """TTL канала с разбросом: каналы, загруженные одним запросом, не устаревают разом."""
    return TTL + zlib.crc32(channel.encode()) % (2 * TTL_SPREAD + 1) - TTL_SPREAD


# Обновление каналов в фоне (stale-while-revalidate). Лента отдаёт кэш сразу, а
# устаревшие каналы тянет в фоне. Ждём только канал, которого ещё нет в кэше, и
# не дольше REFRESH_TIMEOUT. Сеть идёт через пул потоков, а запись в кэш делает
# один фоновый поток со своим подключением, поэтому конфликтов записи нет.
_jobs = {}        # канал -> Event, пока его загружают
_failed = {}      # канал -> {"message", "count", "retry_at"}, пока последняя попытка не удалась
_jobs_lock = threading.Lock()
_workers_lock = threading.Lock()
_fetch_pool = None
_writes = queue.Queue()


def _ensure_workers():
    global _fetch_pool
    with _workers_lock:
        if _fetch_pool is None:
            _fetch_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="fetch")
            threading.Thread(target=_cache_writer, daemon=True, name="cache-writer").start()


# Текст ошибки уходит в ленту, поэтому сырой str(exc) (адрес urllib, «Errno …») туда не попадает
CHANNEL_FAILED = "не удалось загрузить канал"


def _cache_writer():
    while True:
        channel, title, posts = _writes.get()
        error = None
        try:
            db = connect()
            try:
                store_channel_cache(db, channel, title, posts)
                db.commit()
            finally:
                db.close()
        except Exception as exc:
            print("кэш канала " + channel + ": " + type(exc).__name__, file=sys.stderr, flush=True)
            error = CHANNEL_FAILED
        _finish(channel, error)


def _fetch_job(channel):
    try:
        title, posts = fetch_channel(channel)
    except ChannelError as exc:
        _finish(channel, str(exc)[:180])
        return
    except Exception as exc:
        print("канал " + channel + ": " + type(exc).__name__, file=sys.stderr, flush=True)
        _finish(channel, CHANNEL_FAILED)
        return
    _writes.put((channel, title, posts))


def _finish(channel, error):
    with _jobs_lock:
        event = _jobs.pop(channel, None)
        if error is None:
            _failed.pop(channel, None)
        else:
            count = _failed.get(channel, {}).get("count", 0) + 1
            # После ошибки t.me не дёргаем на каждый запрос: пауза растёт до 15 минут
            _failed[channel] = {"message": error, "count": count,
                                "retry_at": time.time() + min(60 * 2 ** (count - 1), 900)}
    if event:
        event.set()


def request_refresh(db, channels, wait_all=False):
    """Ставит в фон устаревшие каналы и возвращает события, которых стоит дождаться:
    для каналов без кэша, а при wait_all и для всех устаревших."""
    now = int(time.time())
    cached = dict(db.execute(
        "SELECT channel,fetched FROM cache WHERE channel IN (%s)" %
        ",".join("?" * len(channels)), channels
    )) if channels else {}
    waits = []
    with _jobs_lock:
        for channel in channels:
            needed = wait_all or channel not in cached
            if channel in _jobs:
                if needed:
                    waits.append(_jobs[channel])
                continue
            if channel in cached and now - cached[channel] < channel_ttl(channel):
                continue
            failure = _failed.get(channel)
            if failure and time.time() < failure["retry_at"]:
                continue
            event = _jobs[channel] = threading.Event()
            if needed:
                waits.append(event)
            _ensure_workers()
            _fetch_pool.submit(_fetch_job, channel)
    return waits


def wait_for(events):
    deadline = time.time() + REFRESH_TIMEOUT
    for event in events:
        event.wait(max(0.0, deadline - time.time()))


def failures_for(channels):
    with _jobs_lock:
        return [{"channel": c, "message": _failed[c]["message"]} for c in channels if c in _failed]


def wait_refresh_idle(timeout=10):
    """Ждёт, пока фоновые загрузки закончатся. Нужна тестам."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _jobs_lock:
            if not _jobs:
                return True
        time.sleep(0.01)
    return False


def refresh_channels(db, channels):
    """Превью канала: обновляет устаревшие каналы и ждёт результата по ним.

    Принудительного обновления нет намеренно: иначе любой, кто знает адрес,
    заставил бы сервер обходить t.me, и Telegram заблокировал бы адрес всей ленты.
    """
    wait_for(request_refresh(db, channels, wait_all=True))
    return failures_for(channels)


def store_channel_cache(db, channel, title, posts):
    db.executemany(
        POST_INSERT,
        posts,
    )
    db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)",
               (channel, title, int(time.time())))


# Фото каналов. Берём og:image со страницы t.me/s, которую приложение и так открывает.
# Ссылку пускаем только на картинки Telegram (telesco.pe, без кавычек и скобок): она
# уходит в атрибут src, и чужой адрес в ответе t.me в ленту попадать не должен.
# Ссылка на фото — короткоживущая: через неделю в кэше уже 404 (проверено на проде). Поэтому кэш
# держит её шесть часов, а клиент, у которого картинка не открылась, просит свежую (fresh=1).
AVATAR_TTL = 6 * 3600
AVATAR_FRESH_COOLDOWN = 10 * 60  # секунд: канал по просьбе fresh берём с t.me не чаще, чем раз в десять минут
AVATAR_FETCH_CAP = 12   # страница t.me весит около 150 КБ: за один запрос ходим не больше чем за 12 каналов
AVATAR_RETRY = 10 * 60  # секунд: канал, который t.me не ответил, спросим снова не раньше чем через десять минут
AVATAR_URL = re.compile(r'https://[\w.-]+\.telesco\.pe/[^\s"\'<>]+')


def avatar_url(html):
    m = re.search(r'property="og:image" content="([^"]+)"', html)
    if not m:
        return ""
    url = unescape(m.group(1))
    return url if AVATAR_URL.fullmatch(url) else ""


def fetch_avatar(channel):
    req = urllib.request.Request(f"https://t.me/s/{channel}", headers={"User-Agent": UA})
    html = urllib.request.urlopen(req, timeout=FETCH_TIMEOUT).read().decode("utf-8", "replace")
    return avatar_url(html)


def channel_avatars(db, channels, fresh=()):
    """Фото каналов: сначала своя таблица, устаревшие и новые догружаем с t.me, но не больше AVATAR_FETCH_CAP.
    fresh — каналы, у которых картинка не открылась: их берём с t.me заново (с кулдауном).
    Возвращает (фото по каналам, какие каналы уже проверены): непроверенные спросим в следующий раз."""
    now = int(time.time())
    known = {}
    if channels:
        marks = ",".join("?" * len(channels))
        known = {c: (u, f) for c, u, f in db.execute(
            f"SELECT channel, url, fetched FROM avatars WHERE channel IN ({marks})", channels)}
    stale = [c for c in channels if c not in known or now - known[c][1] > AVATAR_TTL
             or (c in fresh and now - known[c][1] > AVATAR_FRESH_COOLDOWN)][:AVATAR_FETCH_CAP]

    def pull(channel):
        try:
            return channel, fetch_avatar(channel), True
        except Exception:  # t.me не ответил: отмечаем, но спросим снова не раньше чем через AVATAR_RETRY
            return channel, "", False

    with ThreadPoolExecutor(max_workers=6) as pool:
        for channel, url, ok in pool.map(pull, stale):
            # страница без фото и сбой тоже отмечаем пустой строкой: канал проверен, и его не спрашивают
            # на каждом заходе. Сбой помнится как проверка, которая истекает раньше обычной
            stamp = now if ok else now - AVATAR_TTL + AVATAR_RETRY
            db.execute("INSERT OR REPLACE INTO avatars VALUES (?,?,?)", (channel, url, stamp))
            known[channel] = (url, stamp)
    db.commit()
    avatars = {c: known[c][0] for c in channels if c in known and known[c][0]}
    checked = [c for c in channels if c in known]
    return avatars, checked


# TGMania — открытый каталог каналов (без ключа). Сортировку на их стороне
# приходится переделывать: ответ приходит не по subscriber_count.
TGMANIA = "https://tgmania.com/api/channels"
CATALOG_PAGE = 40     # каналов на страницу каталога; TGMania отдаёт до 50


def last_post_times(db):
    """Время последнего поста по каждому каналу: по нему каталог сортирует свежесть."""
    return dict(db.execute("SELECT channel, MAX(ts) FROM posts GROUP BY channel"))


def search_catalog(q="", category="", limit=CATALOG_PAGE, last_posts=None, offset=0):
    """Каталог каналов из TGMania. Возвращает [{username,name,desc,subs,q,cat,last_ts}]."""
    params = {"limit": min(limit, 50), "type": "channel"}
    if offset:
        params["offset"] = offset
    if q:
        params["q"] = q
    if category and category != "all":
        params["category"] = category

    url = TGMANIA + "?" + urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    data = json.loads(urllib.request.urlopen(req, timeout=20).read())

    out = []
    for c in data.get("channels", []):
        u = c.get("username")
        # Имя из внешнего каталога проверяем так же, как ввод человека: иначе в
        # подписки и в разметку попадает то, чему там быть нельзя.
        if not u or not valid_channel(u):
            continue
        # Название приходит уже HTML-экранированным — без unescape получится "Robots &amp; Art".
        out.append({
            "username": u,
            "name": unescape(c.get("name") or u),
            "desc": unescape(c.get("description") or "").replace("\n", " ")[:110],
            "subs": c.get("subscriber_count") or 0,
            "q": c.get("quality_score") or "",
            "cat": unescape(c.get("category_name") or ""),
            # У категорий короткие служебные обозначения (например, `art`,
            # `tech`), которые не совпадают с транслитерацией названия.
            "cat_slug": c.get("category_slug") or "",
            # Свежесть последнего поста считаем по своей базе: у TGMania её нет.
            "last_ts": (last_posts or {}).get(u, 0),
        })

    return out


CATEGORY_TTL = 24 * 60 * 60  # секунд: список категорий пересобираем раз в сутки
CATEGORY_PAGES = 40          # страниц выдачи TGMania на один обход, по 50 каналов
CATEGORY_CACHE = {"items": None, "built": 0.0, "busy": False}  # в памяти: после перезапуска соберётся заново
_category_lock = threading.Lock()


def category_items(channels):
    """Категории из каналов: одна запись на название. TGMania отдаёт одну категорию с разными
    slug (например, `bots` и `bots-tools`), оба фильтруют одно и то же, берём самый частый."""
    counts = {}
    for c in channels:
        name = c["cat"]
        slug = c["cat_slug"] or name.lower().replace(" ", "-").replace("&", "and")
        if name and slug:
            counts[(name, slug)] = counts.get((name, slug), 0) + 1
    best = {}
    for (name, slug), n in counts.items():
        if name not in best or n > best[name][1]:
            best[name] = (slug, n)
    return sorted(({"name": name, "slug": slug} for name, (slug, _) in best.items()), key=lambda x: x["name"])


def build_categories():
    """Проходит страницы выдачи TGMania и собирает все категории, которые она отдаёт."""
    channels = []
    for page in range(CATEGORY_PAGES):
        found = search_catalog(limit=50, offset=page * 50)
        if not found:
            break
        channels.extend(found)
    return category_items(channels)


def refresh_categories():
    try:
        items = build_categories()
        if items:
            CATEGORY_CACHE.update(items=items, built=time.time())
    except Exception:
        pass  # TGMania не ответила: остаётся прежний список
    finally:
        CATEGORY_CACHE["busy"] = False


def catalog_categories():
    """Список категорий для чипов. Собирается в фоне и живёт сутки. Пока первый обход не готов,
    отдаём категории первой страницы выдачи, чтобы первый заход не ждал двадцать запросов."""
    with _category_lock:
        stale = time.time() - CATEGORY_CACHE["built"] > CATEGORY_TTL
        if (CATEGORY_CACHE["items"] is None or stale) and not CATEGORY_CACHE["busy"]:
            CATEGORY_CACHE["busy"] = True
            threading.Thread(target=refresh_categories, daemon=True, name="categories").start()
    if CATEGORY_CACHE["items"] is not None:
        return CATEGORY_CACHE["items"]
    return category_items(search_catalog(limit=50))


ACCOUNT_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9_-]{3,32}$")
SESSION_COOKIE = "lenta_session"
SESSION_TTL = 30 * 24 * 60 * 60
UNVERIFIED_TTL = 24 * 60 * 60  # неподтверждённую учётку старше этого отдаём под новую регистрацию
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
LOGIN_FAILURES = {}
EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
SMTP_ENABLED = os.environ.get("SMTP_ENABLED", "false").lower() == "true"
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://127.0.0.1:8090").rstrip("/")
MAIL_REQUESTS = {}


def password_digest(password, salt):
    # PBKDF2 доступен и в системном Python 3.9, и в контейнере приложения.
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               bytes.fromhex(salt), 600_000, dklen=64).hex()


def valid_email(value):
    return isinstance(value, str) and len(value) <= 254 and EMAIL_RE.fullmatch(value.strip())


def send_email(recipient, subject, body):
    if not SMTP_ENABLED or not SMTP_HOST or not SMTP_FROM:
        raise RuntimeError("Почта не настроена")
    message = EmailMessage()
    message["From"] = SMTP_FROM
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    if SMTP_PORT == 465:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=12) as server:
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(message)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=12) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(message)


def issue_auth_token(db, profile, purpose, email=None, lifetime=24 * 60 * 60):
    now = int(time.time())
    db.execute("DELETE FROM auth_tokens WHERE profile=? AND purpose=?", (profile, purpose))
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO auth_tokens VALUES (?,?,?,?,?,?)",
               (hashlib.sha256(token.encode()).hexdigest(), profile, purpose,
                email, now, now + lifetime))
    return token


def confirmation_link(path, token):
    return APP_BASE_URL + path + "?" + urlencode({"token": token})


def send_verification_email(email, token):
    link = confirmation_link("/verify", token)
    send_email(email, "Подтверди почту для своей ленты",
               "Чтобы подтвердить адрес почты, открой ссылку:\n\n" + link +
               "\n\nСсылка действует 24 часа. Если ты не создавал профиль, просто проигнорируй это письмо.")


def send_password_reset_email(email, token):
    link = confirmation_link("/reset", token)
    send_email(email, "Восстановление пароля для своей ленты",
               "Чтобы задать новый пароль, открой ссылку:\n\n" + link +
               "\n\nСсылка действует 30 минут. Если ты не запрашивал восстановление, просто проигнорируй это письмо.")


# Письма: на один адрес — раз в минуту, с одного адреса клиента — не больше MAIL_PER_CLIENT за час.
# ponytail: счётчики в памяти, как вход: после перезапуска лимит начинается заново.
MAIL_PER_CLIENT = 10
MAIL_CLIENT_WINDOW = 60 * 60
CLIENT_MAILS = {}
_mail_lock = threading.Lock()


def mail_request_allowed(key, client, cooldown=60):
    """Можно ли послать письмо: на адрес key — раз в cooldown секунд, с клиента — MAIL_PER_CLIENT за час.
    Оба счётчика пишутся только когда письмо разрешено: отказ ничего не тратит."""
    now = time.time()
    with _mail_lock:
        if now - MAIL_REQUESTS.get(key, 0) < cooldown:
            return False
        stamps = [stamp for stamp in CLIENT_MAILS.get(client, []) if now - stamp < MAIL_CLIENT_WINDOW]
        if len(stamps) >= MAIL_PER_CLIENT:
            CLIENT_MAILS[client] = stamps
            return False
        stamps.append(now)
        CLIENT_MAILS[client] = stamps
        MAIL_REQUESTS[key] = now
        return True


# Позиция списка приходит строкой. Число вне 64-битного диапазона рвёт
# соединение: sqlite3 не может положить такое в INTEGER, и OverflowError
# уходит из обработчика. Поэтому граница проверяется до int() на сервере.
SQLITE_MAX_INT = 2 ** 63 - 1


def cursor_int(value, minimum=0):
    if not isinstance(value, str) or not DIGITS.fullmatch(value):
        raise ValueError("позиция не число")
    number = int(value)
    if not minimum <= number <= SQLITE_MAX_INT:
        raise ValueError("позиция вне диапазона")
    return number


def sql_int(value, minimum=1):
    """Целое число для SQLite из тела запроса.

    sqlite3 не кладёт в INTEGER числа длиннее 19 цифр: из обработчика уходит
    OverflowError, клиент видит оборванное соединение вместо ответа. Граница
    проверяется до запроса, поэтому лишнее число отсекается как плохой запрос.
    """
    if isinstance(value, str) and DIGITS.fullmatch(value):
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("не число")
    if not minimum <= value <= SQLITE_MAX_INT:
        raise ValueError("число вне диапазона")
    return value


# Адреса прокси, которым верим заголовку X-Forwarded-For: IP или CIDR через запятую.
# Без переменной заголовок не читаем вовсе.
TRUSTED_PROXIES = [ipaddress.ip_network(part.strip(), strict=False)
                   for part in os.environ.get("TRUSTED_PROXY", "").split(",") if part.strip()]


def trusted_proxy(address):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in TRUSTED_PROXIES)


def client_ip(handler):
    """Адрес клиента для ограничения входа.

    За прокси все запросы приходят с адреса прокси, и подбор пароля одного профиля
    закрыл бы вход всем. Поэтому адрес берём из X-Forwarded-For, но только когда
    соединение пришло от доверенного прокси. Прокси дописывает адрес справа, а левую
    часть заголовка клиент подделывает, поэтому идём справа налево, пока адреса
    доверенные, и берём первый недоверенный. Битый адрес по дороге — заголовок не читаем.
    """
    peer = handler.client_address[0]
    if not TRUSTED_PROXIES or not trusted_proxy(peer):
        return peer
    leftmost = None
    for part in reversed(handler.headers.get("X-Forwarded-For", "").split(",")):
        try:
            leftmost = ipaddress.ip_address(part.strip())
        except ValueError:
            return peer
        if not any(leftmost in network for network in TRUSTED_PROXIES):
            return str(leftmost)
    return str(leftmost) if leftmost is not None else peer


def login_key(username, client=""):
    # ключ по адресу и имени: при ключе только по имени любой, кто знает имя,
    # мог закрыть чужой вход на четверть часа
    return f"{client}|{username.lower()}"


def login_allowed(username, client=""):
    key = login_key(username, client)
    now = time.time()
    recent = [stamp for stamp in LOGIN_FAILURES.get(key, [])
              if now - stamp < 900]
    LOGIN_FAILURES[key] = recent
    return len(recent) < 8


def record_login_failure(username, client=""):
    LOGIN_FAILURES.setdefault(login_key(username, client), []).append(time.time())


def create_session(db, profile):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    db.execute("INSERT INTO sessions VALUES (?,?,?,?)",
               (hashlib.sha256(token.encode()).hexdigest(), profile, now, now + SESSION_TTL))
    db.commit()
    cookie = f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}"
    if COOKIE_SECURE:
        cookie += "; Secure"
    return cookie


def account_for_request(db, handler):
    jar = http.cookies.SimpleCookie()
    try:
        jar.load(handler.headers.get("Cookie", ""))
    except http.cookies.CookieError:
        return None
    morsel = jar.get(SESSION_COOKIE)
    if not morsel:
        return None
    token_hash = hashlib.sha256(morsel.value.encode()).hexdigest()
    row = db.execute(
        "SELECT a.username,a.profile,s.expires,a.email,a.email_verified FROM sessions s "
        "JOIN accounts a ON a.profile=s.profile WHERE s.token_hash=?", (token_hash,)
    ).fetchone()
    if not row:
        return None
    if row[2] <= int(time.time()):
        db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
        db.commit()
        return None
    return {"username": row[0], "profile": row[1], "email": row[3],
            "email_verified": bool(row[4])}


def client_id(handler, db):
    """Возвращает профиль только из действующей сессии с паролем."""
    account = account_for_request(db, handler)
    return account["profile"] if account else ""


# Тема — это палитра кита (Monos), светлота — отдельная настройка.
# Раньше тема была парой «светлота», поэтому старое значение переезжает в светлоту.
SETTINGS_ALLOWED = {"theme": ("gruvbox", "github", "dracula", "nord", "solarized", "catppuccin"),
                    "mode": ("dark", "light", "auto"),
                    "font": ("sans", "serif", "mono")}
DEFAULT_SETTINGS = {"theme": "gruvbox", "mode": "dark", "font": "mono"}
LEGACY_THEMES = ("dark", "light", "auto")


def load_settings(db, cid):
    row = db.execute("SELECT settings FROM profiles WHERE code=?", (cid,)).fetchone()
    saved = {}
    if row and row[0]:
        try:
            saved = json.loads(row[0])
        except ValueError:
            saved = {}
    if not isinstance(saved, dict):
        saved = {}
    # значения приходят из браузера, поэтому сверяем со списком: в разметку
    # попадают как есть, а мусорный класс или шрифт ломает весь интерфейс
    out = dict(DEFAULT_SETTINGS)
    for key, allowed in SETTINGS_ALLOWED.items():
        value = saved.get(key)
        if value in allowed:
            out[key] = value
    # Прежняя настройка была одной парой «тема-светлота»: её значение становится
    # светлотой, а палитра остаётся умолчательной. Иначе у старых профилей
    # настройка молча пропала бы.
    legacy = saved.get("theme")
    if legacy in LEGACY_THEMES:
        out["mode"] = legacy
    return out


def save_settings(db, cid, data):
    # неуказанное поле сохраняем как было, а не сбрасываем в умолчание:
    # частичный PUT из чужого клиента иначе молча ломает вторую настройку
    clean = load_settings(db, cid)
    for key, allowed in SETTINGS_ALLOWED.items():
        if key not in data:
            continue
        value = data[key]
        if value not in allowed:
            raise ValueError(f"неизвестное значение {key}={value!r}")
        clean[key] = value
    db.execute("INSERT OR IGNORE INTO profiles (code,created) VALUES (?,?)",
               (cid, int(time.time())))
    db.execute("UPDATE profiles SET settings=? WHERE code=?",
               (J(clean), cid))
    db.commit()
    return clean


def load_state(db, cid):
    db.execute("INSERT OR IGNORE INTO profiles (code,created) VALUES (?,?)",
               (cid, int(time.time())))
    # Без commit запись терялась: обработчик закрывает соединение, и SQLite
    # откатывает незавершённую транзакцию — ответ был 200, а клиента нет.
    subs = [r[0] for r in db.execute("SELECT channel FROM user_subs WHERE profile=?", (cid,))]
    reads = [r[0] for r in db.execute("SELECT post FROM user_reads WHERE profile=?", (cid,))]
    initialized = bool(db.execute(
        "SELECT initialized FROM profiles WHERE code=?", (cid,)
    ).fetchone()[0])
    if not initialized and (subs or reads):
        db.execute("UPDATE profiles SET initialized=1 WHERE code=?", (cid,))
        initialized = True
    db.commit()
    return {"subs": subs, "reads": reads, "initialized": initialized,
            "settings": load_settings(db, cid)}


def string_list(data, key):
    """Список строк из тела запроса. Значение не того типа отбрасывается,
    а не роняет сервер: перебор целого числа даёт TypeError и рвёт соединение."""
    value = data.get(key, [])
    return value if isinstance(value, list) else []


def list_collections(db, cid):
    # по названию без учёта регистра: порядок одинаковый в интерфейсе и в API
    rows = sorted(db.execute("SELECT id, name FROM collections WHERE profile=?", (cid,)).fetchall(),
                  key=lambda row: row[1].casefold())
    channels = {}
    for collection, channel in db.execute(
            "SELECT cc.collection, cc.channel FROM collection_channels cc "
            "JOIN collections c ON c.id = cc.collection WHERE c.profile=? ORDER BY cc.channel", (cid,)):
        channels.setdefault(collection, []).append(channel)
    return [{"id": cid_, "name": name, "channels": channels.get(cid_, [])} for cid_, name in rows]


# Проверка имени и вставка идут под одним замком: два одновременных сохранения
# одного названия иначе оба проходили проверку и создавали две коллекции.
# ponytail: замок на процесс, сервис один. Для нескольких процессов нужен UNIQUE-индекс по (profile, name).
_collections_lock = threading.Lock()


def save_collection(db, cid, data):
    """Создаёт коллекцию или меняет её название и список каналов. Каналы берём только из подписок."""
    name = data.get("name")
    name = name.strip() if isinstance(name, str) else ""
    if not 1 <= len(name) <= COLLECTION_NAME_MAX or not name.isprintable():
        raise ValueError("Название коллекции: от 1 до 32 символов")
    channels = list(dict.fromkeys(string_list(data, "channels")))
    if len(channels) > COLLECTION_CHANNELS_CAP or not all(valid_feed_channel(c) for c in channels):
        raise ValueError("неверный список каналов")
    subs = {r[0] for r in db.execute("SELECT channel FROM user_subs WHERE profile=?", (cid,))}
    if not set(channels) <= subs:
        raise ValueError("канала нет в подписках")
    collection = data.get("id")
    with _collections_lock:
        # одинаковые названия в списке не отличить друг от друга; свою же коллекцию можно не менять
        taken = {n.casefold() for i, n in db.execute("SELECT id, name FROM collections WHERE profile=?", (cid,))
                 if i != collection}
        if name.casefold() in taken:
            raise ValueError("коллекция с таким названием уже есть")
        if collection is None:
            count = db.execute("SELECT COUNT(*) FROM collections WHERE profile=?", (cid,)).fetchone()[0]
            if count >= COLLECTIONS_CAP:
                raise ValueError("коллекций не больше 50")
            collection = db.execute("INSERT INTO collections (profile, name, created) VALUES (?,?,?)",
                                    (cid, name, int(time.time()))).lastrowid
        else:
            if not isinstance(collection, int) or isinstance(collection, bool):
                raise KeyError(collection)
            cur = db.execute("UPDATE collections SET name=? WHERE id=? AND profile=?", (name, collection, cid))
            if not cur.rowcount:
                raise KeyError(collection)
        db.execute("DELETE FROM collection_channels WHERE collection=?", (collection,))
        db.executemany("INSERT INTO collection_channels VALUES (?,?)", [(collection, c) for c in channels])
        db.commit()
    return next(c for c in list_collections(db, cid) if c["id"] == collection)


def delete_collection(db, cid, collection):
    if not isinstance(collection, int) or isinstance(collection, bool):
        raise KeyError(collection)
    cur = db.execute("DELETE FROM collections WHERE id=? AND profile=?", (collection, cid))
    if not cur.rowcount:
        raise KeyError(collection)
    db.execute("DELETE FROM collection_channels WHERE collection=?", (collection,))
    db.commit()


def prune_collections(db, cid):
    """Канал, с которого сняли подписку, уходит и из коллекций."""
    db.execute("DELETE FROM collection_channels WHERE collection IN "
               "(SELECT id FROM collections WHERE profile=?) "
               "AND channel NOT IN (SELECT channel FROM user_subs WHERE profile=?)", (cid, cid))


def save_state(db, cid, data):
    # Приватные каналы тоже подписки: без них полная замена состояния стирала
    # бы подписку на приватный канал, а её больше негде восстановить.
    subs = [c for c in string_list(data, "subs") if valid_feed_channel(c)][:200]
    reads = [r for r in string_list(data, "reads") if valid_post_key(r)][:READS_CAP]
    db.execute("INSERT OR IGNORE INTO profiles (code,created) VALUES (?,?)",
               (cid, int(time.time())))
    db.execute("DELETE FROM user_subs WHERE profile=?", (cid,))
    db.execute("DELETE FROM user_reads WHERE profile=?", (cid,))
    db.executemany("INSERT OR IGNORE INTO user_subs VALUES (?,?)", [(cid, c) for c in subs])
    prune_collections(db, cid)
    db.executemany("INSERT OR IGNORE INTO user_reads VALUES (?,?)", [(cid, r) for r in reads])
    db.execute("UPDATE profiles SET initialized=1 WHERE code=?", (cid,))
    db.commit()


def merge_state(db, cid, data):
    """Однократно соединяет локальное состояние с уже существующим профилем.
    Подписки из слияния берутся только у пустого профиля (ни одной подписки): иначе устаревшая
    вкладка или другое устройство вернули бы канал, от которого отписались. Отметки сливаются всегда."""
    incoming_subs = [c for c in string_list(data, "subs") if valid_channel(c)]
    reads = list(dict.fromkeys(r for r in string_list(data, "reads") if valid_post_key(r)))
    # Вставка открывает транзакцию с блокировкой записи, и проверка пустоты идёт уже под ней.
    db.execute("INSERT OR IGNORE INTO profiles (code,created) VALUES (?,?)",
               (cid, int(time.time())))
    if not db.execute("SELECT 1 FROM user_subs WHERE profile=? LIMIT 1", (cid,)).fetchone():
        subs = list(dict.fromkeys(incoming_subs))
        if len(subs) > 200:
            db.rollback()
            raise ValueError("После объединения получится больше 200 каналов")
        db.executemany("INSERT OR IGNORE INTO user_subs VALUES (?,?)", [(cid, c) for c in subs])
    db.executemany("INSERT OR IGNORE INTO user_reads VALUES (?,?)", [(cid, r) for r in reads])
    # итог, а не входящий список: новые отметки вместе со старыми не должны пройти мимо предела
    total = db.execute("SELECT COUNT(*) FROM user_reads WHERE profile=?", (cid,)).fetchone()[0]
    if total > READS_CAP:
        db.rollback()
        raise ValueError(READS_FULL)
    db.execute("UPDATE profiles SET initialized=1 WHERE code=?", (cid,))
    db.commit()
    return load_state(db, cid)


def valid_post_key(value):
    if not isinstance(value, str):
        return False
    channel, sep, msg_id = value.partition("/")
    # Приватные посты отмечаются прочитанными по тому же ключу, поэтому валидатор
    # обязан их пропускать: иначе отметка молча не сохранится.
    return bool(sep and valid_feed_channel(channel) and DIGITS.fullmatch(msg_id) and len(msg_id) <= 20)


def update_subscription(db, cid, channel, subscribed):
    db.execute("INSERT OR IGNORE INTO profiles (code,created) VALUES (?,?)",
               (cid, int(time.time())))
    if subscribed:
        count = db.execute("SELECT COUNT(*) FROM user_subs WHERE profile=?", (cid,)).fetchone()[0]
        exists = db.execute("SELECT 1 FROM user_subs WHERE profile=? AND channel=?",
                            (cid, channel)).fetchone()
        if not exists and count >= 200:
            raise ValueError("В одной ленте может быть не больше 200 каналов")
        db.execute("INSERT OR IGNORE INTO user_subs VALUES (?,?)", (cid, channel))
    else:
        db.execute("DELETE FROM user_subs WHERE profile=? AND channel=?", (cid, channel))
        prune_collections(db, cid)
    db.execute("UPDATE profiles SET initialized=1 WHERE code=?", (cid,))
    db.commit()
    return load_state(db, cid)


# Отметок прочтения на профиль больше не копим: тот же предел у полной замены состояния.
READS_CAP = 20000
READS_FULL = "Отметок больше 20 000. Сними отметки у старых постов, чтобы отметить новые."


def mark_posts_read(db, cid, post_keys, unread=False):
    """Отметки прочтения можно и снять: пост, который человек перечитал,
    снова попадает в «Непрочитанные». Новые отметки сверх READS_CAP не принимаются."""
    valid = list(dict.fromkeys(key for key in post_keys if valid_post_key(key)))[:500]
    db.execute("INSERT OR IGNORE INTO profiles (code,created) VALUES (?,?)",
               (cid, int(time.time())))
    if unread:
        db.executemany("DELETE FROM user_reads WHERE profile=? AND post=?",
                       [(cid, key) for key in valid])
    else:
        held = {r[0] for r in db.execute(
            "SELECT post FROM user_reads WHERE profile=? AND post IN (%s)" % ",".join("?" * len(valid)),
            [cid] + valid)} if valid else set()
        total = db.execute("SELECT COUNT(*) FROM user_reads WHERE profile=?", (cid,)).fetchone()[0]
        if total + len([key for key in valid if key not in held]) > READS_CAP:
            db.rollback()
            raise ValueError(READS_FULL)
        db.executemany("INSERT OR IGNORE INTO user_reads VALUES (?,?)",
                       [(cid, key) for key in valid])
    db.execute("UPDATE profiles SET initialized=1 WHERE code=?", (cid,))
    db.commit()
    return load_state(db, cid)


class Server(ThreadingHTTPServer):
    # Очередь ожидающих соединений: по умолчанию пять, и при залпе запросов
    # (страница открывает десяток картинок и API сразу) лишние получали отказ.
    request_queue_size = 128


# Ответ на регистрацию один и тот же, ушло письмо или нет: по тексту не видно, отправлено ли оно.
# Занятое имя отвечает 409 (это публичный логин). Занятая почта (подтверждённая или свежая
# неподтверждённая учётка) тоже отвечает этим ответом, но без записи и без письма; старую
# неподтверждённую учётку (старше UNVERIFIED_TTL) регистрация отдаёт заново.
REGISTER_ANSWER = {"verification_required": True,
                   "message": "Если адрес можно использовать, письмо придёт. Перейди по ссылке из него."}
# Смена почты: тот же ответ, ушло письмо или занята почта другим профилем.
CHANGE_ANSWER = {"message": "Перейди по ссылке в письме, чтобы подтвердить почту."}
# Тело запроса больше этого не читаем: иначе один запрос занимает память и поток.
BODY_LIMIT = 4_000_000
# Политика для всех ответов: интерфейс — свой же код, картинки и видео — с Telegram.
# font-src data: нужен: шрифт JetBrains Mono из Monos-кита встроен в CSS как data:-адрес (проверено в браузере).
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' https: data: blob:; media-src 'self' https: blob:; font-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; "
       "object-src 'none'")


class Handler(BaseHTTPRequestHandler):
    # По умолчанию BaseHTTPRequestHandler отвечает HTTP/1.0 и закрывает
    # соединение после каждого запроса: на первой загрузке страницы это
    # несколько лишних подключений. HTTP/1.1 держит соединение, а
    # Content-Length в _send уже всегда проставлен.
    protocol_version = "HTTP/1.1"
    # Сокет без ответа не держит поток вечно: тело, которое не пришло за это время, и
    # простаивающее соединение закрываются.
    timeout = 15
    _body_read = False

    def version_string(self):
        # по умолчанию BaseHTTPRequestHandler отдаёт точную версию Python
        return "koloda"

    def parse_request(self):
        # признак «тело прочитано» относится к одному запросу: на соединении их много
        self._body_read = False
        return super().parse_request()

    def send_error(self, code, message=None, explain=None):
        # Метод, которого обработчик не знает (TRACE и любой другой), stdlib отвечает
        # HTML-страницей 501. Отвечаем JSON 405, как остальным методам, которые сервис не принимает.
        if code == 501 and not hasattr(self, "do_" + (self.command or "")):
            return self._reject()
        return super().send_error(code, message, explain)

    def _send(self, code, body, ctype, headers=None):
        b = body.encode() if isinstance(body, str) else body
        # Раздаваемый файл весит 167 КБ, а лента и каталог — заметно меньше
        # сжатого размера. Сжимаем всё, кроме мелких ответов, где выигрыш
        # меньше служебных байт заголовка.
        encoding = ""
        # картинки, видео и шрифты уже сжаты, gzip на них только тратит процессор
        if len(b) >= MIN_GZIP and "gzip" in self.headers.get("Accept-Encoding", "") \
                and not ctype.startswith(("image/", "video/", "font/")):
            b = gzip.compress(b, 6)
            encoding = "gzip"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        if encoding:
            self.send_header("Content-Encoding", encoding)
            self.send_header("Vary", "Accept-Encoding")
        # свой Cache-Control из headers заменяет стандартный, а не дублирует его
        if "cache-control" not in {name.lower() for name, _ in headers or []}:
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", CSP)
        # Тело, которое маршрут не прочитал и которое не дочитать (чанками или больше
        # лимита), останется в сокете: соединение закрываем, иначе следующий запрос
        # читался бы с середины.
        if self.close_connection or (not self._body_read and self._body_size() is None):
            self.send_header("Connection", "close")
        for name, value in headers or []:
            self.send_header(name, value)
        self.end_headers()
        # У HEAD тела нет: клиент ждёт только заголовки, а байты в ответе на HEAD
        # ломают протокол и сбивают следующий запрос по тому же соединению.
        if self.command != "HEAD":
            self.wfile.write(b)
        # Тело запроса, которое маршрут не прочитал, не должно остаться в сокете.
        self._drain_body()

    def do_GET(self):
        self._route()

    def do_HEAD(self):   # то же, что GET; _send тела для HEAD не пишет
        self._route()

    def do_PUT(self):
        self._route()

    def _reject(self):
        self._send(405, json.dumps({"error": "метод не поддерживается"}, ensure_ascii=False),
                   "application/json; charset=utf-8")

    # остальные методы должны отвечать JSON-кодом, а не HTML-страницей 501
    do_DELETE = do_PATCH = do_OPTIONS = _reject

    def do_POST(self):   # navigator.sendBeacon при уходе со страницы
        self._route()

    def _route(self):
        path = self.path.split("?")[0]
        # Проверка живости для healthcheck в compose. Без профиля и без
        # обращения к t.me: только база, поэтому отвечает быстро и всегда.
        if path == "/api/health":
            connect().close()
            return self._send(200, json.dumps({"ok": True}), "application/json")
        db = connect()
        try:
            if path in ("/", "/verify", "/reset"):
                # Собранный интерфейс лежит в ui/dist: его делает сборка на
                # node, а сервер только раздаёт готовое. Без сборки отвечаем
                # прямо: пустая страница выглядела бы как сломанный сервис.
                page = os.path.join(UI_DIR, "index.html")
                if not os.path.exists(page):
                    return self._send(503, json.dumps(
                        {"error": "интерфейс не собран: запусти npm run build в ui/"},
                        ensure_ascii=False), "application/json; charset=utf-8")
                with open(page, encoding="utf-8") as html:
                    return self._send(200, html.read(), "text/html; charset=utf-8")
            # Файлы сборки: в именах есть хеш содержимого, поэтому кеш живёт
            # долго. Путь проверяем по расширению и по тому, что имя не выходит
            # за пределы каталога сборки.
            if path.startswith("/assets/"):
                name = path[len("/assets/"):]
                if "/" in name or ".." in name or not name:
                    return self._send(404, json.dumps({"error": "нет такого файла"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                suffix = os.path.splitext(name)[1].lower()
                ctype = UI_ASSET_TYPES.get(suffix)
                if not ctype:
                    return self._send(404, json.dumps({"error": "нет такого файла"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                full = os.path.join(UI_DIR, "assets", name)
                if not os.path.exists(full):
                    return self._send(404, json.dumps({"error": "нет такого файла"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                with open(full, "rb") as asset:
                    return self._send(200, asset.read(), ctype,
                                      [("Cache-Control", "public, max-age=31536000, immutable")])
            # /api/auth/me отвечает всегда: иначе гость не отличит «сессии нет»
            # от «сервис сломан». Публичное чтение ленты и каталога тоже
            # анонимное — это данные из общедоступных каналов; все личные
            # маршруты (/api/state, /api/preview, /api/tg/*) требуют профиль.
            if path == "/api/auth/me":
                account = account_for_request(db, self)
                return self._send(200, json.dumps({"account": account}, ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/auth/register":
                if self.command != "POST":
                    return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                      "application/json")
                data = self._read_json()
                if isinstance(data, int):
                    return self._send(data, json.dumps({"error": "Проверь имя и пароль"}),
                                      "application/json; charset=utf-8")
                username = str(data.get("username", "")).strip()
                email = str(data.get("email", "")).strip().lower()
                password = data.get("password", "")
                if not ACCOUNT_RE.fullmatch(username):
                    return self._send(400, json.dumps({"error": "Имя: 3–32 буквы, цифры, _ или -"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if not isinstance(password, str) or not 8 <= len(password) <= 128:
                    return self._send(400, json.dumps({"error": "Пароль должен быть от 8 до 128 символов"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                # почта обязательна: без неё профиль не создаём, потому что без письма нельзя
                # ни подтвердить вход, ни восстановить пароль
                if not email:
                    return self._send(400, json.dumps({"error": "Для профиля нужна почта"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if not valid_email(email):
                    return self._send(400, json.dumps({"error": "Введи действующий адрес почты"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if not SMTP_ENABLED:
                    return self._send(503, json.dumps({"error": "Отправка писем пока недоступна"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                # Имя и почту держит подтверждённая или свежая учётка. Неподтверждённая старше
                # суток (ссылка из письма в ней протухла) отдаётся заново.
                stale_before = int(time.time()) - UNVERIFIED_TTL
                rows = db.execute(
                    "SELECT profile, email_verified, created, username=? COLLATE NOCASE,"
                    " email=? COLLATE NOCASE FROM accounts"
                    " WHERE username=? COLLATE NOCASE OR email=? COLLATE NOCASE",
                    (username, email, username, email)).fetchall()
                busy = [row for row in rows if row[1] or row[2] > stale_before]
                if busy:
                    if any(row[3] for row in busy):
                        return self._send(409, json.dumps({"error": "Это имя уже занято. Выбери другое."}, ensure_ascii=False),
                                          "application/json; charset=utf-8")
                    # Почту держит подтверждённая или свежая учётка. Ответ тот же 202, что и при письме,
                    # но ни строки в базе, ни письма: по ответу не видно, что адрес занят.
                    return self._send(202, json.dumps(REGISTER_ANSWER, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                # Письмо на один адрес не чаще раза в минуту, как и повторная отправка подтверждения.
                # Ответ тот же 202, что и при отправке: по нему не видно, что письма не будет.
                if not mail_request_allowed("verify:" + email, client_ip(self)):
                    return self._send(202, json.dumps(REGISTER_ANSWER, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                # Старую учётку отдаём заново: строку переписываем, не удаляем. Если почту держит
                # другая старая строка, с неё почту снимаем, иначе та осталась бы занятой.
                target = next((row[0] for row in rows if row[3]), rows[0][0] if rows else None)
                salt = secrets.token_hex(16)
                digest = password_digest(password, salt)
                now = int(time.time())
                try:
                    if target is None:
                        profile = secrets.token_urlsafe(24)
                        db.execute("INSERT INTO profiles (code,created,initialized) VALUES (?,?,1)",
                                   (profile, now))
                        db.execute("INSERT INTO accounts "
                                   "(username,profile,salt,digest,created,email,email_verified) "
                                   "VALUES (?,?,?,?,?,?,0)",
                                   (username, profile, salt, digest, now, email))
                    else:
                        profile = target
                        for row in rows:
                            if row[0] != target and row[4]:
                                db.execute("UPDATE accounts SET email=NULL WHERE profile=?", (row[0],))
                        db.execute("UPDATE accounts SET username=?,salt=?,digest=?,email=?,created=? "
                                   "WHERE profile=?", (username, salt, digest, email, now, profile))
                    token = issue_auth_token(db, profile, "email_verify", email)
                    db.commit()
                except sqlite3.IntegrityError:
                    db.rollback()
                    return self._send(409, json.dumps({"error": "Не удалось создать профиль. Попробуй другое имя."}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                try:
                    send_verification_email(email, token)
                except Exception:
                    # письма не ушло: лимит на адрес не должен держать повторную попытку
                    MAIL_REQUESTS.pop("verify:" + email, None)
                    db.execute("DELETE FROM auth_tokens WHERE profile=?", (profile,))
                    if target is None:
                        db.execute("DELETE FROM accounts WHERE profile=?", (profile,))
                        db.execute("DELETE FROM profiles WHERE code=?", (profile,))
                    else:
                        # письма не ушло: учётка снова старая, и имя с почтой свободны для новой попытки
                        db.execute("UPDATE accounts SET created=0 WHERE profile=?", (profile,))
                    db.commit()
                    return self._send(503, json.dumps({"error": "Не удалось отправить письмо. Проверь настройки почты и попробуй ещё раз."}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                return self._send(202, json.dumps(REGISTER_ANSWER, ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/auth/verify":
                if self.command != "POST":
                    return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                      "application/json")
                data = self._read_json()
                token = data.get("token", "") if isinstance(data, dict) else ""
                if not isinstance(token, str) or len(token) > 128:
                    return self._send(400, json.dumps({"error": "Ссылка недействительна или устарела"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                row = db.execute("SELECT profile,purpose,email,expires FROM auth_tokens WHERE token_hash=?",
                                 (token_hash,)).fetchone()
                if not row or row[3] <= int(time.time()) or row[1] not in ("email_verify", "email_change"):
                    return self._send(400, json.dumps({"error": "Ссылка недействительна или устарела"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                profile, purpose, email = row[:3]
                if purpose == "email_verify":
                    db.execute("UPDATE accounts SET email_verified=1 WHERE profile=? AND email=?",
                               (profile, email))
                    if not db.execute("SELECT 1 FROM accounts WHERE profile=?", (profile,)).fetchone():
                        return self._send(400, json.dumps({"error": "Профиль не найден"}, ensure_ascii=False),
                                          "application/json; charset=utf-8")
                    db.execute("DELETE FROM auth_tokens WHERE profile=?", (profile,))
                    db.commit()
                    # Сессии здесь нет: ссылка из письма только подтверждает почту, а вход — по паролю
                    return self._send(200, json.dumps({"email_verified": True}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                try:
                    db.execute("UPDATE accounts SET email=?,email_verified=1 WHERE profile=?",
                               (email, profile))
                except sqlite3.IntegrityError:
                    return self._send(409, json.dumps({"error": "Эта почта уже привязана к другому профилю"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                db.execute("DELETE FROM auth_tokens WHERE profile=? AND purpose IN "
                           "('email_change','password_reset')", (profile,))
                db.commit()
                return self._send(200, json.dumps({"email_verified": True}), "application/json")

            if path == "/api/auth/resend-verification":
                if self.command != "POST":
                    return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                      "application/json")
                data = self._read_json()
                email = str(data.get("email", "")).strip().lower() if isinstance(data, dict) else ""
                generic = {"message": "Если для этой почты ждёт подтверждения профиль, мы отправили письмо."}
                if valid_email(email):
                    row = db.execute("SELECT profile FROM accounts WHERE email=? COLLATE NOCASE "
                                     "AND email_verified=0", (email,)).fetchone()
                    if row and mail_request_allowed("verify:" + email, client_ip(self)):
                        token = issue_auth_token(db, row[0], "email_verify", email)
                        db.commit()
                        try:
                            send_verification_email(email, token)
                        except Exception:
                            db.execute("DELETE FROM auth_tokens WHERE profile=? AND purpose='email_verify'",
                                       (row[0],))
                            db.commit()
                return self._send(200, json.dumps(generic, ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/auth/forgot":
                if self.command != "POST":
                    return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                      "application/json")
                data = self._read_json()
                email = str(data.get("email", "")).strip().lower() if isinstance(data, dict) else ""
                generic = {"message": "Если почта подтверждена, мы отправили на неё ссылку для восстановления."}
                if valid_email(email):
                    row = db.execute("SELECT profile FROM accounts WHERE email=? COLLATE NOCASE "
                                     "AND email_verified=1", (email,)).fetchone()
                    if row and mail_request_allowed("reset:" + email, client_ip(self)):
                        token = issue_auth_token(db, row[0], "password_reset", email,
                                                 lifetime=30 * 60)
                        db.commit()
                        try:
                            send_password_reset_email(email, token)
                        except Exception:
                            db.execute("DELETE FROM auth_tokens WHERE profile=? AND purpose='password_reset'",
                                       (row[0],))
                            db.commit()
                return self._send(200, json.dumps(generic, ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/auth/reset":
                if self.command != "POST":
                    return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                      "application/json")
                data = self._read_json()
                token = data.get("token", "") if isinstance(data, dict) else ""
                password = data.get("password", "") if isinstance(data, dict) else ""
                if not isinstance(password, str) or not 8 <= len(password) <= 128:
                    return self._send(400, json.dumps({"error": "Пароль должен быть от 8 до 128 символов"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                token_hash = hashlib.sha256(token.encode()).hexdigest() if isinstance(token, str) else ""
                row = db.execute("SELECT profile,expires FROM auth_tokens WHERE token_hash=? "
                                 "AND purpose='password_reset'", (token_hash,)).fetchone()
                if not row or row[1] <= int(time.time()):
                    return self._send(400, json.dumps({"error": "Ссылка недействительна или устарела"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                profile = row[0]
                salt = secrets.token_hex(16)
                digest = password_digest(password, salt)
                db.execute("UPDATE accounts SET salt=?,digest=? WHERE profile=?", (salt, digest, profile))
                db.execute("DELETE FROM auth_tokens WHERE profile=?", (profile,))
                db.execute("DELETE FROM sessions WHERE profile=?", (profile,))
                db.commit()
                return self._send(200, json.dumps({"ok": True,
                                  "message": "Пароль изменён. Теперь войди в профиль."}, ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/auth/email":
                if self.command != "POST":
                    return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                      "application/json")
                account = account_for_request(db, self)
                if not account:
                    return self._send(401, json.dumps({"error": "Войди в профиль ещё раз"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                data = self._read_json()
                password = data.get("password", "") if isinstance(data, dict) else ""
                email = str(data.get("email", "")).strip().lower() if isinstance(data, dict) else ""
                row = db.execute("SELECT salt,digest,email,email_verified FROM accounts WHERE profile=?",
                                 (account["profile"],)).fetchone()
                if not isinstance(password, str) or not 8 <= len(password) <= 128 or not secrets.compare_digest(
                        password_digest(password, row[0]), row[1]):
                    return self._send(401, json.dumps({"error": "Пароль не подошёл"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if not valid_email(email):
                    return self._send(400, json.dumps({"error": "Введи действующий адрес почты"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if not SMTP_ENABLED:
                    return self._send(503, json.dumps({"error": "Отправка писем пока недоступна"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if row[2] and row[2].lower() == email and row[3]:
                    return self._send(200, json.dumps({"message": "Эта почта уже подтверждена"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if not mail_request_allowed("verify:" + email, client_ip(self)):
                    return self._send(429, json.dumps({"error": "Письма уже отправлены. Попробуй позже."}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                # Почту держит другой профиль: ответ тот же, что и при письме, но ни токена, ни письма.
                # Счёт тратится и здесь, поэтому по лимиту занятость тоже не видна.
                if db.execute("SELECT 1 FROM accounts WHERE email=? COLLATE NOCASE AND profile<>?",
                              (email, account["profile"])).fetchone():
                    return self._send(202, json.dumps(CHANGE_ANSWER, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                token = issue_auth_token(db, account["profile"], "email_change", email)
                db.commit()
                try:
                    send_verification_email(email, token)
                except Exception:
                    db.execute("DELETE FROM auth_tokens WHERE profile=? AND purpose='email_change'",
                               (account["profile"],))
                    db.commit()
                    return self._send(503, json.dumps({"error": "Не удалось отправить письмо. Проверь настройки и попробуй ещё раз."}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                return self._send(202, json.dumps(CHANGE_ANSWER, ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/auth/login":
                if self.command != "POST":
                    return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                      "application/json")
                data = self._read_json()
                if isinstance(data, int):
                    return self._send(data, json.dumps({"error": "Введи имя и пароль"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                username = str(data.get("username", "")).strip()
                password = data.get("password", "")
                # пароль не строкой — это плохой запрос, а не попытка входа: в счётчик не идёт
                if not isinstance(password, str):
                    return self._send(400, json.dumps({"error": "Введи имя и пароль"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                client = client_ip(self)
                if not login_allowed(username, client):
                    return self._send(429, json.dumps({"error": "Слишком много попыток. Попробуй позже."}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                row = db.execute("SELECT username,profile,salt,digest,email,email_verified FROM accounts "
                                 "WHERE username=? COLLATE NOCASE", (username,)).fetchone()
                salt = row[2] if row else "00112233445566778899aabbccddeeff"
                candidate = password_digest(password, salt)
                valid = row is not None and secrets.compare_digest(candidate, row[3])
                if not valid:
                    record_login_failure(username, client)
                    return self._send(401, json.dumps({"error": "Имя или пароль не подошли"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if row[4] and not row[5]:
                    return self._send(403, json.dumps({"error": "Сначала подтверди почту по ссылке из письма."}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                LOGIN_FAILURES.pop(login_key(username, client), None)
                cookie = create_session(db, row[1])
                return self._send(200, json.dumps({"account": {"username": row[0], "email": row[4],
                                  "email_verified": True}}, ensure_ascii=False),
                                  "application/json; charset=utf-8", [("Set-Cookie", cookie)])

            if path == "/api/auth/logout":
                # GET не выходит: ссылка или картинка с чужого сайта не должна разлогинивать
                if self.command != "POST":
                    return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                      "application/json")
                jar = http.cookies.SimpleCookie()
                try:
                    jar.load(self.headers.get("Cookie", ""))
                except http.cookies.CookieError:
                    pass
                morsel = jar.get(SESSION_COOKIE)
                if morsel:
                    digest = hashlib.sha256(morsel.value.encode()).hexdigest()
                    db.execute("DELETE FROM sessions WHERE token_hash=?", (digest,))
                    db.commit()
                expired = f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
                if COOKIE_SECURE:
                    expired += "; Secure"
                return self._send(200, json.dumps({"ok": True}), "application/json",
                                  [("Set-Cookie", expired)])

            if path == "/api/history":
                # Состояние догрузки истории по каналам профиля: для списка в профиле и строк в ленте
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                return self._send(200, json.dumps(history_status(db, cid), ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/saved":
                # Сохранённые посты профиля: список для экрана «Сохранённое» и переключение
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if self.command == "POST":
                    data = self._read_json()
                    if isinstance(data, int):
                        return self._send(data, json.dumps({"error": "неверные данные"}, ensure_ascii=False),
                                          "application/json; charset=utf-8")
                    channel = str(data.get("channel", ""))
                    on = data.get("on") is True
                    if not valid_feed_channel(channel):
                        return self._send(400, json.dumps({"error": "неверный пост"}, ensure_ascii=False),
                                          "application/json; charset=utf-8")
                    try:
                        msg_id = sql_int(data.get("msg_id"))
                    except ValueError:
                        return self._send(400, json.dumps({"error": "неверный пост"}, ensure_ascii=False),
                                          "application/json; charset=utf-8")
                    if on:
                        # Приватные посты лежат в своей таблице и принадлежат профилю,
                        # который их принёс: публичную корзину для них не ищем.
                        if private_channel(channel):
                            known = db.execute(
                                "SELECT 1 FROM private_posts WHERE profile=? AND channel=? AND msg_id=?",
                                (cid, channel, msg_id)).fetchone()
                        else:
                            known = db.execute("SELECT 1 FROM posts WHERE channel=? AND msg_id=?",
                                               (channel, msg_id)).fetchone()
                        if not known:
                            return self._send(404, json.dumps({"error": "такой публикации нет"}, ensure_ascii=False),
                                              "application/json; charset=utf-8")
                    if on:
                        db.execute("INSERT OR REPLACE INTO saved_posts VALUES (?,?,?,?)",
                                   (cid, channel, msg_id, int(time.time())))
                    else:
                        db.execute("DELETE FROM saved_posts WHERE profile=? AND channel=? AND msg_id=?",
                                   (cid, channel, msg_id))
                    db.commit()
                    return self._send(200, json.dumps({"saved": on}), "application/json; charset=utf-8")
                q = self._query("q").strip()
                conditions, args = ["s.profile=?"], [cid]
                if q:
                    conditions.append("p.text LIKE ? ESCAPE '\\'")
                    args.append(like_pattern(q))
                if any(self._query(k) for k in ("before_saved", "before_channel", "before_id")):
                    try:
                        before_saved = cursor_int(self._query("before_saved"))
                        before_channel = self._query("before_channel")
                        before_id = cursor_int(self._query("before_id"))
                    except (TypeError, ValueError):
                        return self._send(400, json.dumps({"error": "неверная позиция списка"}, ensure_ascii=False),
                                          "application/json; charset=utf-8")
                    if not valid_feed_channel(before_channel) or before_id < 1:
                        return self._send(400, json.dumps({"error": "неверная позиция списка"}, ensure_ascii=False),
                                          "application/json; charset=utf-8")
                    conditions.append("(s.saved < ? OR (s.saved = ? AND (s.channel < ? OR (s.channel = ? AND p.msg_id < ?))))")
                    args += [before_saved, before_saved, before_channel, before_channel, before_id]
                # Страницы по времени сохранения: порядок как у ленты, курсор — последний пост страницы.
                # Публичные и приватные посты лежат в разных таблицах, поэтому список
                # собирается из обеих: приватный пост иначе сохранился бы, но не показался.
                where = " AND ".join(conditions)
                public = db.execute(
                    "SELECT " + ",".join("p." + f for f in POST_FIELDS) + ",s.saved"
                    " FROM saved_posts s JOIN posts p ON p.channel=s.channel AND p.msg_id=s.msg_id"
                    " WHERE " + where +
                    " ORDER BY s.saved DESC, s.channel DESC, p.msg_id DESC LIMIT ?",
                    args + [FEED_PAGE_SIZE + 1]).fetchall()
                private = db.execute(
                    "SELECT p.channel,p.msg_id,p.url,p.text,p.media,p.views,p.ts,"
                    " NULL,NULL,NULL,NULL,NULL,NULL,s.saved"
                    " FROM saved_posts s JOIN private_posts p"
                    " ON p.channel=s.channel AND p.msg_id=s.msg_id AND p.profile=s.profile"
                    " WHERE " + where +
                    " ORDER BY s.saved DESC, s.channel DESC, p.msg_id DESC LIMIT ?",
                    args + [FEED_PAGE_SIZE + 1]).fetchall()
                # Порядок как в SQL: время сохранения, канал, номер поста.
                rows = sorted(public + private, key=lambda r: (r[-1], r[0], r[1]), reverse=True)
                has_more = len(rows) > FEED_PAGE_SIZE
                rows = rows[:FEED_PAGE_SIZE]
                posts = [dict(zip(POST_FIELDS, r[:-1])) for r in rows]
                next_cursor = None
                if has_more and rows:
                    last = rows[-1]
                    next_cursor = {"saved_at": last[-1], "channel": last[POST_FIELDS.index("channel")],
                                   "msg_id": last[POST_FIELDS.index("msg_id")]}
                for p in posts:
                    p["saved"] = True
                    for k in ("media", "reactions", "preview", "doc", "fwd", "quote"):
                        if p.get(k):
                            try: p[k] = json.loads(p[k])
                            except ValueError: p[k] = None
                names = list({p["channel"] for p in posts})
                titles = dict(db.execute("SELECT channel,title FROM cache WHERE channel IN (%s)"
                                         % ",".join("?" * len(names)), names)) if names else {}
                return self._send(200, json.dumps({"posts": posts, "titles": titles, "has_more": has_more,
                                                   "next": next_cursor}, ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/feed":
                # Лента ходит в t.me, поэтому без входа в профиль не отвечает, как превью и каталог
                unread_profile = client_id(self, db)
                if not unread_profile:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                q = self._query("q")
                chans = list(dict.fromkeys(
                    c for c in self._query("channels", "").split(",") if c
                ))[:200]
                bad = [c for c in chans if not valid_feed_channel(c)]
                if bad:
                    return self._send(400, json.dumps({"error": "неверное имя канала"}),
                                      "application/json")
                if not chans:
                    # без подписок лента пустая: иначе поиск без фильтра отдавал бы
                    # весь кэш, а с пустым списком это весь кэш целиком
                    return self._send(200, json.dumps(
                        {"posts": [], "titles": {}, "errors": [], "has_more": False, "next": None}), "application/json; charset=utf-8")
                before = None
                cursor_values = (self._query("before_ts"), self._query("before_channel"),
                                 self._query("before_id"))
                if any(cursor_values):
                    try:
                        before = (cursor_int(cursor_values[0]), cursor_values[1],
                                  cursor_int(cursor_values[2]))
                    except (TypeError, ValueError):
                        return self._send(400, json.dumps({"error": "неверная позиция ленты"}),
                                          "application/json")
                    if not valid_feed_channel(before[1]) or before[2] < 1:
                        return self._send(400, json.dumps({"error": "неверная позиция ленты"}),
                                          "application/json")

                # Кэш отдаём сразу. Ждём только каналы, которых в базе ещё нет совсем
                public_chans = public_channels(chans)
                wait_for(request_refresh(db, public_chans))
                errors = failures_for(public_chans)
                # Каналы без кэша и без ошибки ещё догружаются в фоне: их посты клиент получит,
                # если спросит ленту ещё раз (поле loading), иначе они появятся только при следующем открытии.
                cached_now = {r[0] for r in db.execute(
                    "SELECT channel FROM cache WHERE channel IN (%s)" % ",".join("?" * len(public_chans)),
                    public_chans)} if public_chans else set()
                failed_now = {e["channel"] for e in errors}
                loading = [c for c in public_chans if c not in cached_now and c not in failed_now]

                # прочитанное по умолчанию не показываем: фильтр в запросе, а не на клиенте,
                # чтобы страница не приходила полупустой
                hide_read = unread_profile if self._query("hide_read") == "1" else None
                # Публичные каналы идут как раньше. Приватные — из своей таблицы и
                # только когда запрос пришёл от профиля со своим входом в Telegram;
                # иначе ключ private:-100… в подписках просто ничего не вернёт.
                want_private = [c for c in chans if private_channel(c)]
                public = public_channels(chans)
                # search_posts на пустом списке каналов отдаёт весь кэш — так он
                # был написан для выгрузки. В ленте это значило бы, что подписчик
                # одних лишь приватных каналов увидит все публичные подряд.
                rows = search_posts(db, q, public, hide_read=hide_read,
                                    limit=FEED_PAGE_SIZE + 1, before=before) if public else []
                if unread_profile and want_private:
                    rows += search_private_posts(db, q, want_private, unread_profile,
                                                hide_read=hide_read,
                                                limit=FEED_PAGE_SIZE + 1, before=before)
                    # Порядок ленты общий для обеих корзин, иначе посты одного
                    # времени шли бы двумя кусками: сначала публичные, потом приватные.
                    rows.sort(key=lambda r: (-r[POST_FIELDS.index("ts")],
                                             r[POST_FIELDS.index("channel")],
                                             -r[POST_FIELDS.index("msg_id")]))
                has_more = len(rows) > FEED_PAGE_SIZE
                rows = rows[:FEED_PAGE_SIZE]
                posts = [dict(zip(POST_FIELDS, r)) for r in rows]
                next_cursor = None
                if has_more and posts:
                    # курсор по именам: позиция ts в выборке менялась вместе с колонками
                    last = posts[-1]
                    next_cursor = {"ts": last["ts"], "channel": last["channel"],
                                   "msg_id": last["msg_id"]}
                # Колонки JSON: на выходе отдаём объектами, а не строками.
                for p in posts:
                    for k in ("media", "reactions", "preview", "doc", "fwd", "quote"):
                        if p.get(k):
                            try: p[k] = json.loads(p[k])
                            except ValueError: p[k] = None
                # флажок «сохранено» нужен кнопке под каждым постом
                saved_keys = {(r[0], r[1]) for r in db.execute(
                    "SELECT channel,msg_id FROM saved_posts WHERE profile=?", (unread_profile,))} if unread_profile else set()
                for p in posts:
                    p["saved"] = (p["channel"], p["msg_id"]) in saved_keys
                # заголовки нужны и запрошенным каналам, и всем подпискам профиля:
                # по ним подписаны вкладки в шапке, а при выборе одного канала
                # остальные остались бы без названия
                wanted = set(chans)
                if unread_profile:
                    wanted |= {r[0] for r in db.execute(
                        "SELECT channel FROM user_subs WHERE profile=?", (unread_profile,))}
                titles = dict(db.execute("SELECT channel,title FROM cache WHERE channel IN (%s)"
                                          % ",".join("?" * len(wanted)), list(wanted)))
                # у приватных каналов названия нет в общем кэше: они лежат у профиля
                if unread_profile and want_private:
                    titles.update(dict(db.execute(
                        "SELECT channel,title FROM tg_channels WHERE profile=? AND channel IN (%s)"
                        % ",".join("?" * len(want_private)), [unread_profile] + want_private)))
                return self._send(200, json.dumps({"posts": posts, "titles": titles,
                                                   "errors": errors, "loading": loading, "has_more": has_more,
                                                   "history": history_status(db, unread_profile),
                                                   "next": next_cursor}),
                                  "application/json; charset=utf-8")

            if path == "/api/tg/status":
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json")
                account = db.execute("SELECT phone,title,linked FROM tg_accounts WHERE profile=?",
                                     (cid,)).fetchone()
                return self._send(200, json.dumps({
                    "installed": tg.installed(),
                    "linked": bool(account and account[2]),
                    "phone": account[0] if account else "",
                    "title": account[1] if account else "",
                }, ensure_ascii=False), "application/json; charset=utf-8")

            if path == "/api/tg/link/start":
                if self.command != "POST":
                    return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                      "application/json")
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json")
                try:
                    state = tg.start_link(cid)
                except tg.TelegramMissing as exc:
                    return self._send(503, json.dumps({"error": str(exc)}, ensure_ascii=False),
                                      "application/json")
                except Exception as exc:
                    return self._send(502, json.dumps({"error": "не удалось начать вход"}, ensure_ascii=False),
                                      "application/json")
                if state.get("state") == "done":
                    db.execute("INSERT OR REPLACE INTO tg_accounts VALUES (?,?,?,1)",
                               (cid, state["account"]["phone"], state["account"]["title"]))
                    db.commit()
                    return self._send(200, json.dumps({"linked": True}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                return self._send(200, json.dumps({"state": "waiting", "token": state["token"],
                                                   "url": state["url"],
                                                   "qr": qrsvg.svg(state["url"])}, ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/tg/link/poll":
                if self.command != "POST":
                    return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                      "application/json")
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json")
                data = self._read_json()
                if isinstance(data, int):
                    return self._send(data, json.dumps({"error": "неверные данные"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                token = str(data.get("token", ""))
                try:
                    result = tg.poll_link(token, cid)
                except tg.ForeignLogin:
                    return self._send(404, json.dumps({"error": "такого входа нет"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                except Exception as exc:
                    return self._send(502, json.dumps({"error": "вход не удался"},
                                                      ensure_ascii=False),
                                      "application/json")
                if result.get("state") == "done":
                    db.execute("INSERT OR REPLACE INTO tg_accounts VALUES (?,?,?,1)",
                               (cid, result["account"]["phone"], result["account"]["title"]))
                    db.commit()
                if result.get("url"):
                    result["qr"] = qrsvg.svg(result["url"])
                return self._send(200, json.dumps(result, ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/tg/link/cancel":
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json")
                data = self._read_json()
                if isinstance(data, int):
                    return self._send(data, json.dumps({"error": "неверные данные"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                token = str(data.get("token", ""))
                try:
                    result = tg.cancel_link(token, cid)
                except tg.ForeignLogin:
                    return self._send(404, json.dumps({"error": "такого входа нет"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                return self._send(200, json.dumps(result), "application/json; charset=utf-8")

            if path == "/api/tg/unlink":
                if self.command != "POST":
                    return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                      "application/json")
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json")
                removed = tg.drop_session(cid)
                db.execute("DELETE FROM tg_accounts WHERE profile=?", (cid,))
                db.execute("DELETE FROM tg_channels WHERE profile=?", (cid,))
                db.execute("DELETE FROM private_posts WHERE profile=?", (cid,))
                db.commit()
                # Файл сессии — это готовый вход в аккаунт. Если он уцелел,
                # говорить «Telegram отключён» нельзя: отключение не состоялось.
                if not removed and tg.linked(cid):
                    return self._send(500, json.dumps(
                        {"error": "не удалось удалить файл сессии: вход в аккаунт остался"},
                        ensure_ascii=False), "application/json; charset=utf-8")
                return self._send(200, json.dumps({"ok": True, "removed": removed}, ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/tg/channels":
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json")
                rows = db.execute(
                    "SELECT channel,title,entity_id,updated FROM tg_channels WHERE profile=?"
                    " ORDER BY title COLLATE NOCASE", (cid,)).fetchall()
                subs = {r[0] for r in db.execute("SELECT channel FROM user_subs WHERE profile=?", (cid,))}
                counts = dict(db.execute(
                    "SELECT channel,COUNT(*) FROM private_posts WHERE profile=? GROUP BY channel", (cid,)))
                return self._send(200, json.dumps({"channels": [
                    {"channel": r[0], "title": r[1] or r[0], "entity_id": r[2], "updated": r[3],
                     "subscribed": r[0] in subs, "posts": counts.get(r[0], 0)} for r in rows]}),
                    "application/json; charset=utf-8")

            if path == "/api/tg/discover":
                if self.command != "POST":
                    return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                      "application/json")
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json")
                # как у входа: без Telethon отвечаем до всякой сети и с тем же текстом
                if not tg.installed():
                    return self._send(503, json.dumps({"error": tg.MISSING_MESSAGE}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                try:
                    found = tg.discover(cid)
                except tg.TelegramMissing as exc:
                    return self._send(400, json.dumps({"error": str(exc)}, ensure_ascii=False),
                                      "application/json")
                except Exception as exc:
                    return self._send(502, json.dumps({"error": "Telegram не ответил"},
                                                      ensure_ascii=False),
                                      "application/json")
                # уже известные каналы не теряем: перезаписываем те же строки
                db.executemany("INSERT OR REPLACE INTO tg_channels VALUES (?,?,?,?,?)",
                               [(cid, c["channel"], c["entity_id"], c["title"], int(time.time()))
                                for c in found])
                db.commit()
                return self._send(200, json.dumps({"found": len(found)}, ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/tg/media":
                # Картинка или видео из приватного поста. Файл тянется через Telegram от
                # имени профиля, поэтому отдаётся только подписчику этого канала.
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                channel, msg = self._query("channel"), self._query("msg")
                if not private_channel(channel) or not DIGITS.fullmatch(msg) or len(msg) > 20:
                    return self._send(400, json.dumps({"error": "неверный адрес медиа"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if not db.execute("SELECT 1 FROM user_subs WHERE profile=? AND channel=?",
                                  (cid, channel)).fetchone():
                    return self._send(404, json.dumps({"error": "канала нет в подписках"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                try:
                    data, mime = tg.media(cid, channel, int(msg))
                except (tg.MediaUnavailable, tg.TelegramMissing):
                    return self._send(404, json.dumps({"error": "медиа недоступно"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                except Exception:  # Telegram не ответил или попросил подождать
                    return self._send(502, json.dumps({"error": "Telegram не отдал файл"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                # файл тот же, что и в Telegram, поэтому браузер может держать его сутки.
                # Видео перематывают через Range, поэтому отдаём и части файла.
                cache = [("Cache-Control", "private, max-age=86400"), ("Accept-Ranges", "bytes")]
                header = self.headers.get("Range", "")
                if not header:
                    return self._send(200, data, mime, cache)
                try:
                    span = parse_range(header, len(data))
                except ValueError:
                    return self._send(416, b"", mime, cache + [("Content-Range", "bytes */%d" % len(data))])
                if span is None:
                    return self._send(200, data, mime, cache)
                start, end = span
                return self._send(206, data[start:end + 1], mime,
                                  cache + [("Content-Range", "bytes %d-%d/%d" % (start, end, len(data)))])

            if path == "/api/tg/sync":
                if self.command != "POST":
                    return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                      "application/json")
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json")
                wanted = [r[0] for r in db.execute(
                    "SELECT channel FROM tg_channels WHERE profile=?", (cid,))]
                errors, total = [], 0
                for channel in wanted:
                    try:
                        rows = tg.fetch(cid, channel)
                    except Exception as exc:
                        errors.append({"channel": channel, "message": "не удалось прочитать канал"})
                        continue
                    store_private_rows(db, cid, channel, rows)
                    db.execute("UPDATE tg_channels SET updated=? WHERE profile=? AND channel=?",
                               (int(time.time()), cid, channel))
                    total += len(rows)
                db.commit()
                return self._send(200, json.dumps({"saved": total, "errors": errors}, ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/tg/subscribe":
                if self.command != "POST":
                    return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                      "application/json")
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json")
                data = self._read_json()
                channel = str(data.get("channel", "")) if isinstance(data, dict) else ""
                subscribed = data.get("subscribed") if isinstance(data, dict) else None
                if not private_channel(channel) or not isinstance(subscribed, bool):
                    return self._send(400, json.dumps({"error": "неверный канал"}, ensure_ascii=False),
                                      "application/json")
                known = db.execute("SELECT 1 FROM tg_channels WHERE profile=? AND channel=?",
                                   (cid, channel)).fetchone()
                if not known:
                    return self._send(404, json.dumps({"error": "канал не найден среди твоих"}),
                                      "application/json")
                try:
                    update_subscription(db, cid, channel, subscribed)
                except ValueError as exc:
                    return self._send(400, json.dumps({"error": str(exc)}, ensure_ascii=False),
                                      "application/json")
                return self._send(200, json.dumps({"ok": True}, ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/settings":
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if self.command == "GET":
                    return self._send(200, json.dumps({"settings": load_settings(db, cid)}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if self.command == "PUT":
                    data = self._read_json()
                    if isinstance(data, int):
                        return self._send(data, json.dumps({"error": "Не удалось сохранить настройки"}),
                                          "application/json")
                    try:
                        saved = save_settings(db, cid, data)
                    except ValueError:
                        return self._send(400, json.dumps({"error": "Такая тема или шрифт не поддерживаются"},
                                                          ensure_ascii=False), "application/json; charset=utf-8")
                    return self._send(200, json.dumps({"settings": saved}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                  "application/json")

            if path == "/api/state":
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if self.command == "GET":
                    return self._send(200, json.dumps(load_state(db, cid), ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if self.command == "PUT":
                    data = self._read_json()
                    if isinstance(data, int):
                        return self._send(data, json.dumps({"error": "не удалось прочитать состояние"}),
                                          "application/json")
                    if load_state(db, cid)["initialized"]:
                        return self._send(409, json.dumps({"error": "профиль уже настроен"}),
                                          "application/json")
                    save_state(db, cid, data)
                    return self._send(200, json.dumps(load_state(db, cid), ensure_ascii=False),
                                      "application/json; charset=utf-8")
                return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                  "application/json")

            if path == "/api/state/export":
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                # отдаём состояние сервера, а не то, что лежит в браузере:
                # файл берут как резервную копию, и он должен быть полным
                state = load_state(db, cid)
                lines = [json.dumps({"type": "channel", "channel": c}, ensure_ascii=False)
                         for c in state["subs"]]
                lines += [json.dumps({"type": "read", "post": r}, ensure_ascii=False)
                          for r in state["reads"]]
                return self._send(200, "\n".join(lines) + ("\n" if lines else ""),
                                  "application/x-ndjson; charset=utf-8",
                                  [("Content-Disposition",
                                    'attachment; filename="koloda-lenta.jsonl"')])

            if path == "/api/state/merge":
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if self.command != "POST":
                    return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                      "application/json")
                data = self._read_json()
                if isinstance(data, int):
                    return self._send(data, json.dumps({"error": "не удалось объединить профили"}),
                                      "application/json")
                try:
                    state = merge_state(db, cid, data)
                except ValueError as exc:
                    return self._send(409, json.dumps({"error": str(exc)}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                return self._send(200, json.dumps(state, ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/state/subscription":
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if self.command != "POST":
                    return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                      "application/json")
                data = self._read_json()
                if isinstance(data, int):
                    return self._send(data, json.dumps({"error": "неверные данные подписки"}),
                                      "application/json")
                channel, subscribed = data.get("channel", ""), data.get("subscribed")
                if not valid_channel(channel) or not isinstance(subscribed, bool):
                    return self._send(400, json.dumps({"error": "неверная подписка"}),
                                      "application/json")
                try:
                    state = update_subscription(db, cid, channel, subscribed)
                except ValueError as exc:
                    return self._send(409, json.dumps({"error": str(exc)}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                return self._send(200, json.dumps(state, ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/collections":
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if self.command != "GET":
                    return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                      "application/json")
                return self._send(200, json.dumps({"collections": list_collections(db, cid)}, ensure_ascii=False),
                                  "application/json; charset=utf-8")
            if path in ("/api/collections/save", "/api/collections/delete"):
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if self.command != "POST":
                    return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                      "application/json")
                data = self._read_json()
                if isinstance(data, int) or not isinstance(data, dict):
                    return self._send(400, json.dumps({"error": "неверные данные коллекции"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                try:
                    if path.endswith("/save"):
                        body = {"collection": save_collection(db, cid, data)}
                    else:
                        delete_collection(db, cid, data.get("id"))
                        body = {"ok": True}
                except KeyError:
                    return self._send(404, json.dumps({"error": "коллекции нет"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                except ValueError as exc:
                    return self._send(400, json.dumps({"error": str(exc)}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                return self._send(200, json.dumps(body, ensure_ascii=False), "application/json; charset=utf-8")
            if path == "/api/state/read":
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if self.command != "POST":
                    return self._send(405, json.dumps({"error": "метод не поддерживается"}),
                                      "application/json")
                data = self._read_json()
                if isinstance(data, int):
                    return self._send(data, json.dumps({"error": "неверные данные прочтения"}),
                                      "application/json")
                keys = data.get("posts", [])
                if not isinstance(keys, list):
                    return self._send(400, json.dumps({"error": "неверный список постов"}),
                                      "application/json")
                try:
                    state = mark_posts_read(db, cid, keys, unread=data.get("unread") is True)
                except ValueError as exc:
                    return self._send(409, json.dumps({"error": str(exc)}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                return self._send(200, json.dumps(state, ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/catalog":
                if not client_id(self, db):
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                offset = self._query("offset", "0")
                if not DIGITS.fullmatch(offset) or len(offset) > 6 or int(offset) > 5000:
                    return self._send(400, json.dumps({"error": "неверная страница каталога"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                try:
                    q = self._query("q")
                    cat = self._query("category", "all")
                    found = search_catalog(q, cat, last_posts=last_post_times(db), offset=int(offset))
                    # полная страница означает, что дальше могут быть ещё каналы
                    return self._send(200, json.dumps(
                        {"channels": found, "has_more": len(found) >= CATALOG_PAGE},
                        ensure_ascii=False),
                        "application/json; charset=utf-8")
                except Exception:
                    # Текст ошибки наружу не отдаём: в нём бывает адрес и детали
                    # запроса к внешнему каталогу, а клиенту достаточно причины.
                    return self._send(502, json.dumps({"error": "каталог не отвечает"}),
                                      "application/json")

            if path == "/api/preview":
                # Без профиля этот маршрут превращал сервис в чужим прокси
                # в t.me: посторонний мог заставить обходить Telegram для
                # любого имени канала и подставиться под блокировку адреса.
                cid = client_id(self, db)
                if not cid:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                channel = self._query("c")
                if not valid_channel(channel):
                    return self._send(400, json.dumps({"error": "неверное имя канала"}),
                                      "application/json; charset=utf-8")
                # Первая страница обновляет канал, следующие читают базу как есть
                cursor_ts, cursor_id = self._query("before_ts"), self._query("before_id")
                paging = bool(cursor_ts or cursor_id)
                try:
                    before = (cursor_int(cursor_ts), cursor_int(cursor_id)) if paging else None
                except ValueError:
                    return self._send(400, json.dumps({"error": "неверная позиция списка"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                errors = [] if paging else refresh_channels(db, [channel])
                where, args = "channel=?", [channel]
                if before:
                    where += " AND (ts < ? OR (ts = ? AND msg_id < ?))"
                    args += [before[0], before[0], before[1]]
                rows = db.execute(
                    "SELECT channel,msg_id,url,text,media,views,ts,reactions,"
                    "preview,doc,fwd,quote,sticker FROM posts WHERE " + where +
                    " ORDER BY ts DESC,msg_id DESC LIMIT ?", args + [CHANNEL_PAGE_SIZE + 1]
                ).fetchall()
                if not rows and not paging:
                    status = 502 if errors else 404
                    message = "Не удалось загрузить публикации канала" if errors else "Канал не найден"
                    return self._send(status, json.dumps({"error": message}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                has_more = len(rows) > CHANNEL_PAGE_SIZE
                rows = rows[:CHANNEL_PAGE_SIZE]
                posts = [dict(zip(POST_FIELDS, row)) for row in rows]
                next_cursor = ({"ts": posts[-1]["ts"], "msg_id": posts[-1]["msg_id"]}
                               if has_more and posts else None)
                for post in posts:
                    for key in ("media", "reactions", "preview", "doc", "fwd", "quote"):
                        if post.get(key):
                            try:
                                post[key] = json.loads(post[key])
                            except ValueError:
                                post[key] = None
                hist = db.execute("SELECT fetched, done FROM backfill WHERE channel=?", (channel,)).fetchone()
                title = db.execute("SELECT title FROM cache WHERE channel=?", (channel,)).fetchone()
                return self._send(200, json.dumps({"channel": channel,
                                                   "title": title[0] if title else channel,
                                                   "posts": posts, "errors": errors,
                                                   "has_more": has_more, "next": next_cursor,
                                                   "history": {"fetched": hist[0] if hist else 0,
                                                               "done": bool(hist and hist[1])}},
                                                  ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/avatars":
                if not client_id(self, db):
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                chans = list(dict.fromkeys(c for c in self._query("channels", "").split(",") if c))[:60]
                if any(not valid_feed_channel(c) for c in chans):
                    return self._send(400, json.dumps({"error": "неверное имя канала"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                fresh = set(chans) if self._query("fresh") == "1" else set()
                avatars, checked = channel_avatars(db, public_channels(chans), fresh=fresh)
                return self._send(200, json.dumps({"avatars": avatars, "checked": checked}, ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/categories":
                if not client_id(self, db):
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                try:
                    return self._send(200, json.dumps({"categories": catalog_categories()}),
                                      "application/json; charset=utf-8")
                except Exception:
                    return self._send(502, json.dumps({"error": "каталог не отвечает"}),
                                      "application/json")

            if path == "/api/media":
                # Свежие ссылки на кадры поста, когда старые не открываются. Ходит в t.me,
                # поэтому только для подписчика канала: иначе сайт снова станет чужим обходом.
                channel, _, msg = (self._query("post") or "").partition("/")
                if not valid_channel(channel) or not DIGITS.fullmatch(msg):
                    return self._send(400, json.dumps({"error": "неверный пост"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                try:
                    msg_id = sql_int(msg)
                except ValueError:
                    return self._send(400, json.dumps({"error": "неверный пост"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                profile = client_id(self, db)
                if not profile:
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if not db.execute("SELECT 1 FROM user_subs WHERE profile=? AND channel=?",
                                  (profile, channel)).fetchone():
                    return self._send(404, json.dumps({"error": "канала нет в подписках"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                try:
                    fresh = refresh_media(db, channel, msg_id)
                except Exception:  # t.me не ответил или попросил подождать
                    return self._send(502, json.dumps({"error": "t.me не отдал пост"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if fresh is None:
                    return self._send(404, json.dumps({"error": "поста нет в ленте"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                return self._send(200, json.dumps(fresh, ensure_ascii=False),
                                  "application/json; charset=utf-8")
            if path == "/api/check":
                # Проверка канала для каталога: 200 если живой.
                c = self._query("c")
                if not valid_channel(c):
                    return self._send(400, json.dumps({"error": "неверное имя канала"}),
                                      "application/json")
                # Запрос идёт в t.me, поэтому без профиля его нет: иначе сайт
                # становится чужим способом обходить Telegram.
                if not client_id(self, db):
                    return self._send(401, json.dumps({"error": "нужно войти в профиль"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                try:
                    title, posts = fetch_channel(c)
                    store_channel_cache(db, c, title, posts)
                    db.commit()
                except ChannelError as exc:
                    # Наши собственные сообщения: «канал не найден», «t.me попросил ждать».
                    return self._send(404, json.dumps({"error": str(exc)}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                except Exception:
                    # Чужие исключения не показываем: в тексте бывает адрес и детали запроса.
                    return self._send(502, json.dumps({"error": "t.me не отвечает"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                return self._send(200, json.dumps({
                    "channel": c, "title": title, "count": len(posts),
                    "url": f"https://t.me/{c}",
                    "preview": (posts[-1]["text"][:100] if posts else ""),
                }), "application/json; charset=utf-8")

            return self._send(404, "not found", "text/plain; charset=utf-8")
        finally:
            db.close()

    def _query(self, k, default=""):
        return parse_qs(urlparse(self.path).query).get(k, [default])[0]

    def _chunked(self):
        return "chunked" in self.headers.get("Transfer-Encoding", "").lower()

    def _body_size(self):
        """Длина тела, которое можно дочитать. None: тело чанками, с непонятной длиной или больше лимита."""
        if self._chunked():
            return None
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        return size if 0 <= size <= BODY_LIMIT else None

    def _read_json(self):
        size = self._body_size()
        if size is None:
            # тело не читаем: _send закроет соединение, и остаток тела уйдёт вместе с ним
            return 411 if self._chunked() else 413
        try:
            raw = self.rfile.read(size)
        except OSError:
            # тело не пришло за отведённое время: отвечаем и закрываем соединение
            self._body_read = True
            self.close_connection = True
            return 400
        self._body_read = True
        try:
            data = json.loads(raw or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return 400
        return data if isinstance(data, dict) else 400

    def _drain_body(self):
        """Дочитывает тело запроса, которое маршрут не прочитал.

        Иначе оно остаётся в сокете, и следующий запрос на том же соединении
        читается с середины: после выхода из профиля страница перезагружалась
        и получала «Unsupported method ('{}GET')». Тело, которое не дочитать
        (чанками или больше лимита), не читаем: соединение уже закрыто в _send.
        """
        if self._body_read:
            return
        self._body_read = True
        size = self._body_size()
        if not size:
            return
        try:
            self.rfile.read(size)
        except OSError:
            self.close_connection = True

    def log_message(self, fmt, *a):
        # Логи выключены по умолчанию: при каждом запросе к t.me и картинкам они
        # засоряют вывод. Включаются переменной LOG=1.
        if os.environ.get("LOG") == "1":
            super().log_message(fmt, *a)


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    connect().close()
    print(f"Лента: http://127.0.0.1:{PORT}")
    # Ссылки из писем ведут на APP_BASE_URL. Если там остался адрес этой машины,
    # друг нажмёт «восстановить пароль» и откроет пустую страницу.
    if SMTP_ENABLED and APP_BASE_URL.startswith("http://"):
        print("ВНИМАНИЕ: ссылки из писем ведут на " + APP_BASE_URL +
              " — они откроются только на этом компьютере. Для сайта в интернете "
              "укажи в APP_BASE_URL публичный адрес с HTTPS.")
    # Cookie без Secure по HTTPS утекает при перехвате и видна в инструментах
    # разработчика. Забытый COOKIE_SECURE=1 — самая частая ошибка при выставлении
    # сайта в интернет.
    if APP_BASE_URL.startswith("https://") and not COOKIE_SECURE:
        print("ВНИМАНИЕ: COOKIE_SECURE=0, а APP_BASE_URL с HTTPS. "
              "Укажи COOKIE_SECURE=1 в .env.")
    # Каждый HTTP-запрос обслуживается отдельным потоком; сетевое ожидание t.me
    # не блокирует другие вкладки.
    # История подписанных каналов догружается в фоне. В тестах фон не запускается:
    # там свой сервер и никаких фоновых запросов к t.me
    threading.Thread(target=_backfill_loop, daemon=True, name="backfill").start()
    threading.Thread(target=_private_loop, daemon=True, name="private").start()
    threading.Thread(target=_media_loop, daemon=True, name="media").start()
    Server(("0.0.0.0", PORT), Handler).serve_forever()
