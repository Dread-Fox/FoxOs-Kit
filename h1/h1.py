#!/usr/bin/env python3
"""h1 — DreadFox HackerOne command line.

  h1 sync                 pull programs, scopes, payouts; diff; rescore
  h1 top [-n 25]          ranked list of the most promising programs
  h1 show <handle>        program detail: score reasons, scope, payouts, policy header
  h1 new [--days 7]       what changed (new programs / new assets / state flips)
  h1 targets <handle>     export scope to ~/h1/targets/<handle>/*.txt for recon tools
  h1 weaknesses <handle>  list weakness (CWE) ids the program accepts
  h1 report new <handle>  scaffold a finding report in ~/h1/reports/<handle>/
  h1 report list          list drafted reports
  h1 report submit <md>   submit a drafted report to HackerOne (asks to confirm)
  h1 me                   your balance and submitted reports
  h1 timer [--every 30]   install/refresh the systemd user timer for auto-sync
  h1 hunt [--plan]        daily preliminary assessment of the top 5 (policy-limited recon + Claude triage)
  h1 recon [--plan]       low-impact recon of the top 10 (passive + 1 GET/host) + diff vs last run
  h1 widget               open the desktop widget
"""
import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import h1lib as L  # noqa: E402

R, B, D, G, Y, C, X = "\033[38;5;160m", "\033[1m", "\033[2m", "\033[38;5;71m", "\033[38;5;178m", "\033[38;5;110m", "\033[0m"
if not sys.stdout.isatty():
    R = B = D = G = Y = C = X = ""

SEV_COL = {"critical": R, "high": Y, "medium": C, "low": D, "none": D}


def money(v):
    return f"${v:,.0f}" if v else "-"


