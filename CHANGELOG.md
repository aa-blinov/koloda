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
