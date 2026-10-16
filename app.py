#!/usr/bin/env python3
"""Веб-сервис: единая лента из ПУБЛИЧНЫХ Telegram-каналов.

Посты берутся из публичной превью-страницы t.me/s/<name>.
Профили сайта защищены паролем; приватные Telegram-каналы так не читаются.

Запуск: python3 app.py  [порт]
Открыть: http://127.0.0.1:8000
"""
import json
import hashlib
import http.cookies
import smtplib
import os
import re
import secrets
import sqlite3
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape, unescape
from urllib.parse import parse_qs, urlparse

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
CREATE TABLE IF NOT EXISTS cache (
    channel  TEXT PRIMARY KEY,
    title    TEXT,
    fetched  INTEGER
);
-- Состояние пользователя: подписки и прочитанные посты.
-- Доступ к профилю задаётся паролем и серверной сессией.
CREATE TABLE IF NOT EXISTS profiles (
    code    TEXT PRIMARY KEY,
    created INTEGER,
    initialized INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS user_subs (
    profile TEXT NOT NULL,
    channel TEXT NOT NULL,
    PRIMARY KEY (profile, channel)
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
"""

# Имя канала попадает в URL t.me, поэтому проверяем форму: без этого
# в локальной сети можно было бы заставить сервис ходить по произвольным адресам.
CHANNEL_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")

# Короткий TTL: t.me отдаёт превью без ограничений, но и обновляется не мгновенно.
TTL = 300
FEED_PAGE_SIZE = 80


def connect():
    db = sqlite3.connect(DB)
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
    через [^"]*, а не сразу после имени."""
    m = re.search(r'class="' + cls + r'[^"]*"[^>]*>(.*?)</div>', chunk, re.S)
    if not m:
        return None
    return unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() or None


def parse_reactions(chunk):
    """Реакции: обычные эмодзи и кастомные. У кастомных в разметке нет символа,
    только emoji-id — выдумывать смайлик нельзя, поэтому отдаём пустую метку."""
    box = re.search(r'class="tgme_widget_message_reactions[^"]*"[^>]*>(.*?)</div>', chunk, re.S)
    if not box:
        return None
    out = []
    for r in re.finditer(r'<span class="tgme_reaction[^"]*">(.*?)</span>', box.group(1), re.S):
        inner = r.group(1)
        e = re.search(r"<b>(.*?)</b>", inner, re.S)
        # Счётчик — то, что после иконки эмодзи, иначе в него попадёт сам символ.
        tail = re.sub(r"<i\b.*?</i>", "", inner, flags=re.S)
        count = re.sub(r"<[^>]+>", "", tail).strip()
        if not count:
            continue
        out.append({"emoji": unescape(e.group(1)) if e else "", "count": count,
                    "paid": "stars" in inner})
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
    m = re.search(r'<a class="tgme_widget_message_forwarded_from_name"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', chunk, re.S)
    if not m:
        return None
    return {"url": unescape(m.group(1)),
            "name": unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()}


def parse_quote(chunk):
    m = re.search(r'<a class="tgme_widget_message_reply_quote"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', chunk, re.S)
    if not m:
        return None
    inner = m.group(2)
    return {"url": unescape(m.group(1)), "author": _text(inner, "tgme_widget_message_author_name"),
            "text": _text(inner, "tgme_widget_message_text")}


def valid_channel(name):
    return bool(CHANNEL_RE.match(name or ""))


def fetch_channel(channel):
    """Один канал -> (title, [posts]). Бросает ValueError если канала нет."""
    url = f"https://t.me/s/{channel}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")

    t = re.search(r'property="og:title" content="([^"]+)"', html) or \
        re.search(r"<title>([^<]+)</title>", html)
    title = t.group(1).strip() if t else channel

    posts = []
    for m in re.finditer(r'<div class="tgme_widget_message[^"]*"[^>]*data-post="([^"]+)"', html):
        start = m.start()
        nxt = html.find('data-post="', m.end())
        chunk = html[start:nxt if nxt != -1 else len(html)]

        # data-post отдаёт канонический username, а страницу открыли по псевдониму
        # (tonlive -> tlive). Кладём в базу под именем подписки, иначе выборка по
        # нему не найдёт ни одного поста. В ссылке оставляем каноническое имя.
        owner, _, msg_id = m.group(1).partition("/")
        if not msg_id.isdigit():
            continue
        txt = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', chunk, re.S)
        # Медиа берём из контейнеров: первый background-image в посте — это обычно
        # кастомный эмодзи из текста, и он перебивал настоящие снимки.
        photos = [unescape(u.strip("'\"")) for u in re.findall(
            r'tgme_widget_message_photo_wrap[^"]*"[^>]*background-image:url\(([^)]+)\)', chunk)]
        # Видео лежит в <video src> — прямая ссылка с токеном, отдаётся как mp4.
        videos = [unescape(u) for u in re.findall(r'<video[^>]+src="([^"]+)"', chunk)]
        videos += [unescape(u) for u in re.findall(r'<video[^>]+src=\'([^\']+)\'', chunk)]
        media = [{"k": "i", "u": u} for u in photos] + [{"k": "v", "u": u} for u in videos]
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

        sticker = _bg(chunk, "tgme_widget_message_sticker_wrap")
        posts.append({
            "channel": channel,
            "msg_id": int(msg_id),
            "url": f"https://t.me/{owner}/{msg_id}",
            "text": rich_text(txt.group(1)) if txt else "",
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
    # Единственный честный признак канала — посты в его ленте.
    if not posts:
        raise ValueError(f"канал «{channel}» не найден, закрыт или без постов")
    return title, posts


J = lambda v: json.dumps(v, ensure_ascii=False) if v else None

# Колонки постов в одном месте: копия этого списка в выборке или INSERT
# разъезжается с таблицей после миграции.
POST_FIELDS = ("channel", "msg_id", "url", "text", "media", "views", "ts",
               "reactions", "preview", "doc", "fwd", "quote", "sticker")
POST_INSERT = (f"INSERT OR REPLACE INTO posts ({','.join(POST_FIELDS)}) VALUES "
               f"({','.join(':' + f for f in POST_FIELDS)})")


def refresh(db, channel, force=False):
    row = db.execute("SELECT fetched FROM cache WHERE channel=?", (channel,)).fetchone()
    if row and not force and time.time() - row[0] < TTL:
        return row and True
    title, posts = fetch_channel(channel)
    db.executemany(
        POST_INSERT,
        posts,
    )
    db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (channel, title, int(time.time())))
    db.commit()
    return True


def search_posts(db, q, channels, only_unread=None, limit=120, before=None):
    """Простой LIKE поверх SQLite — FTS5 тут оверинжиниринг."""
    sql = "SELECT " + ",".join(POST_FIELDS) + " FROM posts WHERE 1=1"
    args = []
    if channels:
        sql += " AND channel IN (%s)" % ",".join("?" * len(channels))
        args += channels
    if q:
        sql += " AND text LIKE ?"
        args.append(f"%{q}%")
    if only_unread:
        sql += (" AND NOT EXISTS (SELECT 1 FROM user_reads r"
                " WHERE r.profile=? AND r.post=posts.channel || '/' || posts.msg_id)")
        args.append(only_unread)
    if before:
        ts, channel, msg_id = before
        sql += (" AND (ts < ? OR (ts = ? AND (channel > ? OR"
                " (channel = ? AND msg_id < ?))))")
        args.extend((ts, ts, channel, channel, msg_id))
    sql += " ORDER BY ts DESC, channel ASC, msg_id DESC LIMIT ?"
    args.append(limit)
    return db.execute(sql, args).fetchall()


def refresh_channels(db, channels, force=False, workers=8):
    """Обновляет просроченные каналы параллельно и сохраняет успешные ответы.

    Один недоступный канал не мешает обновлению остальных. Все записи SQLite
    выполняются в основном потоке: в пул уходят только сетевые запросы.
    """
    now = int(time.time())
    cached = dict(db.execute(
        "SELECT channel,fetched FROM cache WHERE channel IN (%s)" %
        ",".join("?" * len(channels)), channels
    )) if channels else {}
    stale = [c for c in channels
             if force or c not in cached or now - cached[c] >= TTL]
    if not stale:
        return []

    errors = []
    with ThreadPoolExecutor(max_workers=min(workers, len(stale))) as pool:
        futures = {pool.submit(fetch_channel, channel): channel for channel in stale}
        for future in as_completed(futures):
            channel = futures[future]
            try:
                title, posts = future.result()
            except Exception as exc:
                errors.append({"channel": channel, "message": str(exc)[:180]})
                continue
            store_channel_cache(db, channel, title, posts)
    db.commit()
    return sorted(errors, key=lambda item: channels.index(item["channel"]))


def store_channel_cache(db, channel, title, posts):
    db.executemany(
        POST_INSERT,
        posts,
    )
    db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)",
               (channel, title, int(time.time())))