def cmd_sync(a):
    log = (lambda *_: None) if a.quiet else print
    new = L.full_sync(scopes=a.scopes, payout_pages=a.payout_pages, log=log)
    interesting = [ch for ch in new if ch["kind"] in ("new_program", "new_scope", "bounties_on", "scope_bounty", "state")]
    if interesting and not a.quiet:
        print(f"\n{B}Changes{X}")
        for ch in interesting[:60]:
            print(f"  {Y}{ch['kind']:<13}{X} {ch['handle']:<28} {ch['detail']}")
    if a.notify and interesting:
        progs = [ch for ch in interesting if ch["kind"] in ("new_program", "bounties_on")]
        assets = [ch for ch in interesting if ch["kind"] in ("new_scope", "scope_bounty")]
        body = []
        if progs:
            body.append(f"{len(progs)} new/bounty program(s): " + ", ".join(sorted({c['handle'] for c in progs}))[:200])
        if assets:
            body.append(f"{len(assets)} new asset(s) in " + ", ".join(sorted({c['handle'] for c in assets}))[:200])
        if body:
            subprocess.run(["notify-send", "-a", "FoxOS H1", "-i", os.path.expanduser("~/foxos/assets/badge.png"),
                            "HackerOne: new targets", "\n".join(body)], check=False,
                           env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")})


def cmd_top(a):
    c = L.db()
    rows = L.ranked(c, bounty_only=not a.all, open_only=not a.closed, query=a.query, limit=a.n)
    if a.json:
        print(json.dumps([{k: r[k] for k in r.keys() if k not in ("policy", "raw")} for r in rows], indent=1))
        return
    last = L.meta_get(c, "last_sync", "never")
    print(f"{B}{R}▲ DreadFox target lineup{X}  {D}last sync {last} · {len(rows)} shown{X}")
    print(f"{D}{'#':>3} {'score':>5}  {'program':<30} {'web':>4} {'*.':>3} {'sev':<8} {'paid':>4} {'median':>8} {'new30':>5}  flags{X}")
    for i, r in enumerate(rows, 1):
        age = L.days_ago(r["started_at"])
        flags = []
        if age is not None and age <= 60:
            flags.append(f"{G}NEW{X}")
        if r["new_assets_30d"]:
            flags.append(f"{Y}+ASSETS{X}")
        if r["gold_standard"]:
            flags.append("GSSH")
        if r["fast_payments"]:
            flags.append("FAST")
        print(f"{i:>3} {B}{r['score']:>5.1f}{X}  {r['handle'][:30]:<30} {r['web_assets']:>4} {r['wildcards']:>3} "
              f"{SEV_COL.get(r['max_sev'], '')}{r['max_sev']:<8}{X} {r['pay_count']:>4} {money(r['pay_median']):>8} "
              f"{r['new_assets_30d']:>5}  {' '.join(flags)}")


def cmd_show(a):
    c = L.db()
    p = c.execute("SELECT p.*, s.* FROM programs p LEFT JOIN scores s USING(handle) WHERE p.handle=?",
                  (a.handle,)).fetchone()
    if not p:
        sys.exit(f"unknown program {a.handle!r} — try `h1 top -q {a.handle}`")
    print(f"{B}{R}{p['name']}{X} ({p['handle']})  https://hackerone.com/{p['handle']}")
    print(f"  state={p['submission_state']}  bounties={'yes' if p['offers_bounties'] else 'no'}  "
          f"score={p['score']}  started={(p['started_at'] or '')[:10]}  scopes synced={p['scopes_synced'] or 'never'}")
    for w in json.loads(p["reasons"] or "[]"):
        print(f"  {G}+{X} {w}")
    hdr = L.policy_header(p["policy"])
    if hdr:
        print(f"  {Y}required header:{X} {hdr[0]}: {hdr[1]}")
    print(f"\n{B}In-scope assets{X} {D}(★ = bounty){X}")
    for s in c.execute("SELECT * FROM scopes WHERE handle=? AND submission=1 ORDER BY bounty DESC, asset_type",
                       (a.handle,)):
        new = f" {G}new{X}" if (L.days_ago(s["created_at"]) or 999) <= 30 else ""
        print(f"  {'★' if s['bounty'] else ' '} {s['asset_type']:<20} {SEV_COL.get(s['max_severity'], '')}"
              f"{(s['max_severity'] or ''):<8}{X} {s['identifier']}{new}")
    oos = c.execute("SELECT identifier FROM scopes WHERE handle=? AND submission=0", (a.handle,)).fetchall()
    if oos:
        print(f"\n{B}Out of scope{X}  " + ", ".join(r["identifier"] for r in oos)[:600])
    pays = c.execute("SELECT * FROM payouts WHERE handle=? ORDER BY disclosed_at DESC LIMIT 8", (a.handle,)).fetchall()
    if pays:
        print(f"\n{B}Recent paid disclosures{X}")
        for r in pays:
            print(f"  {money(r['amount']):>8}  {(r['severity'] or '-'):<8} {(r['disclosed_at'] or '')[:10]}  {(r['title'] or '(undisclosed title)')[:70]}")
    if a.policy:
        print(f"\n{B}Policy{X}\n{p['policy']}")


def cmd_new(a):
    c = L.db()
    if a.unseen:
        rows = c.execute("SELECT * FROM changes WHERE seen=0 ORDER BY id DESC LIMIT ?", (a.limit,)).fetchall()
    else:
        rows = c.execute("SELECT * FROM changes WHERE ts >= datetime('now', ?) OR ts >= strftime('%Y-%m-%dT%H:%M:%SZ','now',?) "
                         "ORDER BY id DESC LIMIT ?", (f"-{a.days} days", f"-{a.days} days", a.limit)).fetchall()
    if not rows:
        print("no changes recorded yet (first sync is the baseline; changes show up from the next sync on)")
    for r in rows:
        print(f"{D}{r['ts'][:16]}{X} {Y}{r['kind']:<13}{X} {r['handle']:<28} {r['detail']}")
    if a.mark_seen:
        c.execute("UPDATE changes SET seen=1")
        c.commit()


def cmd_targets(a):
    c = L.db()
    d, files = L.export_targets(c, a.handle)
    print(f"{B}{d}{X}")
    for k, p in files.items():
        n = sum(1 for _ in open(p))
        print(f"  {k:<10} {n:>4}  {p}")
    print(f"\n{D}next: subfinder -dL {d}/wildcards.txt | dnsx -silent | httpx -silent  ·  "
          f"nuclei -l {d}/urls.txt  ·  websec recon ...{X}")


def cmd_weaknesses(a):
    cl = L.Client()
    for w in cl.paginate(f"/hackers/programs/{a.handle}/weaknesses"):
        at = w["attributes"]
        line = f"{w['id']:>6}  {at.get('external_id') or '':<10} {at['name']}"
        if not a.query or a.query.lower() in line.lower():
            print(line)


REPORT_TMPL = """---
program: {handle}
title: {title}
asset: {asset}
structured_scope_id: {scope_id}
weakness_id: {weakness}
severity: {severity}
status: draft
created: {created}
---
<!-- Everything above `## Impact` becomes `vulnerability_information`; the Impact section becomes `impact`. -->
<!-- {header_note} -->

## Summary
{asset} is vulnerable to ... An attacker can ...

## Steps To Reproduce
1. Visit `...`
2. Send the following request (research header included per program policy):
```http
GET / HTTP/1.1
Host: {host}
{header_line}
```
3. Observe ...

## Supporting Material / References
- Evidence: ~/h1/reports/{handle}/evidence/
- Tooling: websec / nuclei / httpx output

## Impact
An attacker could ...
"""


def cmd_report(a):
    c = L.db()
    base = os.path.join(L.HOME, "reports")
    if a.action == "list":
        for root, _, fs in os.walk(base):
            for f in sorted(fs):
                if f.endswith(".md"):
                    p = os.path.join(root, f)
                    fm = parse_report(p)[0]
                    print(f"  {fm.get('status', '?'):<10} {fm.get('severity', ''):<9} {p}  {D}{fm.get('title', '')}{X}")
        return
    if a.action == "new":
        p = c.execute("SELECT * FROM programs WHERE handle=?", (a.target,)).fetchone()
        if not p:
            sys.exit(f"unknown program {a.target}")
        scope = None
        if a.asset:
            scope = c.execute("SELECT * FROM scopes WHERE handle=? AND identifier LIKE ? ORDER BY bounty DESC",
                              (a.target, f"%{a.asset}%")).fetchone()
        hdr = L.policy_header(p["policy"])
        title = a.title or "TITLE — <vuln class> in <component> allows <impact>"
        asset = scope["identifier"] if scope else (a.asset or "<asset>")
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50] or "finding"
        d = os.path.join(base, a.target)
        os.makedirs(os.path.join(d, "evidence"), exist_ok=True)
        path = os.path.join(d, f"{datetime.now():%Y%m%d}-{slug}.md")
        open(path, "w").write(REPORT_TMPL.format(
            handle=a.target, title=title, asset=asset, scope_id=scope["id"] if scope else "",
            weakness=a.weakness or "", severity=a.severity, created=L.now(),
            host=re.sub(r"^\*\.|^https?://", "", asset).split("/")[0],
            header_line=f"{hdr[0]}: {hdr[1]}" if hdr else "X-Bug-Bounty: HackerOne-dread_fox",
            header_note=(f"Program requires header {hdr[0]}: {hdr[1]}" if hdr else
                         "No research header found in policy — check it manually")))
        print(path)
        if not a.weakness:
            print(f"{D}tip: h1 weaknesses {a.target} -q xss   → put the id in weakness_id{X}")
        return
    if a.action == "submit":
        fm, vuln, impact = parse_report(a.target)
        missing = [k for k in ("program", "title", "severity") if not fm.get(k)]
        if "TITLE" in fm.get("title", "") or "<" in fm.get("title", ""):
            missing.append("title (still template)")
        if missing:
            sys.exit(f"report incomplete: {', '.join(missing)}")
        attrs = {"team_handle": fm["program"], "title": fm["title"], "vulnerability_information": vuln,
                 "impact": impact, "severity_rating": fm["severity"]}
        if fm.get("weakness_id"):
            attrs["weakness_id"] = int(fm["weakness_id"])
        if fm.get("structured_scope_id"):
            attrs["structured_scope_id"] = int(fm["structured_scope_id"])
        print(f"{B}About to submit to HackerOne program '{fm['program']}':{X}\n  {fm['title']}  [{fm['severity']}]")
        print(textwrap.indent(vuln[:800] + ("…" if len(vuln) > 800 else ""), "  │ "))
        if input(f"{Y}Type 'submit' to send this report: {X}").strip() != "submit":
            sys.exit("aborted — nothing sent")
        r = L.Client().request("POST", "/hackers/reports", body={"data": {"type": "report", "attributes": attrs}})
        rid = r.get("data", {}).get("id")
        print(f"{G}submitted{X}: https://hackerone.com/reports/{rid}")
        txt = open(a.target).read().replace("status: draft", f"status: submitted\nreport_id: {rid}", 1)
        open(a.target, "w").write(txt)


