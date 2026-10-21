#!/usr/bin/env python3
"""Проверка пользовательских путей в браузере по docs/user-paths.md.

Запуск на стенде, где сервис уже поднят:
    python3 ui_paths_check.py http://127.0.0.1:8090
    python3 ui_paths_check.py http://127.0.0.1:8090 --only C1,C2,F2 --device phone

Нужны Playwright для Python и установленный Chrome (канал chrome). Нужен интернет: посты
подтягиваются с t.me. Стенд должен работать без почты (SMTP выключен), иначе регистрация
без почты не пройдёт. Каждая проверка создаёт свой профиль через API и не трогает чужие.
Путь, которому нужны посты, которых нет, или который не на этом устройстве, помечается SKIP
с причиной, а не проходит молча.

Скрипт служебный: не входит в образ и не гоняется в unittest.
"""
import argparse
import json
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


def new_user(prefix):
    """Новый профиль с тремя подписками. Почта не нужна: стенд без SMTP."""
    suffix = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    name = "%s-%s" % (prefix, suffix)
    status, cookie_header, _ = api("/api/auth/register", {"username": name, "password": PASSWORD})
    if status != 201:
        raise RuntimeError("регистрация на стенде не прошла: код %s" % status)
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
    pg.wait_for_selector("#authScreen:not([hidden])")
    pg.fill("#authUsername", user["name"])
    pg.fill("#authPassword", PASSWORD)
    pg.click("#authSubmit")
    pg.wait_for_selector("#appShell:not([hidden])", timeout=30000)
    wait_posts(pg)


def wait_posts(pg, timeout=60000):
    try:
        pg.wait_for_selector("#postList .post", timeout=timeout)
    except Exception:
        pass
    pg.wait_for_timeout(1500)


def open_list(pg):
    pg.click("#tabsToggle")
    pg.wait_for_selector("#channelsOvl.open")


def overlay_open(pg, ovl):
    return pg.evaluate("document.getElementById('%s').classList.contains('open')" % ovl)


def wait_closed(pg, ovl):
    pg.wait_for_function("!document.getElementById('%s').classList.contains('open')" % ovl, timeout=20000)


def feed_channels(pg):
    return sorted({post.split("/")[0] for post in pg.evaluate(
        "[...document.querySelectorAll('#postList .post')].map(e => e.dataset.post)")})


def post_rows(pg):
    return pg.evaluate("[...document.querySelectorAll('#postList .post')]"
                       ".map(e => ({post: e.dataset.post, read: e.classList.contains('read')}))")


def unread_counter(pg):
    text = pg.inner_text("#unreadBtn .u-count") if pg.locator("#unreadBtn .u-count").count() else ""
    return int(text) if text.strip().isdigit() else 0


def open_profile(pg):
    pg.locator("#meBtn:visible, #tbMe:visible").first.click()
    pg.wait_for_selector("#viewMe:not([hidden])")


def pick_collection(pg, name):
    open_list(pg)
    pg.locator("#chanList .coll-row .chan-item", has_text=name).first.click()
    pg.wait_for_timeout(2500)


def edit_from_list(pg, name):
    open_list(pg)
    pg.click("button[aria-label='Изменить коллекцию %s']" % name)
    pg.wait_for_selector("#collOvl.open")


# ---------- A: вход и аккаунт ----------

@check("A3", "Неверный пароль показывает сообщение и не пускает в ленту")
def a3(c):
    c.pg.goto(BASE + "/")
    c.pg.wait_for_selector("#authScreen:not([hidden])")
    c.pg.fill("#authUsername", c.user["name"])
    c.pg.fill("#authPassword", "wrong-password-1")
    c.pg.click("#authSubmit")
    c.pg.wait_for_timeout(2500)
    assert c.pg.inner_text("#authMessage").strip(), "сообщения об ошибке нет"
    assert c.pg.locator("#authScreen").is_visible(), "экран входа пропал без входа"


@check("A4", "Выход из профиля возвращает на экран входа")
def a4(c):
    login(c.pg, c.user)
    open_profile(c.pg)
    # выход подтверждается вторым нажатием в течение шести секунд: защита от случайного выхода
    c.pg.click("#logoutBtn")
    c.pg.wait_for_timeout(300)
    c.pg.click("#logoutBtn")
    c.pg.wait_for_selector("#authScreen:not([hidden])", timeout=20000)


# ---------- C: коллекции ----------

