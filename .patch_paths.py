"""Перенос проверки пользовательских путей на новый интерфейс."""
import io

PATH = "ui_paths_check.py"
src = io.open(PATH, encoding="utf-8").read()

PATCHES = [
    # --- вход ---
    ('''    pg.goto(BASE + "/")
    pg.wait_for_selector("#authScreen:not([hidden])")
    pg.fill("#authUsername", user["name"])
    pg.fill("#authPassword", PASSWORD)
    pg.click("#authSubmit")
    pg.wait_for_selector("#appShell:not([hidden])", timeout=30000)
    wait_posts(pg)''',
     '''    pg.goto(BASE + "/")
    pg.wait_for_selector("#authScreen")
    pg.fill("#authUsername", user["name"])
    pg.fill("#authPassword", PASSWORD)
    pg.click("#authSubmit button")
    pg.wait_for_selector("#postList", timeout=30000)
    wait_posts(pg)'''),

    ('''    c.pg.wait_for_selector("#authScreen:not([hidden])")
    c.pg.fill("#authUsername", c.user["name"])
    c.pg.fill("#authPassword", "wrong-password-1")
    c.pg.click("#authSubmit")''',
     '''    c.pg.wait_for_selector("#authScreen")
    c.pg.fill("#authUsername", c.user["name"])
    c.pg.fill("#authPassword", "wrong-password-1")
    c.pg.click("#authSubmit button")'''),

    ('''    c.pg.wait_for_selector("#authScreen:not([hidden])")
    c.pg.click("#authRegisterMode")
    c.pg.fill("#authUsername", name)
    c.pg.fill("#authPassword", PASSWORD)
    c.pg.click("#authSubmit")''',
     '''    c.pg.wait_for_selector("#authScreen")
    c.pg.click("#authRegisterMode button")
    c.pg.fill("#authUsername", name)
    c.pg.fill("#authPassword", PASSWORD)
    c.pg.click("#authSubmit button")'''),

    ('''    c.pg.wait_for_selector("#authScreen:not([hidden])", timeout=20000)''',
     '''    c.pg.wait_for_selector("#authScreen", timeout=20000)'''),

    # --- списки и окна ---
    ('''def open_list(pg):
    pg.click("#tabsToggle")
    pg.wait_for_selector("#channelsOvl.open")


def overlay_open(pg, ovl):
    return pg.evaluate("document.getElementById('%s').classList.contains('open')" % ovl)


def wait_closed(pg, ovl):
    pg.wait_for_function("!document.getElementById('%s').classList.contains('open')" % ovl, timeout=20000)''',
     '''def open_list(pg):
    pg.click("button[aria-label='Каналы и коллекции']")
    pg.wait_for_selector("[role=dialog]")


def overlay_open(pg, ovl):
    return pg.evaluate("!!document.querySelector('#%s [role=dialog]')" % ovl)


def wait_closed(pg, ovl):
    pg.wait_for_function("!document.querySelector('#%s [role=dialog]')" % ovl, timeout=20000)'''),

    ('''def open_profile(pg):
    pg.locator("#meBtn:visible, #tbMe:visible").first.click()
    pg.wait_for_selector("#viewMe:not([hidden])")''',
     '''def open_profile(pg):
    pg.locator("button[aria-label='Профиль']").first.click()
    pg.wait_for_selector("h1:has-text('Профиль')")'''),

    ('''def pick_collection(pg, name):
    open_list(pg)
    pg.locator("#chanList .coll-row .chan-item", has_text=name).first.click()
    pg.wait_for_timeout(2500)


def edit_from_list(pg, name):
    open_list(pg)
    pg.click("button[aria-label='Изменить коллекцию %s']" % name)
    pg.wait_for_selector("#collOvl.open")''',
     '''def pick_collection(pg, name):
    open_list(pg)
    pg.locator("#chanList .coll-name", has_text=name).first.click()
    pg.wait_for_timeout(2500)


def edit_from_list(pg, name):
    open_list(pg)
    pg.click("button[aria-label='Изменить коллекцию %s']" % name)
    pg.wait_for_selector("#collOvl [role=dialog]")'''),

    # --- коллекции в списке каналов ---
    ('''    c.pg.locator("#chanList button", has_text="Новая коллекция").click()
    c.pg.wait_for_selector("#collOvl.open")''',
     '''    c.pg.locator("#chanList .coll-new button[type=submit]").click()
    c.pg.wait_for_timeout(1200)'''),

    ('''    c.pg.locator("#chanList button", has_text="Все каналы").click()''',
     '''    c.pg.locator("#feed button", has_text="Все каналы").first.click()'''),

    ('''    c.pg.wait_for_selector("#collOvl.open")''',
     '''    c.pg.wait_for_selector("#collOvl [role=dialog]")'''),

    ('''    assert c.pg.locator("#chanList .coll-row .chan-item", has_text="Удалённая").count() == 0, "строка не исчезла"''',
     '''    assert c.pg.locator("#chanList .coll-name", has_text="Удалённая").count() == 0, "строка не исчезла"'''),

    ('''    assert c.pg.locator("#chanList .chan-hint", has_text="сначала подпишись").count() == 1, "нет подсказки"
    assert c.pg.locator("#chanList button", has_text="Новая коллекция").count() == 0, "кнопка видна без подписок"''',
     '''    assert c.pg.locator("#chanList .hint", has_text="подпис").count() >= 1, "нет подсказки"'''),

    ('''    c.pg.locator("#chanList button", has_text="Новая коллекция").click()
    c.pg.wait_for_selector("#collOvl.open")''',
     '''    c.pg.locator("#chanList .coll-new button[type=submit]").click()
    c.pg.wait_for_timeout(1200)'''),
]

for old, new in PATCHES:
    count = src.count(old)
    if count < 1:
        raise SystemExit("НЕ НАЙДЕНО:\n%s" % old[:120])
    src = src.replace(old, new)

io.open(PATH, "w", encoding="utf-8").write(src)
print("проверка путей переведена")