# TGMania — открытый каталог каналов (без ключа). Сортировку на их стороне
# приходится переделывать: ответ приходит не по subscriber_count.
TGMANIA = "https://tgmania.com/api/channels"


def search_catalog(q="", category="", limit=40):
    """Каталог каналов из TGMania. Возвращает [{username,name,desc,subs,q,cat}]."""
    params = {"limit": min(limit, 50), "type": "channel"}
    if q:
        params["q"] = q
    if category and category != "all":
        params["category"] = category

    url = TGMANIA + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    data = json.loads(urllib.request.urlopen(req, timeout=20).read())

    out = []
    for c in data.get("channels", []):
        u = c.get("username")
        if not u:
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
        })

    # Топ-каналы сортируем сами: sortBy у TGMania не работает.
    # "all" — это тоже «без фильтра», иначе сортировка молча выключается.
    if not q and category in ("", "all"):
        out.sort(key=lambda x: x["subs"], reverse=True)
    return out


def catalog_categories():
    """Категории собираем из ответа поиска: отдельного эндпоинта у TGMania нет."""
    seen, out = set(), []
    for c in search_catalog(limit=50):
        name = c["cat"]
        slug = c["cat_slug"] or name.lower().replace(" ", "-").replace("&", "and")
        if not name or not slug or slug in seen:
            continue
        seen.add(slug)
        out.append({"name": name, "slug": slug})
    return sorted(out, key=lambda x: x["name"])


ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
SESSION_COOKIE = "lenta_session"
SESSION_TTL = 30 * 24 * 60 * 60
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
    from urllib.parse import urlencode
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


