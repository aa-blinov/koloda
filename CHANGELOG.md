# Changelog

All notable changes to koloda. Versions are ordered newest first.

<!--
  The format is Keep a Changelog. Each entry says what changed and why it
  was worth changing.

  The traps do not go here. They go in AGENTS.md, because the changelog is
  read by the people who use the software and the traps are read by whoever
  works on the code next.
-->

## [Unreleased]

## 0.3.5 — 2026-10-24

### Added

- A full-screen mode for the deck on a phone: a button in the deck's top row
  hides the header and the tab bar, and the card takes the whole screen. The
  mode is remembered on the device.

### Fixed

- The card lagged on a phone while it was dragged: the next card no longer
  relayouts the page on every touch move.
- The photos of the channels did not appear for many channels: the server reads
  twelve channels per request, and the page now asks for the rest.
- A channel that the server had not read yet when the feed opened left its posts
  out until a reload: the feed is asked again while such a channel is loading.

## 0.3.4 — 2026-10-24

### Fixed

- «Отписаться» in the list of channels did nothing: no handler listened to it.
  It unsubscribes now, with the same undo as the channel page. The list closes
  so that the undo line stays in reach.
- A collection editor that was open when its collection got deleted elsewhere
  refreshes the list on save, so the stale row goes away.

## 0.3.3 — 2026-10-24

### Fixed

- The page carries the content policy in its own meta tag as well as in the
  header. A proxy that replaces the header no longer switches off `script-src`
  and `style-src` in the browser; `frame-ancestors` stays with the header.

## 0.3.2 — 2026-10-24

### Fixed

- A signed-in reader who changes the address no longer learns from the answer
  that the address belongs to another profile. A taken address answers as a
  sent letter would, and counts against the same letter limit.

## 0.3.1 — 2026-10-24

### Fixed

- A channel the reader unsubscribed from does not come back when an old tab or
  another device merges its browser state: the browser's channels move into an
  empty profile only.
- Registration with an address that is already held answers as a sent letter
  would, and writes nothing, so the answer no longer shows who holds it. A
  taken name still answers as taken.
- The licence in the documentation matches the LICENSE file (MIT).

The tag v0.3.0 stays as it was published; its house-style check failed on the
licence wording, which this release fixes.

## 0.3.0 — 2026-10-24

### Changed

- The interface is built on the Monos UI kit 1.5.1 (from 1.0.3). The components
  the product uses kept their props; the kit's new sizes now apply to the search
  field in the header, which is 36 px high instead of 40.
- The faces come from the kit: JetBrains Mono by default, Inter for the sans
  face and Source Serif 4 for the serif face, instead of Georgia and the system
  sans. The product's own font files, their route and their test are removed.
- The safe-area insets come from the kit's tokens, and the page sets the kit's
  device profile: iPhone or Android on a finger, desktop with a mouse.
- The check of the interface fails when a font is loaded from another address.

### Fixed

- Registration reaches the end: the letter says what to do, the link confirms the
  address, and the sign-in screen tells an unconfirmed profile what to do and
  offers to send the letter again.
- Password reset opens the form for a new password. The link works once and for
  30 minutes; a used or expired link says so and offers a new one.
- A confirmation link does not sign anybody in.
- A registration that has not been confirmed for 24 hours gives its name and its
  address to a new registration. A fresh or confirmed one still holds them.
- The letter limit of 60 seconds per address covers registration, and the answer
  no longer claims that a letter was sent when none was.
- The login lock follows the real client behind the reverse proxy (`TRUSTED_PROXY`);
  a forged forwarding header does not move it.
- A request body over 4 MB is not read, and a body that never arrives does not
  hold a thread for ever. The server queues 128 connections.
- The anonymous feed answers 401: the feed and the catalogue need a profile.
- Two collections with one name are not created in parallel; a channel name with
  a trailing newline is refused; a superscript digit in a parameter is a bad
  request, not a broken connection.
- Channel titles show their HTML entities decoded, and the page of a channel takes
  its title from the preview.
- A Content-Security-Policy is sent with the answers.
- A Telegram login belongs to the profile that started it.
- Search reads `%` and `_` as plain text.
- Read marks are capped at 20 000 per profile.
- The labels of the bottom bar reach 4.5:1 contrast on every palette.
- The saved screen says that nothing is saved, and does not offer the feed's button.
- The catalogue's sorting fits the screen on a phone.
- A long channel name ends with an ellipsis in a post.
- The photo in the viewer is centred and Escape closes it the first time.
- All five profile tabs are visible on a phone.
- The reaction counters are readable on every palette.
- The undo line goes away after four seconds, and the buttons under it are free.
- The search field clears with its cross, Escape and the reset button alike.
- A search of one letter says what to type instead of doing nothing.
- The undo of a swipe brings the card back to the top of the deck.
- A post can be removed from the saved list on a phone, and the list updates at once.
- The sync queue is sent again when the network returns, without a button.
- A link from a letter opens its screen for a signed-in reader too.

