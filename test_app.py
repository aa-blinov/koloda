"""Регрессионные проверки личной ленты без обращения к Telegram."""
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from unittest.mock import patch

import app


class FeedAPITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="telegram-lenta-test-")
        self.previous_db = app.DB
        app.DB = self.temp.name + "/feed.db"
        self.db = app.connect()
        self.seed_posts(85)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.cookie = ""

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.db.close()
        app.DB = self.previous_db
        self.temp.cleanup()

    def seed_posts(self, count):
        now = int(time.time())
        rows = []
        channels = {"alpha": [], "beta": []}
        for i in range(count):
            channel = "alpha" if i % 2 == 0 else "beta"
            msg_id = i + 1
            ts = 1_800_000_000 + i // 2
            channels[channel].append((channel, msg_id, "https://t.me/%s/%d" % (channel, msg_id),
                                      "post %d" % msg_id, None, None, None, ts,
                                      None, None, None, None, None, None))
        rows = channels["alpha"] + channels["beta"]
        self.db.executemany("INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.db.executemany("INSERT INTO cache VALUES (?,?,?)",
                            [(channel, channel.title(), now) for channel in channels])
        self.db.commit()

    def request(self, path, method="GET", data=None, client_id=None):
        headers = {}
        payload = None
        if data is not None:
            payload = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        if client_id:
            headers["X-Client-Id"] = client_id
        if self.cookie:
            headers["Cookie"] = self.cookie
        req = Request(self.base + path, data=payload, headers=headers, method=method)
        try:
            with urlopen(req, timeout=5) as response:
                cookie = response.headers.get("Set-Cookie")
                if cookie:
                    self.cookie = cookie.split(";", 1)[0]
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_feed_paginates_without_duplicates_in_stable_time_order(self):
        query = urlencode({"channels": "alpha,beta"})
        status, first = self.request("/api/feed?" + query)
        self.assertEqual(status, 200)
        self.assertEqual(len(first["posts"]), app.FEED_PAGE_SIZE)
        self.assertTrue(first["has_more"])
        tuples = [(p["ts"], p["channel"], p["msg_id"]) for p in first["posts"]]
        self.assertEqual(tuples, sorted(tuples, key=lambda row: (-row[0], row[1], -row[2])))

        cursor = first["next"]
        query = urlencode({"channels": "alpha,beta", "before_ts": cursor["ts"],
                           "before_channel": cursor["channel"], "before_id": cursor["msg_id"]})
        status, second = self.request("/api/feed?" + query)
        self.assertEqual(status, 200)
        self.assertEqual(len(second["posts"]), 5)
        ids1 = {(p["channel"], p["msg_id"]) for p in first["posts"]}
        ids2 = {(p["channel"], p["msg_id"]) for p in second["posts"]}
        self.assertFalse(ids1 & ids2)
        self.assertFalse(second["has_more"])

    def test_feed_accepts_more_than_twenty_channels(self):
        now = int(time.time())
        names = ["ch%03d" % i for i in range(21)]
        self.db.executemany("INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            [(name, 1, "https://t.me/%s/1" % name, name, None, None, None,
                              1_800_000_100, None, None, None, None, None, None)
                             for name in names])
        self.db.executemany("INSERT INTO cache VALUES (?,?,?)",
                            [(name, name, now) for name in names])
        self.db.commit()
        status, payload = self.request("/api/feed?" + urlencode({"channels": ",".join(names)}))
        self.assertEqual(status, 200)
        self.assertEqual({p["channel"] for p in payload["posts"]}, set(names))

    def test_one_failed_channel_keeps_cached_posts_and_reports_partial_error(self):
        self.db.execute("UPDATE cache SET fetched=0 WHERE channel='alpha'")
        self.db.commit()
        with patch.object(app, "fetch_channel", side_effect=TimeoutError("timed out")):
            status, payload = self.request("/api/feed?channels=alpha,beta")
        self.assertEqual(status, 200)
        self.assertTrue(payload["posts"])
        self.assertEqual([error["channel"] for error in payload["errors"]], ["alpha"])

    def test_channel_preview_returns_six_latest_cached_posts(self):
        status, payload = self.request("/api/preview?c=alpha")
        self.assertEqual(status, 200)
        self.assertEqual(payload["title"], "Alpha")
        self.assertEqual(len(payload["posts"]), 6)
        keys = [(post["ts"], post["msg_id"]) for post in payload["posts"]]
        self.assertEqual(keys, sorted(keys, key=lambda row: (-row[0], -row[1])))


    def test_login_checks_password_and_logout_expires_session(self):
        status, _ = self.request("/api/auth/register", method="POST",
                                 data={"username": "profile_login", "password": "long-password"})
        self.assertEqual(status, 201)
        self.cookie = ""
        status, payload = self.request("/api/auth/login", method="POST",
                                      data={"username": "profile_login", "password": "wrong-password"})
        self.assertEqual(status, 401)
        self.assertIsNone(payload.get("account"))
        status, payload = self.request("/api/auth/login", method="POST",
                                      data={"username": "profile_login", "password": "long-password"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["account"]["username"], "profile_login")
        status, _ = self.request("/api/auth/logout", method="POST", data={})
        self.assertEqual(status, 200)
        status, _ = self.request("/api/state")
        self.assertEqual(status, 401)

    def test_state_rejects_browser_supplied_identity(self):
        status, payload = self.request("/api/state", client_id="someone-else")
        self.assertEqual(status, 401)
        self.assertIn("error", payload)

    def test_password_profiles_keep_state_separate(self):
        status, _ = self.request("/api/auth/register", method="POST",
                                 data={"username": "profile_one", "password": "long-password"})
        self.assertEqual(status, 201)
        status, _ = self.request("/api/state/subscription", method="POST",
                                 data={"channel": "alpha", "subscribed": True})
        self.assertEqual(status, 200)
        first_cookie = self.cookie
        self.cookie = ""
        status, _ = self.request("/api/auth/register", method="POST",
                                 data={"username": "profile_two", "password": "long-password"})
        self.assertEqual(status, 201)
        status, second_state = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(second_state["subs"], [])
        self.cookie = first_cookie
        status, first_state = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(first_state["subs"], ["alpha"])

    def test_state_operations_merge_and_sync(self):
        status, account = self.request("/api/auth/register", method="POST",
                                       data={"username": "profile_one", "password": "long-password"})
        self.assertEqual(status, 201)
        status, initial = self.request("/api/state", client_id="local-123")
        self.assertEqual(status, 200)
        self.assertTrue(initial["initialized"])
        status, merged = self.request("/api/state/merge", method="POST", client_id="local-123",
                                      data={"subs": ["alpha"], "reads": []})
        self.assertEqual(status, 200)
        status, merged = self.request("/api/state/merge", method="POST", client_id="shared-123",
                                      data={"subs": ["beta"], "reads": []})
        self.assertEqual(status, 200)
        status, merged = self.request("/api/state/merge", method="POST", client_id="shared-123",
                                      data={"subs": ["alpha"], "reads": ["alpha/1"]})
        self.assertEqual(status, 200)
        self.assertEqual(set(merged["subs"]), {"alpha", "beta"})

        status, state = self.request("/api/state/subscription", method="POST", client_id="shared-123",
                                     data={"channel": "alpha", "subscribed": True})
        self.assertEqual(status, 200)
        self.assertEqual(set(state["subs"]), {"alpha", "beta"})
        status, state = self.request("/api/state/subscription", method="POST", client_id="shared-123",
                                     data={"channel": "beta", "subscribed": False})
        self.assertEqual(status, 200)
        self.assertEqual(state["subs"], ["alpha"])

        status, state = self.request("/api/state/read", method="POST", client_id="shared-123",
                                     data={"posts": ["alpha/1"]})
        self.assertEqual(status, 200)
        self.assertIn("alpha/1", state["reads"])
        query = urlencode({"channels": "alpha,beta", "unread": "1"})
        status, feed = self.request("/api/feed?" + query, client_id="shared-123")
        self.assertEqual(status, 200)
        self.assertNotIn(("alpha", 1), {(p["channel"], p["msg_id"]) for p in feed["posts"]})


class CatalogCategoryTest(unittest.TestCase):
    def test_categories_keep_source_slugs_instead_of_building_them_from_labels(self):
        channels = [
            {"cat": "Art & Design", "cat_slug": "art"},
            {"cat": "Technology", "cat_slug": "tech"},
            {"cat": "Art & Design", "cat_slug": "art"},
        ]
        with patch.object(app, "search_catalog", return_value=channels):
            self.assertEqual(app.catalog_categories(), [
                {"name": "Art & Design", "slug": "art"},
                {"name": "Technology", "slug": "tech"},
            ])


class MediaParseTest(unittest.TestCase):
    """Пост без медиа не должен получать эмодзи из текста вместо картинки."""

    HTML = ('<div class="tgme_widget_message" data-post="durov/1">'
            '<div class="tgme_widget_message_text" dir="auto"><span class="emoji" '
            'style="background-image:url(\'//telegram.org/img/emoji/40/F09F8E81.png\')">✨</span>'
            ' привет</div>%s</div>')

    def parse(self, media):
        html = self.HTML % media
        with patch.object(app.urllib.request, "urlopen") as net:
            net.return_value.read.return_value = (
                '<meta property="og:title" content="Канал"/>' + html).encode()
            title, posts = app.fetch_channel("durov")
        self.assertEqual(title, "Канал")
        return posts[0]

    def test_emoji_in_text_is_not_taken_for_a_photo(self):
        post = self.parse("")
        self.assertIsNone(post["image"])
        self.assertIn("привет", post["text"])

    def test_photo_wins_over_emoji_and_keeps_extra_classes(self):
        post = self.parse('<div class="tgme_widget_message_photo_wrap 5215576584121683352 '
                          '1214346053_460000664" style="width:800px;'
                          'background-image:url(\'https://cdn4.telesco.pe/file/REAL.jpg\')">')
        self.assertEqual(post["image"], "https://cdn4.telesco.pe/file/REAL.jpg")

    def test_video_post_gets_no_separate_cover(self):
        post = self.parse('<div class="tgme_widget_message_video_wrap" '
                          'style="background-image:url(\'https://cdn4.telesco.pe/file/THUMB.jpg\')">'
                          '<video src="https://cdn4.telesco.pe/file/REAL.mp4"></video>')
        self.assertIsNone(post["image"])
        self.assertEqual(post["video"], "https://cdn4.telesco.pe/file/REAL.mp4")


if __name__ == "__main__":
    unittest.main()
