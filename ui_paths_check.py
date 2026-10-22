#!/usr/bin/env python3
"""Проверка пользовательских путей в браузере по docs/user-paths.md.

Запуск на стенде, где сервис уже поднят:
    python3 ui_paths_check.py http://127.0.0.1:8090 --data-dir /path/to/data
    python3 ui_paths_check.py http://127.0.0.1:8090 --data-dir /path/to/data --only C1,C2,F2 --device phone

Нужны Playwright для Python и установленный Chrome (канал chrome). Нужен интернет: посты
подтягиваются с t.me. Нужен --data-dir: каталог данных стенда (где лежит feed.db). Почта на стенде
не нужна: подтверждённый профиль раннер заводит прямо в базе, а вход и всё остальное идёт по HTTP.
Каждая проверка создаёт свой профиль и не трогает чужие.
Путь, которому нужны посты, которых нет, или который не на этом устройстве, помечается SKIP
с причиной, а не проходит молча.

Скрипт служебный: не входит в образ и не гоняется в unittest.
"""
import argparse
import json
import os
import random
import string
import sys
import time
import urllib.error
import urllib.request

BASE = ""
PASSWORD = "long-password-1"
CHANNELS = ("rybolos_channel", "evilfreelancer", "rhymestg")
COLL_CHANNELS = {"rybolos_channel", "evilfreelancer"}
CHECKS = []


class Skip(Exception):
    """Проверке нечего проверить на этом стенде: пост, устройство или данные отсутствуют."""


def check(pid, title, device="desk"):
    def wrap(fn):
        CHECKS.append((pid, title, device, fn))
        return fn
    return wrap


def api(path, body=None, cookie=""):
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, method="POST" if body is not None else "GET",
                                 data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, response.headers.get("Set-Cookie", ""), json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, "", {}


DATA_DIR = ""


def seed_user(name):
    """Подтверждённый профиль прямо в базе стенда. Регистрация требует почту, а письмо на стенде
    не уйдёт, поэтому профиль заводим так же, как это делает регистрация, но сразу с подтверждённой почтой."""
    import secrets
    import time
    os.environ["DATA_DIR"] = DATA_DIR
    import app
    db = app.connect()
    try:
        profile = secrets.token_urlsafe(24)
        salt = secrets.token_hex(16)
        db.execute("INSERT INTO profiles (code,created,initialized) VALUES (?,?,1)", (profile, int(time.time())))
        db.execute("INSERT INTO accounts (username,profile,salt,digest,created,email,email_verified) "
                   "VALUES (?,?,?,?,?,?,1)",
                   (name, profile, salt, app.password_digest(PASSWORD, salt), int(time.time()), name + "@stand.test"))
        db.commit()
    finally:
        db.close()


def new_user(prefix):
    """Новый подтверждённый профиль с тремя подписками."""
    if not DATA_DIR:
        raise RuntimeError("нужен --data-dir: каталог данных стенда, чтобы завести подтверждённый профиль")
    suffix = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    name = "%s-%s" % (prefix, suffix)
    seed_user(name)
    status, cookie_header, _ = api("/api/auth/login", {"username": name, "password": PASSWORD})
    if status != 200:
        raise RuntimeError("вход в подтверждённый профиль не прошёл: код %s" % status)
    cookie = cookie_header.split(";")[0]
    for channel in CHANNELS:
        api("/api/state/subscription", {"channel": channel, "subscribed": True}, cookie)
    return {"name": name, "cookie": cookie}


def make_collection(user, name, channels):
    status, _, payload = api("/api/collections/save", {"name": name, "channels": channels}, user["cookie"])
    assert status == 200, "не удалось создать коллекцию через API: %s" % status
    return payload["collection"]["id"]


def collections_of(user):
    return api("/api/collections", None, user["cookie"])[2].get("collections", [])


def subscriptions_of(user):
    return api("/api/state", None, user["cookie"])[2].get("subs", [])


def login(pg, user):
    pg.goto(BASE + "/")
    pg.wait_for_selector("#authScreen")
    pg.fill("#authUsername", user["name"])
    pg.fill("#authPassword", PASSWORD)
    pg.click("#authSubmit button")
    pg.wait_for_selector("#postList", timeout=30000)
    wait_posts(pg)


def wait_posts(pg, timeout=60000):
    try:
        pg.wait_for_selector("#postList .post", timeout=timeout)
    except Exception:
        pass
    pg.wait_for_timeout(1500)


def open_list(pg):
    pg.click("button[aria-label='Каналы и коллекции']")
    pg.wait_for_selector("[role=dialog]")


def overlay_open(pg, ovl):
    return pg.evaluate("!!document.querySelector('#%s [role=dialog]')" % ovl)


def wait_closed(pg, ovl):
    pg.wait_for_function("!document.querySelector('#%s [role=dialog]')" % ovl, timeout=20000)


def feed_channels(pg):
    return sorted({post.split("/")[0] for post in pg.evaluate(
        "[...document.querySelectorAll('#postList .post')].map(e => e.dataset.post)")})


def post_rows(pg):
    return pg.evaluate("[...document.querySelectorAll('#postList .post')]"
                       ".map(e => ({post: e.dataset.post, read: e.classList.contains('read')}))")