## 0.2.0 — 2026-10-24

### Changed

- A link in the text of a post is easier to tap on a phone: its area is taller
  than its line, and the layout does not move.
- The colour of the browser bar on a phone follows the palette, and it is set
  as soon as the page loads, so the sign-in screen gets it too.

- The deck on a phone looks like a deck: the card fills the stack and lifts off
  the page, the next card shows as a strip under it, and two round buttons under
  the deck save and mark read, the same as the gesture. A card flies off the
  screen instead of snapping back to the centre first. Pulling a card down no
  longer refreshes the feed: the card scrolls, and the button over the deck
  refreshes it.
- The next card of the deck rises with the finger, and a post with pictures
  shows its first picture as the face of the card. The line "Вернуть" after a
  gesture lies over the deck instead of pushing it down on every gesture.
- The phone is checked against the usual rules of touch interfaces. Tap targets
  are at least 48 px, the fields are 16 px so iOS does not zoom the page, the
  hover does not stick after a tap, the labels of the bottom bar are 12 px, and
  the deck keeps its height turned sideways. The channel rows of the dialog show
  the whole name, and the page keeps clear of the notch and the home bar.
- The "all channels" dropdown is gone: the folder with the channels and the
  collections does the same, and two controls with one action say nothing. The
  filter that is on is named by a chip with a cross, and the cross takes it
  off.
- The search stands in the top right corner of the window, and the filters of
  the feed — the channel picker and "hide the read ones" — stand under it with
  the same edge. On a phone the filters stand above the feed: the header has
  no room for them.
- The choice to hide the read ones is remembered on the device: a switch that
  returns to its old position every time is useless.
- A phone in the lying position is wider than 900 px, and there the sidebar
  stood at the side, where the finger does not reach. The layout of the phone
  is now chosen by the pointer as well as by the width.
- The navigation is not the same on the two screens any more. On a wide screen
  the three sections stand in a sidebar on the left, with the name of the
  service above them and the channels and the profile below; on a phone the
  same three sections stand in a bottom bar under the finger, and the channels
  and the profile stay in the header as two signs.
- The signs of the navigation mean one thing each: the feed is lines of posts,
  the catalog is a grid of channels, the saved is a bookmark, the channels and
  the collections are a folder, the profile is a person. Before that the list
  of the channels and the catalog carried the same sign, and the sign said
  nothing.
- The search is always in sight: it used to hide behind a magnifier in the
  header, and on a narrow screen the field moves to a second row. The hint says
  what is searched: "по каналу" in the catalog, "по постам" in the feed.
- The screen of the entry has a switch instead of two buttons: the current mode
  is visible.
- The Telegram login got its own tab in the profile: it is a whole possibility,
  and it used to be a paragraph inside the account tab.
- The state of the history of a channel speaks about what the reader sees:
  "503 поста загружено, остальные — только в Telegram" instead of "загружено
  503, дальше только в Telegram".
- The saved posts have their own heading: the column is the same as the feed's,
  and without it the two were hard to tell apart.
- The channel page explains why there is no "into a collection" until you
  subscribe.
- The categories of the catalog are sorted by name in Russian, and a failed
  request can be repeated without losing the row.
- The exit from the profile asks for a confirmation again: one click should not
  throw you out of the profile.

### Fixed

- The catalog's "All categories" button changed its own label and nothing
  else: the row of chips now wraps when it is open.
- On a phone the catalog column stretched to the width of its chips, about
  750 px on a screen of 390, and the right part of the screen was cut off.
- The collections of the reader were not loaded until the channels dialog
  was opened: the channel page and the dialog of a channel showed no
  collections after a reload.
- A dialog left open stayed on top of the next screen and took the clicks.
  Changing the screen now closes it.
- The gesture "pull to refresh" was gone: the three handlers were written, but
  nothing was signed to them, so the feed could not be pulled down. It works
  again at the top of the feed, and while the refresh runs the indicator stays
  on the screen and says "Обновляю…".
- The "all channels" button of the feed did nothing: it fired an event that
  nobody was listening to. It opens the list of the channels and the
  collections now.
- The bottom bar of the phone was transparent: the kit has no token
  `--ui-canvas`, and the posts of the feed showed through the sections. The
  checker now knows the tokens of the kit and fails on one that is not there.
- Two neighbours in the navigation carried the same sign, and the checker did
  not see it: it now fails when the signs of the sections collide or when a
  sign of a section is the same as the sign of a button next to it.
- The feed was mounted twice after the sign-in: the screen of the entry set the
  session and read the state, and the shell did the same again, so the posts
  were asked for twice and the screen was rebuilt under the reader.