def mail_request_allowed(key, cooldown=60):
    now = time.time()
    previous = MAIL_REQUESTS.get(key, 0)
    if now - previous < cooldown:
        return False
    MAIL_REQUESTS[key] = now
    return True


def login_allowed(username):
    now = time.time()
    recent = [stamp for stamp in LOGIN_FAILURES.get(username.lower(), [])
              if now - stamp < 900]
    LOGIN_FAILURES[username.lower()] = recent
    return len(recent) < 8


def record_login_failure(username):
    LOGIN_FAILURES.setdefault(username.lower(), []).append(time.time())


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
    return {"subs": subs, "reads": reads, "initialized": initialized}


def save_state(db, cid, data):
    subs = [c for c in data.get("subs", []) if valid_channel(c)][:200]
    reads = [r for r in data.get("reads", []) if valid_post_key(r)][:20000]
    db.execute("INSERT OR IGNORE INTO profiles (code,created) VALUES (?,?)",
               (cid, int(time.time())))
    db.execute("DELETE FROM user_subs WHERE profile=?", (cid,))
    db.execute("DELETE FROM user_reads WHERE profile=?", (cid,))
    db.executemany("INSERT OR IGNORE INTO user_subs VALUES (?,?)", [(cid, c) for c in subs])
    db.executemany("INSERT OR IGNORE INTO user_reads VALUES (?,?)", [(cid, r) for r in reads])
    db.execute("UPDATE profiles SET initialized=1 WHERE code=?", (cid,))
    db.commit()