@check("C1", "Создать коллекцию из списка каналов")
def c1(c):
    login(c.pg, c.user)
    open_list(c.pg)
    c.pg.locator("#chanList button", has_text="Новая коллекция").click()
    c.pg.wait_for_selector("#collOvl.open")
    assert c.pg.locator("#collPick input[type=checkbox]").count() == len(CHANNELS), "в редакторе не все подписки"
    c.pg.fill("#collName", "Новости")
    c.pg.locator("#collPick input[value=rybolos_channel]").check()
    c.pg.locator("#collPick input[value=evilfreelancer]").check()
    c.pg.click("#collSave")
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
    c.pg.locator("#chanList button", has_text="Все каналы").click()
    c.pg.wait_for_timeout(3500)
    assert c.pg.inner_text("#tabsToggleLabel") == "Все каналы"
    assert len(set(feed_channels(c.pg))) >= 2, "в общей ленте меньше двух каналов"


@check("C4", "Изменить состав из списка каналов")
def c4(c):
    make_collection(c.user, "Новости", ["rybolos_channel", "evilfreelancer"])
    login(c.pg, c.user)
    edit_from_list(c.pg, "Новости")
    c.pg.locator("#collPick input[value=evilfreelancer]").uncheck()
    c.pg.click("#collSave")
    wait_closed(c.pg, "collOvl")
    c.pg.wait_for_timeout(1500)
    assert collections_of(c.user)[0]["channels"] == ["rybolos_channel"], "состав не сохранился"


@check("C5", "Изменить выбранную коллекцию из ленты: лента обновляется без лишних нажатий")
def c5(c):
    make_collection(c.user, "Новости", ["rybolos_channel", "evilfreelancer"])
    login(c.pg, c.user)
    pick_collection(c.pg, "Новости")
    c.pg.click("#collEditBtn")
    c.pg.wait_for_selector("#collOvl.open")
    c.pg.locator("#collPick input[value=evilfreelancer]").uncheck()
    c.pg.click("#collSave")
    wait_closed(c.pg, "collOvl")
    c.pg.wait_for_timeout(4000)
    assert set(feed_channels(c.pg)) <= {"rybolos_channel"}, feed_channels(c.pg)


@check("C6", "Переименовать коллекцию")
def c6(c):
    make_collection(c.user, "Старое", ["rybolos_channel"])
    login(c.pg, c.user)
    edit_from_list(c.pg, "Старое")
    c.pg.fill("#collName", "Новое")
    c.pg.click("#collSave")
    wait_closed(c.pg, "collOvl")
    c.pg.wait_for_timeout(1500)
    assert collections_of(c.user)[0]["name"] == "Новое"


@check("C7", "Удалить выбранную коллекцию: лента возвращается к «Все каналы»")
def c7(c):
    make_collection(c.user, "Новости", ["rybolos_channel"])
    login(c.pg, c.user)
    pick_collection(c.pg, "Новости")
    c.pg.click("#collEditBtn")
    c.pg.wait_for_selector("#collOvl.open")
    c.pg.click("#collDelete")
    wait_closed(c.pg, "collOvl")
    c.pg.wait_for_timeout(2500)
    assert c.pg.inner_text("#tabsToggleLabel") == "Все каналы"
    assert collections_of(c.user) == [], "коллекция осталась на сервере"


@check("C8", "Дубль имени отклоняется сообщением, редактор не закрывается")
def c8(c):
    make_collection(c.user, "Новости", ["rybolos_channel"])
    login(c.pg, c.user)
    open_list(c.pg)
    c.pg.locator("#chanList button", has_text="Новая коллекция").click()
    c.pg.wait_for_selector("#collOvl.open")
    c.pg.fill("#collName", "  НОВОСТИ ")
    c.pg.click("#collSave")
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
    c.pg.wait_for_function("collections.some(c => c.name === 'Удалённая')", timeout=20000)
    collection_id = c.pg.evaluate("collections.find(c => c.name === 'Удалённая').id")
    api("/api/collections/delete", {"id": collection_id}, c.user["cookie"])
    c.pg.click("button[aria-label='Изменить коллекцию Удалённая']")
    c.pg.wait_for_selector("#collOvl.open")
    c.pg.click("#collSave")
    c.pg.wait_for_timeout(2500)
    assert "нет" in c.pg.inner_text("#collMsg"), c.pg.inner_text("#collMsg")
    assert c.pg.locator("#chanList .coll-row .chan-item", has_text="Удалённая").count() == 0, "строка не исчезла"


@check("C12", "Нет подписок, но есть коллекции: подсказка вместо кнопки «Новая коллекция»")
def c12(c):
    make_collection(c.user, "Пусто", ["rybolos_channel"])
    api("/api/state/subscription", {"channel": "rybolos_channel", "subscribed": False}, c.user["cookie"])
    for channel in CHANNELS[1:]:
        api("/api/state/subscription", {"channel": channel, "subscribed": False}, c.user["cookie"])
    login(c.pg, c.user)
    open_list(c.pg)
    assert c.pg.locator("#chanList .chan-hint", has_text="сначала подпишись").count() == 1, "нет подсказки"
    assert c.pg.locator("#chanList button", has_text="Новая коллекция").count() == 0, "кнопка видна без подписок"


