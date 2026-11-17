# Bundled screen fonts

The screen follows DESIGN-LANGUAGE, "Ledger" (`docs/decisions/design-language.md` §3, requirement, 2026-10-04: "go with
Ledger"): Manrope (variable weight; the screen uses 500, 600, 700 and 800) for everything, headings to body; Geist Mono for
meta and figures that are not read down a column (the clock, rail counts, badges, IDs). Berkeley Mono stays for code,
keyboard keys and raw records (`--font-code`) and for the seating chart's box-drawing seat frames. Both Manrope and Geist
Mono were already bundled (provenance below); nothing new was downloaded for Ledger. The app never fetches fonts from
another site's assets.

The Magazine edition (2026-10-03 to 2026-10-04) used Hedvig Letters Serif, Instrument Serif and Instrument Sans,
downloaded once, by hand, on 2026-10-03, from pinned Fontsource 5.3.0 packages (Google Fonts' own builds), Latin and Latin
Extended subsets. They remain bundled for older asset references but are not used by the screen. Their SIL Open Font
Licenses are retained beside them.

Hedvig Letters Serif ([upstream](https://github.com/KanonFoundry/HedvigLetters)). License: `hedvig-letters-serif-OFL.txt`.

- `hedvig-letters-serif-latin-opsz-normal.woff2` (34,856 bytes): [https://cdn.jsdelivr.net/npm/@fontsource-variable/hedvig-letters-serif@5.3.0/files/hedvig-letters-serif-latin-opsz-normal.woff2](https://cdn.jsdelivr.net/npm/@fontsource-variable/hedvig-letters-serif@5.3.0/files/hedvig-letters-serif-latin-opsz-normal.woff2)
  SHA-256: `2d555f36931055ec6df018ce6ee333036f145943dd8229b3ab24faedd5b9d989`
- `hedvig-letters-serif-latin-ext-opsz-normal.woff2` (13,476 bytes): [https://cdn.jsdelivr.net/npm/@fontsource-variable/hedvig-letters-serif@5.3.0/files/hedvig-letters-serif-latin-ext-opsz-normal.woff2](https://cdn.jsdelivr.net/npm/@fontsource-variable/hedvig-letters-serif@5.3.0/files/hedvig-letters-serif-latin-ext-opsz-normal.woff2)
  SHA-256: `0c3c8add746031997a663f298d73853b7c70d27d3ec1a742bc52b574b78df7d1`

Instrument Serif ([upstream](https://github.com/Instrument/instrument-serif)). License: `instrument-serif-OFL.txt`.

- `instrument-serif-latin-400-normal.woff2` (21,032 bytes): [https://cdn.jsdelivr.net/npm/@fontsource/instrument-serif@5.3.0/files/instrument-serif-latin-400-normal.woff2](https://cdn.jsdelivr.net/npm/@fontsource/instrument-serif@5.3.0/files/instrument-serif-latin-400-normal.woff2)
  SHA-256: `5eb09b5ac0e28b67c2f041c8ba6d244604ca0c0980d65912ab2d47fed84ddc31`
- `instrument-serif-latin-ext-400-normal.woff2` (11,604 bytes): [https://cdn.jsdelivr.net/npm/@fontsource/instrument-serif@5.3.0/files/instrument-serif-latin-ext-400-normal.woff2](https://cdn.jsdelivr.net/npm/@fontsource/instrument-serif@5.3.0/files/instrument-serif-latin-ext-400-normal.woff2)
  SHA-256: `290e6267dd833bf5f899eba4c29ad0a9b09dbe53f6075b18af38057159e1ff20`
- `instrument-serif-latin-400-italic.woff2` (22,128 bytes): [https://cdn.jsdelivr.net/npm/@fontsource/instrument-serif@5.3.0/files/instrument-serif-latin-400-italic.woff2](https://cdn.jsdelivr.net/npm/@fontsource/instrument-serif@5.3.0/files/instrument-serif-latin-400-italic.woff2)
  SHA-256: `5a51946dfffa82972bc98745359c46761515641fda557c25116459a9f83da4a7`
- `instrument-serif-latin-ext-400-italic.woff2` (12,384 bytes): [https://cdn.jsdelivr.net/npm/@fontsource/instrument-serif@5.3.0/files/instrument-serif-latin-ext-400-italic.woff2](https://cdn.jsdelivr.net/npm/@fontsource/instrument-serif@5.3.0/files/instrument-serif-latin-ext-400-italic.woff2)
  SHA-256: `d0014821252a8a770bf7551641572f239a8ede83d75652d84fef0c3627751baf`

Instrument Sans ([upstream](https://github.com/Instrument/instrument-sans)). License: `instrument-sans-OFL.txt`.

- `instrument-sans-latin-wght-normal.woff2` (30,092 bytes): [https://cdn.jsdelivr.net/npm/@fontsource-variable/instrument-sans@5.3.0/files/instrument-sans-latin-wght-normal.woff2](https://cdn.jsdelivr.net/npm/@fontsource-variable/instrument-sans@5.3.0/files/instrument-sans-latin-wght-normal.woff2)
  SHA-256: `2ee17598a98d8a59e4df8152d015bec9ab8e4d5672cc0ab42bef806b568e3971`
- `instrument-sans-latin-ext-wght-normal.woff2` (11,144 bytes): [https://cdn.jsdelivr.net/npm/@fontsource-variable/instrument-sans@5.3.0/files/instrument-sans-latin-ext-wght-normal.woff2](https://cdn.jsdelivr.net/npm/@fontsource-variable/instrument-sans@5.3.0/files/instrument-sans-latin-ext-wght-normal.woff2)
  SHA-256: `c4fcfea41f2c1cfeea9211fa43679845454a1d0e0d7e95e069c7e73c4ae302d2`

The Lambda design language (2026-09-26 to 2026-10-03) used Suisse Int'l Semibold and Suisse Int'l Mono; their
files and the older Fraunces files remain bundled for older asset references but are not used by the screen. Their
provenance follows, with Geist Mono's and Manrope's (both used again since Ledger).

Suisse Int'l (Swiss Typefaces) and Berkeley Mono (US Graphics) are commercial fonts licensed to the original
deployment only, so this public copy does not include them. Their faces in `fonts.css` load only from a copy installed on
the viewer's computer (`local()`); otherwise each font stack falls back to its next face. The browser check of the seat
frames (`tests/browser_theme.py`) expects Berkeley Mono to be installed.

Geist Mono comes from the [official Vercel v1.7.2 release](https://github.com/vercel/geist-font/releases/tag/v1.7.2),
using its original variable WOFF2 files (weights 100–900). License: `geist-OFL.txt`.

- `geist-mono-wght.woff2` (71,368 bytes), upstream `GeistMono/webfonts/GeistMono[wght].woff2`.
  SHA-256: `fba8f577f38a2bbcbe818efa6348dd58f36303a10b8737c42fefad275be563ab`
- `geist-mono-italic-wght.woff2` (78,784 bytes), upstream `GeistMono/webfonts/GeistMono-Italic[wght].woff2`.
  SHA-256: `926f2751ea0c8f737baa4bbd7459376fcbed98f31fbbc7ad19f427010a25e6ad`

The Manrope and retained Fraunces files are from pinned Fontsource 5.3.0 packages;
their upstream SIL Open Font Licenses are retained:

- `fraunces-latin-standard-normal.woff2` (67,304 bytes): [https://cdn.jsdelivr.net/npm/@fontsource-variable/fraunces@5.3.0/files/fraunces-latin-standard-normal.woff2](https://cdn.jsdelivr.net/npm/@fontsource-variable/fraunces@5.3.0/files/fraunces-latin-standard-normal.woff2)
  SHA-256: `7234ed860a9cc83045413c4faee63c960a8f2d1917adcf728119307d56e0d783`
- `fraunces-latin-ext-standard-normal.woff2` (59,388 bytes): [https://cdn.jsdelivr.net/npm/@fontsource-variable/fraunces@5.3.0/files/fraunces-latin-ext-standard-normal.woff2](https://cdn.jsdelivr.net/npm/@fontsource-variable/fraunces@5.3.0/files/fraunces-latin-ext-standard-normal.woff2)
  SHA-256: `a2930b27d13a228bd9ab6a49269b5f800237892ad560cb9dd7fab01b1620f88e`
- `manrope-latin-wght-normal.woff2` (24,836 bytes): [https://cdn.jsdelivr.net/npm/@fontsource-variable/manrope@5.3.0/files/manrope-latin-wght-normal.woff2](https://cdn.jsdelivr.net/npm/@fontsource-variable/manrope@5.3.0/files/manrope-latin-wght-normal.woff2)
  SHA-256: `a30ddcd349703aff7464c34bef3fffdff405ee50c113440d7c8693c02d210972`
- `manrope-latin-ext-wght-normal.woff2` (15,120 bytes): [https://cdn.jsdelivr.net/npm/@fontsource-variable/manrope@5.3.0/files/manrope-latin-ext-wght-normal.woff2](https://cdn.jsdelivr.net/npm/@fontsource-variable/manrope@5.3.0/files/manrope-latin-ext-wght-normal.woff2)
  SHA-256: `3911b66d9f2e005a4b989223405d0e5032619c668597ba467cc76a23c8fffcfb`

Upstream sources: [Fraunces](https://github.com/google/fonts/tree/main/ofl/fraunces), [Manrope](https://github.com/google/fonts/tree/main/ofl/manrope).
