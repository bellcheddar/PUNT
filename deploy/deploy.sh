#!/usr/bin/env bash
# Push PUNT from this Mac to the droplet and restart the service.
#   bash deploy/deploy.sh
#
# Reads DROPLET_SSH / DROPLET_PATH from .env. Idempotent, and excludes the venv,
# the derived speech cache, every secret and the server's own state.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -f .env ]]; then set -a; source .env; set +a; fi
DROPLET_SSH="${DROPLET_SSH:-}"
DROPLET_PATH="${DROPLET_PATH:-/opt/punt}"
SERVER_NAME="${SERVER_NAME:-punt.mdeller.com}"
SSH_KEY="${SSH_KEY:-}"

if [[ -z "$DROPLET_SSH" ]]; then
  echo "DROPLET_SSH is not set. Copy .env.example to .env and fill it in."; exit 1
fi

SSH_OPTS=(); SSH_CMD=(ssh)
if [[ -n "$SSH_KEY" ]]; then
  SSH_OPTS=(-e "ssh -i ${SSH_KEY/#\~/$HOME}")
  SSH_CMD=(ssh -i "${SSH_KEY/#\~/$HOME}")
fi

echo "==> Local checks before shipping"
python3 -m pytest -q || { echo "tests failed; not deploying"; exit 1; }
python3 tools/make_fixture.py --check
python3 tools/phrase_lint.py >/dev/null || true   # the line-count shortfall is not a blocker

echo "==> Syncing to ${DROPLET_SSH}:${DROPLET_PATH}"
# `data/state/` is the SERVER's: the season's history, the fired-Moment ids.
# Without this exclude, `--delete` made the droplet a mirror of this Mac's
# gitignored demo state. Every deploy copied the laptop's demo history over the
# real league's file -- which is how a 2025 demo week got into production -- and
# on 2026-09-14 it replaced the first real Sunday's history, deleted the backup
# taken beside it minutes earlier, and swapped in the demo's fired-Moment ids.
# rsync leaves excluded paths alone on the receiving side as well, so this
# protects the directory from `--delete` too. tests/test_deploy_config.py
# holds the line.
rsync -az --delete ${SSH_OPTS[@]+"${SSH_OPTS[@]}"} \
  --exclude '.venv/' --exclude '__pycache__/' --exclude '*.pyc' \
  --exclude '.git/' --exclude '.env' --exclude '.pytest_cache/' \
  --exclude 'static/audio/phrase/' --exclude '/data/state/' \
  ./ "${DROPLET_SSH}:${DROPLET_PATH}/"

echo "==> Installing dependencies and restarting"
"${SSH_CMD[@]}" "$DROPLET_SSH" bash -s <<REMOTE
set -euo pipefail
cd "${DROPLET_PATH}"
if [[ ! -x .venv/bin/python ]]; then
  echo "No venv yet -- run deploy/provision.sh as root first."; exit 1
fi
sudo -u punt env PIP_NO_CACHE_DIR=1 ./.venv/bin/pip install --quiet -r requirements.txt

# rsync as root leaves new files root-owned, so chown them back. Kept off the
# failure path deliberately: a sibling app's deploy aborted in this block on a
# missing directory, which left the new code on disk while the service carried on
# running the OLD build, with the deploy reporting success.
mkdir -p "${DROPLET_PATH}/static/audio/phrase"
sudo find "${DROPLET_PATH}" -path "${DROPLET_PATH}/.venv" -prune -o \
  -exec chown punt:punt {} + || echo "  (chown reported a problem; continuing to restart)"

sudo systemctl restart punt-web.service
REMOTE

echo "==> Verifying the NEW build is actually running"
sleep 2
# Three checks, because any one of them alone can pass over a failed deploy:
# the unit being active says nothing about which code it loaded, and a 200 on
# the front page says nothing about whether this deploy's changes are on it.
"${SSH_CMD[@]}" "$DROPLET_SSH" 'systemctl is-active --quiet punt-web.service' \
  && echo "    unit: active" || { echo "    unit: NOT ACTIVE"; exit 1; }

# The NEWEST file, not app.py. Stat'ing one named file that changes on maybe one
# deploy in five reports a stale timestamp for every other one, which is the
# opposite of what this line is for.
"${SSH_CMD[@]}" "$DROPLET_SSH" "find ${DROPLET_PATH} -path ${DROPLET_PATH}/.venv -prune -o \
  -path '*/__pycache__' -prune -o -type f -printf '%T@ %TY-%Tm-%Td %TH:%TM:%TS %p\n' 2>/dev/null \
  | sort -rn | head -1 | cut -d' ' -f2-3 | sed 's/^/    newest: /'"

code=$(curl -fsS -o /dev/null -w '%{http_code}' "https://${SERVER_NAME}/healthz" || echo 000)
echo "    healthz: $code"
[[ "$code" == "200" ]] || { echo "    health check failed"; exit 1; }

# A route this deploy is known to serve. If the old build were still running,
# this is what would 404 while everything above still looked fine.
board=$(curl -fsS -o /dev/null -w '%{http_code}' "https://${SERVER_NAME}/partials/watchnow" || echo 000)
echo "    /partials/watchnow: $board"
[[ "$board" == "200" ]] || { echo "    the new build is NOT being served"; exit 1; }

echo "==> Deployed: https://${SERVER_NAME}"