def parse_report(path):
    txt = open(path).read()
    fm = {}
    m = re.match(r"---\n(.*?)\n---\n", txt, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip()
        txt = txt[m.end():]
    txt = re.sub(r"<!--.*?-->\n?", "", txt, flags=re.S).strip()
    parts = re.split(r"^## Impact\s*$", txt, maxsplit=1, flags=re.M)
    return fm, parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")


def cmd_me(a):
    cl = L.Client()
    print(f"balance: {money(cl.get('/hackers/payments/balance').get('data', {}).get('balance', 0)) or '$0'}")
    reps = list(cl.paginate("/hackers/me/reports"))
    print(f"reports: {len(reps)}")
    for r in reps[:30]:
        at = r["attributes"]
        print(f"  #{r['id']}  {at.get('state', ''):<14} {(at.get('created_at') or '')[:10]}  {at.get('title', '')[:70]}")


UNIT_SVC = """[Unit]
Description=DreadFox HackerOne intel sync

[Service]
Type=oneshot
Environment=DISPLAY=:0
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus
TimeoutStartSec=25min
ExecStart={py} -I {h1} sync --quiet --notify
"""
UNIT_TMR = """[Unit]
Description=DreadFox HackerOne intel sync every {every} min

[Timer]
# Fixed wall-clock schedule (e.g. :00 and :30), independent of how long a sync takes.
OnCalendar=*-*-* *:0/{every}:00
# If the Pi was off/asleep at a slot, run once as soon as it's back.
Persistent=true
AccuracySec=30s

[Install]
WantedBy=timers.target
"""


def cmd_timer(a):
    d = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "h1-sync.service"), "w").write(
        UNIT_SVC.format(py=sys.executable, h1=os.path.realpath(__file__), uid=os.getuid()))
    open(os.path.join(d, "h1-sync.timer"), "w").write(UNIT_TMR.format(every=a.every))
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "h1-sync.timer"], check=True)
    subprocess.run(["systemctl", "--user", "list-timers", "h1-sync.timer", "--no-pager"])


