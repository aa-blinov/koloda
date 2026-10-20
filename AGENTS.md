# AGENTS.md — agent context for `koloda`

Koloda is a web reader for public Telegram channels. One feed, one SQLite file,
one Python process. It reads the public preview pages of t.me, so a Telegram
account is not needed, and every profile keeps its own subscriptions and its own
read marks.

This file holds what a session cannot work out from the code: the commands, the
layout, and the traps that cost a rebuild to learn.

## Run / build / test

```bash
docker compose up -d --build    # build the image and start the service on http://127.0.0.1:8090
docker compose logs -f           # the log
docker compose down              # stop
python3 -m unittest -v           # the tests: no network, no Telegram, a temporary database
python3 check_ui.py              # the integrity of index.html, without a browser
docker build -t koloda:ci .      # the image builds
python3 app.py 8090              # run it without Docker, on an empty data/feed.db
```

The port is 8000 inside the container and 8090 on the host, because 8000 is
taken by other processes on that machine. The port of the process itself: the
argument of the command line first, then `$PORT`, then 8000.

The same three commands run in GitHub Actions on every push to `master` and on
every pull request: `python3 -m unittest -v`, `python3 check_ui.py` and
`docker build`.

The audit of the media runs against the live data and needs the internet:

```bash
docker compose exec -T feed python3 -c "import sqlite3;s=sqlite3.connect('/data/feed.db');d=sqlite3.connect('/tmp/feed.db');s.backup(d);d.close();s.close()" && docker compose cp feed:/tmp/feed.db ./feed.db && docker compose exec -T feed rm /tmp/feed.db && python3 audit_media.py
```

The request log is off by default: `LENTELE_LOG=1 docker compose up -d`.

## Where things live

| Path | What it holds |
| --- | --- |
| `app.py` | The whole server: the parsing of `t.me/s/<channel>`, the schema and the migrations, the profiles, the sessions, the letters, the serving of `index.html`. The standard library alone. |
| `index.html` | The whole frontend: markup, styles and script in one file. No build. |
| `tg.py` | The Telegram layer: the login by link and the reading of the private channels. Telethon is imported lazily, so without a linked profile the module never touches the network. |
| `qrsvg.py` | The QR code of the login link, as SVG. |
| `qrcodegen.py` | The QR encoder of Project Nayuki (MIT), unchanged. |
| `test_app.py` | The tests. The HTTP server stands on a random port, Telegram is mocked. |
| `check_ui.py` | The integrity of `index.html` without a browser. It takes no arguments and reads `index.html` next to itself. |
| `audit_media.py` | Compares the media in the database with what t.me serves now. A service script: it is not in the image and it is not in the tests. |
| `sync.py`, `serve.py` | The old variant with Telethon and RSS. The current service does not call them. |
| `fonts/` | JetBrains Mono, in four woff2 files, served from the address of the service itself. |
| `docs/` | The screenshots for the README. |

## Traps

- **The Dockerfile copies the files by name.** `COPY app.py index.html tg.py
  qrsvg.py qrcodegen.py ./`. A new module works on the machine and dies in the
  container at the first request. Add the file to that line.
- **The schema changes by a migration in `connect()`.** `PRAGMA table_info` plus
  `ALTER TABLE`. Changing the `SCHEMA` block alone leaves the databases of the
  users alone.
- **Posts go into the database only through `POST_FIELDS` and `POST_INSERT`.**
  A positional `INSERT` drifts away from the table after a migration, and it
  has happened before.
- **The media of a post is one JSON list in the column `media`,**
  `[{"k":"i|v","u":url}]`. There are no separate columns for the pictures and
  for the video, and an album collapsed into a single frame for exactly that
  reason.
- **`accounts.email` is unique for the values that are not empty.** Write a
  missing address as `None` and not as an empty string, or the second profile
  without an address gets a 409.
- **The values that come from the browser are checked against a white list on
  the server.** The theme, the font, the channel name, the search: they go into
  the markup as they are. The server answers 400 to an unknown theme or font.
- **The colours, the radii, the fonts and the spacing come from the tokens in
  `:root`.** `--r-xs/sm/md/lg/pill/round`, `--fg`, `--dim`, `--acc` and the
  rest. A value of its own in the markup breaks the rule. The contrast of the
  text is at least 4.5:1, so a change to the palette means recomputing the
  contrast and correcting the numbers in the README.
- **The interface texts are in Russian**, addressed to the reader as "ты",
  without a dash in the role of a separator and without service emoji.
- **The name of a channel is checked before it reaches the address.**
  `CHANNEL_RE` is `^[A-Za-z0-9_]{3,32}$`, and without that check the service
  could be made to fetch any address from the local network.
- **The routes that reach t.me want a profile.** `/api/preview`, `/api/check`,
  `/api/media`, `/api/catalog`, `/api/avatars` and `/api/categories` answer
  401 without a session, so that the site cannot become somebody else's way
  around Telegram.
- **The private posts live in their own table with a `profile` column.** The
  registration on the site is open, and a shared table would hand the private
  feed of one person to everybody.
- **A file of a Telegram session is a ready-made login into the account.** It
  lies in the data volume under 0600 and is never written into the database, so
  a backup of SQLite does not hold it. "Disconnect Telegram" deletes the file
  rather than forgetting it.
- **The database runs in WAL.** `feed.db-wal` and `feed.db-shm` sit next to it.
  Copying the single file `feed.db` loses the last committed transactions; take
  the copy with `sqlite3.backup` or copy the whole volume.
- **A feed with no subscriptions stays empty.** `search_posts` with an empty
  list of channels returns the whole cache, which in the feed would be every
  post of the database.
- **`sync.py` and `serve.py` are the old variant with Telethon and RSS.** The
  current service does not call them. Do not touch them without being asked.

## Style / safety rails

- **Always:** a test for the new behaviour, `python3 check_ui.py` after a change
  to `index.html`, and green tests before the commit.
- **Ask:** the removal of a column or of data, a change in the schema of the
  profiles and the sessions, a change in the format of `media`, somebody else's
  screenshots in `docs/`.
- **Never:** commit `.env`, the SMTP keys or a password; write them into the
  log; add a `<script src>`, a build step or a download from outside.
- **Check the result in a browser and with the data**, not with the code and
  not with a tool that reported success. A measurement taken before the data was
  loaded says "all good" while it means nothing, and a script that has never
  failed may be checking nothing. Break the check on purpose first and make
  sure it notices.
- **The branch is `master`, and the commit message is in Russian.** The first
  line says what changed, for example
  `Галерея: альбом листается, а не схлопывается в один кадр`.
- **A one-off helper script does not go into the repository.** A script that is
  used again goes in, and the README describes it.
- **The screenshots in `docs/` show the live data.** When the interface is
  reshot, keep the same sizes: 1280×860 for the wide ones, 420×900 for the
  phone.

## Where the product truth lives

`PRODUCT.md` holds it. Read it before you change the behaviour of the service,
and write the change there when the behaviour changes.