def insert_newer_post(channel):
    """Свежий пост канала прямо в базе стенда: обновление обязано поставить его выше ленты."""
    import time
    os.environ["DATA_DIR"] = DATA_DIR
    import app
    db = app.connect()
    try:
        msg_id = db.execute("SELECT MAX(msg_id) FROM posts WHERE channel=?", (channel,)).fetchone()[0] + 1
        row = dict.fromkeys(app.POST_FIELDS)
        row.update(channel=channel, msg_id=msg_id, url="https://t.me/%s/%d" % (channel, msg_id),
                   text="пост для проверки отметки", media="[]", ts=int(time.time()))
        db.execute(app.POST_INSERT, row)
        db.commit()
    finally:
        db.close()
    return "%s/%d" % (channel, msg_id)


def open_profile(pg):
    pg.locator("button[aria-label='Профиль']").first.click()
    pg.wait_for_selector("h1:has-text('Профиль')")


def pick_collection(pg, name):
    open_list(pg)
    pg.locator("#chanList .coll-name", has_text=name).first.click()
    pg.wait_for_timeout(2500)


def edit_from_list(pg, name):
    open_list(pg)
    pg.click("button[aria-label='Изменить коллекцию %s']" % name)
    pg.wait_for_selector("#collOvl [role=dialog]")


# ---------- A: вход и аккаунт ----------

@check("A3", "Неверный пароль показывает сообщение и не пускает в ленту")
def a3(c):
    c.pg.goto(BASE + "/")
    c.pg.wait_for_selector("#authScreen")
    c.pg.fill("#authUsername", c.user["name"])
    c.pg.fill("#authPassword", "wrong-password-1")
    c.pg.click("#authSubmit button")
    c.pg.wait_for_timeout(2500)
    assert c.pg.inner_text("#authMessage").strip(), "сообщения об ошибке нет"
    assert c.pg.locator("#authScreen").is_visible(), "экран входа пропал без входа"


@check("A2", "Регистрация без почты не проходит: профиль не создаётся")
def a2(c):
    c.pg.goto(BASE + "/")
    c.pg.wait_for_selector("#authScreen")
    c.pg.click("#authRegisterMode button")
    name = "nomail-" + "".join(random.choice(string.ascii_lowercase) for _ in range(6))
    c.pg.fill("#authUsername", name)
    c.pg.fill("#authPassword", PASSWORD)
    c.pg.click("#authSubmit button")
    c.pg.wait_for_timeout(2500)
    assert c.pg.locator("#authScreen").is_visible(), "экран входа пропал"
    assert not c.pg.locator("#appShell").is_visible(), "лента открылась без почты"
    status, _, _ = api("/api/auth/login", {"username": name, "password": PASSWORD})
    assert status == 401, "аккаунт без почты всё же создан: код %s" % status


@check("A4", "Выход из профиля возвращает на экран входа")
def a4(c):
    login(c.pg, c.user)
    open_profile(c.pg)
    # выход подтверждается вторым нажатием в течение шести секунд: защита от случайного выхода
    c.pg.get_by_role("button", name="Выйти из профиля").first.click()
    c.pg.wait_for_timeout(300)
    c.pg.get_by_role("button", name="Выйти из профиля").first.click()
    c.pg.wait_for_selector("#authScreen", timeout=20000)


# ---------- C: коллекции ----------

@check("C1", "Создать коллекцию из списка каналов")
def c1(c):
    login(c.pg, c.user)
    open_list(c.pg)
    c.pg.fill("#chanList .coll-new input", "Новости")
    c.pg.locator("#chanList .coll-new button[type=submit]").click()
    c.pg.wait_for_selector("#collOvl [role=dialog]")
    assert c.pg.locator("#collPick input[type=checkbox]").count() == len(CHANNELS), "в редакторе не все подписки"
    c.pg.fill("#collName", "Новости")
    c.pg.locator("#collPick input[value=rybolos_channel]").check()
    c.pg.locator("#collPick input[value=evilfreelancer]").check()
    c.pg.click("#collSave button")
    wait_closed(c.pg, "collOvl")
    c.pg.wait_for_timeout(3000)
    assert c.pg.inner_text("#tabsToggleLabel") == "Новости", c.pg.inner_text("#tabsToggleLabel")
    channels = feed_channels(c.pg)
    assert channels and set(channels) <= COLL_CHANNELS, "лента коллекции: %s" % channels
    assert collections_of(c.user)[0]["channels"] == sorted(COLL_CHANNELS), "на сервере другой состав"


@check("C2", "Открыть коллекцию: лента только её каналы, в шапке имя")
def c2(c):
    make_collection(c.user, "Новости", ["rybolos_channel", "evilfreelancer"])
    login(c.pg, c.user)
    pick_collection(c.pg, "Новости")
    assert c.pg.inner_text("#tabsToggleLabel") == "Новости"
    channels = feed_channels(c.pg)
    assert channels and set(channels) <= COLL_CHANNELS, channels


@check("C3", "Вернуться к «Все каналы» показывает все подписки")
def c3(c):
    make_collection(c.user, "Новости", ["rybolos_channel"])
    login(c.pg, c.user)
    pick_collection(c.pg, "Новости")
    open_list(c.pg)
    c.pg.locator("#feed button", has_text="Все каналы").first.click()
    c.pg.wait_for_timeout(3500)
    assert c.pg.inner_text("#tabsToggleLabel") == "Все каналы"
    assert len(set(feed_channels(c.pg))) >= 2, "в общей ленте меньше двух каналов"