def cmd_hunt(a):
    import hunt as HU
    c = L.db()
    HU.ensure_table(c)
    if a.timer:
        d = os.path.expanduser("~/.config/systemd/user")
        open(os.path.join(d, "h1-hunt.service"), "w").write(
            f"[Unit]\nDescription=DreadFox daily HackerOne hunt (policy-limited recon + read-only Claude triage)\n"
            f"After=network-online.target\n\n[Service]\nType=oneshot\nEnvironment=DISPLAY=:0\n"
            f"Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{os.getuid()}/bus\n"
            f"Environment=PATH={os.path.expanduser('~/.local/bin')}:/usr/local/bin:/usr/bin:/bin\n"
            f"TimeoutStartSec=6h\nNice=10\nExecStart={sys.executable} -I {os.path.realpath(__file__)} hunt "
            f"-n {a.n} --notify\n")
        open(os.path.join(d, "h1-hunt.timer"), "w").write(
            f"[Unit]\nDescription=Daily HackerOne hunt\n\n[Timer]\nOnCalendar=*-*-* {a.at}:00\nPersistent=true\n"
            f"RandomizedDelaySec=10min\n\n[Install]\nWantedBy=timers.target\n")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "h1-hunt.timer"], check=True)
        subprocess.run(["systemctl", "--user", "list-timers", "h1-hunt.timer", "--no-pager"])
        return
    if a.list:
        for r in c.execute("SELECT * FROM hunts ORDER BY run_at DESC LIMIT 30"):
            print(f"  {r['run_at'][:16]}  {r['handle']:<24} {r['mode']:<8} hosts={r['hosts']:<4} live={r['live']:<4} "
                  f"hits={r['findings']:<3} {r['leads'] or '-'}")
        return
    if a.plan:
        picks = ([r for r in L.ranked(c, bounty_only=False, open_only=False) if r["handle"] in a.only]
                 if a.only else HU.select(c, a.n))
        for r in picks:
            pol, sc = HU.analyze_policy(r["policy"]), HU.Scope(c, r["handle"])
            col = G if pol["mode"] == "active" else Y
            print(f"{B}{r['handle']}{X} score={r['score']}  mode={col}{pol['mode']}{X}  rps={pol['rps']}  "
                  f"header={pol['header'][0]}: {pol['header'][1]}")
            print(f"   wildcard roots: {', '.join(sorted(sc.roots)) or '-'}")
            print(f"   exact hosts:    {', '.join(sorted(sc.exact))[:300] or '-'}")
            print(f"   out of scope:   {', '.join(sorted(sc.oos_roots | sc.oos_exact))[:300] or '-'}")
            for n in pol["notes"]:
                print(f"   {D}· {n}{X}")
        return
    HU.run(n=a.n, only=a.only, triage=not a.no_triage, notify=a.notify)


