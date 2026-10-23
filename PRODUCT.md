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

The service is a page in a browser tab that stays open next to a chat. On a
wide screen the feed is a column and is refreshed by pulling down at the top of
the list; on a phone it is a deck of cards and is refreshed by the button over
it. Nothing arrives on its own. The reader moves through the posts with the
keyboard: `j` and `k` for the next and the previous post, `n` for the first
unread, `u` to take a read mark back, `/` for the search, `?` for the list of
the keys, `Esc` to close a dialog.

On a phone the same service answers on the address of the machine and the
layout changes: a bottom bar with the three sections, a column of one, and an
album paged by a swipe.

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
  the next page marks nothing. In the deck of a phone the card is marked read
  by the swipe, not by the scroll.
- On a phone the feed is a **deck**: one post at a time, no vertical scroll of
  the column. The card is dragged sideways, as in the apps for meetings: to the
  right it is marked read, to the left it is saved. The card follows the
  finger, says what will happen, and the action can be taken back. A post
  that was on the screen has been seen, so a saved post is read as well and
  does not come back to the deck. The next page arrives when the deck runs
  low. On a wide screen the same feed stays a column: there is a mouse, and
  the column is read by scrolling.
- The vertical scroll of the feed is only on the wide screen. The card scrolls
  inside itself when the post is longer than the card.
- An unread post says so: a dot and the word next to the name of the channel,
  so the state is not carried by the shade of the text alone.
- The next page of the feed loads when the reader scrolls near its end; the
  button stays as the way to load it when that does not happen.
- The search runs over the posts of the feed and over the names in the catalog.
- The interface has three main sections: the feed, the catalog and the saved
  posts. They are one tap away, and the navigation is not the same on the two
  screens: on a wide screen the sections stand in a sidebar on the left, with
  the name of the service above them and the channels and the profile below;
  on a phone the same three sections stand in a bottom bar under the finger,
  and the channels and the profile stay in the header as two signs. Two
  screens open over them: the channel and the profile; they close with the
  back arrow in the header, back to where the reader came from.
- Every sign in the navigation means one thing: the feed is lines of posts,
  the catalog is a grid of channels, the saved is a bookmark, the channels
  and the collections are a folder, the profile is a person. Two neighbours
  never share a sign.
- The address follows the screen (`#/feed`, `#/saved`, `#/cat`,
  `#/channel/<name>`, `#/me`), and the back button of the browser does the same
  as the arrow in the header. The arrow never takes the reader off the site:
  at the root it leads to the feed.
- Every screen remembers its place: the reader returns from the catalog to the
  post where they left the feed.
- The search belongs to the section: posts in the feed and in the saved, the
  names in the catalog. Each section keeps its own query, and on the channel
  and the profile there is no search at all, because it would not work there.
- The search stands in the top right corner of the window, and the filters of
  the feed — the chosen channel or collection and "hide the read ones" — stand
  under it with the same edge. On a phone the filters stand above the feed,
  and the choice to hide the read ones is remembered on the device.
- The channel and the collection are chosen in one place: the folder with the
  channels and the collections. There is no second list with the same action.
  A filter that is on is named by a label with a cross, and the cross takes
  it off.
- The transition between the screens is short and is skipped when the system
  asks for less motion.
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
- A post can be saved to the profile: public and private alike. The saved list
  shows both, and only the posts of the profile that saved them.
- The backup file `koloda-lenta.jsonl` holds the channels and the read marks; a
  plain text list holds the channel links.
- The interface is on the Monos kit: the palette, the radii, the spacing and the
  controls are the kit's, and the product adds only the column of the feed, the
  text of a post and the rows of the catalog.
- The palette (Gruvbox by default) and the lightness (dark by default, or the
  one of the system) are kept in the profile and survive a reload. The font is
  JetBrains Mono by default, and can be sans or serif.
- The answers above 1 KB are compressed with gzip; the images, the video and
  the fonts are not.
- The request log is off by default.

Deliberately not true, and worth knowing before a change:

- The project carries no licence file.
- There is no automatic update.
- Nothing arrives in the browser by itself: the new posts wait for the reader.
  On a phone the deck has a button over it, on a desktop the feed refreshes
  when the page reloads or the channel changes.
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
- **The contrast of the text is at least 4.5:1** on every surface in every
  palette, and `ThemeContrastTest` checks it by reading the tokens out of the
  kit. A colour of one's own breaks the theme, and `check_ui.py` fails on it.
- **The theme can follow the system.** The "as in the system" option of the
  profile watches `prefers-color-scheme`.
- **A standard for screen readers has not been set.** The markup carries the
  roles of ARIA for the tabs and the dialogs, but no audit with a reader is
  written down anywhere.