@check("C4", "Изменить состав из списка каналов")
def c4(c):
    make_collection(c.user, "Новости", ["rybolos_channel", "evilfreelancer"])
    login(c.pg, c.user)
    edit_from_list(c.pg, "Новости")
    c.pg.locator("#collPick input[value=evilfreelancer]").uncheck()
    c.pg.click("#collSave button")
    wait_closed(c.pg, "collOvl")
    c.pg.wait_for_timeout(1500)
    assert collections_of(c.user)[0]["channels"] == ["rybolos_channel"], "состав не сохранился"


@check("C5", "Изменить выбранную коллекцию из ленты: лента обновляется без лишних нажатий")
def c5(c):
    make_collection(c.user, "Новости", ["rybolos_channel", "evilfreelancer"])
    login(c.pg, c.user)
    pick_collection(c.pg, "Новости")
    c.pg.locator("button[aria-label^='Изменить коллекцию']").first.click()
    c.pg.wait_for_selector("#collOvl [role=dialog]")
    c.pg.locator("#collPick input[value=evilfreelancer]").uncheck()
    c.pg.click("#collSave button")
    wait_closed(c.pg, "collOvl")
    c.pg.wait_for_timeout(4000)
    assert set(feed_channels(c.pg)) <= {"rybolos_channel"}, feed_channels(c.pg)


@check("C6", "Переименовать коллекцию")
def c6(c):
    make_collection(c.user, "Старое", ["rybolos_channel"])
    login(c.pg, c.user)
    edit_from_list(c.pg, "Старое")
    c.pg.fill("#collName", "Новое")
    c.pg.click("#collSave button")
    wait_closed(c.pg, "collOvl")
    c.pg.wait_for_timeout(1500)
    assert collections_of(c.user)[0]["name"] == "Новое"


@check("C7", "Удалить выбранную коллекцию: лента возвращается к «Все каналы»")
def c7(c):
    make_collection(c.user, "Новости", ["rybolos_channel"])
    login(c.pg, c.user)
    pick_collection(c.pg, "Новости")
    c.pg.locator("button[aria-label^='Изменить коллекцию']").first.click()
    c.pg.wait_for_selector("#collOvl [role=dialog]")
    c.pg.click("#collDelete button")
    wait_closed(c.pg, "collOvl")
    c.pg.wait_for_timeout(2500)
    assert c.pg.inner_text("#tabsToggleLabel") == "Все каналы"
    assert collections_of(c.user) == [], "коллекция осталась на сервере"


@check("C8", "Дубль имени отклоняется сообщением, редактор не закрывается")
def c8(c):
    make_collection(c.user, "Новости", ["rybolos_channel"])
    login(c.pg, c.user)
    open_list(c.pg)
    c.pg.fill("#chanList .coll-new input", "Новости")
    c.pg.locator("#chanList .coll-new button[type=submit]").click()
    c.pg.wait_for_selector("#collOvl [role=dialog]")
    c.pg.fill("#collName", "  НОВОСТИ ")
    c.pg.click("#collSave button")
    c.pg.wait_for_timeout(1500)
    assert "уже есть" in c.pg.inner_text("#collMsg"), c.pg.inner_text("#collMsg")
    assert overlay_open(c.pg, "collOvl"), "редактор закрылся"


@check("C9", "Пустая коллекция: лента говорит, что каналов нет")
def c9(c):
    make_collection(c.user, "Пустая", [])
    login(c.pg, c.user)
    pick_collection(c.pg, "Пустая")
    assert "пока нет каналов" in c.pg.inner_text("#feed"), c.pg.inner_text("#feed")[:120]


@check("C10", "Отписка от канала убирает его из коллекции на сервере")
def c10(c):
    make_collection(c.user, "Две", ["rybolos_channel", "evilfreelancer"])
    api("/api/state/subscription", {"channel": "evilfreelancer", "subscribed": False}, c.user["cookie"])
    assert collections_of(c.user)[0]["channels"] == ["rybolos_channel"], collections_of(c.user)


@check("C11", "Устаревший редактор: коллекцию уже удалили, список обновляется")
def c11(c):
    make_collection(c.user, "Удалённая", ["rybolos_channel"])
    login(c.pg, c.user)
    open_list(c.pg)
    c.pg.wait_for_timeout(2000)
    found = [c for c in collections_of(c.user) if c["name"] == "Удалённая"]
    assert found, "коллекции нет в списке"
    collection_id = found[0]["id"]
    api("/api/collections/delete", {"id": collection_id}, c.user["cookie"])
    c.pg.click("button[aria-label='Изменить коллекцию Удалённая']")
    c.pg.wait_for_selector("#collOvl [role=dialog]")
    c.pg.click("#collSave button")
    c.pg.wait_for_timeout(2500)
    assert "нет" in c.pg.inner_text("#collMsg"), c.pg.inner_text("#collMsg")
    assert c.pg.locator("#chanList .coll-name", has_text="Удалённая").count() == 0, "строка не исчезла"