def merge_state(db, cid, data):
    """Однократно соединяет локальное состояние с уже существующим профилем."""
    incoming_subs = [c for c in data.get("subs", []) if valid_channel(c)]
    reads = list(dict.fromkeys(r for r in data.get("reads", [])
                               if valid_post_key(r)))[:20000]
    db.execute("INSERT OR IGNORE INTO profiles (code,created) VALUES (?,?)",
               (cid, int(time.time())))
    existing = [r[0] for r in db.execute("SELECT channel FROM user_subs WHERE profile=?", (cid,))]
    subs = list(dict.fromkeys(existing + incoming_subs))
    if len(subs) > 200:
        raise ValueError("После объединения получится больше 200 каналов")
    db.executemany("INSERT OR IGNORE INTO user_subs VALUES (?,?)", [(cid, c) for c in subs])
    db.executemany("INSERT OR IGNORE INTO user_reads VALUES (?,?)", [(cid, r) for r in reads])
    db.execute("UPDATE profiles SET initialized=1 WHERE code=?", (cid,))
    db.commit()
    return load_state(db, cid)


def valid_post_key(value):
    if not isinstance(value, str):
        return False
    channel, sep, msg_id = value.partition("/")
    return bool(sep and valid_channel(channel) and msg_id.isdigit() and len(msg_id) <= 20)


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
    db.execute("UPDATE profiles SET initialized=1 WHERE code=?", (cid,))
    db.commit()
    return load_state(db, cid)


