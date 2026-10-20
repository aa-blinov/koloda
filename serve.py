#!/usr/bin/env python3
"""Отдаёт SQLite-базу из sync.py как RSS. Любой ридер подписывается на /feed.xml."""
import os
import sqlite3
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, HTTPServer
from xml.sax.saxutils import escape

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "lenta.db")
PORT = int(os.environ.get("PORT", 8899))
MAX_ITEMS = 300


def build_feed(db):
    cur = db.execute(
        "SELECT channel_id, msg_id, channel, title, url, body, ts "
        "FROM messages ORDER BY ts DESC LIMIT ?",
        (MAX_ITEMS,),
    )
    items = []
    for cid, mid, channel, title, url, body, ts in cur.fetchall():
        items.append(
            "<item>"
            f"<title>{escape('[%s] %s' % (channel, title))}</title>"
            f"<link>{escape(url or '')}</link>"
            # msg_id уникален внутри канала; пара (cid, mid) не collides даже
            # для двух постов с одинаковой секундой публикации.
            f"<guid isPermaLink=\"false\">{cid}-{mid}</guid>"
            f"<description>{escape(body or '')}</description>"
            f"<pubDate>{formatdate(ts, usegmt=True)}</pubDate>"
            "</item>"
        )
    last = formatdate(db.execute("SELECT MAX(ts) FROM messages").fetchone()[0] or 0, usegmt=True)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        "<title>Telegram — единая лента</title>"
        "<link>https://telegram.org</link>"
        f"<description>{len(items)} последних сообщений</description>"
        f"<lastBuildDate>{last}</lastBuildDate>"
        + "".join(items)
        + "</channel></rss>"
    )


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not os.path.exists(DB):
            self.send_error(503, "run sync.py first")
            return
        db = sqlite3.connect(DB)
        xml = build_feed(db).encode()
        db.close()
        self.send_response(200)
        self.send_header("Content-Type", "application/rss+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(xml)))
        self.end_headers()
        self.wfile.write(xml)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"RSS на http://127.0.0.1:{PORT}/feed.xml")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
