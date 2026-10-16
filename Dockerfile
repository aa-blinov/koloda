FROM python:3.12-slim

WORKDIR /app

# Telethon нужен только для входа в Telegram и чтения приватных каналов: без
# подключённого профиля модуль не импортируется и публичная лента работает как
# раньше. Версия зафиксирована, чтобы образ собирался одинаково.
RUN pip install --no-cache-dir telethon==1.45.0

# Сервис использует только стандартную библиотеку — кроме Telethon выше.
COPY app.py index.html tg.py ./

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