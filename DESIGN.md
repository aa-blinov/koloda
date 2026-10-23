---
name: koloda
description: A reader for public Telegram channels on the Monos kit: warm palettes, one monospaced face, cards on a ground, and icons drawn as inline SVG in one stroke.
colors:
  dark:
    bg: "#282828"
    surf: "#3c3836"
    raise: "#504945"
    line: "#504945"
    edge: "#928374"
    fg: "#ebdbb2"
    dim: "#d5c4a1"
    faint: "#d5c4a1"
    acc: "#9ec5b8"
    acc-ink: "#282828"
    acc-cont: "#504945"
    on-acc-cont: "#ebdbb2"
    sec-cont: "#504945"
    on-sec-cont: "#ebdbb2"
    inv-surf: "#ebdbb2"
    inv-fg: "#3c3836"
    inv-acc: "#05525e"
    paid: "#fabd2f"
    error: "#ff7b6b"
    media-bg: "#000000"
    overlay: "rgba(0,0,0,.4)"
  light:
    bg: "#fbf1c7"
    surf: "#ebdbb2"
    raise: "#d5c4a1"
    line: "#d5c4a1"
    edge: "#7c6f64"
    fg: "#3c3836"
    dim: "#504945"
    acc: "#05525e"
    acc-ink: "#fbf1c7"
    acc-cont: "#d5c4a1"
    inv-surf: "#3c3836"
    inv-fg: "#fbf1c7"
    inv-acc: "#9ec5b8"
    paid: "#5c3f00"
    error: "#9d0006"
    media-bg: "#ebdbb2"
    overlay: "rgba(60,56,54,.3)"
typography:
  body:
    fontFamily: "JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.625
  ui:
    fontFamily: "JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "12px"
    fontWeight: 700
    letterSpacing: "0.1em"
  serif:
    fontFamily: "Georgia, Iowan Old Style, Times New Roman, serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.625
rounded:
  radius: "6px"
  pill: "999px"
  round: "50%"
spacing:
  1: "4px"
  2: "8px"
  3: "12px"
  4: "16px"
  5: "24px"
  6: "32px"
  7: "48px"
  8: "64px"
---

# Design system: koloda

The interface is built from the sources in `ui/src` and served from `ui/dist`.
Every value below comes from the tokens of the kit, and the CSS is the
normative layer. Where this document and the tokens disagree, the tokens are
right.

The tokens are the roles of the Monos design system, in the Gruvbox palette.
The interface texts are in Russian and address the reader as "ты". Product
truth: [PRODUCT.md](PRODUCT.md).

## Overview

**Creative North Star: "Paper and Ink"**

The feed reads like a printed page. One warm ground, one set of ink tones, a
single typeface, and the posts are cards laid on the ground with a hairline
between them. The dark theme is not the light theme turned down: it is the
same warm palette on a dark brown ground, and the text is the same parchment
in both.

The density is high and deliberate. The reader is looking at a stream of
short posts, so the type is small, the rhythm is tight, and nothing takes a
whole screen for itself. The service also runs on a phone, and the layout
there is the same page with a bottom bar, one column, and controls that are
large enough to hit with a finger.

The confirmed rejections:

- No emoji anywhere, and no Unicode symbol standing in for an icon.
- No gradient, no glow, no coloured stripe on the side of a card.
- No rounded rectangle that is not the one radius.
- No second typeface inside a screen. The serif face is a setting of the
  profile, not a choice per component.

## Where the system lives

