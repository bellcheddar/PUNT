#!/usr/bin/env bash
# Regenerate the Elementor bundle from the repository README.
#
# Everything in this directory is generated. Edit ../../README.md and re-run.
#
#   bash docs/elementor/forge.sh
#
# Produces, beside this script:
#   punt.md            a copy of README.md, which is what the forge reads
#   punt.polished.md   the branded markdown (idempotent; the README is already to standard)
#   punt.html          paste into an Elementor HTML widget
#   punt.preview.html  open locally to see the styled result before publishing
#   elementor-widget-markdown.css   paste into the widget's Advanced -> Custom CSS
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORGE="$HOME/.claude/skills/marcs-vibe-coding/scripts/readme_forge.py"
RAW="https://raw.githubusercontent.com/bellcheddar/PUNT/main"

cp "$HERE/../../README.md" "$HERE/punt.md"
cd "$HERE"
python3 "$FORGE" punt.md

# The README's screenshots are relative, which is what GitHub needs and what the
# house standard requires. Elementor serves this from marcdeller.com, where a
# relative `docs/screenshots/...` resolves against that site and 404s, silently:
# a broken image is an empty box, not an error. So the HTML (and only the HTML)
# gets absolute raw.githubusercontent URLs.
python3 - "$RAW" <<'PY'
import re, sys
raw = sys.argv[1]
for name in ("punt.html", "punt.preview.html"):
    with open(name, encoding="utf-8") as handle:
        body = handle.read()
    body = re.sub(r'src="(docs/[^"]+)"', lambda m: f'src="{raw}/{m.group(1)}"', body)
    if name.endswith("preview.html"):
        # The widget CSS sets a dark text colour and no background, because on
        # marcdeller.com the page supplies one. The preview supplies none
        # either, so a browser set to dark paints it black and renders the
        # whole check as dark grey on near-black. The preview exists to be
        # looked at, so it gets the light ground the real page has.
        body = body.replace("<body>", '<body style="background:#ffffff;color:#111111">')
    with open(name, "w", encoding="utf-8") as handle:
        handle.write(body)
    print(f"absolutised images in {name}")
PY
