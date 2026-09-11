# Handing over the league credentials

Three values turn PUNT from the demo recording into your league:

| Value | Where it comes from |
|---|---|
| `LEAGUE_ID` | the number in the league URL: `fantasy.espn.com/football/league?leagueId=`**`NNNNNNNNN`** |
| `ESPN_S2` | a browser logged into ESPN: devtools > Application > Cookies > `https://fantasy.espn.com` > `espn_s2`. Long and percent-encoded. |
| `ESPN_SWID` | the same place, cookie `SWID`. **Keep the braces**: `{AAAAAAAA-BBBB-...}` |

## Do this

From the repo, in your own terminal:

```bash
bash deploy/set-credentials.sh
```

It prompts for each value, the two cookies without echoing, and writes them to
`/opt/punt/.env` on the droplet over ssh. Then it restarts the service and tells
you whether ESPN accepted them.

`bash deploy/set-credentials.sh --check` reports what is currently set without
changing anything. It prints a hashed fingerprint of each cookie, never a value.

## Why a script rather than "paste them here"

`ESPN_S2` and `ESPN_SWID` are live session cookies for a real ESPN account.
Anyone holding them can act as that account, not merely read this league. So the
values should exist in exactly two places: your browser, and `/opt/punt/.env`.

The script keeps it that way. The values go from your keyboard into ssh's stdin
and nowhere else. They are never echoed to the screen, never passed as a
command-line argument (which would put them in `ps` output on a box that runs
twelve other apps), never written to this Mac, and never typed into a shell that
records history.

They must also never be pasted into a chat with an agent, including this one.
That is not a general principle, it is a specific one: `/export` writes a
verbatim record of a session into the repository directory, and this repository
is public. A cookie that reaches a transcript is one careless `git add` from
being published, and redacting it afterwards does not help, because the old
commit stays reachable by its SHA.

## What happens if they are wrong, or expire

Nothing dramatic, by design.

- Wrong or missing: the app stays on the demo recording and says so in a banner
  on every page. `--check` will report `mode: demo`.
- Expired mid-season, which they will do every few months: the app keeps serving
  what it already fetched, marks it stale, and shows the commissioner a banner
  saying the cookies need replacing. It does not go blank and it does not lie
  about the score.

To replace them, run the script again. To tell a *running* PUNT to drop its
cache immediately afterwards, without waiting for a restart:

```bash
curl -X POST https://punt.mdeller.com/admin/refresh-cookies \
     -H "X-Punt-Admin: $ADMIN_TOKEN"
```

That route accepts no cookie value in its body, deliberately: a secret sent over
HTTP lands in an access log the first time somebody uses a GET by mistake. It
only tells the process to stop trusting what it fetched with the old one.

`ADMIN_TOKEN` is minted by `set-credentials.sh` on the first run and kept across
later ones. Unset, the route denies everything, which is the right default for a
public URL.

## What is safe to share

`/api/diagnostics` is public on purpose: the stale banner and mdeller.com's
health check both read it. It reports whether each cookie is set, how long it is,
and a three-byte hash of it -- enough to tell two cookies apart in a log, not
enough to use one. It used to print the last four characters of the value, which
is a tenth of a SWID; `tests/test_routes.py` now checks every substring from four
characters up.
