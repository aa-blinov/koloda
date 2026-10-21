# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

The service is a Python web application (Python 3.12, `python:3.12-slim`) with
the whole interface in one `index.html`. One process, one SQLite file, one
Docker image. It runs on a laptop or on a home server behind a reverse proxy.
The page and the JSON API that the page uses come from the same origin.

## Users

The reader: a person who follows a dozen public Telegram channels and wants
them in one chronological feed, in a browser, on a desktop or on a phone in the
same network. The job is to pick the channels out of a catalog, keep the list,
and see what has arrived since the last visit.

The second audience is the owner, who runs the service on his own machine and
lets friends register on it. The README names no other audience, and the
repository holds nothing to count them with: 0 stars, 0 forks, created on
2026-10-15.

## Product Purpose

The service does one thing: it puts the posts of many public Telegram channels
into one feed and reads that feed in a browser, without a Telegram account.

The reader opens the page and sees the new posts, in the order they were
published, mixed from the channels they chose. Success for the reader is that
nothing from Telegram has to be opened to read them. Success for the product is
that the service keeps working for a person who will never create a Telegram
account, and that the feed of one profile stays the feed of that profile.

## Positioning

A neighbouring product is the Telegram client itself, or a bot that throws the
posts into a chat. This one keeps the reading in the browser, gives every
profile its own list of channels and its own read marks, and reads the public
channels without an account.

The second difference is the catalog. The channels come from the open catalog of
TGMania, a channel can be opened and read before it is added, and the sort by
the freshness of the last post is counted by the service from its own database,
because TGMania keeps no such number.

The third is the private channel. It is read through the login of the reader,
and its posts stay in a table keyed by the profile, so the private feed of one
person never reaches another.

## Operating Context

The service is a page in a browser tab that stays open next to a chat. The feed
is refreshed by pulling down at the top of the list; nothing arrives on its
own. The reader moves through the posts with the
keyboard: `j` and `k` for the next and the previous post, `n` for the first
unread, `u` to take a read mark back, `/` for the search, `?` for the list of
the keys, `Esc` to close a dialog.

On a phone the same service answers on the address of the machine and the
layout changes: a bottom bar, a column of one, and an album paged by a swipe.

The service itself runs under Docker Compose with `restart: unless-stopped`, and
a healthcheck asks `/api/health` every 30 seconds. On the machine of the owner
it sits behind a shared Caddy at `koloda-tg.duckdns.org`, the host port is not
published, and the container is on the shared network `edge` at `172.40.0.50`.
The registration is open, so anybody who finds the address can create a
profile.

## Capabilities and Constraints

Confirmed by the code:

- The public channels are read from `t.me/s/<username>`, without a login and
  without `api_id`.
- One feed of up to 200 channels, 80 posts on a page, in the order of the
  publication time.
- The catalog of TGMania: search, categories, three sorts, 40 channels on a
  page.
- A post counts as read once the reader has scrolled it above the top of the
  screen, and the mark can be taken back. A post that a refresh puts above the
  screen is not marked: it stays unread until the reader scrolls past it. Loading
  the next page marks nothing.
- The next page of the feed loads when the reader scrolls near its end; the
  button stays as the way to load it when that does not happen.
- The search runs over the posts of the feed and over the names in the catalog.
- A profile with a password, a session cookie for 30 days, PBKDF2-SHA256 with
  600,000 iterations and a salt of 16 bytes.
- A profile needs an address. The confirmation of the address and the reset of
  the password go by letter; the tokens live 24 hours and 30 minutes. Without an
  address the registration is refused.
- The private channels arrive through a login by QR, up to 40 messages per
  channel, refreshed every 15 minutes at the most.
- The media of a private channel comes over MTProto: up to 15 MB, of the types
  JPEG, PNG, WebP, GIF and MP4, kept on disk in a cache of up to 500 MB and
  served with Range.
- The backup file `koloda-lenta.jsonl` holds the channels and the read marks; a
  plain text list holds the channel links.
- The theme (dark by default) and the font (JetBrains Mono) are kept in the
  profile and survive a reload.
- The answers above 1 KB are compressed with gzip; the images, the video and
  the fonts are not.
- The request log is off by default.

