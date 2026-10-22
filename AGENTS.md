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
cd ui && npm ci && npm run build # the interface: the dependencies and the build
cd ui && npm run dev             # the interface on http://127.0.0.1:5180, the API is proxied
python3 -m unittest -v           # the tests: no network, no Telegram, a temporary database
python3 check_ui.py              # the integrity of the interface, without a browser
docker build -t koloda:ci .      # the image builds
python3 app.py 8090              # run it without Docker, on an empty data/feed.db
```

The interface is built: the sources are in `ui/src`, the ready files in `ui/dist`.
The server only hands over the built files, so after a change in `ui/` the build
has to be repeated, or the service keeps the old interface. The image builds the
interface itself, in the `ui` stage of the Dockerfile.

The port is 8000 inside the container and 8090 on the host, because 8000 is
taken by other processes on that machine. The port of the process itself: the
argument of the command line first, then `$PORT`, then 8000.

The same checks run in GitHub Actions on every push to `master` and on every
pull request: `npm ci && npm run build` in `ui/`, then `python3 -m unittest -v`,
`python3 check_ui.py` and `docker build`.

The audit of the media runs against the live data and needs the internet:

```bash
docker compose exec -T feed python3 -c "import sqlite3;s=sqlite3.connect('/data/feed.db');d=sqlite3.connect('/tmp/feed.db');s.backup(d);d.close();s.close()" && docker compose cp feed:/tmp/feed.db ./feed.db && docker compose exec -T feed rm /tmp/feed.db && python3 audit_media.py
```

The request log is off by default: `LENTELE_LOG=1 docker compose up -d`.

## Where things live

| Path | What it holds |
| --- | --- |
| `app.py` | The whole server: the parsing of `t.me/s/<channel>`, the schema and the migrations, the profiles, the sessions, the letters, the serving of the built interface. The standard library alone. |
| `ui/` | The whole frontend: Svelte 4, Vite and the Monos kit. The sources in `ui/src`, the build in `ui/dist`. |
| `ui/src/lib/` | The parts without a screen: the API client, the state, the formatting, the wording of the history. They are plain modules, and the tests count them in node. |
| `ui/src/components/` | The screens: the feed, the card of a post, the catalog, the channel, the profile, the dialogs, the viewer of the frames. |
| `tg.py` | The Telegram layer: the login by link and the reading of the private channels. Telethon is imported lazily, so without a linked profile the module never touches the network. |
| `qrsvg.py` | The QR code of the login link, as SVG. |
| `qrcodegen.py` | The QR encoder of Project Nayuki (MIT), unchanged. |
| `test_app.py` | The tests. The HTTP server stands on a random port, Telegram is mocked. |
| `check_ui.py` | The integrity of the interface without a browser: the imports, the icons, the colours, the spacing, the build. It takes no arguments. |
| `audit_media.py` | Compares the media in the database with what t.me serves now. A service script: it is not in the image. Only its exit code is covered by the tests, without the network. |
| `sync.py`, `serve.py` | The old variant with Telethon and RSS. The current service does not call them. |
| `fonts/` | JetBrains Mono, in four woff2 files, served from the address of the service itself. |
| `docs/` | The screenshots for the README. |

## Traps

- **The Dockerfile copies the files by name.** `COPY app.py tg.py qrsvg.py
  qrcodegen.py ./`. A new module works on the machine and dies in the
  container at the first request. Add the file to that line.
- **The interface is built, and the build is a separate step.** The server hands
  over `ui/dist` and nothing else. A change in `ui/src` without `npm run build`
  leaves the service with the old interface, and the symptom is "my change did
  not show up" rather than an error.
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
- **The palette, the radii, the fonts and the spacing come from the Monos kit.**
  The tokens are `--ui-bg`, `--ui-surface`, `--ui-ink`, `--ui-accent`,
  `--ui-radius` and `--ui-space-*`. A colour of one's own breaks the theme: the
  palette stops switching. `check_ui.py` looks for a hex colour in the sources
  and fails on it. The text of the interface keeps a contrast of at least 4.5:1,
  and the test counts it over all the palettes of the kit.
- **The theme is a palette, the light is a separate setting.** `theme` is one of
  the six palettes of the kit, `mode` is `dark`, `light` or `auto`. The old
  setting was a pair "light or dark", and its value moves to `mode` when the
  profile is read.
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
- **The word "captcha" in a post is not a block.** The sign of a block is
  looked for only on a page without posts: on a page with posts the word
  belongs to the text of the channel. The check over the whole html took a
  whole channel offline, because one of its posts was about a captcha.
- **The file of a Telegram session is created before Telethon opens it.**
  Telethon opens the file in the constructor, and it appears with the rights of
  the umask, that is readable by all. `tg._client` creates the file itself with
  0600; do not remove that.
- **The audit of the media returns 3 when it could not check everything.**
  Zero mismatches over a check that saw nothing is not a clean result, it is a
  broken parser. The same goes for the exit code of any check of the live data.
- **`sync.py` and `serve.py` are the old variant with Telethon and RSS.** The
  current service does not call them. Do not touch them without being asked.

## Style / safety rails

- **Always:** a test for the new behaviour, `npm run build` and
  `python3 check_ui.py` after a change in `ui/`, and green tests before the
  commit.
- **Ask:** the removal of a column or of data, a change in the schema of the
  profiles and the sessions, a change in the format of `media`, somebody else's
  screenshots in `docs/`.
- **Never:** commit `.env`, the SMTP keys or a password; write them into the
  log; add a `<script src>` to the page or a download from outside. The
  dependencies of the interface come from the registry and the kit comes from
  `github:aa-blinov/monos-ui` with a fixed version; nothing else is fetched at
  runtime.
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
