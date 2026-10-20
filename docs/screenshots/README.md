# Screenshots

The pictures of the interface. They are in the `docs/` folder next to this
one, and the [README](../../README.md) points at them by that path.

Every picture shows the live data of the service: real channels, real posts
and the names that go with them. A picture of this interface is a picture of
what the reader subscribes to, so the same care as for the posts themselves
belongs to it.

## The pictures

| File | What it shows | Size |
| --- | --- | --- |
| `../lenta.png` | The feed: the posts of the chosen channels mixed in the order of the publication. | 1280×860 |
| `../katalog.png` | The catalog: the channel list of TGMania with the categories and the three sorts. | 1280×860 |
| `../poisk.png` | The search over the posts of the feed, with the results and the search field. | 1280×860 |
| `../galereya.png` | An album with several frames, and the counter of the frames in the corner. | 700×771 |
| `../prosmotr.png` | A frame opened whole, with the arrows that page through it. | 1280×860 |
| `../vhod.png` | The sign-in screen of a profile. | 1280×860 |
| `../svetlaya.png` | The same feed in the light theme. | 1280×860 |
| `../mobile.png` | The feed on a phone, with the bottom bar. | 840×1800 |
| `../mobile-svetlaya.png` | The same phone view in the light theme. | 840×1800 |
| `../prosmotr-mobil.png` | A frame opened whole on a phone: the arrows page through it, the cross closes it. | 840×1800 |

## Where they come from

They are captures of the running service on the machine of the owner, in the
browser, at the sizes above. The wide ones are 1280×860 and the phone ones
are 840×1800, and the sizes are kept when the interface is reshot: the
README lays the pictures out in a table, and a picture of a different size
breaks that layout.

The dark theme is the default. `svetlaya.png` and `mobile-svetlaya.png` are
the same screens with the theme of the profile set to `light`.

## Adding one

Put the file in `docs/` and reference it by relative path, the way the README
does:

```markdown
![The feed](docs/lenta.png)
```

The checker resolves every image reference, so a file that is renamed without
its reference being updated fails the build.