@check("C12", "Нет подписок, но есть коллекции: подсказка вместо кнопки «Новая коллекция»")
def c12(c):
    make_collection(c.user, "Пусто", ["rybolos_channel"])
    api("/api/state/subscription", {"channel": "rybolos_channel", "subscribed": False}, c.user["cookie"])
    for channel in CHANNELS[1:]:
        api("/api/state/subscription", {"channel": channel, "subscribed": False}, c.user["cookie"])
    login(c.pg, c.user)
    open_list(c.pg)
    assert c.pg.locator("#chanList .hint").count() >= 1, "нет подсказки"


@check("C17", "Клавиатура: Escape закрывает редактор и оставляет список открытым")
def c17(c):
    login(c.pg, c.user)
    open_list(c.pg)
    c.pg.fill("#chanList .coll-new input", "Новости")
    c.pg.locator("#chanList .coll-new button[type=submit]").click()
    c.pg.wait_for_selector("#collOvl [role=dialog]")
    c.pg.keyboard.press("Escape")
    c.pg.wait_for_timeout(300)
    assert not overlay_open(c.pg, "collOvl") and overlay_open(c.pg, "channelsOvl")


@check("C18", "Кнопка «Изменить коллекцию» в панели видна только при выбранной коллекции")
def c18(c):
    make_collection(c.user, "Новости", ["rybolos_channel"])
    login(c.pg, c.user)
    assert c.pg.locator("#chanList .coll-name").count() == 0, "кнопка видна без выбранной коллекции"
    pick_collection(c.pg, "Новости")
    assert c.pg.locator("button[aria-label^='Изменить коллекцию']").count() >= 1, "кнопки нет при выбранной коллекции"


@check("C19", "Прочитанное внутри коллекции общее с лентой; счётчик считает коллекцию")
def c19(c):
    login(c.pg, c.user)
    if len(post_rows(c.pg)) < 3:
        raise Skip("постов меньше трёх, каналы ещё не подтянулись")
    for _ in range(3):
        c.pg.evaluate("window.scrollBy(0, 900)")
        c.pg.wait_for_timeout(700)
    c.pg.wait_for_timeout(4000)
    read = {r for r in api("/api/state", None, c.user["cookie"])[2].get("reads", [])}
    assert read, "после прокрутки отметок на сервере нет"
    make_collection(c.user, "Новости", ["rybolos_channel", "evilfreelancer"])
    pick_collection(c.pg, "Новости")
    rows = post_rows(c.pg)
    assert not any(r["post"] in read for r in rows), "прочитанные посты видны в коллекции без переключателя"


@check("C20", "Со страницы канала «В коллекцию» ставит галочку; признак на странице обновляется", device="both")
def c20(c):
    coll_id = make_collection(c.user, "Новости", [])
    login(c.pg, c.user)
    c.pg.evaluate("location.hash = '#/channel/rybolos_channel'")
    c.pg.wait_for_selector("#viewChannel", timeout=20000)
    c.pg.wait_for_selector("#chColl button", timeout=20000)
    c.pg.click("#chColl button")
    c.pg.wait_for_selector("#collOvl [role=dialog]")
    c.pg.locator("#chanCollList input[value='%s']" % coll_id).check()
    c.pg.wait_for_timeout(1500)
    assert "rybolos_channel" in collections_of(c.user)[0]["channels"], "канал не попал в коллекцию"
    c.pg.locator("#collOvl button", has_text="Отмена").first.click()
    wait_closed(c.pg, "chanCollOvl")
    c.pg.wait_for_timeout(500)
    assert "Новости" in c.pg.inner_text("#chCollState"), c.pg.inner_text("#chCollState")


@check("C21", "Лента: у канала число коллекций в списке; галочка в диалоге снимает канал", device="both")
def c21(c):
    coll_id = make_collection(c.user, "Новости", ["rybolos_channel"])
    login(c.pg, c.user)
    open_list(c.pg)
    row = c.pg.locator("#chanList .quiet-btn", has_text="В коллекциях: 1")
    assert row.count() == 1, "у канала нет числа коллекций в списке"
    row.click()
    c.pg.wait_for_selector("#collOvl [role=dialog]")
    c.pg.locator("#chanCollList input[value='%s']" % coll_id).uncheck()
    c.pg.wait_for_timeout(1500)
    assert collections_of(c.user)[0]["channels"] == [], "галочка не сняла канал"
    c.pg.locator("#collOvl button", has_text="Отмена").first.click()
    wait_closed(c.pg, "chanCollOvl")
    c.pg.wait_for_timeout(500)
    assert c.pg.locator("#chanList .quiet-btn", has_text="В коллекцию").count() >= 1, "подпись не вернулась"


@check("C22", "Со страницы канала можно сразу создать коллекцию с этим каналом", device="both")
def c22(c):
    login(c.pg, c.user)
    c.pg.evaluate("location.hash = '#/channel/rybolos_channel'")
    c.pg.wait_for_selector("#viewChannel", timeout=20000)
    c.pg.wait_for_selector("#chColl button", timeout=20000)
    c.pg.click("#chColl button")
    c.pg.wait_for_selector("#collOvl [role=dialog]")
    c.pg.fill("#chanCollNew", "С канала")
    c.pg.click("#chanCollAdd button")
    c.pg.wait_for_timeout(2500)
    created = [x for x in collections_of(c.user) if x["name"] == "С канала"]
    assert created and created[0]["channels"] == ["rybolos_channel"], created


