FROM node:26-bookworm-slim AS ui

# Интерфейс собирается на node: Svelte 4, Vite и ки�� Monos.
# Сборка идёт в отдельной стадии, поэтому в итоговый образ попадает только
# готовый каталог ui/dist, а node и зависимости остаются позади.
WORKDIR /build

# Сначала только манифесты: слой с зависимостями пересобирается, когда меняется
# манифест, а не когда меняется исходник.
COPY ui/package.json ui/package-lock.json ./ui/
RUN cd ui && npm ci

COPY ui ./ui
RUN cd ui && npm run build


FROM python:3.12-slim

WORKDIR /app

# Telethon нужен только для входа в Telegram и чтения приватных каналов: без
# подключённого профиля модуль не импортируется и публичная лента работает как
# раньше. Версия зафиксирована, чтобы образ собирался одинаково.
RUN pip install --no-cache-dir telethon==1.45.0

# Сервис использует только стандартную библиотеку — кроме Telethon выше.
COPY app.py tg.py qrsvg.py qrcodegen.py ./
# Собранный интерфейс: сервер только раздаёт эти файлы.
COPY --from=ui /build/ui/dist ./ui/dist
# Шрифт JetBrains Mono отдаётся с нашего адреса, поэтому файлы едут в образ вместе с кодом

ENV PORT=8000
EXPOSE 8000

# SQLite создаётся от непривилегированного пользователя, иначе volume будет root-owned.
# Каталог tg нужен файлам сессии Telegram: он создаётся с правами 0700, а внутри
# файлы сессии лежат под 0600 — это готовый вход в аккаунт, а не пароль.
RUN useradd -m -u 1000 app && mkdir -p /data/tg && chown -R app:app /app /data \
    && chmod 700 /data/tg
USER app

VOLUME ["/data"]
ENV DATA_DIR=/data

CMD ["python", "-u", "app.py"]