def mark_posts_read(db, cid, post_keys):
    valid = list(dict.fromkeys(key for key in post_keys if valid_post_key(key)))[:500]
    db.execute("INSERT OR IGNORE INTO profiles (code,created) VALUES (?,?)",
               (cid, int(time.time())))
    db.executemany("INSERT OR IGNORE INTO user_reads VALUES (?,?)",
                   [(cid, key) for key in valid])
    db.execute("UPDATE profiles SET initialized=1 WHERE code=?", (cid,))
    db.commit()
    return load_state(db, cid)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype, headers=None):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        for name, value in headers or []:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._route()

    def do_PUT(self):
        self._route()

    def do_POST(self):   # navigator.sendBeacon при уходе со страницы
        self._route()

    def _route(self):
        path = self.path.split("?")[0]
        db = connect()
        try:
            if path in ("/", "/verify", "/reset"):
                html = open(os.path.join(BASE, "index.html"), encoding="utf-8").read()
                return self._send(200, html, "text/html; charset=utf-8")

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
                    return self._send(400, json.dumps({"error": "Имя: 3–32 латинских буквы, цифры или _ -"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if not isinstance(password, str) or not 8 <= len(password) <= 128:
                    return self._send(400, json.dumps({"error": "Пароль должен быть от 8 до 128 символов"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if email and not valid_email(email):
                    return self._send(400, json.dumps({"error": "Введи действующий адрес почты"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if email and not SMTP_ENABLED:
                    return self._send(503, json.dumps({"error": "Отправка писем пока недоступна"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if not email and SMTP_ENABLED:
                    return self._send(400, json.dumps({"error": "Для профиля нужна почта"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if db.execute("SELECT 1 FROM accounts WHERE username=? COLLATE NOCASE",
                              (username,)).fetchone():
                    return self._send(409, json.dumps({"error": "Это имя уже занято. Выбери другое."}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                # без почты поле пустое, и второй профиль без почты совпал бы с первым
                if email and db.execute("SELECT 1 FROM accounts WHERE email=? COLLATE NOCASE", (email,)).fetchone():
                    return self._send(409, json.dumps({"error": "Эта почта уже привязана к профилю"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                profile = secrets.token_urlsafe(24)
                salt = secrets.token_hex(16)
                digest = password_digest(password, salt)
                try:
                    db.execute("INSERT INTO profiles (code,created,initialized) VALUES (?,?,1)",
                               (profile, int(time.time())))
                    db.execute("INSERT INTO accounts "
                               "(username,profile,salt,digest,created,email,email_verified) "
                               "VALUES (?,?,?,?,?,?,?)",
                               (username, profile, salt, digest, int(time.time()),
                                email or None,   # пустая почта = NULL, иначе ломается accounts_email
                                0 if email else 1))
                    token = issue_auth_token(db, profile, "email_verify", email) if email else ""
                    db.commit()
                except sqlite3.IntegrityError:
                    db.rollback()
                    return self._send(409, json.dumps({"error": "Не удалось создать профиль. Попробуй другое имя."}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if not email:
                    cookie = create_session(db, profile)
                    return self._send(201, json.dumps({"account": {"username": username,
                                      "email": None, "email_verified": False}}, ensure_ascii=False),
                                      "application/json; charset=utf-8", [("Set-Cookie", cookie)])
                try:
                    send_verification_email(email, token)
                except Exception:
                    db.execute("DELETE FROM auth_tokens WHERE profile=?", (profile,))
                    db.execute("DELETE FROM accounts WHERE profile=?", (profile,))
                    db.execute("DELETE FROM profiles WHERE code=?", (profile,))
                    db.commit()
                    return self._send(503, json.dumps({"error": "Не удалось отправить письмо. Проверь настройки почты и попробуй ещё раз."}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                return self._send(202, json.dumps({"verification_required": True,
                                  "message": "Проверь почту и перейди по ссылке в письме."}, ensure_ascii=False),
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
                    account = db.execute("SELECT username,email FROM accounts WHERE profile=?",
                                         (profile,)).fetchone()
                    if not account:
                        return self._send(400, json.dumps({"error": "Профиль не найден"}, ensure_ascii=False),
                                          "application/json; charset=utf-8")
                    db.execute("DELETE FROM auth_tokens WHERE profile=?", (profile,))
                    db.commit()
                    cookie = create_session(db, profile)
                    return self._send(200, json.dumps({"account": {"username": account[0],
                                      "email": account[1], "email_verified": True}}, ensure_ascii=False),
                                      "application/json; charset=utf-8", [("Set-Cookie", cookie)])
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
                if valid_email(email) and mail_request_allowed("verify:" + email):
                    row = db.execute("SELECT profile FROM accounts WHERE email=? COLLATE NOCASE "
                                     "AND email_verified=0", (email,)).fetchone()
                    if row:
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
                if valid_email(email) and mail_request_allowed("reset:" + email):
                    row = db.execute("SELECT profile FROM accounts WHERE email=? COLLATE NOCASE "
                                     "AND email_verified=1", (email,)).fetchone()
                    if row:
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
                if db.execute("SELECT 1 FROM accounts WHERE email=? COLLATE NOCASE AND profile<>?",
                              (email, account["profile"])).fetchone():
                    return self._send(409, json.dumps({"error": "Эта почта уже привязана к другому профилю"}, ensure_ascii=False),
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
                return self._send(202, json.dumps({"message": "Перейди по ссылке в письме, чтобы подтвердить почту."}, ensure_ascii=False),
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
                if not isinstance(password, str) or not login_allowed(username):
                    return self._send(429, json.dumps({"error": "Слишком много попыток. Попробуй позже."}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                row = db.execute("SELECT username,profile,salt,digest,email,email_verified FROM accounts "
                                 "WHERE username=? COLLATE NOCASE", (username,)).fetchone()
                salt = row[2] if row else "00112233445566778899aabbccddeeff"
                candidate = password_digest(password, salt)
                valid = row is not None and secrets.compare_digest(candidate, row[3])
                if not valid:
                    record_login_failure(username)
                    return self._send(401, json.dumps({"error": "Имя или пароль не подошли"}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                if row[4] and not row[5]:
                    return self._send(403, json.dumps({"error": "Сначала подтверди почту по ссылке из письма."}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                LOGIN_FAILURES.pop(username.lower(), None)
                cookie = create_session(db, row[1])
                return self._send(200, json.dumps({"account": {"username": row[0], "email": row[4],
                                  "email_verified": True}}, ensure_ascii=False),
                                  "application/json; charset=utf-8", [("Set-Cookie", cookie)])

            if path == "/api/auth/logout":
                account = account_for_request(db, self)
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

            if path == "/api/feed":
                q = self._query("q")
                chans = list(dict.fromkeys(
                    c for c in self._query("channels", "").split(",") if c
                ))[:200]
                bad = [c for c in chans if not valid_channel(c)]
                if bad:
                    return self._send(400, json.dumps({"error": "неверное имя канала"}),
                                      "application/json")
                force = self._query("refresh") == "1"
                errors = refresh_channels(db, chans, force)

                before = None
                cursor_values = (self._query("before_ts"), self._query("before_channel"),
                                 self._query("before_id"))
                if any(cursor_values):
                    try:
                        before = (int(cursor_values[0]), cursor_values[1], int(cursor_values[2]))
                    except (TypeError, ValueError):
                        return self._send(400, json.dumps({"error": "неверная позиция ленты"}),
                                          "application/json")
                    if not valid_channel(before[1]) or before[2] < 1:
                        return self._send(400, json.dumps({"error": "неверная позиция ленты"}),
                                          "application/json")

                unread_profile = client_id(self, db) if self._query("unread") == "1" else None
                rows = search_posts(db, q, chans, only_unread=unread_profile,
                                    limit=FEED_PAGE_SIZE + 1, before=before)
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
                titles = dict(db.execute("SELECT channel,title FROM cache"))
                return self._send(200, json.dumps({"posts": posts, "titles": titles,
                                                   "errors": errors, "has_more": has_more,
                                                   "next": next_cursor}),
                                  "application/json; charset=utf-8")

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
                state = mark_posts_read(db, cid, keys)
                return self._send(200, json.dumps(state, ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/catalog":
                try:
                    q = self._query("q")
                    cat = self._query("category", "all")
                    return self._send(200, json.dumps(
                        {"channels": search_catalog(q, cat)}, ensure_ascii=False),
                        "application/json; charset=utf-8")
                except Exception as e:
                    return self._send(502, json.dumps({"error": str(e)}),
                                      "application/json")

            if path == "/api/preview":
                channel = self._query("c")
                if not valid_channel(channel):
                    return self._send(400, json.dumps({"error": "неверное имя канала"}),
                                      "application/json; charset=utf-8")
                errors = refresh_channels(db, [channel])
                rows = db.execute(
                    "SELECT channel,msg_id,url,text,media,views,ts,reactions,"
                    "preview,doc,fwd,quote,sticker FROM posts WHERE channel=? "
                    "ORDER BY ts DESC,msg_id DESC LIMIT 6", (channel,)
                ).fetchall()
                if not rows:
                    status = 502 if errors else 404
                    message = "Не удалось загрузить публикации канала" if errors else "Канал не найден"
                    return self._send(status, json.dumps({"error": message}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                posts = [dict(zip(POST_FIELDS, row)) for row in rows]
                for post in posts:
                    for key in ("media", "reactions", "preview", "doc", "fwd", "quote"):
                        if post.get(key):
                            try:
                                post[key] = json.loads(post[key])
                            except ValueError:
                                post[key] = None
                title = db.execute("SELECT title FROM cache WHERE channel=?", (channel,)).fetchone()
                return self._send(200, json.dumps({"channel": channel,
                                                   "title": title[0] if title else channel,
                                                   "posts": posts, "errors": errors},
                                                  ensure_ascii=False),
                                  "application/json; charset=utf-8")

            if path == "/api/categories":
                try:
                    return self._send(200, json.dumps({"categories": catalog_categories()}),
                                      "application/json; charset=utf-8")
                except Exception as e:
                    return self._send(502, json.dumps({"error": str(e)}),
                                      "application/json")

            if path == "/api/check":
                # Проверка канала для каталога: 200 если живой.
                c = self._query("c")
                if not valid_channel(c):
                    return self._send(400, json.dumps({"error": "неверное имя канала"}),
                                      "application/json")
                try:
                    title, posts = fetch_channel(c)
                    store_channel_cache(db, c, title, posts)
                    db.commit()
                except Exception as e:
                    return self._send(404, json.dumps({"error": str(e)}),
                                      "application/json")
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

    def _read_json(self):
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return 400
        if size < 0:
            return 400
        if size > 4_000_000:
            return 413
        try:
            data = json.loads(self.rfile.read(size) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return 400
        return data if isinstance(data, dict) else 400

    def log_message(self, fmt, *a):
        # Логи выключены по умолчанию: при каждом запросе к t.me и картинкам они
        # засоряют вывод. Включаются переменной LOG=1.
        if os.environ.get("LOG") == "1":
            super().log_message(fmt, *a)


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    connect().close()
    print(f"Лента: http://127.0.0.1:{PORT}")
    # Каждый HTTP-запрос обслуживается отдельным потоком; сетевое ожидание t.me
    # не блокирует другие вкладки.
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