def cmd_recon(a):
    import recon as RC
    c = L.db()
    RC.ensure_table(c)
    if a.timer:
        d = os.path.expanduser("~/.config/systemd/user")
        open(os.path.join(d, "h1-recon.service"), "w").write(
            f"[Unit]\nDescription=FoxOS low-impact recon of top HackerOne programs\n"
            f"After=network-online.target\n\n[Service]\nType=oneshot\nEnvironment=DISPLAY=:0\n"
            f"Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{os.getuid()}/bus\n"
            f"Environment=PATH=/usr/local/bin:/usr/bin:/bin\nTimeoutStartSec=5h\nNice=15\n"
            f"IOSchedulingClass=idle\nExecStart={sys.executable} -I {os.path.realpath(__file__)} recon "
            f"-n {a.n} --notify{' --passive-only' if a.passive_only else ''}\n")
        open(os.path.join(d, "h1-recon.timer"), "w").write(
            f"[Unit]\nDescription=Daily passive recon of top HackerOne programs\n\n[Timer]\n"
            f"OnCalendar=*-*-* {a.at}:00\nPersistent=true\nRandomizedDelaySec=10min\n\n"
            f"[Install]\nWantedBy=timers.target\n")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "h1-recon.timer"], check=True)
        subprocess.run(["systemctl", "--user", "list-timers", "h1-recon.timer", "--no-pager"])
        return
    if a.list:
        for r in c.execute("SELECT * FROM recon ORDER BY run_at DESC LIMIT 40"):
            s = json.loads(r["stats"])
            new = "baseline" if s.get("baseline") else (f"+{s['new_hosts']} hosts +{s.get('new_live', 0)} live "
                                                         f"+{s['new_interesting']} urls")
            print(f"  {r['run_at'][:16]}  {r['handle']:<24} {s.get('mode', 'passive'):<7} hosts={s['hosts']:<5} "
                  f"resolving={s['resolved']:<5} live={s.get('live', 0):<4} "
                  f"interesting={s['interesting']:<5} dangling={s['dangling']:<3} {new}")
        return
    if a.plan:
        import hunt as HU
        picks = ([r for r in L.ranked(c, bounty_only=False, open_only=False) if r["handle"] in a.only]
                 if a.only else RC.select(c, a.n))
        for r in picks:
            sc, pol = HU.Scope(c, r["handle"]), HU.analyze_policy(r["policy"])
            probe = "off" if a.passive_only else (f"{G}light{X} {min(pol['rps'], RC.LIGHT_MAX_RPS)} req/s "
                     f"{pol['header'][0]}: {pol['header'][1]}" if pol["mode"] == "active"
                     else f"{Y}skipped — policy restricts automation{X}")
            print(f"{B}{r['handle']}{X} score={r['score']}  roots={len(sc.roots)} exact={len(sc.exact)}  probe={probe}")
            print(f"   wildcard roots: {', '.join(sorted(sc.roots))[:300] or '-'}")
            print(f"   exact hosts:    {', '.join(sorted(sc.exact))[:300] or '-'}")
            print(f"   out of scope:   {', '.join(sorted(sc.oos_roots | sc.oos_exact))[:300] or '-'}")
        return
    RC.run(n=a.n, only=a.only, notify=a.notify, active=not a.passive_only)


