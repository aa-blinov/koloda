"""Регрессионные проверки личной ленты без обращения к Telegram."""
import asyncio
import contextlib
import gzip
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from unittest.mock import call, patch

import app
import qrcodegen
import qrsvg


def fake_post(channel, msg_id, ts):
    """Пост с полным набором колонок, как его отдаёт fetch_channel."""
    post = {field: None for field in app.POST_FIELDS}
    post.update(channel=channel, msg_id=msg_id, url=f"https://t.me/{channel}/{msg_id}",
                text=f"post {msg_id}", ts=ts)
    return post


@contextlib.contextmanager
def feed_server():
    """Поднимает сервис на случайном порту с пустой базой и гасит его потом."""
    with tempfile.TemporaryDirectory(prefix="koloda-test-") as folder:
        previous = app.DB
        app.DB = folder + "/feed.db"
        db = app.connect()
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        server.base = "http://127.0.0.1:%d" % server.server_address[1]
        server.cookie = ""
        server.request = types.MethodType(FeedAPITest.request, server)
        server.request_raw = types.MethodType(FeedAPITest.request_raw, server)
        try:
            yield server
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            app.wait_refresh_idle()
            app._failed.clear()
            db.close()
            app.DB = previous


def register_with_mail(base, username, password="long-password-1"):
    """Регистрация с почтой по HTTP и подтверждение по ссылке из письма (письмо перехватываем).
    Возвращает cookie сессии, которую выдаёт подтверждение."""
    sent = {}
    with patch.object(app, "SMTP_ENABLED", True), \
            patch.object(app, "send_verification_email", side_effect=lambda e, t: sent.update(token=t)):
        request = Request(base + "/api/auth/register", method="POST",
                          data=json.dumps({"username": username, "password": password,
                                           "email": username + "@example.com"}).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=5) as response:
            assert response.status == 202, response.status
    verify = Request(base + "/api/auth/verify", method="POST",
                     data=json.dumps({"token": sent["token"]}).encode(),
                     headers={"Content-Type": "application/json"})
    with urlopen(verify, timeout=5) as response:
        return response.headers.get("Set-Cookie", "").split(";", 1)[0]


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
        app.wait_refresh_idle()
        app._failed.clear()
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
                                      "post %d" % msg_id, None, None, ts,
                                      None, None, None, None, None, None))
        rows = channels["alpha"] + channels["beta"]
        self.db.executemany("INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
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

    def request_raw(self, path, method="GET", data=None):
        """Как request, но с заголовками ответа: их проверяют отдельные тесты."""
        payload = json.dumps(data).encode() if data is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        if self.cookie:
            headers["Cookie"] = self.cookie
        req = Request(self.base + path, data=payload, headers=headers, method=method)
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, response.headers, response.read()
        except HTTPError as exc:
            return exc.code, exc.headers, exc.read()

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
        self.db.executemany("INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            [(name, 1, "https://t.me/%s/1" % name, name, None, None,
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
            self.assertTrue(app.wait_refresh_idle())
            # Ошибка попадает в ответ следующего запроса: обновление идёт в фоне
            status, payload = self.request("/api/feed?channels=alpha,beta")
        self.assertEqual(status, 200)
        self.assertTrue(payload["posts"])
        self.assertEqual([error["channel"] for error in payload["errors"]], ["alpha"])

    def test_feed_answers_from_cache_while_a_stale_channel_is_still_loading(self):
        self.db.execute("UPDATE cache SET fetched=0 WHERE channel='alpha'")
        self.db.commit()
        release = threading.Event()

        def slow(channel):
            release.wait(5)
            return "Alpha", []

        with patch.object(app, "fetch_channel", side_effect=slow):
            started = time.time()
            status, payload = self.request("/api/feed?channels=alpha,beta")
            elapsed = time.time() - started
            release.set()
            self.assertTrue(app.wait_refresh_idle())
        self.assertEqual(status, 200)
        self.assertTrue(payload["posts"])
        self.assertLess(elapsed, 3)

    def test_failing_channel_is_not_retried_on_every_request(self):
        self.db.execute("UPDATE cache SET fetched=0 WHERE channel='alpha'")
        self.db.commit()
        with patch.object(app, "fetch_channel", side_effect=TimeoutError("timed out")) as fetch:
            self.request("/api/feed?channels=alpha,beta")
            self.assertTrue(app.wait_refresh_idle())
            self.request("/api/feed?channels=alpha,beta")
            self.assertTrue(app.wait_refresh_idle())
        self.assertEqual(fetch.call_count, 1)

    def test_saved_posts_need_a_profile(self):
        self.cookie = ""
        self.assertEqual(self.request("/api/saved")[0], 401)
        self.assertEqual(self.request("/api/saved", "POST", {"channel": "alpha", "msg_id": 1, "on": True})[0], 401)

    def test_saved_post_shows_in_saved_list_and_feed_flag(self):
        self.cookie = ""
        status, _ = self.register("saver", "long-password")
        self.assertEqual(status, 201)
        channel, msg_id = self.db.execute(
            "SELECT channel,msg_id FROM posts WHERE channel='alpha' ORDER BY ts DESC LIMIT 1").fetchone()
        status, payload = self.request("/api/saved", "POST", {"channel": channel, "msg_id": msg_id, "on": True})
        self.assertEqual((status, payload), (200, {"saved": True}))
        status, saved = self.request("/api/saved")
        self.assertEqual(status, 200)
        self.assertEqual([(p["channel"], p["msg_id"]) for p in saved["posts"]], [(channel, msg_id)])
        self.assertTrue(saved["posts"][0]["saved"])
        status, feed = self.request("/api/feed?channels=alpha,beta")
        flags = {(p["channel"], p["msg_id"]): p["saved"] for p in feed["posts"]}
        self.assertTrue(flags[(channel, msg_id)])
        self.assertEqual(sum(flags.values()), 1)
        self.request("/api/saved", "POST", {"channel": channel, "msg_id": msg_id, "on": False})
        self.assertEqual(self.request("/api/saved")[1]["posts"], [])

    def test_saved_post_rejects_a_bad_channel_name(self):
        self.cookie = ""
        self.register("saver2", "long-password")
        status, _ = self.request("/api/saved", "POST", {"channel": "bad name!", "msg_id": 1, "on": True})
        self.assertEqual(status, 400)

    def test_saving_an_unknown_post_is_refused(self):
        self.cookie = ""
        self.register("saver3", "long-password")
        status, _ = self.request("/api/saved", "POST", {"channel": "alpha", "msg_id": 999999, "on": True})
        self.assertEqual(status, 404)

    def test_saved_list_searches_text_and_pages_without_gaps(self):
        self.cookie = ""
        self.register("saver4", "long-password")
        posts = self.db.execute("SELECT channel,msg_id FROM posts ORDER BY ts DESC, msg_id DESC").fetchall()
        self.assertGreaterEqual(len(posts), 81)
        self.db.execute("UPDATE posts SET text=? WHERE channel=? AND msg_id=?", ("уникальныйтекст", *posts[0]))
        self.db.commit()
        for channel, msg_id in posts[:81]:
            status, _ = self.request("/api/saved", "POST", {"channel": channel, "msg_id": msg_id, "on": True})
            self.assertEqual(status, 200)
        status, first = self.request("/api/saved")
        self.assertEqual(len(first["posts"]), 80)
        self.assertTrue(first["has_more"])
        status, second = self.request("/api/saved?" + urlencode({
            "before_saved": first["next"]["saved_at"], "before_channel": first["next"]["channel"],
            "before_id": first["next"]["msg_id"]}))
        self.assertEqual(len(second["posts"]), 1)
        self.assertFalse(second["has_more"])
        seen_first = {(p["channel"], p["msg_id"]) for p in first["posts"]}
        self.assertFalse(seen_first & {(p["channel"], p["msg_id"]) for p in second["posts"]})
        status, found = self.request("/api/saved?" + urlencode({"q": "уникальныйтекст"}))
        self.assertEqual([(p["channel"], p["msg_id"]) for p in found["posts"]], [posts[0]])

    def test_backfill_pages_older_posts_without_touching_the_cache(self):
        self.db.execute("INSERT INTO user_subs (profile, channel) VALUES ('prof_bf', 'alpha')")
        self.db.commit()
        lowest = self.db.execute("SELECT MIN(msg_id) FROM posts WHERE channel='alpha'").fetchone()[0]
        # Старая отметка обновления: если догрузка её перезапишет, это будет видно
        self.db.execute("UPDATE cache SET fetched=1000 WHERE channel='alpha'")
        self.db.commit()
        cached_before = 1000

        def fake_fetch(channel, before=None):
            if before == lowest:
                return "Alpha", [fake_post("alpha", m, 1_600_000_000 - m) for m in (lowest - 1, lowest - 2, lowest - 3)]
            return "Alpha", []

        with patch.object(app, "fetch_channel", side_effect=fake_fetch):
            self.assertTrue(app.backfill_step(self.db))
            status = app.history_status(self.db, "prof_bf")
            self.assertEqual((status["loading"], status["fetched"]), (True, 3))
            self.assertTrue(app.backfill_step(self.db))
        self.db.commit()
        self.assertEqual(self.db.execute("SELECT fetched FROM cache WHERE channel='alpha'").fetchone()[0], cached_before)
        stored = self.db.execute("SELECT COUNT(*) FROM posts WHERE channel='alpha' AND msg_id < ?", (lowest,)).fetchone()[0]
        self.assertEqual(stored, 3)
        status = app.history_status(self.db, "prof_bf")
        self.assertEqual((status["loading"], status["fetched"]), (False, 3))
        self.assertEqual([c["state"] for c in status["channels"]], ["done"])

    def test_backfill_backs_off_after_an_error_and_keeps_its_place(self):
        self.db.execute("INSERT INTO user_subs (profile, channel) VALUES ('prof_bf2', 'alpha')")
        self.db.commit()
        with patch.object(app, "fetch_channel", side_effect=ValueError("t.me попросил подождать")):
            self.assertTrue(app.backfill_step(self.db))
        next_before, failures, retry_at, done = self.db.execute(
            "SELECT next_before, failures, retry_at, done FROM backfill WHERE channel='alpha'").fetchone()
        self.assertEqual((failures, done), (1, 0))
        self.assertIsNotNone(next_before)
        self.assertGreater(retry_at, int(time.time()))
        self.assertNotIn("alpha", app.backfill_candidates(self.db))

    def test_backfill_waits_for_the_posts_of_a_new_subscription(self):
        # Подписка появилась раньше, чем лента записала посты: листать пока не от чего
        self.db.execute("INSERT INTO user_subs (profile, channel) VALUES ('prof_new', 'delta')")
        self.db.commit()
        self.assertNotIn("delta", app.backfill_candidates(self.db))
        self.assertFalse(app.backfill_step(self.db))
        self.assertIsNone(self.db.execute("SELECT done FROM backfill WHERE channel='delta'").fetchone())
        self.assertEqual(app.history_status(self.db, "prof_new")["channels"][0]["state"], "waiting")
        self.db.executemany(app.POST_INSERT, [fake_post("delta", 50, 1_600_000_000)])
        self.db.commit()
        self.assertIn("delta", app.backfill_candidates(self.db))

    def test_backfill_marks_the_channel_done_on_an_empty_older_page(self):
        self.db.execute("INSERT INTO user_subs (profile, channel) VALUES ('prof_end', 'alpha')")
        self.db.commit()
        # Самый старый пост уже в базе: t.me за ним отдаёт пустую страницу, это конец истории
        with patch.object(app, "fetch_channel", return_value=("Alpha", [])):
            self.assertTrue(app.backfill_step(self.db))
        self.db.commit()
        status = app.history_status(self.db, "prof_end")
        self.assertEqual([(c["state"], c["fetched"]) for c in status["channels"]], [("done", 0)])
        self.assertFalse(status["loading"])

    def test_history_status_names_the_state_of_every_channel(self):
        now = int(time.time())
        for channel in ("alpha", "beta", "gamma", "delta"):
            self.db.execute("INSERT INTO user_subs (profile, channel) VALUES ('prof_h', ?)", (channel,))
        self.db.execute("INSERT INTO backfill VALUES ('alpha', 900, 120, 0, 0, 0)")
        self.db.execute("INSERT INTO backfill VALUES ('beta', 800, 40, 0, 2, ?)", (now + 600,))
        self.db.execute("INSERT INTO backfill VALUES ('gamma', 700, 500, 1, 0, 0)")
        self.db.commit()
        status = app.history_status(self.db, "prof_h")
        states = {c["channel"]: c["state"] for c in status["channels"]}
        self.assertEqual(states, {"alpha": "loading", "beta": "paused", "gamma": "capped", "delta": "waiting"})
        self.assertTrue(status["loading"])
        self.assertEqual(status["fetched"], 660)
        self.assertEqual(status["cap"], app.BACKFILL_CAP)

    def test_history_endpoint_needs_a_profile(self):
        self.cookie = ""
        self.assertEqual(self.request("/api/history")[0], 401)

    def register(self, name, password="long-password"):
        """Регистрация с почтой, подтверждение и вход: без почты профиль не создаём."""
        self.cookie = ""
        # почта случайная и ASCII: имя может быть кириллическим, а адрес должен пройти проверку
        email = "reg%s@example.com" % os.urandom(4).hex()
        with patch.object(app, "SMTP_ENABLED", True), patch.object(app, "send_verification_email"):
            status, payload = self.request("/api/auth/register", method="POST", data={
                "username": name, "password": password, "email": email})
        if status == 202:
            self.db.execute("UPDATE accounts SET email_verified=1 WHERE username=?", (name,))
            self.db.commit()
            status, payload = self.request("/api/auth/login", method="POST",
                                           data={"username": name, "password": password})
            status = 201 if status == 200 else status  # профиль создан и вход выполнен
        return status, payload

    def login(self, name):
        status, _ = self.register(name, "password123")
        self.assertEqual(status, 201)
        return self.db.execute("SELECT profile FROM accounts WHERE username=?", (name,)).fetchone()[0]

    def subscribe(self, profile, channel="alpha"):
        self.db.execute("INSERT INTO user_subs (profile, channel) VALUES (?, ?)", (profile, channel))
        self.db.commit()

    def last_msg_id(self, channel="alpha"):
        return self.db.execute("SELECT MAX(msg_id) FROM posts WHERE channel=?", (channel,)).fetchone()[0]

    def test_dead_frame_gets_fresh_links_from_telegram(self):
        # t.me сменил адреса файлов: старая ссылка 404, свежая со страницы канала открывается
        msg = self.last_msg_id()
        self.subscribe(self.login("media_fan"))
        self.db.execute("UPDATE posts SET media=? WHERE channel='alpha' AND msg_id=?",
                        (json.dumps([{"k": "i", "u": "https://cdn.example/old.jpg"}]), msg))
        self.db.commit()
        fresh = fake_post("alpha", msg, 1_600_000_000)
        fresh["media"] = json.dumps([{"k": "i", "u": "https://cdn.example/new.jpg"}])
        with patch.dict(app._media_checked, clear=True), \
                patch.object(app, "fetch_channel", return_value=("Alpha", [fresh])) as fetch:
            status, payload = self.request(f"/api/media?post=alpha/{msg}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["media"][0]["u"], "https://cdn.example/new.jpg")
        # страница до msg + 1 захватывает сам пост, даже если он не на первой странице канала
        fetch.assert_called_once_with("alpha", before=msg + 1)
        stored = self.db.execute("SELECT media FROM posts WHERE channel='alpha' AND msg_id=?", (msg,)).fetchone()[0]
        self.assertEqual(json.loads(stored)[0]["u"], "https://cdn.example/new.jpg")

    def test_dead_preview_image_gets_a_fresh_link(self):
        # Картинка карточки ссылки мертва так же, как кадр: свежая приходит в том же ответе
        msg = self.last_msg_id()
        self.subscribe(self.login("preview_fan"))
        stored = {"url": "https://example.com/a", "site": "Example", "title": "Old", "desc": "",
                  "image": "https://cdn.example/old.jpg"}
        self.db.execute("UPDATE posts SET preview=? WHERE channel='alpha' AND msg_id=?", (json.dumps(stored), msg))
        self.db.commit()
        fresh = fake_post("alpha", msg, 1_600_000_000)
        fresh["preview"] = json.dumps({**stored, "image": "https://cdn.example/new.jpg"})
        with patch.dict(app._media_checked, clear=True), \
                patch.object(app, "fetch_channel", return_value=("Alpha", [fresh])):
            status, payload = self.request(f"/api/media?post=alpha/{msg}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["preview"]["image"], "https://cdn.example/new.jpg")
        saved = self.db.execute("SELECT preview FROM posts WHERE channel='alpha' AND msg_id=?", (msg,)).fetchone()[0]
        self.assertEqual(json.loads(saved)["image"], "https://cdn.example/new.jpg")

    def test_fresh_links_are_only_for_subscribers(self):
        msg = self.last_msg_id()
        self.login("media_stranger")  # без подписки на alpha
        with patch.dict(app._media_checked, clear=True), patch.object(app, "fetch_channel") as fetch:
            self.assertEqual(self.request(f"/api/media?post=alpha/{msg}")[0], 404)
            self.cookie = ""
            self.assertEqual(self.request(f"/api/media?post=alpha/{msg}")[0], 401)
        fetch.assert_not_called()

    def test_fresh_links_wait_between_checks_of_one_post(self):
        msg = self.last_msg_id()
        self.subscribe(self.login("media_busy"))
        with patch.dict(app._media_checked, clear=True), \
                patch.object(app, "fetch_channel", return_value=("Alpha", [])) as fetch:
            self.assertEqual(self.request(f"/api/media?post=alpha/{msg}")[0], 200)
            self.assertEqual(self.request(f"/api/media?post=alpha/{msg}")[0], 200)
        fetch.assert_called_once()

    def test_one_page_refreshes_every_post_on_it(self):
        # Страница t.me отдаёт около двадцати постов: свежие ссылки всех них берём одним запросом,
        # а не по одному запросу на каждый протухший кадр
        msg, other = [r[0] for r in self.db.execute(
            "SELECT msg_id FROM posts WHERE channel='alpha' ORDER BY msg_id DESC LIMIT 2")]
        self.subscribe(self.login("media_page"))
        old = json.dumps([{"k": "i", "u": "https://cdn.example/old.jpg"}])
        self.db.execute("UPDATE posts SET media=? WHERE channel='alpha' AND msg_id IN (?,?)", (old, msg, other))
        self.db.commit()
        page = []
        for mid in (msg, other):
            post = fake_post("alpha", mid, 1_600_000_000)
            post["media"] = json.dumps([{"k": "i", "u": f"https://cdn.example/new-{mid}.jpg"}])
            page.append(post)
        with patch.dict(app._media_checked, clear=True), \
                patch.object(app, "fetch_channel", return_value=("Alpha", page)) as fetch:
            self.assertEqual(self.request(f"/api/media?post=alpha/{msg}")[0], 200)
            self.assertEqual(self.request(f"/api/media?post=alpha/{other}")[0], 200)
        fetch.assert_called_once()
        stored = dict(self.db.execute(
            "SELECT msg_id, media FROM posts WHERE channel='alpha' AND msg_id IN (?,?)", (msg, other)).fetchall())
        self.assertEqual(json.loads(stored[other])[0]["u"], f"https://cdn.example/new-{other}.jpg")

    def test_fresh_links_report_telegram_errors_as_bad_gateway(self):
        msg = self.last_msg_id()
        self.subscribe(self.login("media_unlucky"))
        with patch.dict(app._media_checked, clear=True), \
                patch.object(app, "fetch_channel", side_effect=TimeoutError("timed out")):
            self.assertEqual(self.request(f"/api/media?post=alpha/{msg}")[0], 502)

    def test_collections_belong_to_one_profile(self):
        # Коллекция видна только своему профилю, и чужой id её не меняет и не удаляет
        self.subscribe(self.login("coll_owner"), "alpha")
        status, made = self.request("/api/collections/save", "POST", {"name": "Новости", "channels": ["alpha"]})
        self.assertEqual(status, 200)
        cid = made["collection"]["id"]
        self.login("coll_guest")
        self.assertEqual(self.request("/api/collections")[1]["collections"], [])
        self.assertEqual(self.request("/api/collections/save", "POST", {"id": cid, "name": "Чужая", "channels": []})[0], 404)
        self.assertEqual(self.request("/api/collections/delete", "POST", {"id": cid})[0], 404)

    def test_collection_is_saved_renamed_and_deleted(self):
        profile = self.login("coll_crud")
        self.subscribe(profile, "alpha")
        self.subscribe(profile, "beta")
        status, made = self.request("/api/collections/save", "POST", {"name": "Новости", "channels": ["alpha", "beta"]})
        self.assertEqual(status, 200)
        cid = made["collection"]["id"]
        status, renamed = self.request("/api/collections/save", "POST",
                                       {"id": cid, "name": "Все новости", "channels": ["beta"]})
        self.assertEqual((status, renamed["collection"]["name"], renamed["collection"]["channels"]),
                         (200, "Все новости", ["beta"]))
        self.assertEqual(self.request("/api/collections")[1]["collections"],
                         [{"id": cid, "name": "Все новости", "channels": ["beta"]}])
        self.assertEqual(self.request("/api/collections/delete", "POST", {"id": cid})[0], 200)
        self.assertEqual(self.request("/api/collections")[1]["collections"], [])

    def test_collection_rejects_strangers_and_bad_names(self):
        self.subscribe(self.login("coll_checks"), "alpha")
        self.assertEqual(self.request("/api/collections/save", "POST", {"name": "  ", "channels": []})[0], 400)
        self.assertEqual(self.request("/api/collections/save", "POST", {"name": "x" * 33, "channels": []})[0], 400)
        # канал, на который нет подписки, в коллекцию не попадает
        self.assertEqual(self.request("/api/collections/save", "POST", {"name": "Чужой", "channels": ["beta"]})[0], 400)
        self.assertEqual(self.request("/api/collections/save", "POST", {"name": "Ок", "channels": ["bad name!"]})[0], 400)
        self.assertEqual(self.request("/api/collections")[1]["collections"], [])

    def test_collection_names_are_unique_per_profile(self):
        # одинаковые названия в списке не различить: второе сохранение с тем же именем (без учёта
        # регистра и пробелов по краям) отклоняем, а переименовать коллекцию в её же название можно
        self.subscribe(self.login("coll_names"), "alpha")
        first = self.request("/api/collections/save", "POST", {"name": "Новости", "channels": ["alpha"]})[1]["collection"]
        self.assertEqual(self.request("/api/collections/save", "POST", {"name": " новости ", "channels": []})[0], 400)
        self.assertEqual(self.request("/api/collections/save", "POST",
                                      {"id": first["id"], "name": "Новости", "channels": ["alpha"]})[0], 200)
        other = self.request("/api/collections/save", "POST", {"name": "Тех", "channels": []})[1]["collection"]
        self.assertEqual(self.request("/api/collections/save", "POST",
                                      {"id": other["id"], "name": "НОВОСТИ", "channels": []})[0], 400)
        self.assertEqual([c["name"] for c in self.request("/api/collections")[1]["collections"]], ["Новости", "Тех"])

    def test_collections_are_listed_by_name_not_by_creation(self):
        # порядок коллекций по названию: создали «Яблоки», потом «Арбуз», а в списке первым «Арбуз»
        self.subscribe(self.login("coll_order"), "alpha")
        self.request("/api/collections/save", "POST", {"name": "Яблоки", "channels": []})
        self.request("/api/collections/save", "POST", {"name": "арбуз", "channels": []})
        self.assertEqual([c["name"] for c in self.request("/api/collections")[1]["collections"]],
                         ["арбуз", "Яблоки"])

    def test_unsubscribing_takes_the_channel_out_of_collections(self):
        profile = self.login("coll_prune")
        self.subscribe(profile, "alpha")
        self.subscribe(profile, "beta")
        self.request("/api/collections/save", "POST", {"name": "Две", "channels": ["alpha", "beta"]})
        self.request("/api/state/subscription", "POST", {"channel": "alpha", "subscribed": False})
        self.assertEqual(self.request("/api/collections")[1]["collections"][0]["channels"], ["beta"])

    @patch.object(app, "SMTP_ENABLED", True)
    def test_password_reset_sets_a_new_password_and_the_link_works_once(self):
        # A5, A6: почта подтверждается по ссылке, потом забытый пароль меняется по ссылке из письма
        sent = {}
        with patch.object(app, "send_verification_email", side_effect=lambda e, t: sent.update(verify=t)), \
                patch.object(app, "send_password_reset_email", side_effect=lambda e, t: sent.update(reset=t)):
            status, _ = self.request("/api/auth/register", method="POST", data={
                "username": "reset_user", "password": "password123", "email": "reset-user@example.com"})
            self.assertEqual(status, 202)  # профиль создан, но ждёт подтверждения почты
            self.assertEqual(self.request("/api/auth/verify", method="POST", data={"token": sent["verify"]})[0], 200)
            self.assertEqual(self.request("/api/auth/verify", method="POST", data={"token": sent["verify"]})[0], 400)
            self.assertEqual(self.request("/api/auth/forgot", method="POST",
                                          data={"email": "reset-user@example.com"})[0], 200)
            self.assertEqual(self.request("/api/auth/reset", method="POST",
                                          data={"token": sent["reset"], "password": "brandnew123"})[0], 200)
            self.assertEqual(self.request("/api/auth/reset", method="POST",
                                          data={"token": sent["reset"], "password": "again12345"})[0], 400)
        self.assertEqual(self.request("/api/auth/login", method="POST",
                                      data={"username": "reset_user", "password": "password123"})[0], 401)
        self.assertEqual(self.request("/api/auth/login", method="POST",
                                      data={"username": "reset_user", "password": "brandnew123"})[0], 200)

    @patch.object(app, "SMTP_ENABLED", True)
    def test_forgot_password_answers_the_same_for_strangers_and_unverified_mail(self):
        # Ответ одинаковый, чтобы по форме нельзя было узнать, есть ли такая почта в сервисе
        with patch.object(app, "send_password_reset_email") as send, patch.dict(app.MAIL_REQUESTS, clear=True):
            self.request("/api/auth/register", method="POST", data={
                "username": "unverified_user", "password": "password123", "email": "unverified@example.com"})
            status_unknown, payload_unknown = self.request("/api/auth/forgot", method="POST",
                                                           data={"email": "nobody-here@example.com"})
            status_unverified, payload_unverified = self.request("/api/auth/forgot", method="POST",
                                                                 data={"email": "unverified@example.com"})
        self.assertEqual((status_unknown, status_unverified), (200, 200))
        self.assertEqual(payload_unknown, payload_unverified)
        send.assert_not_called()

    @patch.object(app, "SMTP_ENABLED", True)
    def test_resend_verification_waits_a_minute_between_mails(self):
        with patch.object(app, "send_verification_email") as send, patch.dict(app.MAIL_REQUESTS, clear=True):
            self.request("/api/auth/register", method="POST", data={
                "username": "resend_user", "password": "password123", "email": "resend-user@example.com"})
            self.request("/api/auth/resend-verification", method="POST", data={"email": "resend-user@example.com"})
            self.request("/api/auth/resend-verification", method="POST", data={"email": "resend-user@example.com"})
        self.assertEqual(send.call_count, 2)  # регистрация и одна повторная отправка, вторая попытка ждёт минуту

    @patch.object(app, "SMTP_ENABLED", True)
    def test_email_change_needs_the_password_and_a_confirmed_link(self):
        # A8: смена почты требует текущий пароль и работает только по ссылке из нового письма
        sent = {}
        with patch.object(app, "send_verification_email", side_effect=lambda e, t: sent.update(verify=t)):
            self.assertEqual(self.request("/api/auth/register", method="POST", data={
                "username": "change_user", "password": "password123", "email": "old-mail@example.com"})[0], 202)
        # без подтверждения сессии нет: смена почты доступна только после входа
        self.assertEqual(self.request("/api/auth/email", method="POST",
                                      data={"password": "password123", "email": "new-mail@example.com"})[0], 401)
        self.assertEqual(self.request("/api/auth/verify", method="POST", data={"token": sent["verify"]})[0], 200)
        with patch.object(app, "send_verification_email", side_effect=lambda e, t: sent.update(change=t)):
            self.assertEqual(self.request("/api/auth/email", method="POST",
                                          data={"password": "wrong-pass", "email": "new-mail@example.com"})[0], 401)
            self.assertEqual(self.request("/api/auth/email", method="POST",
                                          data={"password": "password123", "email": "not-an-address"})[0], 400)
            self.assertEqual(self.request("/api/auth/email", method="POST",
                                          data={"password": "password123", "email": "new-mail@example.com"})[0], 202)
        self.assertEqual(self.request("/api/auth/verify", method="POST", data={"token": sent["change"]})[0], 200)
        email = self.db.execute("SELECT email FROM accounts WHERE username='change_user'").fetchone()[0]
        self.assertEqual(email, "new-mail@example.com")

    def test_collection_and_subscription_limits_hold(self):
        # C13: 50 коллекций на профиль, 200 каналов в коллекции и 200 подписок в ленте
        profile = self.login("limits_user")
        for i in range(50):
            self.assertEqual(self.request("/api/collections/save", "POST",
                                          {"name": "Коллекция %d" % i, "channels": []})[0], 200)
        self.assertEqual(self.request("/api/collections/save", "POST", {"name": "Лишняя", "channels": []})[0], 400)
        names = ["chan%03d" % i for i in range(201)]
        self.assertEqual(self.request("/api/collections/save", "POST", {"name": "Длинная", "channels": names})[0], 400)
        for name in names[:200]:
            self.subscribe(profile, name)
        self.assertEqual(self.request("/api/state/subscription", "POST",
                                      {"channel": "chan200", "subscribed": True})[0], 409)
        self.assertEqual(self.request("/api/state/merge", "POST", {"subs": ["chan201"], "reads": []})[0], 409)

    def test_feed_search_keeps_only_matching_posts(self):
        # F7: поиск по ленте отдаёт только посты с запросом из выбранных каналов
        profile = self.login("search_user")
        self.subscribe(profile, "alpha")
        self.subscribe(profile, "beta")
        status, payload = self.request("/api/feed?channels=alpha,beta&q=post%207")
        self.assertEqual(status, 200)
        texts = [post["text"] for post in payload["posts"]]
        self.assertTrue(texts)
        self.assertTrue(all("post 7" in text for text in texts), texts)

    def test_catalog_is_for_signed_in_profiles_only(self):
        # Каталог и категории отдают список каналов, поэтому без входа их не получают
        self.assertEqual(self.request("/api/catalog")[0], 401)
        self.assertEqual(self.request("/api/categories")[0], 401)
        self.login("catalogcheck")
        with patch.object(app, "search_catalog", return_value=[]):
            self.assertEqual(self.request("/api/catalog")[0], 200)
        with patch.object(app, "catalog_categories", return_value=[]):
            self.assertEqual(self.request("/api/categories")[0], 200)

    def test_catalog_reports_whether_more_pages_follow(self):
        self.login("catalogpages")
        # Каталог листается страницами: полная страница значит, что дальше могут быть ещё каналы
        full = [{"username": f"chan{i}", "name": f"Chan {i}"} for i in range(app.CATALOG_PAGE)]
        with patch.object(app, "search_catalog", return_value=full) as search:
            status, payload = self.request("/api/catalog?offset=40")
        self.assertEqual(status, 200)
        self.assertTrue(payload["has_more"])
        self.assertEqual(search.call_args.kwargs["offset"], 40)
        with patch.object(app, "search_catalog", return_value=full[:3]):
            self.assertFalse(self.request("/api/catalog")[1]["has_more"])
        self.assertEqual(self.request("/api/catalog?offset=abc")[0], 400)

    def test_profile_names_accept_both_alphabets(self):
        self.cookie = ""
        self.assertEqual(self.register("Иван_2", "password123")[0], 201)
        self.cookie = ""
        self.assertEqual(self.register("a b", "password123")[0], 400)

    def test_unread_counts_only_the_last_two_weeks(self):
        # Старый архив догрузки не должен раздувать счётчик непрочитанного
        profile = self.login("unread_window")
        self.subscribe(profile)
        before = self.request("/api/feed?channels=alpha")[1]["unread"]
        now = int(time.time())
        self.db.executemany(app.POST_INSERT, [fake_post("alpha", 90001, now - 30 * 86400),
                                             fake_post("alpha", 90002, now)])
        self.db.commit()
        after = self.request("/api/feed?channels=alpha")[1]["unread"]
        self.assertEqual(after - before, 1)

    def test_read_posts_can_be_left_out_of_the_feed(self):
        # прочитанное без hide_read возвращается как раньше, а с hide_read страница
        # приходит без него целиком, а не полупустой после фильтрации на клиенте
        self.register("hide_read", "long-password")
        query = urlencode({"channels": "alpha"})
        status, full = self.request("/api/feed?" + query)
        keys = ["%s/%d" % (p["channel"], p["msg_id"]) for p in full["posts"]]
        self.request("/api/state/read", method="POST", data={"posts": keys[:2]})
        status, shown = self.request("/api/feed?" + query + "&hide_read=1")
        self.assertEqual(status, 200)
        self.assertEqual(["%s/%d" % (p["channel"], p["msg_id"]) for p in shown["posts"]], keys[2:])
        self.assertEqual(len(self.request("/api/feed?" + query)[1]["posts"]), len(keys))

    def test_private_media_goes_only_to_subscribers(self):
        profile = self.login("tg_media_fan")
        signed_in = self.cookie
        key = "private:-1001234567"
        path = "/api/tg/media?channel=%s&msg=5" % quote(key, safe="")
        with patch.object(app.tg, "media", return_value=(b"\x89PNG\r\n", "image/png")) as media:
            self.assertEqual(self.request_raw(path)[0], 404)  # канала нет в подписках
            self.subscribe(profile, key)
            status, headers, body = self.request_raw(path)
            self.assertEqual((status, body), (200, b"\x89PNG\r\n"))
            self.assertEqual(headers["Content-Type"], "image/png")
            self.assertEqual(headers["Cache-Control"], "private, max-age=86400")
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            media.assert_called_once_with(profile, key, 5)
            self.assertEqual(self.request_raw("/api/tg/media?channel=%s&msg=abc" % quote(key, safe=""))[0], 400)
            self.cookie = ""
            self.assertEqual(self.request_raw(path)[0], 401)
            self.cookie = signed_in
        with patch.object(app.tg, "media", side_effect=app.tg.MediaUnavailable("нет")):
            self.assertEqual(self.request_raw(path)[0], 404)
        with patch.object(app.tg, "media", side_effect=TimeoutError("timed out")):
            self.assertEqual(self.request_raw(path)[0], 502)

    def test_private_video_can_be_scrubbed_with_range_requests(self):
        profile = self.login("tg_range_fan")
        key = "private:-1001234567"
        self.subscribe(profile, key)
        url = "%s/api/tg/media?channel=%s&msg=5" % (self.base, quote(key, safe=""))

        def get(range_header=None):
            headers = {"Cookie": self.cookie}
            if range_header:
                headers["Range"] = range_header
            try:
                with urlopen(Request(url, headers=headers), timeout=5) as response:
                    return response.status, response.headers, response.read()
            except HTTPError as exc:
                return exc.code, exc.headers, exc.read()

        with patch.object(app.tg, "media", return_value=(b"0123456789", "video/mp4")):
            status, headers, body = get()
            self.assertEqual((status, body), (200, b"0123456789"))
            self.assertEqual(headers["Accept-Ranges"], "bytes")
            status, headers, body = get("bytes=2-4")
            self.assertEqual((status, body), (206, b"234"))
            self.assertEqual(headers["Content-Range"], "bytes 2-4/10")
            self.assertEqual(get("bytes=-3")[2], b"789")
            self.assertEqual(get("bytes=7-")[2], b"789")
            status, headers, _ = get("bytes=20-")
            self.assertEqual(status, 416)
            self.assertEqual(headers["Content-Range"], "bytes */10")

    def test_private_channels_in_the_feed_refresh_in_the_background(self):
        profile = self.login("tg_sync_fan")
        key = "private:-1001234567"
        self.subscribe(profile, key)
        self.db.execute("INSERT INTO tg_channels (profile, channel, entity_id, title, updated) "
                        "VALUES (?,?,?,?,NULL)", (profile, key, 1234567, "Закрытый"))
        self.db.commit()
        fresh = [{"channel": key, "msg_id": 3, "url": "https://t.me/c/1234567/3", "text": "новое",
                  "media": None, "views": None, "ts": 1700000000, "reactions": None, "preview": None,
                  "doc": None, "fwd": None, "quote": None, "sticker": None}]
        with patch.dict(app._private_retry, clear=True), \
                patch.object(app.tg, "linked", return_value=True), \
                patch.object(app.tg, "fetch", return_value=fresh) as fetch:
            self.assertTrue(app.private_sync_step(self.db))
            self.assertFalse(app.private_sync_step(self.db))  # уже свежий, второй раз не тянем
        fetch.assert_called_once_with(profile, key)
        stored = self.db.execute("SELECT text FROM private_posts WHERE profile=? AND channel=? AND msg_id=3",
                                 (profile, key)).fetchone()
        self.assertEqual(stored[0], "новое")

    def test_private_channel_backs_off_after_a_telegram_error(self):
        profile = self.login("tg_retry_fan")
        key = "private:-1001234567"
        self.subscribe(profile, key)
        self.db.execute("INSERT INTO tg_channels (profile, channel, entity_id, title, updated) "
                        "VALUES (?,?,?,?,NULL)", (profile, key, 1234567, "Закрытый"))
        self.db.commit()
        with patch.dict(app._private_retry, clear=True), \
                patch.object(app.tg, "linked", return_value=True), \
                patch.object(app.tg, "fetch", side_effect=RuntimeError("flood wait")) as fetch:
            self.assertTrue(app.private_sync_step(self.db))
            self.assertFalse(app.private_sync_step(self.db))  # ждём, не долбим Telegram
        fetch.assert_called_once()

    def test_telegram_is_not_fetched_on_behalf_of_a_stranger(self):
        # Сайт в интернете: без профиля нельзя ни проверить канал, ни заставить
        # сервер обойти t.me. Иначе посторонний проксирует обход через наш адрес,
        # и Telegram блокирует адрес всей ленты.
        self.cookie = ""
        with patch.object(app, "fetch_channel") as fetch:
            self.assertEqual(self.request("/api/check?c=durov")[0], 401)
            self.assertEqual(self.request("/api/feed?channels=alpha&refresh=1")[0], 200)
        # refresh=1 больше не форсирует обновление: кэш свежий, сети быть не должно
        fetch.assert_not_called()

    def test_channel_preview_pages_through_every_cached_post(self):
        # предпросмотр читает канал и обновляет его: с 19 октября только
        # с профилем, иначе сервис становился чужим прокси в t.me
        self.assertEqual(self.request("/api/preview?c=alpha")[0], 401)
        self.register("preview_user", "long-password")
        status, first = self.request("/api/preview?c=alpha")
        self.assertEqual(status, 200)
        self.assertEqual(first["title"], "Alpha")
        self.assertEqual(len(first["posts"]), 10)
        self.assertTrue(first["has_more"])
        seen = [(post["ts"], post["msg_id"]) for post in first["posts"]]
        cursor = first["next"]
        for _ in range(50):  # предел на случай сломанного курсора: тест не должен зависать
            status, page = self.request("/api/preview?" + urlencode(
                {"c": "alpha", "before_ts": cursor["ts"], "before_id": cursor["msg_id"]}))
            self.assertEqual(status, 200)
            seen += [(post["ts"], post["msg_id"]) for post in page["posts"]]
            if not page["has_more"]:
                break
            cursor = page["next"]
        total = self.db.execute("SELECT COUNT(*) FROM posts WHERE channel='alpha'").fetchone()[0]
        self.assertEqual(len(seen), total)
        self.assertEqual(len(set(seen)), total)
        self.assertEqual(seen, sorted(seen, key=lambda row: (-row[0], -row[1])))


    def test_login_checks_password_and_logout_expires_session(self):
        status, _ = self.register("profile_login", "long-password")
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
        status, _ = self.register("profile_one", "long-password")
        self.assertEqual(status, 201)
        status, _ = self.request("/api/state/subscription", method="POST",
                                 data={"channel": "alpha", "subscribed": True})
        self.assertEqual(status, 200)
        first_cookie = self.cookie
        self.cookie = ""
        status, _ = self.register("profile_two", "long-password")
        self.assertEqual(status, 201)
        status, second_state = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(second_state["subs"], [])
        self.cookie = first_cookie
        status, first_state = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(first_state["subs"], ["alpha"])

    def test_settings_survive_reload_and_reject_values_from_browser(self):
        status, _ = self.register("profile_settings", "long-password")
        self.assertEqual(status, 201)
        status, payload = self.request("/api/settings")
        self.assertEqual(payload["settings"], {"theme": "dark", "font": "mono"})
        status, _ = self.request("/api/settings", method="PUT",
                                 data={"theme": "light", "font": "serif"})
        self.assertEqual(status, 200)
        status, state = self.request("/api/state")
        self.assertEqual(state["settings"], {"theme": "light", "font": "serif"})
        status, payload = self.request("/api/settings", method="PUT", data={"theme": "neon"})
        self.assertEqual(status, 400)
        status, payload = self.request("/api/settings")
        self.assertEqual(payload["settings"], {"theme": "light", "font": "serif"})
        self.cookie = ""
        self.assertEqual(self.request("/api/settings")[0], 401)

    def test_channel_name_is_checked_before_it_reaches_the_address(self):
        # имя канала подставляется в https://t.me/s/<имя>, кривое значение ушло бы
        # на произвольный адрес, поэтому проверка стоит на входе, а не при запросе
        for bad in ("../../etc/passwd", "t.me/durov", "дуров", "a", "durov/1"):
            status, payload = self.request("/api/feed?" + urlencode({"channels": bad}))
            self.assertEqual(status, 400, bad)
            status, payload = self.request("/api/check?c=" + quote(bad))
            self.assertEqual(status, 400, bad)
        self.register("channel_guard", "long-password")
        status, payload = self.request("/api/state/subscription", method="POST",
                                       data={"channel": "../../etc/passwd", "subscribed": True})
        self.assertEqual(status, 400)

    def test_feed_returns_media_as_a_list_not_a_json_string(self):
        self.db.execute("UPDATE posts SET media=? WHERE channel='alpha' AND msg_id=1",
                        (json.dumps([{"k": "i", "u": "https://cdn/1.jpg"},
                                     {"k": "v", "u": "https://cdn/2.mp4"}]),))
        self.db.commit()
        status, payload = self.request("/api/feed?" + urlencode({"channels": "alpha"}))
        post = next(p for p in payload["posts"] if p["msg_id"] == 1)
        self.assertEqual(post["media"], [{"k": "i", "u": "https://cdn/1.jpg"},
                                         {"k": "v", "u": "https://cdn/2.mp4"}])

    def test_registration_without_email_is_refused(self):
        # почта обязательна всегда: без неё профиль не создаётся, и аккаунта в базе не остаётся
        status, payload = self.request("/api/auth/register", method="POST",
                                       data={"username": "no_email_one", "password": "long-password"})
        self.assertEqual(status, 400)
        self.assertIn("почта", payload["error"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 0)

    def test_feed_stops_at_two_hundred_channels(self):
        self.register("cap_profile", "long-password")
        for i in range(200):
            status, state = self.request("/api/state/subscription", method="POST",
                                         data={"channel": "cap%03d" % i, "subscribed": True})
            self.assertEqual(status, 200, i)
        status, payload = self.request("/api/state/subscription", method="POST",
                                       data={"channel": "cap200", "subscribed": True})
        self.assertEqual(status, 409)
        status, state = self.request("/api/state")
        self.assertEqual(len(state["subs"]), 200)

    def test_feed_reports_how_much_is_left_unread(self):
        # без числа фильтр «Непрочитанные» включают вслепую
        self.register("unread_count", "long-password")
        query = urlencode({"channels": "alpha"})
        status, payload = self.request("/api/feed?" + query)
        self.assertEqual(status, 200)
        self.assertEqual(payload["unread"], 43)
        self.request("/api/state/read", method="POST",
                     data={"posts": ["alpha/%d" % i for i in range(1, 20, 2)]})
        status, payload = self.request("/api/feed?" + query)
        self.assertEqual(payload["unread"], 33)
        # отметка чужого канала не уменьшает счётчик своей ленты
        self.request("/api/state/read", method="POST", data={"posts": ["beta/1", "beta/2"]})
        self.assertEqual(self.request("/api/feed?" + query)[1]["unread"], 33)
        # без сессии счётчик не считается
        self.cookie = ""
        self.assertEqual(self.request("/api/feed?" + query)[1]["unread"], 0)

    def test_read_mark_can_be_removed(self):
        # человек перечитал пост: он снова должен попасть в «Непрочитанные»
        self.register("unread_mark", "long-password")
        status, state = self.request("/api/state/read", method="POST",
                                     data={"posts": ["alpha/1", "alpha/3"]})
        self.assertEqual(status, 200)
        self.assertEqual(set(state["reads"]), {"alpha/1", "alpha/3"})
        status, state = self.request("/api/state/read", method="POST",
                                     data={"posts": ["alpha/3"], "unread": True})
        self.assertEqual(status, 200)
        self.assertEqual(state["reads"], ["alpha/1"])
        # мусорный ключ не должен ни удаляться, ни падать
        status, state = self.request("/api/state/read", method="POST",
                                     data={"posts": ["../etc/passwd"], "unread": True})
        self.assertEqual(status, 200)
        self.assertEqual(state["reads"], ["alpha/1"])
        # без флага поведение прежнее: отметка ставится
        status, state = self.request("/api/state/read", method="POST",
                                     data={"posts": ["alpha/3"]})
        self.assertIn("alpha/3", state["reads"])

    def test_login_lockout_does_not_follow_the_username_alone(self):
        # иначе любой, кто знает имя, закрывает чужой вход на 15 минут
        app.LOGIN_FAILURES.clear()
        for _ in range(8):
            app.record_login_failure("victim", "10.0.0.1")
        self.assertFalse(app.login_allowed("victim", "10.0.0.1"))
        self.assertTrue(app.login_allowed("victim", "10.0.0.2"))
        app.LOGIN_FAILURES.clear()

    def test_wrong_types_in_body_do_not_break_the_server(self):
        # из браузера приходит всё, включая списки вместо строк: сервер должен
        # отвечать кодом, а не рвать соединение
        self.register("types", "long-password")
        for payload in ({"channel": ["durov"], "subscribed": True},
                        {"channel": 5, "subscribed": True},
                        {"channel": {"a": 1}, "subscribed": "yes"}):
            status, _ = self.request("/api/state/subscription", method="POST", data=payload)
            self.assertEqual(status, 400, payload)
        for payload in ({"subs": 5}, {"reads": 5}, {"subs": [5], "reads": [7]}):
            status, _ = self.request("/api/state/merge", method="POST", data=payload)
            self.assertEqual(status, 200, payload)
        status, _ = self.request("/api/state/read", method="POST", data={"posts": [5]})
        self.assertEqual(status, 200)
        status, _ = self.request("/api/settings", method="PUT", data={"theme": ["light"]})
        self.assertEqual(status, 400)

    def test_partial_settings_update_keeps_the_other_field(self):
        self.register("settings_partial", "long-password")
        self.request("/api/settings", method="PUT", data={"theme": "light", "font": "mono"})
        status, payload = self.request("/api/settings", method="PUT", data={"theme": "dark"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["settings"], {"theme": "dark", "font": "mono"})

    def test_empty_feed_does_not_leak_the_whole_cache(self):
        # без подписок лента пустая: пустой список каналов не должен отдавать
        # весь кэш, где лежат посты чужих подписок
        status, payload = self.request("/api/feed?" + urlencode({"channels": ""}))
        self.assertEqual(status, 200)
        self.assertEqual(payload["posts"], [])
        self.assertEqual(payload["titles"], {})
        self.assertEqual(payload["unread"], 0)

    def test_feed_titles_cover_only_requested_channels(self):
        self.db.execute("INSERT INTO cache VALUES ('gamma','Gamma',1)")
        self.db.commit()
        status, payload = self.request("/api/feed?" + urlencode({"channels": "alpha"}))
        self.assertEqual(set(payload["titles"]), {"alpha"})

    def test_feed_titles_also_cover_the_channels_of_the_profile(self):
        # по заголовкам подписаны вкладки в шапке: при выборе одного канала
        # остальные не должны остаться сырыми именами
        self.register("titles", "long-password")
        for channel in ("alpha", "beta"):
            self.request("/api/state/subscription", method="POST",
                         data={"channel": channel, "subscribed": True})
        self.db.execute("INSERT INTO cache VALUES ('gamma','Gamma',1)")
        self.db.commit()
        status, payload = self.request("/api/feed?" + urlencode({"channels": "alpha"}))
        self.assertEqual(set(payload["titles"]), {"alpha", "beta"})

    def test_response_headers_do_not_advertise_the_runtime(self):
        status, headers, _ = self.request_raw("/api/feed?" + urlencode({"channels": "alpha"}))
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("Python", headers.get("Server", ""))

    def test_unsupported_methods_answer_json_not_html(self):
        for method in ("DELETE", "PATCH", "OPTIONS"):
            status, headers, body = self.request_raw("/api/state", method=method)
            self.assertNotEqual(status, 501, method)   # раньше приходил HTML-501
            self.assertIn("application/json", headers["Content-Type"], method)
            self.assertNotIn(b"<html", body.lower(), method)
        # с сессией и не-GET методом на маршрут без такого метода — 405, а не 501
        self.register("methods", "long-password")
        status, headers, _ = self.request_raw("/api/feed?channels=alpha", method="DELETE")
        self.assertEqual(status, 405)
        self.assertIn("application/json", headers["Content-Type"])

    def test_export_gives_the_whole_feed_as_lines_one_record_each(self):
        self.register("profile_one", "long-password")
        self.request("/api/state/merge", method="POST",
                     data={"subs": ["alpha", "beta"], "reads": ["alpha/1", "beta/2"]})
        self.cookie = ""
        self.assertEqual(self.request("/api/state/export")[0], 401)

        self.request("/api/auth/login", method="POST",
                     data={"username": "profile_one", "password": "long-password"})
        status, headers, body = self.request_raw("/api/state/export")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        records = [json.loads(line) for line in body.decode().splitlines() if line.strip()]
        self.assertEqual({r["channel"] for r in records if r["type"] == "channel"}, {"alpha", "beta"})
        self.assertEqual({r["post"] for r in records if r["type"] == "read"}, {"alpha/1", "beta/2"})

        # файл из выгрузки возвращается тем же слиянием, поэтому лента восстанавливается
        status, state = self.request("/api/state/merge", method="POST",
                                     data={"subs": [r["channel"] for r in records if r["type"] == "channel"],
                                           "reads": [r["post"] for r in records if r["type"] == "read"]})
        self.assertEqual(status, 200)
        self.assertEqual(set(state["subs"]), {"alpha", "beta"})
        self.assertEqual(set(state["reads"]), {"alpha/1", "beta/2"})

    def test_state_operations_merge_and_sync(self):
        status, account = self.register("profile_one", "long-password")
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


class FetchChannelTest(unittest.TestCase):
    """Пустая страница значит разное: без before это канал без постов, с before — конец истории."""

    PAGE = '<meta property="og:title" content="Канал"/>'.encode()

    def test_empty_page_behind_the_first_post_is_the_end_of_history(self):
        with patch.object(app.urllib.request, "urlopen") as net:
            net.return_value.read.return_value = self.PAGE
            self.assertEqual(app.fetch_channel("durov", before=1), ("Канал", []))

    def test_empty_first_page_is_still_an_error(self):
        with patch.object(app.urllib.request, "urlopen") as net:
            net.return_value.read.return_value = self.PAGE
            with self.assertRaises(ValueError):
                app.fetch_channel("durov")


class HistoryTextTest(unittest.TestCase):
    """Строки догрузки собирает браузер: берём функции из index.html и считаем их в node."""

    @unittest.skipUnless(shutil.which("node"), "нужен node")
    def test_channel_line_says_loading_only_once(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
        with open(path, encoding="utf-8") as page:
            html = page.read()
        functions = "\n".join(re.search(r"^function %s\(.*?^}" % name, html, re.S | re.M).group(0)
                              for name in ("historyDetail", "channelHistoryText"))
        cases = [
            ({"state": "loading", "fetched": 8}, "true", "Загружаем старые публикации канала: 8 из 500"),
            ({"state": "waiting", "fetched": 0}, "true", "Старые публикации канала: в очереди"),
            ({"state": "paused", "fetched": 40}, "true", "Старые публикации канала: t.me не отвечает, повторим позже"),
            ({"state": "done", "fetched": 13}, "false", "вся история загружена"),
        ]
        script = functions + "\n" + "\n".join(
            "console.log(channelHistoryText(%s, 500, %s));" % (json.dumps(ch), pending)
            for ch, pending, _ in cases)
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.splitlines(), [expected for _, _, expected in cases])


class CollectionFeedTest(unittest.TestCase):
    """Лента коллекции это лента с фильтром: каналы берём из коллекции и только из подписок."""

    @unittest.skipUnless(shutil.which("node"), "нужен node")
    def test_collection_feed_takes_only_its_subscribed_channels(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
        with open(path, encoding="utf-8") as page:
            html = page.read()
        lookup = re.search(r"^const collectionOf = .*$", html, re.M).group(0)
        feed = re.search(r"^function feedChannels\(\).*?^}", html, re.S | re.M).group(0)
        cases = [
            ("''", "[]", ["alpha", "beta"], ["alpha", "beta"]),
            ("'c:1'", "[{id: 1, name: 'Новости', channels: ['alpha', 'gamma']}]", ["alpha", "beta"], ["alpha"]),
            ("'c:2'", "[{id: 1, name: 'Новости', channels: ['alpha']}]", ["alpha", "beta"], []),
            ("'beta'", "[]", ["alpha", "beta"], ["beta"]),
        ]
        script = "var tab = '', collections = [], subsList = [];\nvar subs = () => subsList;\n" + lookup + "\n" + feed + "\n" + "\n".join(
            "tab = %s; collections = %s; subsList = %s; console.log(JSON.stringify(feedChannels()));"
            % (selected, collections, json.dumps(subscribed))
            for selected, collections, subscribed, _ in cases)
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
        self.assertEqual([json.loads(line) for line in out.splitlines()], [expected for *_, expected in cases])


class BackfillLoopTest(unittest.TestCase):
    """Пока догружать нечего, цикл ждёт недолго: новая подписка не должна ждать минуту."""

    def test_idle_loop_rechecks_soon(self):
        class Stop(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp, \
                patch.object(app, "DB", temp + "/feed.db"), \
                patch.object(app, "backfill_step", return_value=False), \
                patch.object(app, "time") as clock:
            clock.sleep.side_effect = Stop
            with self.assertRaises(Stop):
                app._backfill_loop()
        clock.sleep.assert_called_once_with(app.BACKFILL_IDLE)
        self.assertLessEqual(app.BACKFILL_IDLE, 5)


class MediaSweepTest(unittest.TestCase):
    """Фон освежает ссылки на медиа подписанных каналов: страницы за окно, не чаще интервала."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="media-sweep-test-")
        self.patches = [patch.object(app, "DB", self.temp.name + "/feed.db"),
                        patch.object(app, "MEDIA_PAUSE", 0)]
        for p in self.patches:
            p.start()
        app._media_swept.clear()
        self.db = app.connect()
        self.now = int(time.time())
        rows = [fake_post("alpha", msg, self.age_ts(msg)) for msg in range(1, 61)]
        for row in rows:
            row["media"] = json.dumps([{"k": "i", "u": "https://cdn.example/old-%d.jpg" % row["msg_id"]}])
        self.db.executemany(app.POST_INSERT, rows)
        self.db.execute("INSERT INTO user_subs (profile, channel) VALUES ('sweep_fan', 'alpha')")
        self.db.commit()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        app._media_swept.clear()
        self.db.close()
        self.temp.cleanup()

    def age_ts(self, msg):
        # три часа между постами: пост 60 свежий, пост 1 старше недели
        return self.now - (61 - msg) * 3 * 3600

    def page(self, channel, before=None):
        # страницы по двадцать постов, от свежих к старым, как отдаёт t.me
        ids = {None: range(60, 40, -1), 41: range(40, 20, -1), 21: range(20, 0, -1)}[before]
        posts = [fake_post(channel, msg, self.age_ts(msg)) for msg in ids]
        for post in posts:
            post["media"] = json.dumps([{"k": "i", "u": "https://cdn.example/new-%d.jpg" % post["msg_id"]}])
        return ("Alpha", posts)

    def media_url(self, msg):
        raw = self.db.execute("SELECT media FROM posts WHERE channel='alpha' AND msg_id=?", (msg,)).fetchone()[0]
        return json.loads(raw)[0]["u"]

    def test_sweep_walks_pages_only_until_the_window_ends(self):
        with patch.object(app, "fetch_channel", side_effect=self.page) as fetch:
            self.assertTrue(app.media_step(self.db))
        # первая страница и вторая, на которой окно уже кончается; третью не запрашиваем
        self.assertEqual(fetch.call_args_list, [call("alpha", before=None), call("alpha", before=41)])
        self.assertEqual(self.media_url(60), "https://cdn.example/new-60.jpg")
        self.assertEqual(self.media_url(25), "https://cdn.example/new-25.jpg")
        self.assertEqual(self.media_url(10), "https://cdn.example/old-10.jpg")

    def test_telegram_error_skips_the_channel_until_the_next_interval(self):
        with patch.object(app, "fetch_channel", side_effect=ValueError("t.me попросил подождать")) as fetch:
            self.assertTrue(app.media_step(self.db))
            self.assertFalse(app.media_step(self.db))
            app._media_swept["alpha"] = 0
            self.assertTrue(app.media_step(self.db))
        self.assertEqual(fetch.call_count, 2)


class CatalogCategoryTest(unittest.TestCase):
    def test_categories_keep_source_slugs_instead_of_building_them_from_labels(self):
        channels = [
            {"cat": "Art & Design", "cat_slug": "art"},
            {"cat": "Technology", "cat_slug": "tech"},
            {"cat": "Art & Design", "cat_slug": "art"},
        ]
        self.assertEqual(app.category_items(channels), [
            {"name": "Art & Design", "slug": "art"},
            {"name": "Technology", "slug": "tech"},
        ])

    def test_one_category_with_several_slugs_is_listed_once_with_the_common_slug(self):
        # TGMania отдаёт одну категорию под разными slug: в чипах она одна, slug берём самый частый
        channels = [{"cat": "Bots & Tools", "cat_slug": "bots"}] * 3 + [{"cat": "Bots & Tools", "cat_slug": "bots-tools"}]
        self.assertEqual(app.category_items(channels), [{"name": "Bots & Tools", "slug": "bots"}])

    def test_build_walks_every_page_of_the_catalog(self):
        # K10: категории берём со всех страниц выдачи, а не только с первой
        pages = {0: [{"cat": "Art & Design", "cat_slug": "art"}],
                 50: [{"cat": "Health & Fitness", "cat_slug": "health"}],
                 100: [{"cat": "Art & Design", "cat_slug": "art"}, {"cat": "Education", "cat_slug": "edu"}],
                 150: []}
        with patch.object(app, "search_catalog", side_effect=lambda limit, offset: pages.get(offset, [])) as search:
            names = [c["name"] for c in app.build_categories()]
        self.assertEqual(names, ["Art & Design", "Education", "Health & Fitness"])
        self.assertEqual(search.call_count, 4)

    def test_fresh_cache_answers_without_touching_telegram_catalog(self):
        cached = [{"name": "Music", "slug": "music"}]
        with patch.dict(app.CATEGORY_CACHE, {"items": cached, "built": time.time(), "busy": False}), \
                patch.object(app, "search_catalog") as search, patch.object(app.threading, "Thread") as thread:
            self.assertEqual(app.catalog_categories(), cached)
        search.assert_not_called()
        thread.assert_not_called()

    def test_stale_cache_is_served_while_a_new_pass_runs(self):
        cached = [{"name": "Music", "slug": "music"}]
        with patch.dict(app.CATEGORY_CACHE, {"items": cached, "built": 0.0, "busy": False}), \
                patch.object(app.threading, "Thread") as thread:
            self.assertEqual(app.catalog_categories(), cached)
            thread.assert_called_once()
            self.assertTrue(app.CATEGORY_CACHE["busy"])


class CatalogSearchTest(unittest.TestCase):
    """Сортировка «по постам» идёт по свежести из своей базы, значит она должна доезжать."""

    def test_last_post_time_passes_through_to_the_client(self):
        payload = json.dumps({"channels": [{"username": "durov", "name": "Pavel Durov"}]}).encode()
        with patch.object(app.urllib.request, "urlopen") as net:
            net.return_value.read.return_value = payload
            channels = app.search_catalog(last_posts={"durov": 1700000000})
        self.assertEqual(channels[0]["last_ts"], 1700000000)

    def test_channel_without_posts_gets_zero(self):
        payload = json.dumps({"channels": [{"username": "durov", "name": "Pavel Durov"}]}).encode()
        with patch.object(app.urllib.request, "urlopen") as net:
            net.return_value.read.return_value = payload
            channels = app.search_catalog(last_posts={})
        self.assertEqual(channels[0]["last_ts"], 0)

    def test_next_catalog_page_asks_telegram_for_the_offset(self):
        with patch.object(app.urllib.request, "urlopen") as net:
            net.return_value.read.return_value = json.dumps({"channels": []}).encode()
            app.search_catalog(offset=40)
        self.assertIn("offset=40", net.call_args[0][0].full_url)

    def test_last_post_times_reads_the_newest_post_per_channel(self):
        # База в памяти: app.connect() берёт путь из настроек, а в CI папки data нет.
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE posts (channel TEXT, msg_id INTEGER, ts INTEGER)")
        rows = [("fresh", 1, 1700000000), ("fresh", 2, 1700009999),
                ("other", 1, 1700005555)]
        db.executemany("INSERT INTO posts (channel,msg_id,ts) VALUES (?,?,?)", rows)
        self.assertEqual(app.last_post_times(db), {"fresh": 1700009999, "other": 1700005555})


class PrivateFetchTest(unittest.TestCase):
    """Посты приватного канала: текст экранируется, картинка и видео становятся ссылкой на эндпоинт."""

    class FakeClient:
        def __init__(self, messages):
            self.messages = messages

        async def connect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def get_entity(self, entity_id):
            return SimpleNamespace(id=entity_id)

        async def get_messages(self, entity, limit=None, ids=None):
            return self.messages

        async def disconnect(self):
            pass

    def test_photo_and_video_become_links_to_the_media_endpoint(self):
        when = datetime(2026, 10, 1, tzinfo=timezone.utc)
        messages = [
            SimpleNamespace(id=7, message="привет <b>", photo=SimpleNamespace(), video=None, views=None, date=when),
            SimpleNamespace(id=8, message="", photo=None, video=SimpleNamespace(), views=None, date=when),
            SimpleNamespace(id=9, message="", photo=None, video=None, views=None, date=when),
        ]
        key = "private:-1001234567"
        with patch.object(app.tg, "_client", return_value=self.FakeClient(messages)):
            rows = asyncio.run(app.tg._fetch("prof", key, 10))
        self.assertEqual([r["msg_id"] for r in rows], [7, 8])  # пустое сообщение без медиа пропускаем
        self.assertEqual(rows[0]["text"], "привет &lt;b&gt;")
        self.assertEqual(json.loads(rows[0]["media"]),
                         [{"k": "i", "u": "/api/tg/media?channel=private%3A-1001234567&msg=7"}])
        self.assertEqual(json.loads(rows[1]["media"])[0]["k"], "v")

    def test_album_becomes_one_post_with_all_its_media(self):
        when = datetime(2026, 10, 1, tzinfo=timezone.utc)
        messages = [
            SimpleNamespace(id=11, message="подпись", photo=SimpleNamespace(), video=None, views=None,
                            date=when, grouped_id=77),
            SimpleNamespace(id=10, message="", photo=SimpleNamespace(), video=None, views=None,
                            date=when, grouped_id=77),
            SimpleNamespace(id=9, message="отдельный пост", photo=None, video=None, views=None,
                            date=when, grouped_id=None),
        ]
        with patch.object(app.tg, "_client", return_value=self.FakeClient(messages)):
            rows = asyncio.run(app.tg._fetch("prof", "private:-1001234567", 10))
        self.assertEqual([r["msg_id"] for r in rows], [10, 9])  # номер альбома — его первое сообщение
        self.assertEqual(rows[0]["text"], "подпись")  # подпись может стоять у любого кадра
        self.assertEqual(json.loads(rows[0]["media"]), [
            {"k": "i", "u": "/api/tg/media?channel=private%3A-1001234567&msg=10"},
            {"k": "i", "u": "/api/tg/media?channel=private%3A-1001234567&msg=11"},
        ])

    def test_downloaded_media_is_cached_on_disk(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(app.tg, "TG_DIR", temp), \
                patch.object(app.tg, "_download", new=lambda *args: None), \
                patch.object(app.tg, "run", return_value=(b"jpegdata", "image/jpeg")) as run:
            first = app.tg.media("prof", "private:-1001234567", 7)
            second = app.tg.media("prof", "private:-1001234567", 7)
        self.assertEqual(first, (b"jpegdata", "image/jpeg"))
        self.assertEqual(second, (b"jpegdata", "image/jpeg"))
        self.assertEqual(run.call_count, 1)  # второй раз берём с диска


class MediaParseTest(unittest.TestCase):
    """Пост без медиа не должен получать эмодзи из текста вместо картинки."""

    def test_sticker_comes_from_its_webp_attribute(self):
        # background-image у стикера заглушка в base64, настоящая картинка в data-webp
        post = self.parse('<div class="tgme_widget_message_sticker_wrap"><i class="tgme_widget_message_sticker '
                          'js-sticker_image" style="background-image:url(\'data:image/svg+xml;base64,AAAA\')" '
                          'data-webp="https://cdn.example/s.webp?token=t&amp;x=1"></i></div>')
        self.assertEqual(post["sticker"], "https://cdn.example/s.webp?token=t&x=1")

    HTML = ('<div class="tgme_widget_message" data-post="durov/1">'
            '<div class="tgme_widget_message_text" dir="auto"><span class="emoji" '
            'style="background-image:url(\'//telegram.org/img/emoji/40/F09F8E81.png\')">✨</span>'
            ' привет</div>%s</div>')

    def parse(self, fragment):
        html = self.HTML % fragment
        with patch.object(app.urllib.request, "urlopen") as net:
            net.return_value.read.return_value = (
                '<meta property="og:title" content="Канал"/>' + html).encode()
            title, posts = app.fetch_channel("durov")
        self.assertEqual(title, "Канал")
        return posts[0]

    @staticmethod
    def media(post):
        return json.loads(post["media"] or "[]")

    def test_emoji_in_text_is_not_taken_for_a_photo(self):
        post = self.parse("")
        self.assertEqual(self.media(post), [])
        self.assertIn("привет", post["text"])

    def test_same_video_twice_in_one_post_is_one_item(self):
        video = '<video src="https://cdn4.telesco.pe/file/V.mp4?token=t" preload="none"></video>'
        media = self.media(self.parse(video + video))
        self.assertEqual(media, [{"k": "v", "u": "https://cdn4.telesco.pe/file/V.mp4?token=t"}])

    def test_video_keeps_poster_and_frame_ratio_from_player(self):
        fragment = ('<a class="tgme_widget_message_video_player blured" href="https://t.me/durov/1">'
                    '<i class="tgme_widget_message_video_thumb" style="background-image:url(\'https://cdn4.telesco.pe/file/P.jpg\')"></i>'
                    '<video src="https://cdn4.telesco.pe/file/V.mp4" muted></video>'
                    '<div class="tgme_widget_message_video_wrap" style="width:720px;padding-top:75%">'
                    '<video src="https://cdn4.telesco.pe/file/V.mp4"></video></div></a>')
        media = self.media(self.parse(fragment))
        self.assertEqual(media, [{"k": "v", "u": "https://cdn4.telesco.pe/file/V.mp4",
                                  "p": "https://cdn4.telesco.pe/file/P.jpg", "r": 0.75}])

    def test_photo_wins_over_emoji_and_keeps_extra_classes(self):
        media = self.media(self.parse('<div class="tgme_widget_message_photo_wrap 5215576584121683352 '
                           '1214346053_460000664" style="width:800px;'
                           'background-image:url(\'https://cdn4.telesco.pe/file/REAL.jpg\')">'))
        self.assertEqual(media, [{"k": "i", "u": "https://cdn4.telesco.pe/file/REAL.jpg"}])

    def test_video_without_src_falls_back_to_its_cover(self):
        media = self.media(self.parse('<div class="tgme_widget_message_video_wrap" '
                           'style="background-image:url(\'https://cdn4.telesco.pe/file/THUMB.jpg\')">'
                           '<div class="video_play"></div>'))
        self.assertEqual(media, [{"k": "i", "u": "https://cdn4.telesco.pe/file/THUMB.jpg"}])

    def test_album_keeps_every_item_in_order(self):
        media = self.media(self.parse(
            ''.join(f'<div class="tgme_widget_message_photo_wrap 111 222" '
                    f'style="background-image:url(\'https://cdn4.telesco.pe/file/{n}.jpg\')">'
                    for n in ("one", "two", "three"))))
        self.assertEqual([m["u"] for m in media], [
            "https://cdn4.telesco.pe/file/one.jpg",
            "https://cdn4.telesco.pe/file/two.jpg",
            "https://cdn4.telesco.pe/file/three.jpg"])

    def test_video_album_keeps_every_clip(self):
        media = self.media(self.parse(''.join(f'<div class="tgme_widget_message_video_wrap">'
                                   f'<video src="https://cdn4.telesco.pe/file/{n}.mp4"></video>'
                                   for n in ("a", "b", "c"))))
        self.assertEqual([m["k"] for m in media], ["v", "v", "v"])
        self.assertEqual(media[2]["u"], "https://cdn4.telesco.pe/file/c.mp4")


class PrivateChannelsTest(unittest.TestCase):
    """Приватные каналы приходят через вход в Telegram. Проверяем то, ради чего
    всё затевалось: пост одного профиля не должен попасть в ленту другого."""

    KEY = "private:-1001234567890"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="telegram-lenta-private-")
        self.previous_db = app.DB
        app.DB = self.temp.name + "/feed.db"
        self.db = app.connect()
        now = int(time.time())
        self.db.executemany("INSERT INTO cache VALUES (?,?,?)", [("alpha", "Alpha", now)])
        self.db.executemany("INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("alpha", 1, "https://t.me/alpha/1", "публичный пост", None, None,
             1_800_000_050, None, None, None, None, None, None)])
        self.db.commit()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        app.wait_refresh_idle()
        app._failed.clear()
        self.db.close()
        app.DB = self.previous_db
        self.temp.cleanup()

    def call(self, path, method="GET", data=None, cookie=""):
        headers = {}
        payload = None
        if data is not None:
            payload = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = cookie
        req = Request(self.base + path, data=payload, headers=headers, method=method)
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def sign_in(self, username):
        sent = {}
        with patch.object(app, "SMTP_ENABLED", True), \
                patch.object(app, "send_verification_email", side_effect=lambda e, t: sent.update(token=t)):
            status, _ = self.call("/api/auth/register", method="POST",
                                  data={"username": username, "password": "long-password",
                                        "email": username + "@example.com"})
        self.assertEqual(status, 202)
        status, _ = self.call("/api/auth/verify", method="POST", data={"token": sent["token"]})
        self.assertEqual(status, 200)
        status, _ = self.call("/api/auth/login", method="POST",
                              data={"username": username, "password": "long-password"})
        self.assertEqual(status, 200)

    def session_cookie(self, username):
        """Входит и отдаёт куку. Код профиля сервер выдаёт сам, поэтому он
        спрашивается из базы: заранее его не угадать, а приватные посты
        привязаны именно к нему."""
        self.sign_in(username)
        req = Request(self.base + "/api/auth/login", method="POST",
                      headers={"Content-Type": "application/json"},
                      data=json.dumps({"username": username,
                                       "password": "long-password"}).encode())
        with urlopen(req, timeout=5) as response:
            cookie = response.headers.get("Set-Cookie").split(";", 1)[0]
        profile = self.db.execute("SELECT profile FROM accounts WHERE username=?",
                                  (username,)).fetchone()[0]
        return cookie, profile

    def seed_private(self, profile):
        """Кладёт приватный канал и его пост от имени профиля."""
        now = int(time.time())
        self.db.execute("INSERT INTO private_posts VALUES (?,?,?,?,?,?,?,?)",
                        (profile, self.KEY, 7, "https://t.me/c/1234567890/7",
                         "секретный пост", None, None, 1_800_000_090))
        self.db.execute("INSERT OR REPLACE INTO tg_channels VALUES (?,?,?,?,?)",
                        (profile, self.KEY, -1001234567890, "Секретный канал", now))
        self.db.commit()

    def test_private_post_visible_only_to_the_profile_that_brought_it(self):
        cookie, profile = self.session_cookie("profil_a")
        self.seed_private(profile)
        status, payload = self.call("/api/feed?" + urlencode({"channels": self.KEY}),
                                    cookie=cookie)
        self.assertEqual(status, 200)
        texts = [p["text"] for p in payload["posts"]]
        self.assertIn("секретный пост", texts)

        other, _ = self.session_cookie("profil_b")
        status, payload = self.call("/api/feed?" + urlencode({"channels": self.KEY}),
                                    cookie=other)
        self.assertEqual(status, 200)
        self.assertEqual(payload["posts"], [])

    def test_public_and_private_posts_share_one_order(self):
        cookie, profile = self.session_cookie("profil_a")
        self.seed_private(profile)
        status, payload = self.call("/api/feed?" + urlencode({"channels": "alpha," + self.KEY}),
                                    cookie=cookie)
        self.assertEqual(status, 200)
        texts = [p["text"] for p in payload["posts"]]
        self.assertEqual(texts, ["секретный пост", "публичный пост"])
        # заголовок приватного канала приезжает из подписки профиля, не из кэша
        self.assertEqual(payload["titles"].get(self.KEY), "Секретный канал")

    def test_feed_rejects_broken_private_key(self):
        cookie, _ = self.session_cookie("profil_a")
        status, _ = self.call("/api/feed?" + urlencode({"channels": "private:-100abc"}),
                              cookie=cookie)
        self.assertEqual(status, 400)

    def test_profile_with_only_private_channels_does_not_see_public_cache(self):
        """Пустой список публичных каналов в ленте означает «ничего», а не
        «весь кэш»: так отдаёт выгрузка, и в ленте это стало бы утечкой."""
        cookie, profile = self.session_cookie("profil_a")
        self.seed_private(profile)
        status, payload = self.call("/api/feed?" + urlencode({"channels": self.KEY}),
                                    cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual([p["text"] for p in payload["posts"]], ["секретный пост"])

    def test_read_mark_of_private_post_is_accepted(self):
        self.assertTrue(app.valid_post_key(self.KEY + "/7"))
        self.assertFalse(app.valid_post_key("private:-100abc/7"))

    def test_public_subscription_rejects_private_key(self):
        cookie, _ = self.session_cookie("profil_a")
        status, _ = self.call("/api/state/subscription", method="POST",
                              data={"channel": self.KEY, "subscribed": True}, cookie=cookie)
        self.assertEqual(status, 400)

    def test_tg_endpoints_need_profile(self):
        for path in ("/api/tg/status", "/api/tg/channels"):
            status, _ = self.call(path)
            self.assertEqual(status, 401, path)

    def test_link_start_reports_missing_telethon_instead_of_falling_back(self):
        cookie, _ = self.session_cookie("profil_a")
        with patch.object(app.tg, "installed", return_value=False):
            status, payload = self.call("/api/tg/link/start", method="POST", data={},
                                        cookie=cookie)
        self.assertEqual(status, 503)
        self.assertIn("Telethon", payload["error"])

    def test_status_reflects_linked_account(self):
        cookie, profile = self.session_cookie("profil_a")
        status, payload = self.call("/api/tg/status", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertFalse(payload["linked"])
        self.db.execute("INSERT OR REPLACE INTO tg_accounts VALUES (?,?,?,1)",
                        (profile, "+79990000000", "Я"))
        self.db.commit()
        status, payload = self.call("/api/tg/status", cookie=cookie)
        self.assertTrue(payload["linked"])
        self.assertEqual(payload["phone"], "+79990000000")

    def test_unlink_wipes_profile_private_data(self):
        cookie, profile = self.session_cookie("profil_a")
        self.seed_private(profile)
        with patch.object(app.tg, "drop_session", return_value=True):
            status, _ = self.call("/api/tg/unlink", method="POST", data={}, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM private_posts").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM tg_channels").fetchone()[0], 0)

    def test_channels_list_only_for_own_profile(self):
        cookie, profile = self.session_cookie("profil_a")
        self.seed_private(profile)
        status, payload = self.call("/api/tg/channels", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual([c["channel"] for c in payload["channels"]], [self.KEY])

        other, _ = self.session_cookie("profil_b")
        status, payload = self.call("/api/tg/channels", cookie=other)
        self.assertEqual(status, 200)
        self.assertEqual(payload["channels"], [])


def luminance(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a, b):
    light, dark = sorted((luminance(a), luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


class ThemeContrastTest(unittest.TestCase):
    """Пары текста и фона в обеих темах держат 4.5:1, как записано в README."""

    TEXT_ON_BG = [
        ("fg", "bg"), ("fg", "surf"), ("fg", "raise"),
        ("dim", "bg"), ("dim", "surf"), ("dim", "raise"),
        ("faint", "bg"), ("faint", "surf"), ("faint", "raise"),
        ("acc", "bg"), ("acc", "surf"), ("acc", "raise"),
        ("error", "bg"), ("error", "surf"), ("paid", "raise"),
        ("on-acc-cont", "acc-cont"), ("on-sec-cont", "sec-cont"), ("acc-ink", "acc"),
        ("inv-fg", "inv-surf"), ("inv-acc", "inv-surf"),
    ]

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(app.BASE, "index.html"), encoding="utf-8") as page:
            html = page.read()
        dark = re.search(r":root\{(.*?)\}", html, re.S).group(1)
        light = re.search(r':root\[data-theme="light"\]\{(.*?)\}', html, re.S).group(1)
        cls.themes = {
            "dark": cls.tokens(dark),
            "light": {**cls.tokens(dark), **cls.tokens(light)},
        }

    @staticmethod
    def tokens(block):
        return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", block))

    def test_text_pairs_reach_4_5_in_both_themes(self):
        for theme, tokens in self.themes.items():
            for text, bg in self.TEXT_ON_BG:
                with self.subTest(theme=theme, text=text, bg=bg):
                    self.assertGreaterEqual(contrast(tokens[text], tokens[bg]), 4.5)


class SpacingScaleTest(unittest.TestCase):
    """Отступы берутся из шкалы --sp-N: литерал вне шкалы сбивает ритм, и тест его ловит."""

    # 2 px остаются для волосяных зазоров, 0 и отрицательные значения задают оптические сдвиги
    HAIRLINE = {0, 2}
    SPACING = re.compile(r"(?<![\w-])(?:padding|margin|gap)(?:-(?:top|right|bottom|left))?:([^;{}]*)")

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(app.BASE, "index.html"), encoding="utf-8") as page:
            cls.html = page.read()

    def test_literal_spacing_stays_on_the_scale(self):
        style = self.html[self.html.index("<style>"):self.html.index("</style>")]
        literals = sorted({int(px) for value in self.SPACING.findall(style)
                           for px in re.findall(r"(?<![\w.-])(\d+)px", value)} - self.HAIRLINE)
        self.assertEqual(literals, [])

    def test_every_spacing_token_is_declared(self):
        root = self.html[self.html.index(":root{"):self.html.index("}", self.html.index(":root{"))]
        declared = set(re.findall(r"--sp-(\d+):", root))
        used = set(re.findall(r"var\(--sp-(\d+)\)", self.html))
        self.assertTrue(used)
        self.assertEqual(used - declared, set())


class FontFileTest(unittest.TestCase):
    """Шрифт отдаётся с нашего адреса: сеть ему не нужна, и чужие файлы по этому пути не открываются."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="telegram-lenta-test-")
        self.previous_db = app.DB
        app.DB = self.temp.name + "/feed.db"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        app.DB = self.previous_db
        self.temp.cleanup()

    def fetch(self, path, headers=None):
        req = Request(self.base + path, headers=headers or {})
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, response.headers, response.read()
        except HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def test_font_comes_from_our_own_address_as_woff2(self):
        status, headers, body = self.fetch("/fonts/jetbrains-mono-latin.woff2")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "font/woff2")
        self.assertEqual(body[:4], b"wOF2")

    def test_font_is_not_gzipped_even_when_the_browser_accepts_it(self):
        # woff2 уже сжат: второй слой gzip тратит процессор и ничего не даёт
        status, headers, body = self.fetch("/fonts/jetbrains-mono-latin.woff2", {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertIsNone(headers.get("Content-Encoding"))
        self.assertEqual(body[:4], b"wOF2")

    def test_only_listed_font_names_are_served(self):
        self.assertEqual(self.fetch("/fonts/LICENSE-JetBrainsMono.txt")[0], 404)
        self.assertEqual(self.fetch("/fonts/../app.py")[0], 404)
        self.assertEqual(self.fetch("/fonts/%2E%2E%2Fapp.py")[0], 404)


class QrSvgTest(unittest.TestCase):
    """QR-код входа: телефон сканирует с экрана компьютера, поэтому каждая тёмная клетка должна быть нарисована."""

    def test_every_dark_module_of_the_link_is_drawn(self):
        url = "tg://login?token=" + "AbC_-9" * 6
        code = qrcodegen.QrCode.encode_text(url, qrcodegen.QrCode.Ecc.MEDIUM)
        n = code.get_size()
        dark = sum(code.get_module(x, y) for y in range(n) for x in range(n))
        drawn = qrsvg.svg(url)
        self.assertEqual(drawn.count("h1v1h-1z"), dark)
        # цвета задаются классами из index.html, сами по себе в SVG их нет
        self.assertNotIn("#", drawn)


class QrLinkResponseTest(unittest.TestCase):
    """Вход в Telegram отдаёт QR вместе со ссылкой, пока вход ждёт подтверждения."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="telegram-lenta-test-")
        self.previous_db = app.DB
        app.DB = self.temp.name + "/feed.db"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        patcher = patch.object(app.tg, "installed", lambda: True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(app.tg._pending.clear)
        self.cookie = register_with_mail(self.base, "qrlink")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        app.DB = self.previous_db
        self.temp.cleanup()

    def call(self, path, data):
        req = Request(self.base + path, method="POST", data=json.dumps(data).encode(),
                      headers={"Content-Type": "application/json", "Cookie": self.cookie})
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_start_answers_with_a_qr_of_the_link(self):
        url = "tg://login?token=QrStart123"
        with patch.object(app.tg, "start_link", return_value={"state": "waiting", "token": "t1", "url": url}):
            status, body = self.call("/api/tg/link/start", {})
        self.assertEqual(status, 200)
        self.assertEqual(body["url"], url)
        self.assertEqual(body["qr"], qrsvg.svg(url))

    def test_poll_refreshes_the_qr_while_waiting(self):
        url = "tg://login?token=QrPoll456"
        with patch.object(app.tg, "poll_link", return_value={"state": "waiting", "url": url}):
            status, body = self.call("/api/tg/link/poll", {"token": "t1"})
        self.assertEqual(status, 200)
        self.assertEqual(body["qr"], qrsvg.svg(url))

    def test_finished_login_has_no_qr_left_to_scan(self):
        account = {"phone": "+70000000000", "title": "Тест"}
        with patch.object(app.tg, "poll_link", return_value={"state": "done", "account": account}):
            status, body = self.call("/api/tg/link/poll", {"token": "t1"})
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "done")
        self.assertNotIn("qr", body)


class AvatarTest(unittest.TestCase):
    """Фото канала берём с t.me, но в src уйдёт только картинка Telegram."""

    def test_takes_the_channel_photo_from_og_image(self):
        html = '<meta property="og:image" content="https://cdn4.telesco.pe/file/abc_-1.jpg">'
        self.assertEqual(app.avatar_url(html), "https://cdn4.telesco.pe/file/abc_-1.jpg")

    def test_ignores_other_hosts_quotes_and_a_missing_photo(self):
        self.assertEqual(app.avatar_url('<meta property="og:image" content="https://telegram.org/img/t_logo.png">'), "")
        self.assertEqual(app.avatar_url('<meta property="og:image" content="https://cdn4.telesco.pe/a&quot;b">'), "")
        self.assertEqual(app.avatar_url("<title>без фото</title>"), "")


class AvatarEndpointTest(unittest.TestCase):
    """Фото догружается пачками, один раз на канал, и отдаётся только вошедшим."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="telegram-lenta-test-")
        self.previous_db = app.DB
        app.DB = self.temp.name + "/feed.db"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.cookie = register_with_mail(self.base, "avatars")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        app.DB = self.previous_db
        self.temp.cleanup()

    def get(self, path, cookie=None):
        req = Request(self.base + path, headers={"Cookie": self.cookie if cookie is None else cookie})
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read())

    def test_photos_are_fetched_once_then_read_from_the_table(self):
        seen = []

        def fake(channel):
            seen.append(channel)
            return f"https://cdn4.telesco.pe/file/{channel}.jpg"

        with patch.object(app, "fetch_avatar", side_effect=fake):
            status, body = self.get("/api/avatars?channels=alpha,beta")
            self.assertEqual(status, 200)
            self.assertEqual(body["avatars"], {"alpha": "https://cdn4.telesco.pe/file/alpha.jpg",
                                               "beta": "https://cdn4.telesco.pe/file/beta.jpg"})
            self.assertEqual(body["checked"], ["alpha", "beta"])
            self.get("/api/avatars?channels=alpha,beta")
        self.assertEqual(sorted(seen), ["alpha", "beta"])  # второй раз t.me не спрашиваем

    def test_private_channels_have_no_public_page_to_ask(self):
        with patch.object(app, "fetch_avatar") as fetch:
            status, body = self.get("/api/avatars?channels=private:-1001234567890")
        self.assertEqual(status, 200)
        fetch.assert_not_called()
        self.assertEqual(body["avatars"], {})

    def test_photos_need_a_profile(self):
        status, _ = self.get("/api/avatars?channels=alpha", cookie="")
        self.assertEqual(status, 401)


class ShellNesting(HTMLParser):
    """Запоминает, в каких элементах с id стоит каждый элемент с id."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self):
        super().__init__()
        self.open = []
        self.parents = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.parents[attrs["id"]] = [i for _, i in self.open if i]
        if tag not in self.VOID:
            self.open.append((tag, attrs.get("id")))

    def handle_endtag(self, tag):
        while self.open:
            name, _ = self.open.pop()
            if name == tag:
                break


class GuestNavigationTest(unittest.TestCase):
    """Гость видит экран входа, а не нижнюю панель: она должна жить внутри оболочки приложения."""

    def test_tab_bar_lives_inside_app_shell(self):
        nesting = ShellNesting()
        with open(os.path.join(app.BASE, "index.html"), encoding="utf-8") as page:
            nesting.feed(page.read())
        self.assertIn("appShell", nesting.parents["tabBar"])
        self.assertNotIn("appShell", nesting.parents["authScreen"])


class FreshMediaPlaceholderTest(unittest.TestCase):
    """Кадр с протухшей ссылкой ждёт свежую пустым: битой картинки с подписью быть не должно."""

    def test_broken_frame_is_emptied_before_fresh_links_are_asked(self):
        with open(os.path.join(app.BASE, "index.html"), encoding="utf-8") as page:
            text = page.read()
        start = text.index("document.addEventListener('error'")
        handler = text[start:text.index("}, true);", start)]
        self.assertIn("removeAttribute('src')", handler)
        self.assertLess(handler.index("removeAttribute('src')"), handler.index("freshMedia(post.dataset.post)"))


class TapFeedbackTest(unittest.TestCase):
    """Нажатие не рисует синюю подсветку браузера, а клавиатурный фокус остаётся видимым."""

    def test_tap_highlight_is_off_and_keyboard_focus_stays(self):
        with open(os.path.join(app.BASE, "index.html"), encoding="utf-8") as page:
            html = page.read()
        self.assertIn("-webkit-tap-highlight-color:transparent", html)
        self.assertIn(":is(button,a,[role=button]):active{opacity:.72}", html)
        self.assertIn(":focus-visible{outline:2px solid", html)


class DatabaseModeTest(unittest.TestCase):
    """База открывается в WAL: лента читает, пока фон пишет кэш."""

    def test_connect_switches_the_database_to_wal(self):
        with tempfile.TemporaryDirectory(prefix="wal-test-") as folder:
            previous = app.DB
            app.DB = folder + "/feed.db"
            try:
                db = app.connect()
                mode = db.execute("PRAGMA journal_mode").fetchone()[0]
                db.close()
            finally:
                app.DB = previous
        self.assertEqual(mode, "wal")

    def test_connect_sets_lock_wait_so_writers_dont_get_locked_out(self):
        """Без busy_timeout фоновый писатель кэша рвёт соединение читателю."""
        with tempfile.TemporaryDirectory(prefix="busy-test-") as folder:
            previous = app.DB
            app.DB = folder + "/feed.db"
            try:
                db = app.connect()
                timeout = db.execute("PRAGMA busy_timeout").fetchone()[0]
                db.close()
            finally:
                app.DB = previous
        self.assertGreaterEqual(timeout, 5000)

    def test_old_schema_gains_new_columns_without_losing_data(self):
        """Миграция на базе из-под прежней версии: колонки добавляются, данные целы."""
        with tempfile.TemporaryDirectory(prefix="migrate-test-") as folder:
            previous = app.DB
            app.DB = folder + "/feed.db"
            try:
                legacy = sqlite3.connect(app.DB)
                legacy.executescript(
                    "CREATE TABLE posts (channel TEXT, msg_id INTEGER, url TEXT,"
                    " text TEXT, image TEXT, video TEXT, views TEXT, ts INTEGER,"
                    " PRIMARY KEY (channel, msg_id));"
                    "CREATE TABLE profiles (code TEXT PRIMARY KEY, name TEXT);"
                    "CREATE TABLE accounts (username TEXT PRIMARY KEY COLLATE NOCASE,"
                    " profile TEXT, salt TEXT, digest TEXT);")
                legacy.execute("INSERT INTO posts VALUES ('alpha', 7, 'u', 'текст',"
                               " 'img', NULL, NULL, 1800000000)")
                legacy.execute("INSERT INTO profiles VALUES ('code-1', 'Друг')")
                legacy.execute("INSERT INTO accounts VALUES ('friend', 'code-1', 's', 'd')")
                legacy.commit()
                legacy.close()

                db = app.connect()
                columns = {row[1] for row in db.execute("PRAGMA table_info(accounts)")}
                self.assertIn("email", columns)
                post_columns = {row[1] for row in db.execute("PRAGMA table_info(posts)")}
                self.assertIn("media", post_columns)
                self.assertEqual(db.execute("SELECT text FROM posts").fetchone()[0], "текст")
                self.assertEqual(db.execute("SELECT name FROM profiles").fetchone()[0], "Друг")
                db.close()
            finally:
                app.DB = previous


class CursorIntTest(unittest.TestCase):
    """Позиция списка приходит строкой: число вне 64 бит рвало соединение."""

    def test_rejects_values_outside_the_sqlite_range(self):
        self.assertEqual(app.cursor_int("1800000000"), 1800000000)
        self.assertRaises(ValueError, app.cursor_int, "99999999999999999999999")
        self.assertRaises(ValueError, app.cursor_int, "-1")

    def test_oversized_cursor_gives_an_answer_instead_of_a_broken_connection(self):
        with feed_server() as server:
            status, payload = server.request("/api/feed?channels=alpha"
                                            "&before_ts=99999999999999999999999"
                                            "&before_channel=alpha&before_id=1")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "неверная позиция ленты")


class PreviewAuthTest(unittest.TestCase):
    """/api/preview без профиля заставлял сервер ходить в Telegram для любого канала."""

    def test_anonymous_preview_is_refused(self):
        with feed_server() as server:
            status, payload = server.request("/api/preview?c=durov")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "нужно войти в профиль")

    def test_healthcheck_answers_without_a_profile(self):
        with feed_server() as server:
            status, payload = server.request("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])


class ResponseCompressionTest(unittest.TestCase):
    """Раздаваемый файл весит 167 КБ и раньше уходил без сжатия."""

    def test_html_is_gzipped_when_the_client_accepts_it(self):
        with feed_server() as server:
            req = Request(server.base + "/", headers={"Accept-Encoding": "gzip, br"})
            with urlopen(req, timeout=5) as response:
                encoding = response.headers.get("Content-Encoding")
                body = response.read()
        self.assertEqual(encoding, "gzip")
        self.assertIn(b"<!doctype html>", gzip.decompress(body).lower())

    def test_plain_client_still_gets_usable_html(self):
        with feed_server() as server:
            status, headers, body = server.request_raw("/")
        self.assertEqual(status, 200)
        self.assertIsNone(headers.get("Content-Encoding"))
        self.assertIn(b"<!doctype html>", body.lower())


class StartLinkProfileTest(unittest.TestCase):
    """Второе устройство того же человека не должно запускать второй вход."""

    def setUp(self):
        # installed() ложно вне контейнера: без этой подмены start_link
        # выходит с TelegramMissing раньше, чем до проверки профиля.
        patcher = patch.object(app.tg, "installed", lambda: True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(app.tg._pending.clear)

    def test_second_start_reuses_the_pending_login_of_the_same_profile(self):
        qr = types.SimpleNamespace(url="tg://login?token=0")
        app.tg._pending["tok"] = {"profile": "code-1", "client": None, "qr": qr,
                                  "started": time.time(), "task": None}
        state = app.tg.start_link("code-1")
        self.assertEqual(state["token"], "tok")
        self.assertEqual(state["url"], qr.url)
        self.assertEqual(len(app.tg._pending), 1)

    def test_other_profile_starts_its_own_login(self):
        qr = types.SimpleNamespace(url="tg://login?token=9")
        # run() уводит корутину в отдельный поток с настоящим Telethon,
        # поэтому подменяется он: проверяем только решение по профилю.
        with patch.object(app.tg, "run", lambda coro, timeout=None: (coro.close(),
                                                                     {"state": "waiting",
                                                                      "client": None,
                                                                      "qr": qr})[1]):
            state = app.tg.start_link("code-2")
        self.assertNotEqual(state["token"], "tok")
        self.assertEqual(state["url"], qr.url)
        self.assertEqual(len(app.tg._pending), 1)


class TgLinkRenewTest(unittest.TestCase):
    """Ссылка входа перевыпускается, пока её не открыли, и подтверждение не теряется."""

    class FakeQR:
        def __init__(self, accept_after=1):
            self.token = 0
            self.recreated = 0
            self.waits = 0
            self.accept_after = accept_after
            self.expires = datetime.now(timezone.utc) + timedelta(seconds=30)

        @property
        def url(self):
            return "tg://login?token=%d" % self.token

        async def recreate(self):
            self.recreated += 1
            self.token += 1

        async def wait(self, timeout=None):
            self.waits += 1
            if self.waits <= self.accept_after:
                raise asyncio.TimeoutError
            return SimpleNamespace(first_name="Ира", last_name="", username="ira", phone="")

    def test_watch_recreates_expired_link_and_keeps_the_account(self):
        qr = self.FakeQR(accept_after=1)
        entry = {"profile": "p", "client": None, "qr": qr, "started": time.time()}
        with patch.object(app.tg, "_tighten"):
            app.tg.run(app.tg._watch(entry))
        self.assertEqual(qr.recreated, 1)
        self.assertEqual(entry["account"]["title"], "Ира")
        self.assertFalse(entry.get("failed"))

    def test_poll_returns_the_current_url_then_done_or_expired(self):
        qr = self.FakeQR()
        app.tg._pending["live"] = {"profile": "p", "client": None, "qr": qr,
                                   "started": time.time(), "task": None}
        self.assertEqual(app.tg.poll_link("live"), {"state": "waiting", "url": "tg://login?token=0"})
        qr.token = 1
        self.assertEqual(app.tg.poll_link("live")["url"], "tg://login?token=1")
        app.tg._pending["live"]["account"] = {"title": "Ира", "phone": ""}
        self.assertEqual(app.tg.poll_link("live")["state"], "done")
        app.tg._pending["old"] = {"profile": "p", "client": None, "qr": qr,
                                  "started": time.time() - 700, "task": None}
        self.assertEqual(app.tg.poll_link("old"), {"state": "expired"})


if __name__ == "__main__":
    unittest.main()
