# Koloda

Collect public Telegram channels into one feed and read it in your own reader.
No Telegram account is needed: the service reads the public preview pages that
Telegram serves to anyone. Every profile keeps its own subscriptions, its own
read marks and its own theme.

![The feed](docs/lenta.png)

[![CI](https://github.com/aa-blinov/koloda/actions/workflows/ci.yml/badge.svg)](https://github.com/aa-blinov/koloda/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12-blue)](Dockerfile)

---

## Quick start

```bash
git clone https://github.com/aa-blinov/koloda.git && cd koloda
cp .env.example .env     # fill in the SMTP settings
docker compose up -d
```

Open http://127.0.0.1:8090, create a profile, and open the **Catalog**: it shows
the latest posts of every channel, and the button adds that channel to your
feed.

Mail is needed for the registration: a profile is activated after you follow the
link in the letter. The feed itself reads without a profile, and the catalog and
the preview of a single channel ask for one.

From a phone on the same network, open `http://<the-computer's-ip>:8090`.

![Signing in to a profile](docs/vhod.png)

Python 3.12 and Docker. Without Docker:

```bash
python3 app.py 8090
```

The service then starts on an empty local database at `data/feed.db`.

## What it does

The interface is in Russian; the labels quoted here are translated.

- **Sections.** Three main screens: the feed, the catalog and the saved posts.
  On a wide screen they stand in a sidebar on the left; on a phone the same
  three sit in a bottom bar under the finger, and the channels and the profile
  stay in the header as two signs. The page of a channel and the profile open
  over them and close with the arrow in the header, back to where the reader
  came from. The address of the screen is in the hash (`#/feed`, `#/saved`,
  `#/cat`, `#/channel/<name>`, `#/me`), so the back button of the browser
  does the same as the arrow, and every screen remembers the place where the
  reader stopped scrolling.
- **Search and filters.** The search stands in the top right corner, and under
  it the filters of the feed: the chosen channel or collection and "hide the
  read ones". On a phone the filters stand above the feed, and the choice to
  hide the read ones is remembered on the device. The channel and the
  collection are chosen in one place — the folder with the channels and the
  collections.
- **Avatars.** The photo of a channel is taken from the public page on t.me
  (`og:image`) and kept for seven days. A channel with no photo shows the first
  letter of its name.
- **Catalog.** The channel list of TGMania, with categories and search, for a
  signed-in profile. A channel can be opened and read before it is added. Next
  to the search are three sorts: by subscribers, by name and by posts (the
  freshness of the last post in the feed, and the channels with no posts yet go
  to the end). The order is set by the client, because TGMania returns the
  channels in no order at all, and the sort by post freshness is counted by the
  server from its own database.
- **Your own channel.** Add one by name or by a `t.me/username` link; the
  service checks that it exists before it adds it.
- **The feed.** The posts of the chosen channels mixed together, in the order of
  publication. 80 entries on a page, and "Load more" continues the same
  chronology. Up to 200 channels in one feed.
- **Read.** A post counts as read once you scroll it above the top of the
  screen, so nothing has to be clicked. The `u` key takes a read mark back.
  Read posts stay out of the feed on the next open: the "Hide read" button above
  the feed is on by default, and turning it off brings them back. New posts do
  not change the feed by themselves. The pull-down gesture at the top of the
  feed refreshes it on a desktop, and the read posts leave the screen. A post
  that the refresh puts above the screen stays unread until you scroll past
  it. Until the refresh, what you read in this session stays in place.
- **Search.** Over the posts of the feed and over the names in the catalog. The
  search waits 320 ms after the last keystroke.
- **Channel tabs.** A quick jump to one channel.
- **Media.** Photos and videos load straight from the CDN of Telegram, and the
  server proxies nothing. An album is swiped through, with a counter of the
  frames in the corner; the frames load as you scroll, not all at once. A click
  on a frame opens it whole, and the arrows page through it. Video does not load
  until play is pressed.
- **Profiles.** You sign in with a name and a password, and every profile has
  its own feed. The password is reset by a link to a confirmed address. The
  channels a browser held before the sign-in move to a profile only while the
  profile has none; the read marks always move, up to the same 20 000.
- **A deck on a phone.** The feed on a phone is a deck of cards, one post at a
  time, with no vertical scroll of the column. A card is dragged sideways: to
  the right it is marked read, to the left it is saved. The card follows the
  finger and says what will happen, the action can be taken back, and the next
  post comes forward. Two round buttons under the deck do the same without a
  gesture. On a wide screen the same feed stays a column: there is
  a mouse, and a column is read by scrolling.

## Screenshots

| The feed | The catalog |
| :---: | :---: |
| ![The feed](docs/lenta.png) | ![The catalog](docs/katalog.png) |

| Search | An album with several frames |
| :---: | :---: |
| ![Search](docs/poisk.png) | ![An album with several frames](docs/galereya.png) |

| A frame opened whole | The light theme |
| :---: | :---: |
| ![A frame opened whole](docs/prosmotr.png) | ![The light theme](docs/svetlaya.png) |

| The mobile view | The light theme on a phone |
| :---: | :---: |
| ![The mobile view](docs/mobile.png) | ![The light theme on a phone](docs/mobile-svetlaya.png) |

A frame opened whole on a phone: the arrows page through it, the cross in the
corner closes it.

![A frame opened whole on a phone](docs/prosmotr-mobil.png)

## How it works

```
browser -> app.py (ThreadingHTTPServer) -> SQLite (feed.db)
                                  \-> t.me/s/<channel>          public preview pages
                                  \-> tgmania.com/api/channels  the catalog
                                  \-> tg.py -> Telegram          private channels, by QR login
```

The service is one Python process on the standard library alone. The interface
is a Svelte 4 app built with Vite on the [Monos](https://github.com/aa-blinov/monos-ui)
kit: the sources are in `ui/`, the ready files in `ui/dist`, and the server hands
over the built files. The only dependency of the service in the image is
`telethon==1.45.0`, and it is imported only when a profile connects Telegram.

Posts come from the public preview page `t.me/s/<username>`. Telegram serves it
without a login, so no `api_id`, no phone number and no Telethon session are
needed for the public channels. Everything lands in SQLite (`feed.db`): the
posts, the titles, the profiles, the subscriptions and the read marks.

A channel is refreshed at most once every five minutes. The lifetime of the
cache is 300 seconds with a spread of 60, so the channels fetched together do
not go stale at the same moment. The feed is answered from the cache at once, a
channel that is not in the cache yet is waited for (8 seconds at the most), and
the stale ones are reloaded in the background, up to eight at a time. When one
source fails, the other channels are still shown and the failure comes with the
answer.

The history of a subscribed channel is loaded in the background, one page of
older posts every 4 seconds, up to 500 posts per channel. The position is kept
in the `backfill` table, so a restart does not lose it, and the writes of the
history do not touch the cache of the channel.

The catalog comes from the open API of TGMania. Its own order is unusable, so
the client sorts, and the freshness of the last post is counted by the server
from its own database.

A private channel is read through the login of the profile: a `tg://login`
link, shown as a QR code that `qrsvg.py` draws on top of `qrcodegen.py`. The
private posts live in a table of their own with a `profile` column, and their
media is pulled over MTProto when it is opened and handed to the subscribers of
that channel alone.

## Configuration

The service reads its settings from the environment. The ones below are the
whole list.

| Option | Default | What it does |
| --- | --- | --- |
| `SMTP_ENABLED` | `false` | Sends the letters. With it on, a profile asks for a mail address. |
| `SMTP_HOST` | none | The mail server. |
| `SMTP_PORT` | `465` | Port 465 uses implicit TLS, any other port uses STARTTLS. |
| `SMTP_USER` | none | The login for the mail server. |
| `SMTP_PASSWORD` | none | The password for the mail server. |
| `SMTP_FROM` | `SMTP_USER` | The sender address. |
| `APP_BASE_URL` | `http://127.0.0.1:8090` | The base of the links in the letters: the confirmation of the address and the reset of the password. |
| `COOKIE_SECURE` | `0` | `1` adds the Secure flag to the session cookie. |
| `TRUSTED_PROXY` | none | The reverse proxy in front of the service: IP addresses or CIDR ranges, comma-separated, e.g. `172.40.0.1`. Only a connection from one of them takes the client address from `X-Forwarded-For`: the rightmost address that is not one of them, because the proxy appends to the right and the left part is the client's own. The login lock uses that address. Without it the header is ignored, and every request behind the proxy shares one lock. |
| `LOG` | `0` | `1` writes the request log. `compose.yaml` takes it from `LENTELE_LOG`. |
| `DATA_DIR` | `<the app>/data`, `/data` in the image | Where `feed.db` and the files of the Telegram sessions live. |
| `PORT` | `8000` | The port to listen on. The argument of the command line wins over it. |
| `TG_API_ID` | `2040` | The Telegram API id. The default is the official one of Telegram Desktop. |
| `TG_API_HASH` | the official Telegram Desktop hash | The Telegram API hash. |

Two of them cannot be left out when the site is on the internet, and the
service prints a warning at startup when they are wrong:

```
COOKIE_SECURE=1
APP_BASE_URL=https://your-domain
```

`COOKIE_SECURE=1` adds the Secure flag to the session cookie. While it is off,
the password and the cookie travel in plain text, and neither the page nor the
log shows it. `APP_BASE_URL` goes into the links in the letters: while it still
says `http://127.0.0.1:8090`, the letter to a friend carries a link that opens
only on your own computer, and the password cannot be recovered. On
`http://127.0.0.1:8090` leave `COOKIE_SECURE` at `0`.

Letters are limited in two ways: one address gets one letter a minute, and one
client address gets ten letters an hour. The limit covers the registration, the
repeated confirmation, the reset of the password and the change of the address,
and all of them share the ten. A letter that the mail server refused still
counts. A refused letter does not change the answer, so nobody can tell from it
whether the address is in use: the registration answers 202 with the same text as
a sent letter, the confirmation and the reset answer 200 with the generic text, and
the change of the address answers 429 with «Письма уже отправлены. Попробуй позже.».
A registration with an address that a confirmed profile, or an unconfirmed one
younger than 24 hours, holds gets the same 202 and no letter and no new profile; a
taken name still answers 409, because the name is public. The client address is the
address of the connection, or with `TRUSTED_PROXY` set, the rightmost
`X-Forwarded-For` address that is not a trusted proxy. Behind a proxy without
`TRUSTED_PROXY` every client has the proxy's address, so the ten an hour are shared
by all of them: set `TRUSTED_PROXY` for the site.
The counters live in memory and start again after a restart.

`docker-compose.override.yml` can take the `ports` line away and put the
container on a shared network with Caddy. That file belongs to one machine and
is not in the repository.

The theme and the font are stored in the profile, so they survive a reload and
travel to the other device. The server checks both against a list and answers
400 to an unknown value.

| Setting | Default | What it does |
| --- | --- | --- |
| `theme` | `dark` | `dark`, `light` or `auto`. `auto` follows `prefers-color-scheme`. |
| `font` | `mono` | `mono` (JetBrains Mono), `sans`, `serif` or `grotesk`. |

`config.example.json` holds an optional `api_id`, `api_hash` and
`limit_per_channel`. `sync.py` reads that file; the current service takes the
same values from `TG_API_ID` and `TG_API_HASH`.

## Development

```bash
cd ui && npm ci                  # the dependencies of the interface, once
cd ui && npm run build           # the interface: the sources in ui/src, the files in ui/dist
cd ui && npm run dev             # the interface on http://127.0.0.1:5180, the API is proxied
docker compose up -d --build    # rebuild after a change to the code
docker compose logs -f           # the log
docker compose down              # stop
```

The server hands over the built interface, so after a change in `ui/` the build
has to be repeated. The image builds the interface itself, in the `ui` stage of
the Dockerfile.

The tests and the check of the interface run on every push in GitHub Actions:

```bash
python3 -m unittest -v           # the tests: no network, no Telegram, a temporary database
python3 check_ui.py              # the integrity of the interface, without a browser
docker build -t koloda:ci .      # the image builds
```

The build is part of the checks because the `Dockerfile` copies the files by
name: a new module without a line in `COPY` builds and then fails at the first
request.

The audit of the media compares the database with what t.me serves right now.
It needs the internet, so it is not part of the test run; only its exit code is
covered, and that without the network:

```bash
docker compose exec -T feed python3 -c "import sqlite3;s=sqlite3.connect('/data/feed.db');d=sqlite3.connect('/tmp/feed.db');s.backup(d);d.close();s.close()" && docker compose cp feed:/tmp/feed.db ./feed.db && docker compose exec -T feed rm /tmp/feed.db && python3 audit_media.py
```

The copy is made with SQLite inside the container: the database runs in WAL, so
copying the single file `feed.db` could lose the last records. The script finds
the posts where an emoji stayed in the database instead of a photo, the lost
frames of an album and the stale addresses.

The script says how much it managed to check and returns a code: `0` when
everything matches, `1` when there are mismatches, `2` when there is nothing to
check, and `3` when the check was incomplete. The third code matters: zero
mismatches over a check that saw nothing is a broken parser, not a clean
result.

The user paths are described in `docs/user-paths.md`, each with an ID. The
browser check walks the paths that can be checked on a running stand. It needs
Playwright and Chrome, the internet for the channels, and the data directory of
the stand: a profile needs a confirmed address, so the check creates its profiles
in the stand's database and signs in over HTTP like a reader does:

```bash
python3 ui_paths_check.py http://127.0.0.1:8090 --data-dir ./data                # every path for its device
python3 ui_paths_check.py http://127.0.0.1:8090 --data-dir ./data --only C1,F2   # some paths
python3 ui_paths_check.py http://127.0.0.1:8090 --data-dir ./data --device both  # desktop and phone
```

Each check creates its own profile and prints PASS, FAIL or SKIP. A path that
needs posts which the stand does not have is SKIP, not PASS.

| Path | What it holds |
| --- | --- |
| `app.py` | The server: the parsing of t.me, the API, the profiles, the sessions, the letters, the serving of the interface. |
| `ui/` | The interface: Svelte 4, Vite and the Monos kit. The sources in `ui/src`, the build in `ui/dist`. |
| `tg.py` | The Telegram layer: the login by link and the reading of the private channels. |
| `qrsvg.py`, `qrcodegen.py` | The QR code of the login link. `qrcodegen.py` is the encoder from Project Nayuki, unchanged. |
| `test_app.py` | The tests. |
| `check_ui.py` | The integrity of the markup, without a browser. |
| `ui_paths_check.py` | The user paths of `docs/user-paths.md` in a browser, on a running stand. A service script: not in the image, not in the tests. |
| `docs/user-paths.md` | The user paths, with their IDs and what checks them. |
| `audit_media.py` | The audit of the media against t.me. A service script: it is not in the image. Only its exit code is covered by the tests, without the network. |
| `sync.py`, `serve.py` | The old variant with Telethon and RSS. The current service does not call them. |
| `docs/` | The screenshots of this file. |

## License

The service is under the MIT licence ([`LICENSE`](LICENSE)). `qrcodegen.py` is
under the MIT licence of Project Nayuki, and the faces come from the Monos kit,
which keeps their licences beside them (SIL Open Font License 1.1 for JetBrains
Mono, Inter and Source Serif 4).
