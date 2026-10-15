FROM python:3.12-slim

WORKDIR /app

# Сервис использует только стандартную библиотеку — зависимостей нет.
COPY app.py index.html ./

ENV PORT=8000
EXPOSE 8000

# SQLite создаётся от непривилегированного пользователя, иначе volume будет root-owned.
RUN useradd -m -u 1000 app && mkdir -p /data && chown -R app:app /app /data
USER app

VOLUME ["/data"]
ENV DATA_DIR=/data

CMD ["python", "-u", "app.py"]