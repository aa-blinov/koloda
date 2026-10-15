#!/usr/bin/env python3
"""Проверка целостности index.html без браузера.

Ловит класс ошибок, из-за которого интерфейс молча ломается: скрипт ссылается
на id, которого нет в разметке, или использует класс без соответствующих
стилей. Оба случая не дают ошибок в консоли — просто ничего не работает.

Запуск: python3 check_ui.py
"""
import re
import sys

HTML = open(__file__.replace("check_ui.py", "index.html"), encoding="utf-8").read()

# --- id, на которые ссылается скрипт: $('#foo') и getElementById('foo') ---
used = set(re.findall(r"\$\('#([\w-]+)'\)", HTML))
used |= set(re.findall(r"getElementById\('([\w-]+)'\)", HTML))

# --- id, объявленные в разметке: id="foo" (в том числе в разметке внутри <script>) ---
declared = set(re.findall(r'id="([\w-]+)"', HTML))

missing = sorted(used - declared)
if missing:
    print("НЕТ В РАЗМЕТКЕ:", ", ".join(missing))
    sys.exit(1)
print(f"id: {len(used)} используется, все найдены")

# --- классы, которые скрипт вешает через classList.add / className ---
toggled = set(re.findall(r"classList\.(?:add|remove)\('([\w-]+)'\)", HTML))
css_classes = set(re.findall(r"\.([a-zA-Z][\w-]*)\s*[,{:]", HTML))

# classList.add('open') — это состояние, а не имя класса в разметке
missing_css = sorted(c for c in toggled if c not in css_classes)
if missing_css:
    print("КЛАСС БЕЗ СТИЛЕЙ:", ", ".join(missing_css))
    sys.exit(1)
print(f"классы-переключатели: {len(toggled)}, у всех есть стили")

# --- оверлеи обязаны быть position:fixed, иначе раздвигают страницу ---
overlays = re.findall(r'<div class="ov[^"]*" id="([\w-]+)"', HTML)
fixed = len(re.findall(r"\.ov\s*\{[^}]*position:\s*fixed", HTML))
if overlays and not fixed:
    print("ОВЕРЛЕЙ БЕЗ position:fixed — раздвинет страницу")
    sys.exit(1)
print(f"оверлеи: {len(overlays)}, position:fixed есть")

# --- внешние скрипты не должны появляться: сервис работает без сборки ---
if re.search(r"<script[^>]+src=", HTML):
    print("ВНЕШНИЙ СКРИПТ — сборка не нужна, но он подключается извне")
    sys.exit(1)
print("внешних скриптов нет")

print("\nвсё в порядке")