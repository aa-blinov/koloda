"""Шаги проверки путей под новые сценарии: коллекции, галерея, категории."""
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

    # --- C3: «Все каналы» без списка поверх ленты ---
    ("""    pick_collection(c.pg, "Новости")
    open_list(c.pg)
    c.pg.locator("#feed button", has_text="Все каналы").first.click()""",
     """    pick_collection(c.pg, "Новости")
    c.pg.locator("#feed button", has_text="Все каналы").first.click()"""),

    # --- C5: правка коллекции из списка ---
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

    # --- C7: удаление коллекции ---
    ("""    pick_collection(c.pg, "Новости")
    c.pg.locator("button[aria-label^='Изменить коллекцию']").first.click()
    c.pg.wait_for_selector("#collOvl [role=dialog]")
    c.pg.click("#collDelete button")""",
     """    pick_collection(c.pg, "Новости")
    open_list(c.pg)
    c.pg.locator("button[aria-label^='Изменить коллекцию']").first.click()
    c.pg.wait_for_selector("#collOvl [role=dialog]")
    c.pg.click("#collDelete button")"""),

    # --- C8: дубль имени сообщается в списке ---
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
    c.pg.wait_for_timeout(2500)
    assert "уже есть" in c.pg.inner_text("#collListMsg"), c.pg.inner_text("#collListMsg")
    assert c.pg.locator("[role=dialog]").count() == 1, "список закрылся\""""),

    # --- C11: устаревший редактор ---
    ("""    api("/api/collections/delete", {"id": collection_id}, c.user["cookie"])
    c.pg.click("button[aria-label='Изменить коллекцию Удалённая']")""",
     """    api("/api/collections/delete", {"id": collection_id}, c.user["cookie"])
    open_list(c.pg)
    c.pg.click("button[aria-label='Изменить коллекцию Удалённая']")"""),

    # --- C18: кнопка правки есть только когда есть коллекция ---
    ("""    assert c.pg.locator("#chanList .coll-name").count() == 0, "кнопка видна без выбранной коллекции"
    pick_collection(c.pg, "Новости")
    assert c.pg.locator("button[aria-label^='Изменить коллекцию']").count() >= 1, "кнопки нет при выбранной коллекции\"""",
     """    open_list(c.pg)
    assert c.pg.locator("#chanList .coll-name").count() == 0, "кнопка видна без выбранной коллекции"
    pick_collection(c.pg, "Новости")
    open_list(c.pg)
    assert c.pg.locator("button[aria-label^='Изменить коллекцию']").count() >= 1, "кнопки нет при выбранной коллекции\""""),

    # --- C20/C21: окно коллекций канала ---
    ("""    wait_closed(c.pg, "chanCollOvl")""", """    wait_closed(c.pg, "collOvl")"""),

    # --- C22: коллекция с каналом создаётся в списке, а канал отмечается со страницы канала ---
    ("""    c.pg.fill("#chanCollNew", "С канала")
    c.pg.click("#chanCollAdd button")
    c.pg.wait_for_timeout(2500)
    created = [x for x in collections_of(c.user) if x["name"] == "С канала"]
    assert created and created[0]["channels"] == ["rybolos_channel"], created""",
     """    # коллекцию создают в списке каналов, а канал отмечают отсюда
    open_list(c.pg)
    c.pg.fill("#chanList .coll-new input", "С канала")
    c.pg.locator("#chanList .coll-new button[type=submit]").click()
    c.pg.wait_for_timeout(2500)
    created = [x for x in collections_of(c.user) if x["name"] == "С канала"]
    assert created, "коллекция не создалась"
    c.pg.evaluate("location.hash = '#/channel/rybolos_channel'")
    c.pg.wait_for_selector("#viewChannel", timeout=20000)
    c.pg.click("#chColl button")
    c.pg.wait_for_selector("#collOvl [role=dialog]")
    c.pg.locator("#chanCollList input[value='%s']" % created[0]["id"]).check()
    c.pg.wait_for_timeout(2000)
    after = [x for x in collections_of(c.user) if x["name"] == "С канала"]
    assert after and after[0]["channels"] == ["rybolos_channel"], after"""),

    # --- F12: отписка прямо из списка ---
    ("""    open_list(c.pg)
    c.pg.locator("#chanList .coll-name").first.click()
    c.pg.wait_for_timeout(2500)
    c.pg.locator("#chanList .unsub-btn").first.click()""",
     """    open_list(c.pg)
    c.pg.locator("#chanList .unsub-btn").first.click()"""),

    # --- F15/F16: галерея и просмотрщик ---
    ("""    post = c.pg.locator("#postList .post").filter(has=c.pg.locator(".gal > img")).first""",
     """    post = c.pg.locator("#postList .post").filter(has=c.pg.locator(".gal-frame img")).first"""),
    ("""    post.locator(".gal > img").first.click()
    c.pg.wait_for_selector("#lightbox.open", timeout=10000)""",
     """    post.locator(".gal-frame img").first.click()
    c.pg.wait_for_selector("#collOvl [role=dialog], [role=dialog]", timeout=10000)"""),
    ("""    c.pg.locator("#postList .post").nth(index).locator(".gal > img").first.click()
    c.pg.wait_for_selector("#lightbox.open", timeout=10000)""",
     """    c.pg.locator("#postList .post").nth(index).locator(".gal-frame img").first.click()
    c.pg.wait_for_selector("[role=dialog]", timeout=10000)"""),
    ("""                          "e => e.querySelectorAll('.gal > *').length >= 3 && e.querySelector('.gal > img'))")""",
     """                          "e => e.querySelectorAll('.gal > *').length >= 3 && e.querySelector('.gal-frame img'))")"""),

    # --- F17: протухший кадр в новой разметке ---
    ("""    state = c.pg.evaluate(\"\"\"() => {
        const imgs = [...document.querySelectorAll('#feed .gal img')];
        const waiting = imgs.filter(i => i.dataset.fresh && !i.getAttribute('src'));
        const broken = imgs.filter(i => i.getAttribute('src') && i.complete && i.naturalWidth === 0);
        return {waiting: waiting.length, emptyAlt: waiting.filter(i => i.alt === '').length, broken: broken.length};
    }\"\"\")
    assert state["waiting"] >= 1 and state["emptyAlt"] == state["waiting"], "во время ожидания: %s" % state
    assert state["broken"] == 0, "битая картинка видна во время ожидания: %s" % state""",
     """    state = c.pg.evaluate(\"\"\"() => {
        const imgs = [...document.querySelectorAll('#feed .gal img')];
        const waiting = imgs.filter(i => !i.getAttribute('src'));
        const broken = imgs.filter(i => i.getAttribute('src') && i.complete && i.naturalWidth === 0);
        return {waiting: waiting.length, broken: broken.length};
    }\"\"\")
    assert state["waiting"] >= 1, "протухший кадр не очищен: %s" % state
    assert state["broken"] == 0, "битая картинка видна во время ожидания: %s" % state"""),
    ("""    frames = c.pg.locator("#feed .gal img[data-fresh]")""",
     """    frames = c.pg.locator("#feed .gal img")"""),

    # --- K10: категории ---
    ("""    c.pg.reload()
    c.pg.wait_for_selector("#postList", timeout=30000)
    c.pg.click("#tabCat:visible, #tbCat:visible")
    c.pg.wait_for_selector("#catRetry", timeout=20000)""",
     """    c.pg.reload()
    c.pg.wait_for_selector("#postList", timeout=30000)
    c.pg.get_by_role("button", name="Каталог", exact=True).first.click()
    c.pg.wait_for_selector("#catRetry", timeout=20000)"""),

    # --- X3: зоны нажатия ---
    ("""    sizes = c.pg.evaluate(\"\"\"() => [...document.querySelectorAll('#tabBar .tabbar-item, #readToggle')]
        .filter(e => e.offsetParent !== null).map(e => [e.id || e.textContent.trim().slice(0, 12),
        Math.round(e.getBoundingClientRect().height)])\"\"\")""",
     """    sizes = c.pg.evaluate(\"\"\"() => [...document.querySelectorAll('header button, #readToggle button, .feed-bar button')]
        .filter(e => e.offsetParent !== null).map(e => [e.id || e.textContent.trim().slice(0, 12),
        Math.round(e.getBoundingClientRect().height)])\"\"\")"""),
]

for old, new in PATCHES:
    count = src.count(old)
    if count != 1:
        raise SystemExit("СОВПАДЕНИЕ %d (нужно 1):\n%s" % (count, old[:150]))
    src = src.replace(old, new, 1)

io.open(PATH, "w", encoding="utf-8").write(src)
print("шаги перенесены")