- The feed asked for the posts before the subscriptions arrived, and the
  server answered with the whole cache: the channels of other people flashed
  on the screen for a moment.
- The page of a channel had a second "back to the catalog" button next to the
  arrow in the header.
- A reference to a name that does not exist on the page of a channel broke the
  application on the way back from it. The check of the sources fails the
  build on such a warning now.
- The exit from the profile broke the connection: the body of the request was
  not read by the route and stayed in the socket, so the reload after the exit
  got "501 Unsupported method ('{}GET')" instead of the screen of the entry.
  Now the body is always read out.
- The number of the collections next to a channel did not appear: the store was
  read only inside a helper, and Svelte does not see such a dependency — the
  button kept the old text.
- The search did not work anywhere: the kit sends a native input event, which
  has no value of its own, so the request went out without a query while the
  field looked alive.
- The keys j, k and n did not scroll the feed, and u fell with an error.
- The collection filter did not re-read the feed, and "All channels" did not
  drop it.
- The channel page showed the name of the channel instead of its title.
- The editor of a collection showed only the channels that were not in it, so
  a tick could not be taken off.

- The interface moved to the Monos kit: the sources are in `ui/`, the build in
  `ui/dist`, and the server hands over the built files. The palette, the
  radius, the spacing and the controls are the kit's; the product keeps the
  column of the feed, the text of a post, the album of the frames and the rows
  of the catalog.
- The theme is now a palette of the kit (Gruvbox, GitHub, Dracula, Nord,
  Solarized, Catppuccin), and the lightness is a separate setting: dark, light
  or as in the system. The old setting was a pair "light or dark", so its value
  moves to the lightness when the profile is read.
- The image is built in two stages: the interface on node, the service on
  Python. The image keeps only the ready files of the interface.

### Added

- The feed on a phone is now a deck of cards, one post at a time, with no
  vertical scroll of the column: the card is dragged sideways, to the right it
  is marked read, to the left it is saved, the card follows the finger and says
  what will happen, the action can be taken back, and the next post comes
  forward. On a wide screen the same feed stays a column.
- The post card on a phone is now dragged sideways, as in the apps for
  meetings: to the right it is marked read, to the left it is saved. The card
  follows the finger and says what will happen, the action can be taken back,
  and the scroll of the feed is not disturbed.

### Fixed

- A channel whose posts mention "captcha" is no longer treated as blocked by
  Telegram: the whole channel was unavailable, and the error said "t.me asked
  to wait".
- Replies and forwards are shown again. The parser looked for classes that
  Telegram stopped using in the preview page, so both were always empty.
- The description of a channel in the catalog is no longer missing: the client
  read a field the server does not send.
- The title of a channel on its own page now follows the name Telegram gives.
- A private post can be saved and is shown in the saved list. Saving failed
  with "there is no such publication".
- Clicking the channel name in the feed filters the feed by that channel
  again. After the first page was loaded, the second click handler cancelled
  the filter.
- The search no longer throws the reader back to the top of the feed on every
  keystroke.
- The key `?` opens the list of the keys, as the documentation promises.
- The keys of the feed do nothing on the screens where the feed is not on
  screen, and `/` does not steal the key on the screens without a search.
- An unread post shows a dot and a word next to the channel name: the unread
  state was visible only as a shade of text.
- The list of the channels keeps the keyboard focus after it is redrawn, and
  the editor of a collection hides the list of the channels behind it.
- The catalog is sorted over all the loaded channels, not over one page, and
  the button "show more channels" survives a failed page.
- The feed keeps at most 400 posts in the page: the memory of the browser no
  longer grows with every loaded page.
- A number that does not fit into the database is answered with "wrong post"
  instead of breaking the connection.
- A broken body in the login by QR is answered with 400 instead of breaking
  the connection.
- The answer to HEAD has no body.
- The old databases with empty or duplicate addresses no longer prevent the
  service from starting.
- The full save of the state no longer wipes the subscription to a private
  channel.
- The names from the external catalog are checked before they reach the
  subscriptions and the markup.
- The details of an internal error no longer reach the browser.
- The Telegram login no longer leaves a connected client behind, the file of
  the session is created with the rights 0600, an account with a two-factor
  password says so instead of "the link expired", and "Disconnect Telegram"
  really deletes the file of the session and cancels the login in progress.
- The audit of the media reports how much it managed to check and returns a
  non-zero code when it could not check everything: it used to report "0
  mismatches" exactly when the parser had stopped seeing the posts.
- The requests to t.me from the request path are throttled, and a channel
  that failed in the background is retried in a quarter of an hour instead of
  in six hours.

## 0.1.0 — 2026

### Added

- The first release. What it does, in one paragraph, and what a user can
  do with it that they could not do before.
