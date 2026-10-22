"""Перенос шагов проверки путей на новые сценарии интерфейса."""
import io

PATH = "ui_paths_check.py"
src = io.open(PATH, encoding="utf-8").read()

PATCHES = [
    # --- A4: выход с подтверждением ---
    ("""    c.pg.get_by_role("button", name="Выйти из профиля").first.click()
    c.pg.wait_for_timeout(300)
    c.pg.get_by_role("button", name="Выйти из профиля").first.click()
    c.pg.wait_for_selector("#authScreen", timeout=20000)""",
     """    c.pg.get_by_role("button", name="Выйти из профиля").first.click()
    c.pg.wait_for_timeout(300)
    # выход подтверждается вторым нажатием
    c.pg.get_by_role("button", name="Точно выйти?").first.click()
    c.pg.wait_for_selector("#authScreen", timeout=20000)"""),

    # --- C1: создание коллекции в списке, состав — в редакторе ---
    ("""    c.pg.fill("#chanList .coll-new input", "Новости")
    c.pg.locator("#chanList .coll-new button[type=submit]").click()
    c.pg.wait_for_selector("#collOvl [role=dialog]")
    assert c.pg.locator("#collPick input[type=checkbox]").count() == len(CHANNELS), "в редакторе не все подписки"
    c.pg.fill("#collName", "Новости")
    c.pg.locator("#collPick input[value=rybolos_channel]").check()
    c.pg.locator("#collPick input[value=evilfreelancer]").check()
    c.pg.click("#collSave button")
    wait_closed(c.pg, "collOvl")
    c.pg.wait_for_timeout(3000)
    assert c.pg.inner_text("#tabsToggleLabel") == "Новости", c.pg.inner_text("#tabsToggleLabel")""",
     """    c.pg.fill("#chanList .coll-new input", "Новости")
    c.pg.locator("#chanList .coll-new button[type=submit]").click()
    c.pg.wait_for_timeout(2500)
    assert c.pg.locator("#chanList .coll-name", has_text="Новости").count() == 1, "коллекция не создалась"
    # состав коллекции задаётся в редакторе
    c.pg.click("button[aria-label='Изменить коллекцию Новости']")
    c.pg.wait_for_selector("#collOvl [role=dialog]")
    assert c.pg.locator("#collPick input[type=checkbox]").count() == len(CHANNELS), "в редакторе не все подписки"
    c.pg.locator("#collPick input[value=rybolos_channel]").check()
    c.pg.locator("#collPick input[value=evilfreelancer]").check()
    c.pg.click("#collSave button")
    wait_closed(c.pg, "collOvl")
    c.pg.wait_for_timeout(2000)
    # открыть коллекцию: лента показывает только её каналы
    c.pg.locator("#chanList .coll-name", has_text="Новости").first.click()
    c.pg.wait_for_timeout(3500)
    assert c.pg.inner_text("#tabsToggleLabel") == "Новости", c.pg.inner_text("#tabsToggleLabel")"""),

    # --- C3: «Все каналы» без открытого списка поверх ленты ---
    ("""    pick_collection(c.pg, "Новости")
    open_list(c.pg)
    c.pg.locator("#feed button", has_text="Все каналы").first.click()""",
     """    pick_collection(c.pg, "Новости")
    c.pg.locator("#feed button", has_text="Все каналы").first.click()"""),

    # --- C5: правка коллекции из списка, лента перечитывается сама ---
    ("""    pick_collection(c.pg, "Новости")
    c.pg.locator("button[aria-label^='Изменить коллекцию']").first.click()
    c.pg.wait_for_selector("#collOvl [role=dialog]")
    c.pg.locator("#collPick input[value=evilfreelancer]").uncheck()
    c.pg.click("#collSave button")
    wait_closed(c.pg, "collOvl")
    c.pg.wait_for_timeout(4000)
    assert set(feed_channels(c.pg)) <= {"rybolos_channel"}, feed_channels(c.pg)""",
     """    pick_collection(c.pg, "Новости")
    open_list(c.pg)
    c.pg.locator("button[aria-label^='Изменить коллекцию']").first.click()
    c.pg.wait_for_selector("#collOvl [role=dialog]")
    c.pg.locator("#collPick input[value=evilfreelancer]").uncheck()
    c.pg.click("#collSave button")
    wait_closed(c.pg, "collOvl")
    c.pg.wait_for_timeout(4000)
    assert set(feed_channels(c.pg)) <= {"rybolos_channel"}, feed_channels(c.pg)"""),

    # --- C7: удаление из редактора, лента возвращается к «Все каналы» ---
    ("""    pick_collection(c.pg, "Новости")
    c.pg.locator("button[aria-label^='Изменить коллекцию']").first.click()
    c.pg.wait_for_selector("#collOvl [role=dialog]")
    c.pg.click("#collDelete button")""",
     """    pick_collection(c.pg, "Новости")
    open_list(c.pg)
    c.pg.locator("button[aria-label^='Изменить коллекцию']").first.click()
    c.pg.wait_for_selector("#collOvl [role=dialog]")
    c.pg.click("#collDelete button")"""),

    # --- C8: дубль имени сообщается в списке, список остаётся открытым ---
    ("""    c.pg.fill("#chanList .coll-new input", "Новости")
    c.pg.locator("#chanList .coll-new button[type=submit]").click()
    c.pg.wait_for_selector("#collOvl [role=dialog]")
    c.pg.fill("#collName", "  НОВОСТИ ")
    c.pg.click("#collSave button")
    c.pg.wait_for_timeout(1500)
    assert "уже есть" in c.pg.inner_text("#collMsg"), c.pg.inner_text("#collMsg")
    assert overlay_open(c.pg, "collOvl"), "редактор закрылся\"""",
     """    c.pg.fill("#chanList .coll-new input", "  НОВОСТИ ")
    c.pg.locator("#chanList .coll-new button[type=submit]").click()
    c.pg.wait_for_timeout(2000)
    assert "уже есть" in c.pg.inner_text("#collListMsg"), c.pg.inner_text("#collListMsg")
    assert c.pg.locator("[role=dialog]").count() == 1, "список закрылся\""""),

    # --- C18: кнопка правки есть, когда есть коллекция ---
    ("""    assert not c.pg.locator("#collEditBtn").is_visible(), "кнопка видна без выбранной коллекции\"""",
     """    assert c.pg.locator("button[aria-label^='Изменить коллекцию']").count() == 0, "кнопка есть без коллекций\""""),
    ("""    assert c.pg.locator("#collEditBtn").is_visible(), "кнопки нет при выбранной коллекции\"""",
     """    assert c.pg.locator("button[aria-label^='Изменить коллекцию']").count() >= 1, "кнопки нет при выбранной коллекции\""""),

    # --- V1/V2: перед перезагрузкой возвращаемся в ленту ---
    ("""    c.pg.reload()
    c.pg.wait_for_selector("#postList", timeout=30000)
    assert c.pg.evaluate("document.documentElement.dataset.modePref") == "light", "после перезагрузки тема слетела\"""",
     """    c.pg.get_by_role("button", name="Лента", exact=True).first.click()
    c.pg.wait_for_timeout(2500)
    c.pg.reload()
    c.pg.wait_for_selector("#postList", timeout=30000)
    assert c.pg.evaluate("document.documentElement.dataset.modePref") == "light", "после перезагрузки тема слетела\""""),
    ("""    c.pg.get_by_role("button", name="С засечками").first.click()
    c.pg.wait_for_timeout(1500)
    c.pg.reload()""",
     """    c.pg.get_by_role("button", name="С засечками").first.click()
    c.pg.wait_for_timeout(1500)
    c.pg.get_by_role("button", name="Лента", exact=True).first.click()
    c.pg.wait_for_timeout(2500)
    c.pg.reload()"""),
]

for old, new in PATCHES:
    count = src.count(old)
    if count != 1:
        raise SystemExit("СОВПАДЕНИЕ %d (нужно 1):\n%s" % (count, old[:160]))
    src = src.replace(old, new, 1)

io.open(PATH, "w", encoding="utf-8").write(src)
print("шаги проверки путей перенесены")