@check("C17", "Клавиатура: Escape закрывает редактор и оставляет список открытым")
def c17(c):
    login(c.pg, c.user)
    open_list(c.pg)
    c.pg.locator("#chanList button", has_text="Новая коллекция").click()
    c.pg.wait_for_selector("#collOvl.open")
    c.pg.keyboard.press("Escape")
    c.pg.wait_for_timeout(300)
    assert not overlay_open(c.pg, "collOvl") and overlay_open(c.pg, "channelsOvl")


@check("C18", "Кнопка «Изменить коллекцию» в панели видна только при выбранной коллекции")
def c18(c):
    make_collection(c.user, "Новости", ["rybolos_channel"])
    login(c.pg, c.user)
    assert not c.pg.locator("#collEditBtn").is_visible(), "кнопка видна без выбранной коллекции"
    pick_collection(c.pg, "Новости")
    assert c.pg.locator("#collEditBtn").is_visible(), "кнопки нет при выбранной коллекции"


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


@check("F3", "«Показать прочитанное» возвращает прочитанные посты и прячет обратно")
def f3(c):
    login(c.pg, c.user)
    if len(post_rows(c.pg)) < 3:
        raise Skip("постов меньше трёх")
    for _ in range(3):
        c.pg.evaluate("window.scrollBy(0, 900)")
        c.pg.wait_for_timeout(700)
    c.pg.wait_for_timeout(4000)
    c.pg.click("#readToggle")
    c.pg.wait_for_timeout(3000)
    assert any(r["read"] for r in post_rows(c.pg)), "прочитанных постов не видно"
    c.pg.click("#readToggle")
    c.pg.wait_for_timeout(2500)
    assert not any(r["read"] for r in post_rows(c.pg)), "прочитанные не спрятались"


@check("F4", "«Непрочитанные»: прочитанное уходит из списка, счётчик совпадает с первой страницей")
def f4(c):
    login(c.pg, c.user)
    if len(post_rows(c.pg)) < 3:
        raise Skip("постов меньше трёх")
    # сначала отмечаем прочитанное прокруткой: без этого фильтр ничем не отличается от ленты
    for _ in range(3):
        c.pg.evaluate("window.scrollBy(0, 900)")
        c.pg.wait_for_timeout(700)
    c.pg.wait_for_timeout(4000)
    marked = {r["post"] for r in post_rows(c.pg) if r["read"]}
    assert marked, "прокруткой ничего не отметилось"
    c.pg.evaluate("window.scrollTo(0, 0)")
    c.pg.wait_for_timeout(500)
    c.pg.click("#unreadBtn")
    c.pg.wait_for_timeout(3000)
    # страница после фильтра приходит с сервера, без прокрутки: прочитанного в ней быть не должно
    rows = post_rows(c.pg)
    shown = [r["post"] for r in rows if r["post"] in marked]
    assert not shown, "в фильтре остались прочитанные: %s" % shown[:3]
    expected = min(unread_counter(c.pg), 80)
    assert len(rows) == expected, "счётчик %s, на первой странице %s" % (unread_counter(c.pg), len(rows))


@check("F8", "«Загрузить ещё» дописывает страницу без повторов")
def f8(c):
    login(c.pg, c.user)
    if not c.pg.locator("#loadMore").is_visible():
        raise Skip("страница одна: нужно больше постов в подписках")
    before = c.pg.locator("#postList .post").count()
    c.pg.click("#loadMore")
    c.pg.wait_for_timeout(3000)
    after = c.pg.locator("#postList .post").count()
    keys = [r["post"] for r in post_rows(c.pg)]
    assert after > before and len(keys) == len(set(keys)), (before, after)


@check("F12", "Убрать канал из ленты с отменой")
def f12(c):
    login(c.pg, c.user)
    open_list(c.pg)
    c.pg.locator("#chanList > button.chan-item").filter(has_not_text="Все каналы").first.click()
    c.pg.wait_for_timeout(2500)
    c.pg.click("#unsubBtn")
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
    c.pg.click("#meTabHelp")
    c.pg.wait_for_timeout(500)
    assert "Клавиши" in c.pg.inner_text("#mePanelHelp"), "список клавиш не открылся"