The system is the [Monos kit](https://github.com/aa-blinov/monos-ui): the
palettes, the radius, the spacing scale, the controls and the icons of the
interface. Koloda takes the kit as it is and adds only what belongs to the
product: the column of the feed, the text of a post, the album of the frames
and the rows of the catalog.

The kit is a dependency with a fixed version (`ui/package.json`), and its tokens
are the only source of colour, radius and spacing in the product. A colour of
one's own breaks the theme, and `check_ui.py` fails on it.

The rules below describe the product on top of the kit. Where the kit and this
document used to disagree, the kit wins.

**Key characteristics:**

- The palette and the lightness are chosen by the profile and stored with it:
  one of the six palettes of the kit, dark or light or as in the system.
- One typeface, JetBrains Mono, served from the address of the service
  itself, so the page does not ask a third party for a font.
- One radius, the one of the kit, on every rectangle. The pill belongs to the
  search, the circle to the dots and to the scrollbar.
- One spacing scale, the one of the kit: 2, 4, 6, 8, 10, 12, 16, 24 and 32 px.
  A value outside the scale is not written at all.
- Depth is tone. A shadow appears only on the dialog and when a card is
  lifted.
- Icons are inline SVG, 16 px, one stroke, `currentColor`.

## Colors

The roles below are the same in both themes; only the values change. The
frontmatter carries both sets.

### Surfaces

- **Ground** (`#282828` dark, `#fbf1c7` light): the page, and the inside of a
  card. A card is the ground with a hairline, so a card on the ground reads
  as a sheet of paper on a desk.
- **Surface** (`#3c3836`, `#ebdbb2`): the bar, the bottom bar on a phone, and
  the background of a field.
- **Raised** (`#504945`, `#d5c4a1`): the secondary container and the hover of
  a row.
- **Line** (`#504945`, `#d5c4a1`): the hairline around a card and around a
  dialog.
- **Edge** (`#928374`, `#7c6f64`): the border of a control that has to be
  found, such as a segmented group or a field on the phone.

### Text

- **Foreground** (`#ebdbb2`, `#3c3836`): the text of a post.
- **Dim** (`#d5c4a1`, `#504945`): the second line of a card, the label of a
  group, the empty state.
- **Faint** (`#d5c4a1`, `#504945`): the icon of a search field and the icon of
  an empty state. It shares the value of the dim text on purpose; the
  difference is the size, not the colour.

### Accent

- **Accent** (`#9ec5b8`, `#05525e`): the link, the focus ring and the mark of
  the current tab. It is the only colour that marks a choice. A button is text,
  not an accent fill (Monos).
- **Accent ink** (`#282828`, `#fbf1c7`): the text on the accent.
- **Accent container** and **secondary container** (`#504945`, `#d5c4a1`):
  the tinted ground under a selected row.

### Inverse and status

- **Inverse** (`#ebdbb2` on `#3c3836` dark, inverted in light): the snackbar.
  Its accent comes from the other theme, so the bar is legible in both.
- **Paid** (`#fabd2f`, `#5c3f00`): the marker of a paid post.
- **Error** (`#ff7b6b`, `#9d0006`): the state of a channel that failed.
- **Media ground** (`#000000`, `#ebdbb2`): behind a photo or a video.
- **Overlay** (`rgba(0,0,0,.4)`, `rgba(60,56,54,.3)`): behind a dialog.

### Named rules

**The One Accent Rule.** The accent marks a choice and a state. A second
element that is accent on the same screen means one of the two is wrong.

**The Contrast Floor Rule.** Every text pair holds 4.5:1 in both themes.
`ThemeContrastTest` in `test_app.py` reads the tokens out of `index.html` and
fails when a pair drops below it. A change to the palette means running that
test and recomputing the numbers quoted in the README.

## Typography

**Display face:** JetBrains Mono.
**Body face:** JetBrains Mono.
**Serif face:** Georgia, for the profile that asks for it.

JetBrains Mono is bundled in `fonts/` as four woff2 files and served from the
address of the service itself, with a system stack behind it in case the file
does not load. The profile can switch the interface to a serif face, and the
reading face follows it. The server accepts three names for the setting,
`mono`, `serif` and `sans`; the dialog presents the first two, and the third
has no rule in the CSS, so it falls back to the default.

### Hierarchy

- **Body** (400, 14px, 1.625): the text of a post, the labels of a field.
- **Interface** (700, 12px, 0.1em, uppercase): a button. The tracking and the
  capitals are what make a small button read as a button.
- **Small** (400, 12.5px and 13px): the label of a group, the metadata of a
  card, the text of a segment.
- **Field** (400, 16px): an input. The size is a floor, so that iOS does not
  zoom the page when the field takes the focus.
- **Title** (400, 20px and above): the name of a channel and of a dialog.

## Layout

One column in the middle of the page: `main` is at most 748 px wide and
centred, with 24 px of padding on each side. The bar above it is wider, at
most 1120 px, because it holds the search field and the filters.

A post is a card: a hairline border, the radius, 16 px of vertical and 24 px
of horizontal padding, and 12 px of space under it. The cards are the
content, so the space between them is smaller than the space inside them.

The spacing scale is 4 px and its multiples: 4, 8, 12, 16, 24, 32, 48, 64.
Two pixels appear only as the hairline gap between the segments of a group. A
value outside the scale is rounded up to the next one, so 10 becomes 12, 14
becomes 16 and 28 becomes 32.

Two breakpoints. Below 900 px the search field takes a row of its own, because
five items in the bar do not fit and the field was the one that collapsed.
Below 640 px the layout becomes a phone: one column, the padding drops to 16
px, and the channel list becomes a dialog that covers the page.

The bottom bar is 64 px tall, and the sections in it are the three main
screens: the feed, the catalog and the saved. On a wide screen the same three
sections stand in a sidebar of 232 px on the left, and the bottom bar is not
shown at all: a finger does not reach the bottom of a wide screen, and there
the place is better spent on naming every destination.

## Navigation

The interface has five screens and three of them are main ones.

- **Main:** the feed, the catalog and the saved posts. They are peers: one tap
  away, always in the same place, and the current one is said out loud by the
  colour and by `aria-current`.
- **Sub:** the channel and the profile. They open from a main screen and close
  with the arrow in the header, back to where the reader came from. On them
  the bottom bar is hidden and the arrow takes its place: the reader is one
  step deep, and the way out is where they came from.

**The navigation is not the same on the two screens, and it is not the same
screen twice.** On a wide screen there is room, the pointer is exact, and every
destination is worth naming: the sections stand in a **sidebar on the left**,
with the name of the service above them and the channels and the profile
below. On a phone the finger does not reach the top of the screen, so the same
three sections stand in a **bottom bar**, with the icon over the word, and the
channels and the profile stay in the header as two signs. The header on a
phone holds the name of the service on the left and the search, the channels
and the profile on the right.

The screen of a phone in the lying position is wider than 900 px, and there the
sidebar would be at the side, where the finger does not reach. The width alone
is not the sign of a phone: the **pointer** is. The layout of the phone is
chosen by `(pointer: coarse)` as well as by the narrow width, and the sidebar
only appears where the pointer is a mouse.

**The search stands in the top right corner of the window**, and the filters
of the feed — the channel picker and "hide the read ones" — stand under it,
with the same right edge. The eye goes from the field to the posts without a
turn. On a phone the header has no room for the filters, and they stand above
the feed instead. The same filters in both places, one state: the filters
live in the shell, and the feed only draws the posts.

The signs are not decoration and not one sign for everything:

- the feed is **lines of posts**;
- the catalog is a **grid of squares** — a directory of channels;
- the saved is a **bookmark**;
- the channels and the collections are a **folder**;
- the profile is a **person**.

No two neighbours in the navigation share a sign, and `check_ui.py` fails when
they do: the list of the channels and the catalog used to carry the same one,
and the sign said nothing.

The address of the screen is in the hash: `#/feed`, `#/saved`, `#/cat`,
`#/channel/<name>`, `#/me`. Every screen goes into the history of the
browser, so the back button of the browser does the same as the arrow in the
header. The arrow of the header never leaves the site: at the root it leads
to the feed.

A screen that is opened again finds the reader where they were: the place of
the scroll is kept per screen, and it is restored after the content of the
screen has arrived. The pictures of the posts load later than the posts
themselves, so the place is confirmed for a moment after the screen appears.

The change of screen is one short movement of 220 ms: forward the new screen
comes from below, backward from above. The system asks for less motion, and
then there is no movement at all.

## Elevation & Depth

Flat at rest. A card sits on the ground with a hairline and no shadow; a
selected row is a tone lighter, not a shadow. The only two shadows in the
system are `--sh-1`, `0 1px 2px rgba(0,0,0,.03)`, for a card that is being
lifted, and `--sh-2`, `0 25px 50px -12px rgba(0,0,0,.5)`, for the dialog and
the overlay behind it.

Depth is carried by tone, by the hairline and by the order of the surfaces.
A new shadow in a new place is a new rule and has to be argued for.

## Shapes

One radius, 6 px, on every rectangle: cards, dialogs, buttons, segments,
fields, the empty state. The aliases `--r-xs`, `--r-sm`, `--r-md` and
`--r-xl` all point at it, so a new element cannot pick a different one by
accident.

Two shapes are exceptions and both are named: the pill, 999 px, for the
search field and for the pill under the icon of the current tab; and the
circle, 50%, for the dots of the busy state and for the thumb of the
scrollbar.

The border of a card and of a dialog is one pixel of `--line`. The border of a
control that has to be found is `--edge`, which is lighter against the
ground.

## Components

### Buttons

The text is the whole button (Monos). A filled or outlined button contradicts
the type, so no button has a fill or a frame at rest.

- **Shape:** the text is the button. The radius, 6 px, belongs to the focus
  ring, the cards and the dialogs.
- **Primary:** the foreground colour as text, 12 px, weight 700, capitals and
  0.1em of tracking, 40 px high at the least.
- **Secondary:** the dim text, the same size, weight 700. No fill, no frame.
- **Hover and press:** opacity 0.7 on hover and 0.55 when pressed. The accent
  is the focus ring and nothing else on a button.
- **Spacing:** two text actions sit at least 24 px apart, so that they never
  read as one phrase.
- **Chosen option:** a toggle that picks one of several options marks the
  chosen one with the secondary container. The others stay text.

### Inputs and fields

- **Style:** a field is the surface colour, the pill radius, no border on a
  wide screen, and at least 44 px high. On a phone it loses the pill and
  takes an inset rule of `--edge` under the text.
- **Text:** 16 px, so that a tap does not zoom the page.
- **Error:** the error colour, and a sentence under the field. The state is
  never a colour alone.

### Segmented groups

- **Style:** no frame on the group. The options are text with a 2 px gap
  between them. On a phone the group goes the width of the page.
- **Selected:** the secondary container under the chosen option, with the
  foreground on it.

### The post card

- The ground with a hairline, the radius, and no shadow.
- The text at 14 px with a line height of 1.625, and the pre-wrap of the
  text of the post so that a line break in the message stays a line break.
- The media goes straight to the CDN of Telegram, and the page does not proxy
  it. A video does not load until play is pressed.

### The dialog

- 420 px at the most, 86vh at the most, the ground, a hairline, 32 px of
  padding and `--sh-2`.
- On a phone it covers the page, keeps its own scroll and takes the safe
  area at the bottom.

### Icons

- Inline SVG, 16 px, one stroke of 1.7, round caps and joins, filled with
  `currentColor`, drawn once in a hidden block and used with `<use>`.
- An icon that is bigger than the interface says something else: the icon of
  an empty state is 28 px, the icon of the current tab is 24 px.

### The bottom bar on a phone

- Three columns, 64 px tall, the icon above the label at 12 px.
- The icon is 20 px, the label is 11 px, and the gap between them is 2 px.
- The bar is a solid ground: the posts do not show through it.
- The bar is hidden on the sub-screens: there the arrow in the header leads
  back.

### The sidebar on a wide screen

- 232 px wide, fixed, with the name of the service above the sections and the
  channels and the profile below them.
- The rows are the rows of the kit: the icon on the left, the name next to it,
  and the current section on a lighter ground.
- The search is not in the sidebar: it stays in the header, on the right, over
  the column of the posts, so that the eye goes from the field to the list
  without a turn.

## Do's and Don'ts

### Do:

- **Do** take every colour, radius and distance from the tokens in `:root`. A
  value of its own in the markup is a second source of truth.
- **Do** run `python3 check_ui.py` after a change to `index.html`, and
  `python3 -m unittest -v` when a token changes, because the contrast test
  reads the tokens out of the file.
- **Do** keep the touch targets at 44 px on a phone, and the bottom bar at
  64 px.
- **Do** draw an icon as inline SVG in one stroke, and take its colour from
  `currentColor`.
- **Do** round a distance that is outside the scale up to the next value on
  the scale.

### Don't:

- **Don't** hard-code a colour, a radius or a font in the markup.
- **Don't** put an emoji or a Unicode symbol where an icon belongs.
- **Don't** add a second radius, a shadow on a card, or a gradient.
- **Don't** use a colour alone to say that something is selected, that
  something failed, or that something is unread. Say it in words as well.
- **Don't** put a second typeface inside a screen. The serif face is a
  setting of the profile.
- **Don't** change a token without reading the contrast test, and without
  correcting the numbers in the README.
