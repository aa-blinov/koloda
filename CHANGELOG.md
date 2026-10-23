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

### Changed

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

- Nothing yet.

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