def cmd_widget(a):
    w = os.path.join(os.path.dirname(os.path.realpath(__file__)), "h1_widget.py")
    subprocess.Popen([sys.executable, "-I", w], env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
                     start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("widget launched on", os.environ.get("DISPLAY", ":0"))


def main():
    ap = argparse.ArgumentParser(prog="h1", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("sync")
    s.add_argument("--scopes", choices=["bounty", "all", "none"], default="bounty")
    s.add_argument("--payout-pages", type=int, default=150)
    s.add_argument("--notify", action="store_true")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(f=cmd_sync)
    s = sp.add_parser("top")
    s.add_argument("-n", type=int, default=25)
    s.add_argument("-q", "--query")
    s.add_argument("--all", action="store_true", help="include non-bounty (VDP) programs")
    s.add_argument("--closed", action="store_true", help="include paused programs")
    s.add_argument("--json", action="store_true")
    s.set_defaults(f=cmd_top)
    s = sp.add_parser("show")
    s.add_argument("handle")
    s.add_argument("--policy", action="store_true")
    s.set_defaults(f=cmd_show)
    s = sp.add_parser("new")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--limit", type=int, default=100)
    s.add_argument("--unseen", action="store_true")
    s.add_argument("--mark-seen", action="store_true")
    s.set_defaults(f=cmd_new)
    s = sp.add_parser("targets")
    s.add_argument("handle")
    s.set_defaults(f=cmd_targets)
    s = sp.add_parser("weaknesses")
    s.add_argument("handle")
    s.add_argument("-q", "--query")
    s.set_defaults(f=cmd_weaknesses)
    s = sp.add_parser("report")
    s.add_argument("action", choices=["new", "list", "submit"])
    s.add_argument("target", nargs="?", help="program handle (new) or report .md path (submit)")
    s.add_argument("--asset")
    s.add_argument("--title")
    s.add_argument("--weakness")
    s.add_argument("--severity", default="medium", choices=["none", "low", "medium", "high", "critical"])
    s.set_defaults(f=cmd_report)
    s = sp.add_parser("me")
    s.set_defaults(f=cmd_me)
    s = sp.add_parser("timer")
    s.add_argument("--every", type=int, default=30, help="minutes between syncs")
    s.set_defaults(f=cmd_timer)
    s = sp.add_parser("hunt", help="daily unattended preliminary assessment of the top programs")
    s.add_argument("-n", type=int, default=5)
    s.add_argument("--only", nargs="+", help="hunt these handles instead of auto-selecting")
    s.add_argument("--plan", action="store_true", help="dry run: show picks, mode, rate, header, scope")
    s.add_argument("--list", action="store_true", help="show past hunts")
    s.add_argument("--no-triage", action="store_true")
    s.add_argument("--notify", action="store_true")
    s.add_argument("--timer", action="store_true", help="install the daily systemd timer")
    s.add_argument("--at", default="03:37", help="daily time for --timer (HH:MM)")
    s.set_defaults(f=cmd_hunt)
    s = sp.add_parser("recon", help="low-impact recon of the top programs: passive sources + 1 GET per host")
    s.add_argument("-n", type=int, default=10)
    s.add_argument("--only", nargs="+", help="recon these handles instead of auto-selecting")
    s.add_argument("--passive-only", action="store_true", help="skip the light probe (zero packets to targets)")
    s.add_argument("--plan", action="store_true", help="dry run: show picks, probe mode, rate, header, scope")
    s.add_argument("--list", action="store_true", help="show past recon runs")
    s.add_argument("--notify", action="store_true")
    s.add_argument("--timer", action="store_true", help="install the daily systemd timer")
    s.add_argument("--at", default="01:43", help="daily time for --timer (HH:MM)")
    s.set_defaults(f=cmd_recon)
    s = sp.add_parser("widget")
    s.set_defaults(f=cmd_widget)
    a = ap.parse_args()
    if a.cmd == "report" and a.action != "list" and not a.target:
        ap.error("report new needs a program handle; report submit needs a .md path")
    try:
        a.f(a)
    except L.H1Error as e:
        sys.exit(f"{R}error:{X} {e}")


if __name__ == "__main__":
    main()
