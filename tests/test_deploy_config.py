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
