"""The deployment configuration, as invariants rather than as folklore.

Two of these settings look like ordinary tuning and are not: changing either one
breaks the app in a way that is invisible until ten people are in a bar.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


@pytest.fixture(scope="module")
def gunicorn_conf():
    spec = importlib.util.spec_from_file_location("punt_gunicorn", DEPLOY / "gunicorn.conf.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exactly_one_worker(gunicorn_conf):
    """Not a resource decision.

    The live feed's poller, the event engine's dedupe set and the Moment buffer
    are all per-process. A second worker polls ESPN a second time, keeps its own
    commentary history, and gives half the phones in the room a different feed
    from the other half. Scaling out means moving that state into Redis first.
    """
    assert gunicorn_conf.workers == 1


def test_threads_not_sync_workers(gunicorn_conf):
    """The SSE stream holds a connection open for the whole afternoon.

    With the default sync worker, ten phones on /stream occupy every worker
    permanently and nothing else is ever served again."""
    assert gunicorn_conf.worker_class == "gthread"
    assert gunicorn_conf.threads >= 12, "fewer threads than a league plus the bar screen"


def test_the_timeout_survives_a_quiet_sunday_evening(gunicorn_conf):
    """An SSE connection is supposed to sit idle between events. The default 30s
    would cut it on every quiet stretch and the client would reconnect for ever."""
    assert gunicorn_conf.timeout >= 120


def test_the_port_is_the_same_everywhere():
    """A mismatch between the unit and the vhost is a 502 that looks like an app
    crash. There is one number and it appears in three files."""
    conf = (DEPLOY / "gunicorn.conf.py").read_text("utf-8")
    nginx = (DEPLOY / "nginx-punt.conf").read_text("utf-8")
    provision = (DEPLOY / "provision.sh").read_text("utf-8")

    bound = set(re.findall(r"127\.0\.0\.1:(\d+)", conf))
    proxied = set(re.findall(r"proxy_pass http://127\.0\.0\.1:(\d+)", nginx))
    checked = set(re.findall(r"^PORT=(\d+)", provision, re.M))

    assert len(bound) == 1, f"gunicorn binds more than one port: {bound}"
    assert bound == proxied == checked, f"gunicorn {bound}, nginx {proxied}, provision {checked}"


def test_the_port_is_not_one_another_app_already_has():
    """Every app on the droplet is a Flask process on 127.0.0.1. Taking a port
    another one already has is a 502 on whichever loses the race."""
    taken = {
        "8000": "AlphaFraud", "8001": "chem_sage-web", "8002": "chatPDB-web",
        "8003": "BoltzMaker", "8004": "FlexAppeal", "8005": "PANTS",
        "8006": "CODSWALLOP", "8007": "ButtFold", "8008": "ALPHABETTI",
        "8009": "GOBSMACKED", "8010": "chatMCD",
    }
    port = re.findall(r"127\.0\.0\.1:(\d+)", (DEPLOY / "gunicorn.conf.py").read_text("utf-8"))[0]
    assert port not in taken, f"port {port} belongs to {taken.get(port)}"


def test_the_vhost_logs_where_the_hit_counter_looks():
    """mdeller-stats.py globs access.log* and mdeller.access.log*.

    A vhost that redirects its log to a private file is invisible to the site
    counter and reads a flat zero for ever, with nothing anywhere to say why.
    And the format name is not optional: a bare `access_log /path;` silently
    means `combined`, which carries no $host."""
    nginx = (DEPLOY / "nginx-punt.conf").read_text("utf-8")
    assert re.search(r"access_log\s+/var/log/nginx/access\.log\s+vhost;", nginx)


def test_the_stream_location_disables_every_buffer():
    """nginx buffers a proxied response by default, which holds an SSE event
    until the buffer fills: for ever, on a stream that sends a few hundred bytes
    a minute."""
    nginx = (DEPLOY / "nginx-punt.conf").read_text("utf-8")
    stream = nginx.split("location /stream", 1)[1].split("location ", 1)[0]
    assert "proxy_buffering off" in stream
    assert "proxy_cache off" in stream
    assert re.search(r"proxy_read_timeout\s+\d{3,}s", stream), "an idle stream would be cut"


def test_html_is_not_cached_but_static_is():
    """Flask sends no Cache-Control on a rendered template, so a browser pins the
    ?v= it already has and a CSS deploy is invisible to returning visitors.

    Headers set in a location block replace those set outside it, which is why
    this has to be inside `location /` rather than declared once at the top."""
    nginx = (DEPLOY / "nginx-punt.conf").read_text("utf-8")
    root = nginx.rsplit("location / {", 1)[1]
    assert "no-cache" in root
    static = nginx.split("location /static/", 1)[1].split("location ", 1)[0]
    assert "max-age=" in static


def test_provision_reruns_certbot_every_time():
    """Every provisioning script on this droplet re-runs certbot, because the
    template has no TLS block and certbot adds one in place. A script that copies
    the template over the live config without re-running it deletes HTTPS -- and
    fails silently, because the port 80 block left behind is valid on its own and
    `nginx -t` still passes."""
    provision = (DEPLOY / "provision.sh").read_text("utf-8")
    assert "certbot --nginx" in provision
    assert "listen 443 ssl http2;" in provision, "nginx 1.24 needs http2 on the listen line"

    # Comments stripped first: the file *explains* why `http2 on;` is wrong, and
    # matching the raw text flagged the explanation as the mistake.
    code = "\n".join(line for line in provision.splitlines()
                      if not line.lstrip().startswith("#"))
    assert not re.search(r"^\s*http2\s+on\s*;", code, re.M), \
        "the `http2 on;` form is 1.25.1+ and fails nginx -t on this box"


def test_the_deploy_verifies_the_new_build_is_actually_serving():
    """`systemctl is-active` says nothing about which code was loaded, and a 200
    on the front page says nothing about whether this deploy's changes are on
    it. A sibling app deployed successfully for days while serving the old
    build."""
    deploy = (DEPLOY / "deploy.sh").read_text("utf-8")
    assert "is-active" in deploy
    assert "stat -c" in deploy
    assert "/healthz" in deploy
    assert "/partials/watchnow" in deploy, "no check for a route only the new build serves"


def test_secrets_and_derived_files_are_never_shipped():
    deploy = (DEPLOY / "deploy.sh").read_text("utf-8")
    for excluded in ("--exclude '.env'", "--exclude '.venv/'",
                     "--exclude 'static/audio/phrase/'", "--exclude '.git/'"):
        assert excluded in deploy, f"deploy.sh does not exclude {excluded}"


# --------------------------------------------------------------------------
# the stylesheet
# --------------------------------------------------------------------------

def _strip_at_rules(css: str) -> str:
    """Remove every `@media`/`@keyframes` block, braces balanced."""
    out, i = [], 0
    while i < len(css):
        at = css.find("@", i)
        if at == -1:
            out.append(css[i:])
            break
        out.append(css[i:at])
        brace = css.find("{", at)
        if brace == -1:
            break
        depth, j = 0, brace
        while j < len(css):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        i = j + 1
    return "".join(out)


def test_no_selector_is_defined_twice_in_the_stylesheet():
    """Duplicated rules are how a careless edit changes a colour silently.

    A `str.replace` on the anchor `.verdict {` once matched both the base rule
    and `.cheer--conflicted .verdict`, which duplicated a whole block and left a
    stray base rule carrying a background. At equal specificity the later rule
    wins, so every "IN" chip on the Multiverse tab came out the amber of "WIN 2"
    while the markup said `verdict--in` throughout. Nothing was broken enough to
    fail; it was just wrong, and only visible in a screenshot.
    """
    import re
    from collections import Counter

    css = (ROOT / "static" / "css" / "theme.css").read_text("utf-8")
    # Comments out first, or a selector quoted inside one counts as a definition.
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    # And at-rules out too. A selector redefined inside `@media (prefers-reduced-
    # motion)` or a step repeated across two `@keyframes` is the point of those
    # constructs, not a mistake; only top-level rules are checked.
    css = _strip_at_rules(css)

    selectors: list[str] = []
    for block in re.finditer(r"(^|\})\s*([^{}@]+?)\s*\{", css, re.M):
        selector = " ".join(block.group(2).split())
        if selector:
            selectors.append(selector)

    repeated = {s: n for s, n in Counter(selectors).items() if n > 1}
    assert not repeated, f"selectors defined more than once: {repeated}"


def test_the_stylesheet_has_balanced_braces():
    """One unbalanced brace silently drops every rule after it, and the page
    still renders -- just wrongly, from there down."""
    css = (ROOT / "static" / "css" / "theme.css").read_text("utf-8")
    assert css.count("{") == css.count("}")


def test_verdict_chips_carry_no_colour_on_the_base_rule():
    """The base rule is shape only. A background on it sits after the modifiers
    in source order and wins at equal specificity, which is the exact shape of
    the bug above."""
    import re

    css = (ROOT / "static" / "css" / "theme.css").read_text("utf-8")
    base = re.search(r"\n\.verdict\s*\{(.*?)\}", css, re.S)
    assert base, "no base .verdict rule found"
    assert "background" not in base.group(1)
    assert "color" not in base.group(1)