@check("V1", "Тема сохраняется в профиле и переживает перезагрузку")
def v1(c):
    login(c.pg, c.user)
    open_profile(c.pg)
    c.pg.click("#meTabView")
    c.pg.click("button[data-set=theme][data-value=light]")
    c.pg.wait_for_timeout(1500)
    assert c.pg.evaluate("document.documentElement.dataset.theme") == "light", "тема не применилась"
    c.pg.reload()
    c.pg.wait_for_selector("#appShell:not([hidden])", timeout=30000)
    assert c.pg.evaluate("document.documentElement.dataset.themePref") == "light", "после перезагрузки тема слетела"


@check("V2", "Шрифт меняется и сохраняется")
def v2(c):
    login(c.pg, c.user)
    open_profile(c.pg)
    c.pg.click("#meTabView")
    c.pg.click("button[data-set=font][data-value=serif]")
    c.pg.wait_for_timeout(1500)
    c.pg.reload()
    c.pg.wait_for_selector("#appShell:not([hidden])", timeout=30000)
    assert c.pg.evaluate("document.documentElement.dataset.font") == "serif", "шрифт не сохранился"


@check("D1", "Резервная копия: «Скачать файл» отдаёт файл")
def d1(c):
    login(c.pg, c.user)
    open_profile(c.pg)
    c.pg.click("#meTabData")
    with c.pg.expect_download(timeout=20000) as download:
        c.pg.click("#exportBtn")
    assert download.value.suggested_filename, "файл без имени"


# ---------- K, P: каталог и канал ----------

@check("K3", "Ссылка, которая не похожа на канал, отклоняется сообщением")
def k3(c):
    login(c.pg, c.user)
    c.pg.click("#tabCat:visible, #tbCat:visible")
    c.pg.wait_for_selector("#viewCat:not([hidden])")
    c.pg.fill("#channelInput", "нет такого!!")
    c.pg.click("#channelAdd")
    c.pg.wait_for_timeout(1500)
    assert c.pg.inner_text("#channelState").strip(), "сообщения нет"
    assert set(subscriptions_of(c.user)) == set(CHANNELS), "подписка появилась"


@check("P1", "Страница канала открывается с заголовком")
def p1(c):
    login(c.pg, c.user)
    c.pg.evaluate("openChannel('rybolos_channel')")
    c.pg.wait_for_selector("#viewChannel:not([hidden])", timeout=20000)
    assert c.pg.inner_text("#chTitle").strip(), "заголовка нет"


@check("P2", "Кнопка на странице канала меняет подписку")
def p2(c):
    login(c.pg, c.user)
    c.pg.evaluate("openChannel('rybolos_channel')")
    c.pg.wait_for_selector("#viewChannel:not([hidden])", timeout=20000)
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
    c.pg.evaluate("openChannel('rybolos_channel')")
    c.pg.wait_for_selector("#viewChannel:not([hidden])", timeout=20000)
    href = c.pg.get_attribute("#chTg", "href") or ""
    assert href.startswith("https://t.me/") and "rybolos_channel" in href, href


# ---------- E: устойчивость ----------

@check("E3", "Перезагрузка: коллекции остаются, фильтр ленты сбрасывается")
def e3(c):
    make_collection(c.user, "Новости", ["rybolos_channel"])
    login(c.pg, c.user)
    pick_collection(c.pg, "Новости")
    c.pg.reload()
    c.pg.wait_for_selector("#appShell:not([hidden])", timeout=30000)
    assert c.pg.inner_text("#tabsToggleLabel") == "Все каналы", "фильтр пережил перезагрузку"
    open_list(c.pg)
    assert c.pg.locator("#chanList .coll-row .chan-item", has_text="Новости").count() == 1, "коллекции пропали"


# ---------- X: доступность и размеры ----------

@check("X3", "Нажимаемые зоны не ниже 44 px на телефоне", device="phone")
def x3(c):
    login(c.pg, c.user)
    sizes = c.pg.evaluate("""() => [...document.querySelectorAll('#tabBar .tabbar-item, #readToggle, #unreadBtn')]
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
            if device_filter != "both" and device != device_filter:
                results.append((pid, title, "SKIP", "проверка для устройства %s" % device))
                continue
            phone = device == "phone"
            viewport = {"width": 420, "height": 900} if phone else {"width": 1280, "height": 860}
            ctx = browser.new_context(viewport=viewport, is_mobile=phone, has_touch=phone)
            pg = ctx.new_page()
            pg.set_default_timeout(20000)
            pg.on("dialog", lambda d: d.accept())
            try:
                user = new_user("ui" + pid.lower())
                fn(type("Ctx", (), {"pg": pg, "ctx": ctx, "user": user})())
                results.append((pid, title, "PASS", ""))
            except Skip as skipped:
                results.append((pid, title, "SKIP", str(skipped)))
            except Exception as failed:
                results.append((pid, title, "FAIL", str(failed).splitlines()[0][:200]))
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
    args = parser.parse_args()
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