@check("C23", "Канал без подписки не предлагает «В коллекцию»", device="both")
def c23(c):
    login(c.pg, c.user)
    c.pg.evaluate("location.hash = '#/channel/durov'")
    c.pg.wait_for_selector("#viewChannel", timeout=20000)
    c.pg.wait_for_timeout(1500)
    assert c.pg.locator("#chColl").is_hidden(), "кнопка видна у неподписанного канала"


# ---------- F: лента ----------

@check("F2", "Прокрутка мимо поста отмечает его прочитанным на сервере")
def f2(c):
    login(c.pg, c.user)
    if len(post_rows(c.pg)) < 3:
        raise Skip("постов меньше трёх")
    for _ in range(3):
        c.pg.evaluate("window.scrollBy(0, 900)")
        c.pg.wait_for_timeout(700)
    c.pg.wait_for_timeout(4000)
    assert api("/api/state", None, c.user["cookie"])[2].get("reads"), "отметок на сервере нет"


@check("F3", "«Скрыть прочитанные» показывает прочитанное и прячет обратно")
def f3(c):
    login(c.pg, c.user)
    if len(post_rows(c.pg)) < 3:
        raise Skip("постов меньше трёх")
    assert c.pg.get_attribute("#readToggle button", "aria-pressed") == "true", "по умолчанию прочитанное должно быть спрятано"
    for _ in range(3):
        c.pg.evaluate("window.scrollBy(0, 900)")
        c.pg.wait_for_timeout(700)
    c.pg.wait_for_timeout(4000)
    c.pg.click("#readToggle")
    c.pg.wait_for_timeout(3000)
    assert c.pg.get_attribute("#readToggle button", "aria-pressed") == "false", "кнопка не отжалась"
    assert any(r["read"] for r in post_rows(c.pg)), "прочитанных постов не видно"
    c.pg.click("#readToggle")
    c.pg.wait_for_timeout(2500)
    assert c.pg.get_attribute("#readToggle button", "aria-pressed") == "true", "кнопка не нажалась обратно"
    assert not any(r["read"] for r in post_rows(c.pg)), "прочитанные не спрятались"


@check("F4", "Обновление не отмечает прочитанным посты, которые встали выше экрана")
def f4(c):
    login(c.pg, c.user)
    if len(post_rows(c.pg)) < 3:
        raise Skip("постов меньше трёх")
    for _ in range(3):
        c.pg.evaluate("window.scrollBy(0, 900)")
        c.pg.wait_for_timeout(700)
    c.pg.wait_for_timeout(4000)
    marked = {r["post"] for r in post_rows(c.pg) if r["read"]}
    assert marked, "прокруткой ничего не отметилось, проверка пуста"
    new_post = insert_newer_post(CHANNELS[0])
    # жест «потянуть» вызывает refreshCurrent; на ПК жеста нет, проверяем саму функцию
    c.pg.evaluate("window.dispatchEvent(new Event('koloda:refresh'))")
    c.pg.wait_for_function("key => (document.querySelector('#postList .post') || {}).dataset?.post === key",
                           arg=new_post, timeout=20000)
    c.pg.wait_for_timeout(2000)
    stolen = [r["post"] for r in post_rows(c.pg) if r["read"] and r["post"] not in marked]
    assert not stolen, "обновление отметило прочитанными непросмотренные: %s" % stolen[:3]
    # контроль: после обновления прокрутка по-прежнему отмечает посты
    c.pg.evaluate("window.scrollBy(0, 1500)")
    c.pg.wait_for_timeout(1500)
    passed = [r["post"] for r in post_rows(c.pg) if r["read"] and r["post"] not in marked]
    assert passed, "после обновления прокрутка ничего не отметила"


@check("F8", "Прокрутка к концу ленты подгружает следующую страницу без повторов")
def f8(c):
    login(c.pg, c.user)
    if c.pg.locator("#loadMore").is_hidden():
        raise Skip("страница одна: нужно больше постов в подписках")
    before = c.pg.locator("#postList .post").count()
    for _ in range(15):
        c.pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        c.pg.wait_for_timeout(1500)
        if c.pg.locator("#postList .post").count() > before:
            break
    keys = [r["post"] for r in post_rows(c.pg)]
    assert len(keys) > before, (before, len(keys))
    assert len(keys) == len(set(keys)), "повторы при подгрузке"


@check("F12", "Убрать канал из ленты с отменой")
def f12(c):
    login(c.pg, c.user)
    open_list(c.pg)
    c.pg.locator("#chanList .coll-name").first.click()
    c.pg.wait_for_timeout(2500)
    c.pg.locator("#chanList .unsub-btn").first.click()
    c.pg.wait_for_timeout(1500)
    removed = [ch for ch in CHANNELS if ch not in subscriptions_of(c.user)]
    assert len(removed) == 1, "канал не ушёл из подписок: %s" % removed
    assert c.pg.locator("#undoBar").is_visible(), "панели отмены нет"
    c.pg.locator("#undoBar button").first.click()
    c.pg.wait_for_timeout(2000)
    assert sorted(subscriptions_of(c.user)) == sorted(CHANNELS), "отмена не вернула канал"