Deliberately not true, and worth knowing before a change:

- The project carries no licence file.
- There is no automatic update.
- Nothing arrives in the browser by itself: the new posts wait for the pull
  gesture on a phone. On a desktop the feed refreshes when the page reloads or
  the channel changes.
- The history of a channel stops at 500 posts, and the rest is only in Telegram.
- A private channel is not read through `t.me/s`. It needs the login of the
  reader, and the file of the session is a ready-made login into the account.

The questions that nobody has answered yet:

- The licence of the service itself. There is no `LICENSE` file in the
  repository.
- Whether the registration stays open for anybody, or is closed once the
  friends of the owner have signed up. The README says that it is open, and
  does not say for how long.

## Brand Commitments

**Name:** Koloda, a deck of cards: a hand of channels shuffled into one feed.
The interface calls itself Колода.

**Voice:** the interface is in Russian, addresses the reader as "ты", and says
what failed instead of reassuring. An error is a sentence with a name in it,
not an icon and not a colour.

**Assets:** the Gruvbox palette of the Monos design system, JetBrains Mono served
from the address of the service itself, and icons drawn as inline SVG in one
stroke. No emoji, and no Unicode symbol in place of an icon.

## Evidence on Hand

- `README.md` describes what the service does, the private channels, the
  backup, the design and the limits.
- `app.py` holds the routes, the limits and the defaults: 80 posts on a page,
  200 channels in a feed, 500 posts of history, 30 days of session.
- `index.html` holds the tokens of the palette and the whole interface.
- `test_app.py` holds 133 tests, and `check_ui.py` checks the integrity of the
  markup.
- `docs/` holds ten screenshots of the live interface, in the dark and the
  light theme, on a desktop and on a phone.
- `qrsvg.py` and `qrcodegen.py` hold the QR code of the login link, and
  `fonts/LICENSE-JetBrainsMono.txt` the licence of the fonts.

Absent, and not to be invented: no download counts, no usage numbers, no
testimonials, and no comparison with other readers. The repository has 0 stars
and 0 forks, and it was created on 2026-10-15.

## Product Principles

1. **A Telegram account is not required for the public feed.** A feature that
   asks for a login before the first post is a feature most of the readers
   never reach.
2. **The profile owns the feed.** The subscriptions and the read marks belong
   to the profile on the server, and not to the browser that happened to open
   the page. A collection is a named group of the profile's own subscriptions:
   the feed can show one collection, and it is the same feed with a filter, so
   the read marks and the paging work the same way inside it.
   Collections are listed by name, without regard to case.
   A name is unique per profile (case and edge spaces ignored), up to 50
   collections with up to 200 channels each. Unsubscribing removes the channel
   from every collection, and a collection can be empty.
3. **A failure names itself.** A dead channel, a letter that did not go, an
   error of Telegram: each says what happened and what is left to do.
4. **The service does not carry the media.** The photos and the video of the
   public posts come straight from the CDN of Telegram, and the server stays out
   of the hot path. A link that has expired is replaced by a fresh one from the
   same post, and until it arrives the frame stays empty: a broken picture with
   its caption is never shown. In the background the server walks the pages of
   every subscribed public channel back to three days, about once in six hours,
   so the links of recent posts are refreshed before anyone opens them.
5. **The private data of one profile never reaches another.** The private posts
   live in a table keyed by the profile, and their media is given to the
   subscribers of that channel alone.

## Accessibility & Inclusion

- **The keyboard reaches everything.** `j` and `k` move between the posts, `n`
  jumps to the first unread, `u` takes a read mark back, `/` opens the search,
  `?` shows the list of the keys, `Esc` closes a dialog. The list is in the
  profile.
- **The touch targets are large enough.** On a phone the buttons and the
  segments are at least 44 px, and the bottom bar is exactly 64 px.
- **The contrast of the text is at least 4.5:1** on every surface in both
  themes, and `ThemeContrastTest` checks it by reading the tokens out of
  `index.html`.
- **The theme can follow the system.** The "as in the system" option of the
  profile watches `prefers-color-scheme`.
- **A standard for screen readers has not been set.** The markup carries the
  roles of ARIA for the tabs and the dialogs, but no audit with a reader is
  written down anywhere.
