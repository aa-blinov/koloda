"""Регрессионные проверки личной ленты без обращения к Telegram."""
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
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
        self.assertEqual([error["channel"] for error in payload["errors"]], ["alpha"])

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

    def test_settings_survive_reload_and_reject_values_from_browser(self):
        status, _ = self.request("/api/auth/register", method="POST",
                                 data={"username": "profile_settings", "password": "long-password"})
        self.assertEqual(status, 201)
        status, payload = self.request("/api/settings")
        self.assertEqual(payload["settings"], {"theme": "dark", "font": "sans"})
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
        self.request("/api/auth/register", method="POST",
                     data={"username": "channel_guard", "password": "long-password"})
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

    def test_two_profiles_without_email_do_not_collide(self):
        # accounts_email уникален для непустых значений, поэтому почта пишется как NULL
        for name in ("no_email_one", "no_email_two"):
            status, _ = self.request("/api/auth/register", method="POST",
                                     data={"username": name, "password": "long-password"})
            self.assertEqual(status, 201, name)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM accounts WHERE email IS NULL").fetchone()[0], 2)

    def test_feed_stops_at_two_hundred_channels(self):
        self.request("/api/auth/register", method="POST",
                     data={"username": "cap_profile", "password": "long-password"})
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
        self.request("/api/auth/register", method="POST",
                     data={"username": "unread_count", "password": "long-password"})
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
        self.request("/api/auth/register", method="POST",
                     data={"username": "unread_mark", "password": "long-password"})
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
        self.request("/api/auth/register", method="POST",
                     data={"username": "types", "password": "long-password"})
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
        self.request("/api/auth/register", method="POST",
                     data={"username": "settings_partial", "password": "long-password"})
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
        self.request("/api/auth/register", method="POST",
                     data={"username": "titles", "password": "long-password"})
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
        self.request("/api/auth/register", method="POST",
                     data={"username": "methods", "password": "long-password"})
        status, headers, _ = self.request_raw("/api/feed?channels=alpha", method="DELETE")
        self.assertEqual(status, 405)
        self.assertIn("application/json", headers["Content-Type"])

    def test_export_gives_the_whole_feed_as_lines_one_record_each(self):
        self.request("/api/auth/register", method="POST",
                     data={"username": "profile_one", "password": "long-password"})
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

    def test_last_post_times_reads_the_newest_post_per_channel(self):
        # База в памяти: app.connect() берёт путь из настроек, а в CI папки data нет.
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE posts (channel TEXT, msg_id INTEGER, ts INTEGER)")
        rows = [("fresh", 1, 1700000000), ("fresh", 2, 1700009999),
                ("other", 1, 1700005555)]
        db.executemany("INSERT INTO posts (channel,msg_id,ts) VALUES (?,?,?)", rows)
        self.assertEqual(app.last_post_times(db), {"fresh": 1700009999, "other": 1700005555})


class MediaParseTest(unittest.TestCase):
    """Пост без медиа не должен получать эмодзи из текста вместо картинки."""

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
        status, _ = self.call("/api/auth/register", method="POST",
                              data={"username": username, "password": "long-password"})
        self.assertEqual(status, 201)
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


if __name__ == "__main__":
    unittest.main()
