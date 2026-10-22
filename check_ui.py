#!/usr/bin/env python3
"""Проверка целостности интерфейса без браузера.

Интерфейс теперь собирается: исходники в ui/src, готовые файлы в ui/dist.
Проверка ловит то, что не видно до первого запроса в браузере: несуществующий
компонент, иконку без пути, цвет вне токенов, отступ вне шкалы, ссылку на
внешний скрипт.

    python3 check_ui.py

Ни одного аргумента: файлы читаются рядом с собой.
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(BASE, "ui")
SRC = os.path.join(UI, "src")
DIST = os.path.join(UI, "dist")

problems = []


def fail(message):
    problems.append(message)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def svelte_files():
    found = []
    for root, _dirs, files in os.walk(SRC):
        for name in sorted(files):
            if name.endswith(".svelte") or name.endswith(".js"):
                found.append(os.path.join(root, name))
    return sorted(found)


# --- исходники на месте ---

for needed in ("package.json", "vite.config.js", "tailwind.config.js", "index.html"):
    if not os.path.exists(os.path.join(UI, needed)):
        fail("НЕТ ФАЙЛА: ui/%s" % needed)
for needed in ("main.js", "App.svelte", "app.css"):
    if not os.path.exists(os.path.join(SRC, needed)):
        fail("НЕТ ФАЙЛА: ui/src/%s" % needed)

if problems:
    for line in problems:
        print(line)
    sys.exit(1)

files = svelte_files()
sources = {path: read(path) for path in files}

# --- каждый импорт с относительным путём ведёт в существующий файл ---

IMPORT = re.compile(r"""(?:from|import)\s+['"](\.[^'"]+)['"]""")
for path, text in sources.items():
    for target in IMPORT.findall(text):
        if target.endswith(".css"):
            continue
        resolved = os.path.normpath(os.path.join(os.path.dirname(path), target))
        if not any(resolved == other or resolved + ".svelte" == other
                   or resolved + ".js" == other for other in sources):
            fail("НЕТ ФАЙЛА: %s импортирует %s" % (os.path.relpath(path, BASE), target))

# --- каждая иконка нарисована ---

icon_source = sources.get(os.path.join(SRC, "components", "Icon.svelte"), "")
icon_names = set(re.findall(r"^\s{4}([a-z]+):\s*'", icon_source, re.M))
if not icon_names:
    fail("ИКОНКИ: в Icon.svelte нет ни одной иконки")
used = set()
for path, text in sources.items():
    if path.endswith("Icon.svelte"):
        continue
    used.update(re.findall(r'<Icon\s+name="([a-z]+)"', text))
    used.update(re.findall(r"icon:\s*'([a-z]+)'", text))
for name in sorted(used - icon_names):
    fail("ИКОНКА БЕЗ ПУТИ: %s" % name)
print("иконок нарисовано: %d, использовано: %d" % (len(icon_names), len(used)))

# --- цвет приходит из токенов кита, а не из своего ---

# Свой цвет в разметке ломает тему: палитра перестаёт переключаться.
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
for path, text in sources.items():
    if path.endswith(("Icon.svelte",)):
        continue
    for line in text.splitlines():
        code = line.split("//")[0]
        if HEX.search(code):
            fail("СВОЙ ЦВЕТ: %s: %s" % (os.path.relpath(path, BASE), line.strip()[:90]))
print("своих цветов нет: цвета только из токенов кита")

# --- отступы на шкале кита ---

SCALE = {2, 4, 6, 8, 10, 12, 16, 20, 24, 32}
SPACING = re.compile(r"(?<![\w-])(?:padding|margin|gap)(?:-(?:top|right|bottom|left))?\s*:\s*([^;{}]*)")
for path, text in sources.items():
    for value in SPACING.findall(text):
        for px in re.findall(r"(?<![\w.-])(\d+)px", value):
            if int(px) not in SCALE:
                fail("ОТСТУП ВНЕ ШКАЛЫ: %s: %spx" % (os.path.relpath(path, BASE), px))
print("отступы на шкале кита")

# --- токены кита существуют ---

# Несуществующий токен не выдаёт ошибку: браузер молча рисует прозрачный фон
# или цвет по умолчанию. Нижняя панель разделов так осталась без подложки.
KIT_DIR = os.path.join(UI, "node_modules", "@monos", "ui", "src", "styles")
kit_tokens = set()
for name in ("tokens.css", "themes.css", "base.css"):
    kit_path = os.path.join(KIT_DIR, name)
    if not os.path.exists(kit_path):
        continue
    kit_tokens |= set(re.findall(r"(--ui-[a-z0-9-]+)\s*:", read(kit_path)))
used_tokens = {}
for path, text in sources.items():
    for token in re.findall(r"var\((--ui-[a-z0-9-]+)", text):
        used_tokens.setdefault(token, os.path.relpath(path, BASE))
for token in sorted(set(used_tokens) - kit_tokens):
    fail("НЕТ ТОКЕНА: %s: %s" % (token, used_tokens[token]))
print("токены кита на месте: %d" % len(used_tokens))

# --- стили кита подключены ---

kit_styles = any("@monos/ui/styles.css" in text for text in sources.values())
if not kit_styles:
    fail("НЕТ СТИЛЕЙ КИТА: ни один файл не подключает @monos/ui/styles.css")
print("стили кита подключены")

# --- внешних скриптов нет ---

entry = read(os.path.join(UI, "index.html"))
if re.search(r"<script[^>]+src=[\"']https?://", entry):
    fail("ВНЕШНИЙ СКРИПТ: сборка не нужна, а скрипт подключается извне")
if re.search(r"<link[^>]+href=[\"']https?://", entry):
    fail("ВНЕШНИЙ СКРИПТ: внешняя таблица стилей")
if "cdn" in entry:
    fail("ВНЕШНИЙ СКРИПТ: ссылка на cdn")
print("внешних скриптов нет")

# --- палитра выбирается атрибутом, как это делает ки�� ---

if "data-theme" not in entry or "data-mode" not in entry:
    fail("НЕТ ТЕМЫ: в разметке нет data-theme и data-mode")
print("тема и светлота выбираются атрибутом")

# --- собранный вывод ---

if os.path.isdir(DIST):
    built_index = os.path.join(DIST, "index.html")
    if not os.path.exists(built_index):
        fail("СБОРКА: в ui/dist нет index.html")
    else:
        page = read(built_index)
        assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', page)
        if not assets:
            fail("СБОРКА: собранный index.html не ссылается на файлы сборки")
        for asset in assets:
            if not os.path.exists(os.path.join(DIST, asset.lstrip("/"))):
                fail("СБОРКА: нет файла %s" % asset)
        if "<script>" in page and "type=\"module\"" not in page:
            fail("СБОРКА: в собранной странице есть обычный скрипт")
        print("сборка на месте: %d файлов в assets" % len(assets))
else:
    print("сборки нет: ui/dist её соберёт npm run build")

# --- служебные скрипты должны быть хотя бы синтаксически целы ---
# Они не входят в тесты, и опечатка в них всплыла бы только на стенде.
import py_compile
import tempfile

for script in ("ui_paths_check.py", "audit_media.py", "app.py", "tg.py"):
    path = os.path.join(BASE, script)
    if not os.path.exists(path):
        continue
    try:
        py_compile.compile(path, doraise=True,
                           cfile=os.path.join(tempfile.gettempdir(), script + "c"))
    except py_compile.PyCompileError as error:
        fail("СИНТАКСИС: %s: %s" % (script, str(error).splitlines()[-1][:120]))
print("служебные скрипты собираются")

if problems:
    print()
    for line in problems:
        print(line)
    sys.exit(1)

print("\nвсё в порядке")
