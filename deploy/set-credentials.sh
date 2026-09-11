#!/usr/bin/env bash
# Put the league's ESPN credentials on the droplet, and nowhere else.
#
#   bash deploy/set-credentials.sh           # prompt, write, restart, verify
#   bash deploy/set-credentials.sh --check    # report what is set, change nothing
#
# The three values go straight from your keyboard into /opt/punt/.env over ssh.
# They are never echoed, never passed as a command-line argument (which would put
# them in `ps` output on a shared box), never written to this Mac, and never
# entered into a shell history file or an agent transcript. That last one is not
# hypothetical: `/export` writes a verbatim record of a session into the repo.
#
# ESPN_S2 and ESPN_SWID are live session cookies for a real ESPN account. Anyone
# holding them can act as that account, not merely read this league.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -f .env ]]; then set -a; source .env; set +a; fi
DROPLET_SSH="${DROPLET_SSH:-}"
DROPLET_PATH="${DROPLET_PATH:-/opt/punt}"
SERVER_NAME="${SERVER_NAME:-punt.mdeller.com}"

if [[ -z "$DROPLET_SSH" ]]; then
  echo "DROPLET_SSH is not set. Copy .env.example to .env and fill in that one line."
  exit 1
fi

remote() { ssh "$DROPLET_SSH" "$@"; }

report() {
  echo "==> What ${SERVER_NAME} is running on now"
  curl -fsS "https://${SERVER_NAME}/healthz" >/dev/null 2>&1 \
    && echo "    healthz:   ok" || echo "    healthz:   FAILED"
  # `mode` is demo until a league id and both cookies are present, and the app
  # says so in a banner on every page rather than pretending.
  # demo until a league id and both cookies are present. The app says so in a
  # banner on every page rather than pretending; this is the same fact. The
  # cookie lines are hashed fingerprints, never the values.
  #
  # Via a temp file rather than `python3 - <<PY`: a heredoc IS stdin, so the
  # piped JSON is discarded and the reader sees an empty document. It fails as a
  # JSONDecodeError about column 1, which does not obviously mean "your heredoc
  # ate the pipe".
  local reader; reader=$(mktemp)
  cat > "$reader" <<'PY'
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    print("    diagnostics: unreadable"); raise SystemExit(0)
cfg = d.get("config", {})
for key in ("mode", "league_id", "espn_s2", "espn_swid"):
    print(f"    {key + ':':<11}{cfg.get(key, '?')}")
print(f"    {'auth_ok:':<11}{d.get('auth_ok', '?')} {d.get('auth_detail') or ''}".rstrip())
PY
  curl -fsS "https://${SERVER_NAME}/api/diagnostics" 2>/dev/null | python3 "$reader"
  rm -f "$reader"

  # Only whether each key has a value, never the value.
  remote "bash -c 'f=${DROPLET_PATH}/.env
    if [[ ! -f \$f ]]; then echo \"    .env:      not present\"; exit 0; fi
    echo \"    .env:      \$(stat -c %a:%U:%G \$f)\"
    for k in LEAGUE_ID SEASON ESPN_S2 ESPN_SWID ADMIN_TOKEN ROAST_LEVEL; do
      v=\$(grep -E \"^\$k=\" \$f | head -1 | cut -d= -f2-)
      if [[ -z \$v ]]; then echo \"    \$k: (unset)\"; else echo \"    \$k: set, \${#v} characters\"; fi
    done'"
}

if [[ "${1:-}" == "--check" ]]; then
  report
  exit 0
fi

echo "PUNT credentials -> ${DROPLET_SSH}:${DROPLET_PATH}/.env"
echo
echo "Where each one comes from, in a browser logged into ESPN:"
echo "  LEAGUE_ID  the number in the league URL:"
echo "             https://fantasy.espn.com/football/league?leagueId=NNNNNNNNN"
echo "  ESPN_S2    devtools > Application > Cookies > https://fantasy.espn.com"
echo "             > espn_s2. Long, and percent-encoded: paste it exactly."
echo "  ESPN_SWID  the same place, cookie SWID. KEEP THE BRACES: {AAAA-BBBB-...}"
echo
echo "Nothing you type below is echoed or stored on this Mac."
echo

read -rp  "LEAGUE_ID:  " LEAGUE_ID
read -rp  "SEASON [${SEASON:-2025}]: " SEASON_IN
read -rsp "ESPN_S2:    " ESPN_S2;   echo "  (${#ESPN_S2} characters)"
read -rsp "ESPN_SWID:  " ESPN_SWID; echo "  (${#ESPN_SWID} characters)"
echo

SEASON="${SEASON_IN:-${SEASON:-2025}}"

[[ -n "$LEAGUE_ID" ]] || { echo "LEAGUE_ID is empty; nothing written."; exit 1; }
[[ "$LEAGUE_ID" =~ ^[0-9]+$ ]] || { echo "LEAGUE_ID should be digits only; nothing written."; exit 1; }
[[ -n "$ESPN_S2" && -n "$ESPN_SWID" ]] || { echo "A cookie is empty; nothing written."; exit 1; }

# A SWID without its braces is the commonest way this fails, and it fails as a
# 401 four hours later rather than as an error now.
if [[ "$ESPN_SWID" != \{*\} ]]; then
  echo "    note: SWID had no braces; adding them."
  ESPN_SWID="{${ESPN_SWID//[\{\}]/}}"
fi

# Keep the admin token across runs, and mint one the first time. It gates
# POST /admin/refresh-cookies, which is how you tell a running PUNT to drop its
# cache after these cookies are replaced -- without it that route denies
# everything, which is the right default for a public URL.
ADMIN_TOKEN=$(remote "grep -E '^ADMIN_TOKEN=' ${DROPLET_PATH}/.env 2>/dev/null | head -1 | cut -d= -f2-" || true)
if [[ -z "$ADMIN_TOKEN" ]]; then
  ADMIN_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
  echo "    minted a new ADMIN_TOKEN (read it back later with --check-token)"
fi

echo "==> Writing ${DROPLET_PATH}/.env"
# Over stdin, so no value ever appears in an argument list, in ~/.bash_history on
# either machine, or in the ssh command line that `ps` on the droplet would show.
{
  printf 'LEAGUE_ID=%s\n' "$LEAGUE_ID"
  printf 'SEASON=%s\n' "$SEASON"
  printf 'ESPN_S2=%s\n' "$ESPN_S2"
  printf 'ESPN_SWID=%s\n' "$ESPN_SWID"
  printf 'ADMIN_TOKEN=%s\n' "$ADMIN_TOKEN"
  printf 'ROAST_LEVEL=%s\n' "${ROAST_LEVEL:-1}"
  printf 'POLL_SECONDS=%s\n' "${POLL_SECONDS:-30}"
  printf 'BIND_ADDR=127.0.0.1:8011\n'
} | remote "cat > ${DROPLET_PATH}/.env.new \
  && chown punt:punt ${DROPLET_PATH}/.env.new \
  && chmod 600 ${DROPLET_PATH}/.env.new \
  && mv ${DROPLET_PATH}/.env.new ${DROPLET_PATH}/.env"

unset ESPN_S2 ESPN_SWID ADMIN_TOKEN

echo "==> Restarting"
remote "systemctl restart punt-web.service"
sleep 3
report

echo
echo "If mode still reads 'demo', the cookies were rejected. The app does not"
echo "break for that: it keeps serving the demo recording and says so in a banner."
echo "Check with:  ssh ${DROPLET_SSH} journalctl -u punt-web -n 40 --no-pager"