@check("F15", "Галерея: кадр открывает просмотрщик, стрелка листает «N из M»")
def f15(c):
    login(c.pg, c.user)
    post = c.pg.locator("#postList .post").filter(has=c.pg.locator(".gal > img")).first
    if post.count() == 0:
        raise Skip("нет поста с фото в галерее")
    post.locator(".gal > img").first.click()
    c.pg.wait_for_selector("#lightbox.open", timeout=10000)
    total = int(c.pg.inner_text("#lbN").split(" из ")[1]) if " из " in c.pg.inner_text("#lbN") else 1
    assert total >= 1
    c.pg.keyboard.press("ArrowRight")
    c.pg.wait_for_timeout(500)
    if total > 1:
        assert c.pg.inner_text("#lbN").startswith("2 из"), c.pg.inner_text("#lbN")
    c.pg.keyboard.press("Escape")
    wait_closed(c.pg, "lightbox")


@check("F20", "Потянуть ленту вниз обновляет её", device="phone")
def f20(c):
    login(c.pg, c.user)
    c.pg.evaluate("window.scrollTo(0, 0)")
    c.pg.wait_for_timeout(300)
    cdp = c.ctx.new_cdp_session(c.pg)
    seen = []
    c.pg.on("request", lambda r: seen.append(r.url) if "/api/feed" in r.url else None)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": 200, "y": 200}]})
    for dy in (40, 80, 120):
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": 200, "y": 200 + dy}]})
        c.pg.wait_for_timeout(60)
    height = c.pg.evaluate("document.getElementById('pull').style.height")
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    c.pg.wait_for_timeout(2000)
    assert height == "48px", "индикатор не вырос: %r" % height
    assert seen, "запроса ленты при отпускании не было"


# ---------- H, V, D: профиль ----------

@check("H1", "Вкладка «Справка» показывает список клавиш")
def h1(c):
    login(c.pg, c.user)
    open_profile(c.pg)
    c.pg.get_by_role("tab", name="Справка").click()
    c.pg.wait_for_timeout(800)
    assert "Клавиши" in c.pg.get_by_role("tabpanel").inner_text(), "список клавиш не открылся"


@check("V1", "Тема сохраняется в профиле и переживает перезагрузку")
def v1(c):
    login(c.pg, c.user)
    open_profile(c.pg)
    c.pg.get_by_role("tab", name="Вид").click()
    c.pg.get_by_role("button", name="Светлая").first.click()
    c.pg.wait_for_timeout(1800)
    assert c.pg.evaluate("document.documentElement.dataset.mode") == "light", "тема не применилась"
    c.pg.reload()
    c.pg.wait_for_selector("#postList", timeout=30000)
    assert c.pg.evaluate("document.documentElement.dataset.modePref") == "light", "после перезагрузки тема слетела"


@check("V2", "Шрифт меняется и сохраняется")
def v2(c):
    login(c.pg, c.user)
    open_profile(c.pg)
    c.pg.get_by_role("tab", name="Вид").click()
    c.pg.get_by_role("button", name="С засечками").first.click()
    c.pg.wait_for_timeout(1500)
    c.pg.reload()
    c.pg.wait_for_selector("#postList", timeout=30000)
    assert c.pg.evaluate("document.documentElement.dataset.font") == "serif", "шрифт не сохранился"


@check("D1", "Резервная копия: «Скачать файл» отдаёт файл")
def d1(c):
    login(c.pg, c.user)
    open_profile(c.pg)
    c.pg.get_by_role("tab", name="Данные").click()
    with c.pg.expect_download(timeout=20000) as download:
        c.pg.get_by_role("link", name="Скачать koloda-lenta.jsonl").click()
    assert download.value.suggested_filename, "файл без имени"


# ---------- K, P: каталог и канал ----------

@check("K3", "Ссылка, которая не похожа на канал, отклоняется сообщением")
def k3(c):
    login(c.pg, c.user)
    c.pg.get_by_role("button", name="Каталог", exact=True).first.click()
    c.pg.wait_for_selector(".channel-entry, .empty")
    c.pg.fill("#channelInput", "нет такого!!")
    c.pg.click("#channelAdd button")
    c.pg.wait_for_timeout(1500)
    assert c.pg.inner_text("#channelState").strip(), "сообщения нет"
    assert set(subscriptions_of(c.user)) == set(CHANNELS), "подписка появилась"


@check("K10", "Категории: «Все» первая, остальные по русским названиям; ошибка не прячет ряд и повторяется")
def k10(c):
    login(c.pg, c.user)
    c.pg.get_by_role("button", name="Каталог", exact=True).first.click()
    c.pg.wait_for_selector("#cats button")
    labels = c.pg.evaluate("[...document.querySelectorAll('#cats button')].map(b => b.textContent)")
    assert labels and labels[0] == "Все", labels
    assert len(labels) > 2, "категорий меньше двух: %s" % labels
    ordered = c.pg.evaluate("""() => { const a = [...document.querySelectorAll('#cats button')].slice(1).map(b => b.textContent);
        return a.every((x, i, all) => i === 0 || all[i - 1].localeCompare(x, 'ru') <= 0); }""")
    assert ordered, "категории не по русскому алфавиту: %s" % labels
    assert len(set(labels)) == len(labels), "категории повторяются: %s" % labels
    c.pg.route("**/api/categories", lambda route: route.fulfill(status=502, body="{}", content_type="application/json"))
    c.pg.reload()
    c.pg.wait_for_selector("#postList", timeout=30000)
    c.pg.click("#tabCat:visible, #tbCat:visible")
    c.pg.wait_for_selector("#catRetry", timeout=20000)
    shown = c.pg.evaluate("[...document.querySelectorAll('#cats button')].map(b => b.textContent)")
    assert shown[0] == "Все", "при ошибке пропала кнопка «Все»: %s" % shown
    c.pg.unroute("**/api/categories")
    c.pg.click("#catRetry")
    c.pg.wait_for_function("document.querySelectorAll('#cats button').length > 2", timeout=20000)


