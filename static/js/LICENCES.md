# Third-party assets in `static/`

Every vendored file needs a line here. No exceptions, no orphan files.

| File | Source | Version | Licence |
|---|---|---|---|
| `js/htmx.min.js` | [htmx.org](https://htmx.org) via unpkg | 2.0.4 | [BSD 2-Clause](https://github.com/bigskysoftware/htmx/blob/master/LICENSE) |

Fonts (Anton, Inter, Roboto Mono) are loaded from Google Fonts and are all under the
[SIL Open Font Licence 1.1](https://openfontlicense.org). Self-hosting them is a Phase 3
item: a bar's shared wifi is exactly where a third-party font origin costs a second of
first paint.

Audio assets land in `audio/LICENCES.md` in Phase 4, under the sourcing rules in the
build spec: no broadcast themes, no fight songs, no commercially released tracks.
