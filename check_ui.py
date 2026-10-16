#!/usr/bin/env python3
"""Проверка целостности index.html без браузера.

Ловит класс ошибок, из-за которого интерфейс молча ломается: скрипт ссылается
на id, которого нет в разметке, или использует класс без соответствующих
стилей. В обоих случаях в консоли нет ошибок: просто ничего не работает.

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

# classList.add('open') означает состояние, а не имя класса в разметке
missing_css = sorted(c for c in toggled if c not in css_classes)
if missing_css:
    print("КЛАСС БЕЗ СТИЛЕЙ:", ", ".join(missing_css))
    sys.exit(1)
print(f"классы-переключатели: {len(toggled)}, у всех есть стили")

# --- оверлеи обязаны быть position:fixed, иначе раздвигают страницу ---
overlays = re.findall(r'<div class="ov[^"]*" id="([\w-]+)"', HTML)
fixed = len(re.findall(r"\.ov\s*\{[^}]*position:\s*fixed", HTML))
if overlays and not fixed:
    print("ОВЕРЛЕЙ БЕЗ position:fixed, раздвинет страницу")
    sys.exit(1)
print(f"оверлеи: {len(overlays)}, position:fixed есть")

# --- режимы формы: setAuthMode обязан знать каждый режим, в который его зовут.
#     Вызов с незнакомым именем не падает: форма молча остаётся в прежнем виде.
auth_mode = re.search(r"function setAuthMode\(mode\)\{.*?\n\}", HTML, re.S)
if not auth_mode:
    print("НЕТ ФУНКЦИИ setAuthMode")
    sys.exit(1)
auth_mode = auth_mode.group(0)
known = set(re.findall(r"=mode==='(\w+)'", auth_mode))
unknown = sorted(set(re.findall(r"setAuthMode\('(\w+)'\)", HTML)) - known)
if unknown:
    print("НЕИЗВЕСТНЫЙ РЕЖИМ ФОРМЫ:", ", ".join(unknown))
    sys.exit(1)

# --- кнопка отправки: у неё своя надпись для части режимов, и прятать её можно
#     только там, где надписи нет. Иначе режим остаётся с текстом «нажми кнопку»
#     и без самой кнопки — как было с подтверждением почты.
label_expr = re.search(r"\$\('#authSubmit'\)\.textContent=([^;]+);", HTML)
hidden_expr = re.search(r"\$\('#authSubmit'\)\.hidden=([^;]+);", HTML)
if not label_expr or not hidden_expr:
    print("КНОПКЕ ОТПРАВКИ НЕТ НАДПИСИ ИЛИ СКРЫТИЯ")
    sys.exit(1)
labeled = set(re.findall(r"(\w+)\?'[^']*'", label_expr.group(1)))
hid = {flag.strip() for flag in hidden_expr.group(1).split("||")}
lost = sorted(labeled & hid)
if lost:
    print("КНОПКА СПРЯТАНА ТАМ, ГДЕ У НЕЁ ЕСТЬ НАДПИСЬ:", ", ".join(lost))
    sys.exit(1)
print(f"режимы формы: {len(known)}, все вызовы известны; "
      f"кнопка на месте во всех {len(labeled)} режимах с надписью")

# --- внешние скрипты не должны появляться: сервис работает без сборки ---
if re.search(r"<script[^>]+src=", HTML):
    print("ВНЕШНИЙ СКРИПТ: сборка не нужна, но он подключается извне")
    sys.exit(1)
print("внешних скриптов нет")

print("\nвсё в порядке")