@check("K11", "Телефон: «Все категории» показывает все чипы в несколько строк", device="phone")
def k11(c):
    login(c.pg, c.user)
    c.pg.click("#tbCat:visible, #tabCat:visible")
    c.pg.wait_for_selector("#cats button")
    c.pg.wait_for_timeout(1000)
    expand = c.pg.locator("#catExpand")
    if not expand.is_visible():
        raise Skip("все категории помещаются в одну строку")
    expand.click()
    c.pg.wait_for_timeout(500)
    assert c.pg.evaluate("document.getElementById('cats').classList.contains('wrap')"), "ряд не развернулся"
    right = c.pg.evaluate("Math.max(...[...document.querySelectorAll('#cats button')].map(b => b.getBoundingClientRect().right))")
    assert right <= c.pg.evaluate("innerWidth") + 1, "чипы выходят за экран: %s" % right


@check("P1", "Страница канала открывается с заголовком")
def p1(c):
    login(c.pg, c.user)
    c.pg.evaluate("location.hash = '#/channel/rybolos_channel'")
    c.pg.wait_for_selector("#viewChannel", timeout=20000)
    assert c.pg.inner_text("#chTitle").strip(), "заголовка нет"


@check("P2", "Кнопка на странице канала меняет подписку")
def p2(c):
    login(c.pg, c.user)
    c.pg.evaluate("location.hash = '#/channel/rybolos_channel'")
    c.pg.wait_for_selector("#viewChannel", timeout=20000)
    c.pg.wait_for_function("document.getElementById('chAction').textContent.trim().length > 0", timeout=20000)
    c.pg.wait_for_timeout(1500)
    before = "rybolos_channel" in subscriptions_of(c.user)
    c.pg.click("#chAction")
    c.pg.wait_for_timeout(2000)
    assert ("rybolos_channel" in subscriptions_of(c.user)) != before, "подписка не изменилась"
    c.pg.click("#chAction")
    c.pg.wait_for_timeout(2000)


@check("P3", "«Открыть в Telegram» ведёт на канал")
def p3(c):
    login(c.pg, c.user)
    c.pg.evaluate("location.hash = '#/channel/rybolos_channel'")
    c.pg.wait_for_selector("#viewChannel", timeout=20000)
    href = c.pg.get_attribute("#chTg", "href") or ""
    assert href.startswith("https://t.me/") and "rybolos_channel" in href, href


# ---------- E: устойчивость ----------

@check("E3", "Перезагрузка: коллекции остаются, фильтр ленты сбрасывается")
def e3(c):
    make_collection(c.user, "Новости", ["rybolos_channel"])
    login(c.pg, c.user)
    pick_collection(c.pg, "Новости")
    c.pg.reload()
    c.pg.wait_for_selector("#postList", timeout=30000)
    assert c.pg.inner_text("#tabsToggleLabel") == "Все каналы", "фильтр пережил перезагрузку"
    open_list(c.pg)
    assert c.pg.locator("#chanList .coll-row .chan-item", has_text="Новости").count() == 1, "коллекции пропали"


# ---------- X: доступность и размеры ----------

@check("X3", "Нажимаемые зоны не ниже 44 px на телефоне", device="phone")
def x3(c):
    login(c.pg, c.user)
    sizes = c.pg.evaluate("""() => [...document.querySelectorAll('#tabBar .tabbar-item, #readToggle')]
        .filter(e => e.offsetParent !== null).map(e => [e.id || e.textContent.trim().slice(0, 12),
        Math.round(e.getBoundingClientRect().height)])""")
    small = [s for s in sizes if s[1] < 44]
    assert not small, "меньше 44 px: %s" % small


def swipe(cdp, x0, y0, x1, y1, steps=4):
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x0, "y": y0}]})
    for i in range(1, steps + 1):
        x = x0 + (x1 - x0) * i / steps
        y = y0 + (y1 - y0) * i / steps
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y}]})
        time.sleep(0.02)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    time.sleep(0.3)


