# Architecture

How koloda is put together.

One Python process on the standard library alone, one SQLite file, and one
`index.html` that holds the whole interface. The service reads the public
preview pages of t.me, so a Telegram account is not needed for the public
channels, and it keeps everything in `feed.db`.

For what the service does and how to run it, read the
[README](../README.md). For the traps, read [AGENTS.md](../AGENTS.md). For
the screen itself, read [../DESIGN.md](../DESIGN.md).

## The shape

| Module | What it is responsible for |
| --- | --- |
| `app.py` | The whole server. The parsing of `t.me/s/<channel>`, the schema and the migrations, the profiles and the sessions, the letters, the cache and the background workers, and the serving of `index.html`. |
| `index.html` | The whole frontend: markup, styles and script in one file. The tokens in `:root` are the design system. |
| `tg.py` | The Telegram layer: the login by link and the reading of the private channels. Telethon is imported lazily, so without a linked profile the module never touches the network. |
| `qrsvg.py`, `qrcodegen.py` | The QR code of the login link. `qrcodegen.py` is the encoder that Project Nayuki wrote, unchanged. |
| `check_ui.py` | The integrity of the markup, without a browser. It takes no arguments and reads `index.html` next to itself. |
| `test_app.py` | The tests. The HTTP server stands on a random port and Telegram is mocked. |
| `audit_media.py` | The audit of the media against what t.me serves now. A service script: not in the image, not in the tests. |
| `sync.py`, `serve.py` | The old variant with Telethon and RSS. The current service does not call them. |
| `fonts/` | JetBrains Mono, four woff2 files, served from the address of the service itself. |

Inside `app.py` the code is grouped by concern: the parsing of the preview
pages (`strip_pagination`, `rich_text`, `parse_preview`, `parse_doc`,
`parse_fwd`, `parse_quote`, `parse_reactions`), the cache and the background
workers (`_ensure_workers`, `_fetch_job`, `_cache_writer`, `refresh_channels`,
`request_refresh`), the history (`backfill_candidates`, `backfill_step`), the
private channels (`private_sync_step`, `search_private_posts`,
`store_private_rows`), the profiles (`create_session`, `account_for_request`,
`password_digest`, `send_email`, `issue_auth_token`) and the HTTP handler
(`class Handler`).

## The data flow

```
browser -> app.py (ThreadingHTTPServer) -> SQLite (feed.db)
                                  \-> t.me/s/<channel>          public preview pages
                                  \-> tgmania.com/api/channels  the catalog
                                  \-> tg.py -> Telegram          private channels, by QR login
```

A feed request is answered from the cache. A channel that is not in the cache
yet is waited for, at most 8 seconds; the stale ones are reloaded in the
background, up to eight at a time. When one source fails, the other channels
are still shown and the failure comes with the answer.

The cache lives at 300 seconds with a spread of 60, so channels fetched
together do not go stale at the same moment. The history of a subscribed
channel is loaded in the background, one page of older posts every 4 seconds,
up to 500 posts per channel, and the position is kept in the `backfill`
table so that a restart does not lose it. The writes of the history do not
touch the cache of the channel.

The media of a public post is not stored. The URL of the picture or of the
video goes into the `media` column as a JSON list, and the browser loads it
straight from the CDN of Telegram. The media of a private channel is different:
it comes over MTProto and is kept on disk, because it cannot be handed out as
a public URL.

## Decisions worth knowing about

### The public preview pages, not the API

`fetch_channel` reads `t.me/s/<username>`, the page Telegram serves to anyone
who asks for it. That removes `api_id`, the phone number and a Telethon
session from the path of a public channel.

The cost is the parsing. The page is HTML, and the code carries a set of
parsers for it: `strip_pagination` removes the navigation tail, `rich_text`
unwraps the formatting, and one parser per kind of block keeps the preview,
the document, the forward and the quote. A change in the markup of t.me
breaks the parsers and nothing else fails.

### The channel name is checked before it reaches the address

`CHANNEL_RE` is `^[A-Za-z0-9_]{3,32}$`. The name goes into the URL of
t.me, so without the check the service could be made to fetch an arbitrary
address from the local network.

### One list of posts, one table for the private ones

The private posts live in their own table with a `profile` column. The
registration on the site is open, so a shared table would hand the private
feed of one person to everybody.

### The cache and the history do not share a write path

The cache is written by one background writer thread, the history by the
backfill loop. They write to the same database but not to the same rows, and
the database runs in WAL with `synchronous=NORMAL`, so a read of the feed does
not wait for the background write.

The cost is the copy. The database runs in WAL, so `feed.db-wal` and
`feed.db-shm` sit next to it, and copying the single file `feed.db` loses
the last committed transactions. Take the copy with `sqlite3.backup` or copy
the whole volume.

### The values from the browser are checked against a white list

The theme, the font, the channel name and the search go into the markup as
they are, and the server answers 400 to an unknown theme or font. The check
is in `SETTINGS_ALLOWED` and `load_settings`, and the reason is in a comment
next to it: a stray class or a stray font breaks the whole interface.

### The posts go into the database through two constants

`POST_FIELDS` and `POST_INSERT` are the only way a post is written. A
positional `INSERT` drifts away from the table after a migration, and it has
happened before.

### The interface is one file

There is no build step, no bundler and no script from outside. The markup,
the styles and the script live in `index.html`, and `check_ui.py` checks the
integrity of that file in CI without a browser.

What that costs is a file of about 189 KB, and the styles are 44 KB of it.
A change to the interface is a change to one file, and it needs the check.

## Boundaries

- **`app.py` imports `tg`, and `tg` imports nothing from `app.py`.** The
  Telegram layer is reached only through the functions `app.py` calls, and
  Telethon is imported inside the functions that need it.
- **Only `app.py` opens the database.** Every query in the service is in
  that one file, and the schema and its migrations are in `SCHEMA` and
  `connect()`.
- **Only `app.py` fetches from the outside.** The catalog, the preview pages
  and the avatars all go through it; `tg.py` is the one exception, and it
  speaks to Telegram for the private channels.
- **The private rows never leave the table with the `profile` column.** Every
  read of `private_posts` takes the profile of the request.
- **`sync.py` and `serve.py` are outside the service.** They are the old
  variant, nothing imports them, and the Dockerfile does not copy them.
- **`check_ui.py` reads `index.html` next to itself.** It takes no arguments
  and touches nothing else.
