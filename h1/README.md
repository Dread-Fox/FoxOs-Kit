# ~/h1 — DreadFox HackerOne toolkit

Live HackerOne bounty intel → ranked target lineup → scope export → report drafting/submission.

```
h1.py          CLI (symlinked to /usr/local/bin/h1)
h1lib.py       API client (auth, pagination, 429 backoff), SQLite intel DB, scoring, target export
h1_widget.py   "Bounty Radar" desktop widget (Tk, FoxOS crimson/steel), live-reloads the DB
data/h1.db     programs, scopes, payouts (from paid hacktivity), changes feed, scores
targets/<h>/   exported scope lists: wildcards.txt urls.txt cidrs.txt hosts.txt mobile.txt
reports/<h>/   drafted findings (*.md with front matter) + evidence/
fetch_programs.py / programs.json   original one-shot dump (kept)
```
Creds: `~/.config/hackerone/creds` (H1_USER, H1_TOKEN, chmod 600).

## Daily use
```
h1 sync                     # programs + scopes of bounty programs + ~1000 recent paid disclosures; diffs; rescores
h1 top -n 30                # ranked lineup (add --all for VDPs, --closed for paused, -q <text> to search names/assets)
h1 new --days 3             # new programs, new assets, bounty flips, open/paused flips
h1 show <handle>            # why it scored, required research header, scope, recent payouts (--policy for full text)
h1 targets <handle>         # writes ~/h1/targets/<handle>/*.txt → subfinder/dnsx/httpx/nuclei/websec
h1 weaknesses <handle> -q idor
h1 report new <handle> --asset api.example.com --title "IDOR in /v2/orders exposes other users' PII" --severity high --weakness 55
h1 report list
h1 report submit ~/h1/reports/<handle>/<file>.md   # previews, then requires typing 'submit'
h1 me                       # balance + your reports
h1 timer --every 30         # systemd user timer: auto-sync + desktop notification on new targets
h1 widget                   # open Bounty Radar (also: desktop icon / app menu "Bounty Radar")
```

## Recon (`recon.py`, daily timer `h1-recon.timer` 01:43)
Low-impact recon of the top 10 open bounty programs (skips any reconned in the last day):
1. **Passive**: subfinder, crt.sh, Wayback CDX (archived URLs → API/admin/files/redirect/SSRF/ID categories,
   deduped by host + path shape + param names), dnsx via public resolvers, Team Cymru ASN lookup.
2. **Light probe**: ONE `GET /` per resolving in-scope host (httpx-toolkit: status/title/tech/TLS), policy rate
   capped at 2 req/s, required research header, no redirects/retries. Skipped if the policy restricts automation,
   for third-party-hosted names, or with `--passive-only`. Wildcard-DNS roots get a random-label baseline and
   catch-all look-alike responses are dropped.
3. **Diff** vs the previous run → new hosts, new/changed live services, new interesting URLs → change feed + notification.
```
h1 recon --plan             # picks, probe mode, rate, header, scope
h1 recon --only stripe      # one program now
h1 recon --list             # history
h1 recon --timer --at 01:43 -n 10 [--passive-only]
```
Output: `recon/<handle>/<date>/SUMMARY.md` (+ `latest` symlink), `recon/RUN-<date>.md`. Radar: ⌖ line + "Recon" button.

## Score (0–~110) — "how promising for us"
| signal | points |
|---|---|
| offers bounties | +15 |
| submissions not open | −40 |
| bounty-eligible web/net assets (URL, WILDCARD, API, CIDR, IP, OTHER) | up to +20 (log) |
| wildcards (recon surface) | up to +15 (log) |
| critical / high max severity | +8 / +4 |
| paid disclosures: median & max payout | up to +20 (log); recent (≤120d) payouts up to +6 |
| launched ≤120 days ago | up to +15 (decays linearly) |
| assets added in last 30 days | up to +10 |
| fast payments / gold-standard safe harbor / triage active / open scope / bookmarked / your valid reports | +3 / +4 / +2 / +2 / +3 / +2 |

Payout stats only cover programs with publicly disclosed paid reports — absence ≠ low pay.
The first sync is a baseline; the change feed fills from the second sync on.

## Report file format
Front matter (`program`, `title`, `structured_scope_id`, `weakness_id`, `severity`, `status`), then markdown.
Everything above `## Impact` → `vulnerability_information`; the Impact section → `impact`.
Always test only bounty-eligible, in-scope assets, and send the program's required research header.