@check("F16", "Свайп влево и вправо листает кадры; короткий и вертикальный жесты ничего не меняют", device="phone")
def f16(c):
    login(c.pg, c.user)
    index = c.pg.evaluate("[...document.querySelectorAll('#postList .post')].findIndex("
                          "e => e.querySelectorAll('.gal > *').length >= 3 && e.querySelector('.gal > img'))")
    if index < 0:
        raise Skip("нет альбома из трёх и больше кадров на первой странице")
    c.pg.locator("#postList .post").nth(index).locator(".gal > img").first.click()
    c.pg.wait_for_selector("#lightbox.open", timeout=10000)
    counter = c.pg.inner_text("#lbN")
    if " из " not in counter or int(counter.split(" из ")[1]) < 2:
        raise Skip("в просмотрщике один кадр")
    total = counter.split(" из ")[1]
    cdp = c.ctx.new_cdp_session(c.pg)
    swipe(cdp, 340, 450, 200, 450)
    assert c.pg.inner_text("#lbN") == "2 из " + total, "свайп влево: %s" % c.pg.inner_text("#lbN")
    swipe(cdp, 200, 450, 340, 450)
    assert c.pg.inner_text("#lbN") == "1 из " + total, "свайп вправо: %s" % c.pg.inner_text("#lbN")
    swipe(cdp, 340, 450, 320, 450)
    assert c.pg.inner_text("#lbN") == "1 из " + total, "короткий жест листнул"
    swipe(cdp, 200, 300, 210, 600)
    assert c.pg.inner_text("#lbN") == "1 из " + total, "вертикальный жест листнул"


@check("F17", "Протухший кадр: пустой квадрат до свежей ссылки, потом кадр без битой картинки")
def f17(c):
    held = []
    c.pg.route("**/api/media**", lambda route: held.append(route))
    login(c.pg, c.user)
    for _ in range(12):
        if held:
            break
        c.pg.evaluate("window.scrollBy(0, 1400)")
        c.pg.wait_for_timeout(400)
        if c.pg.locator("#loadMore").is_visible():
            c.pg.click("#loadMore")
            c.pg.wait_for_timeout(2000)
    c.pg.wait_for_timeout(1500)
    if not held:
        raise Skip("протухших кадров не нашлось на первых страницах")
    state = c.pg.evaluate("""() => {
        const imgs = [...document.querySelectorAll('#feed .gal img')];
        const waiting = imgs.filter(i => i.dataset.fresh && !i.getAttribute('src'));
        const broken = imgs.filter(i => i.getAttribute('src') && i.complete && i.naturalWidth === 0);
        return {waiting: waiting.length, emptyAlt: waiting.filter(i => i.alt === '').length, broken: broken.length};
    }""")
    assert state["waiting"] >= 1 and state["emptyAlt"] == state["waiting"], "во время ожидания: %s" % state
    assert state["broken"] == 0, "битая картинка видна во время ожидания: %s" % state
    for route in held:
        route.continue_()
    held.clear()
    c.pg.unroute("**/api/media**")
    c.pg.wait_for_timeout(8000)
    frames = c.pg.locator("#feed .gal img[data-fresh]")
    loaded = 0
    for index in range(frames.count()):
        frame = frames.nth(index)
        frame.scroll_into_view_if_needed()
        c.pg.wait_for_timeout(1200)
        loaded += 1 if frame.evaluate("el => el.naturalWidth > 0") else 0
    assert loaded >= 1, "ни один свежий кадр не загрузился"


# ---------- запуск ----------

def run(device_filter, only):
    from playwright.sync_api import sync_playwright
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        for pid, title, device, fn in CHECKS:
            if only and pid not in only:
                continue
            # device="both" значит: одна и та же проверка на ПК и на телефоне
            for run_device in (("desk", "phone") if device == "both" else (device,)):
                label = pid if device != "both" else "%s/%s" % (pid, "ПК" if run_device == "desk" else "тел")
                if device_filter != "both" and run_device != device_filter:
                    results.append((label, title, "SKIP", "проверка для устройства %s" % run_device))
                    continue
                phone = run_device == "phone"
                viewport = {"width": 420, "height": 900} if phone else {"width": 1280, "height": 860}
                ctx = browser.new_context(viewport=viewport, is_mobile=phone, has_touch=phone)
                pg = ctx.new_page()
                pg.set_default_timeout(20000)
                pg.on("dialog", lambda d: d.accept())
                try:
                    user = new_user("ui" + pid.lower())
                    fn(type("Ctx", (), {"pg": pg, "ctx": ctx, "user": user})())
                    results.append((label, title, "PASS", ""))
                except Skip as skipped:
                    results.append((label, title, "SKIP", str(skipped)))
                except Exception as failed:
                    results.append((label, title, "FAIL", str(failed).splitlines()[0][:200]))
                finally:
                    ctx.close()
        browser.close()
    return results


def main():
    global BASE
    parser = argparse.ArgumentParser(description="Проверка пользовательских путей в браузере")
    parser.add_argument("base", help="адрес стенда, например http://127.0.0.1:8090")
    parser.add_argument("--only", default="", help="ID через запятую, например C1,F2")
    parser.add_argument("--device", default="desk", choices=("desk", "phone", "both"))
    parser.add_argument("--data-dir", default="", help="каталог данных стенда (где лежит feed.db)")
    args = parser.parse_args()
    global DATA_DIR
    DATA_DIR = args.data_dir
    BASE = args.base.rstrip("/")
    only = {item.strip().upper() for item in args.only.split(",") if item.strip()}
    results = run(args.device, only)
    for pid, title, status, detail in results:
        print("%-5s %-4s %s%s" % (status, pid, title, ("  [" + detail + "]") if detail else ""), flush=True)
    failed = [r for r in results if r[2] == "FAIL"]
    passed = sum(1 for r in results if r[2] == "PASS")
    skipped = sum(1 for r in results if r[2] == "SKIP")
    print("\nPASS %d, FAIL %d, SKIP %d" % (passed, len(failed), skipped))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
