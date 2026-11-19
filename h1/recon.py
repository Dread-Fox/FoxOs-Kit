"""recon — low-impact recon of the top-ranked bounty programs.

Stage 1 is PASSIVE (zero packets to the program's servers); every source is a third party:
  * subfinder        passive subdomain sources (cert logs, search engines, DNS datasets)
  * crt.sh           certificate-transparency log search
  * Wayback CDX      archived URLs → hidden hosts, API/admin/debug paths, juicy files, redirect/SSRF-style params
  * dnsx             A/CNAME lookups through PUBLIC resolvers (1.1.1.1/8.8.8.8/9.9.9.9), rate-limited
  * Team Cymru whois IP → ASN/provider classification (one bulk query)
Scope is enforced with hunt.Scope (bounty-eligible wildcards/hosts only, out-of-scope removed).
Each run is diffed against the program's previous run, so the radar shows what is NEW.
Stage 2 is LIGHT active: ONE `GET /` per resolving in-scope host via httpx (status, title, tech, TLS), at the
program's policy rate capped at 2 req/s, with the required research header, no redirects, no retries.
Skipped when the policy restricts automation, for third-party-hosted names, or with --passive-only.
No scanners, fuzzing, brute force, crawling or logins. Deeper testing stays a manual, human decision.
"""
import fcntl
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime

import h1lib as L
from hunt import HTTPX, THIRD_PARTY, Scope, _host, analyze_policy

RECON = os.path.join(L.HOME, "recon")
UA = "Mozilla/5.0 (X11; Linux aarch64) passive-recon"
RESOLVERS = "1.1.1.1,8.8.8.8,9.9.9.9,1.0.0.1"
PROGRAM_BUDGET_S = 20 * 60
MAX_ROOTS = 25          # per program; very broad programs get their first 25 wildcard roots
WAYBACK_LIMIT = 25000   # archived URL rows per root
MAX_PROBE = 200         # light probe: hosts per program (1 request each)
LIGHT_MAX_RPS = 2.0
WILD_PROBE_CAP = 25     # wildcard look-alike names probed per program

INTERESTING = {
    "api":      re.compile(r"/(api|graphql|gql|v\d+|rest|rpc|swagger|openapi|api-docs)(/|\.|$|\?)", re.I),
    "admin":    re.compile(r"/(admin|internal|debug|staging|dev|test|console|dashboard|manage|actuator|"
                           r"phpmyadmin|jenkins|grafana|kibana|_next/data|server-status)(/|$|\?)", re.I),
    "auth":     re.compile(r"/(oauth|sso|saml|login|signin|auth|reset|forgot|token|callback)(/|$|\?)", re.I),
    "files":    re.compile(r"\.(env|bak|old|backup|sql|db|sqlite|zip|tar|gz|7z|config|cfg|ini|log|yml|yaml|"
                           r"pem|key|p12|git|svn|DS_Store|swp|map)(\?|$)", re.I),
    "redirect": re.compile(r"[?&](redirect|redirect_uri|redirect_url|return|returnTo|return_url|next|url|"
                           r"continue|dest|destination|goto|callback|target|r|u)=", re.I),
    "ssrf":     re.compile(r"[?&](url|uri|path|file|feed|host|domain|site|image|img|src|fetch|proxy|load|"
                           r"webhook|pdf|document)=https?", re.I),
    "id":       re.compile(r"[?&](id|user_?id|uid|account_?id|order_?id|invoice|doc_?id|org_?id)=\d+", re.I),
}
SKIP_EXT = re.compile(r"\.(png|jpe?g|gif|svg|ico|webp|woff2?|ttf|eot|css|mp4|mp3|webm|avif)(\?|$)", re.I)


def _log_to(path, log):
    f = open(path, "a")

    def w(msg):
        log(msg)
        f.write(msg + "\n")
        f.flush()
    return w


def _run(cmd, timeout, log):
    log(f"    $ {' '.join(cmd)[:200]}")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=max(30, timeout), stdin=subprocess.DEVNULL)
        if p.returncode:
            log(f"      exit {p.returncode}: {(p.stderr or '').strip()[-200:]}")
        return p.stdout
    except subprocess.TimeoutExpired:
        log(f"      timed out after {timeout:.0f}s")
        return ""


def _get(url, timeout=90, tries=3, log=print):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001 — public archives are flaky; back off and move on
            log(f"      {urllib.parse.urlsplit(url).netloc}: {type(e).__name__} {str(e)[:80]} (try {i + 1}/{tries})")
            time.sleep(6 * (i + 1))
    return ""


# ----------------------------------------------------------------------- sources
def crtsh(root, log):
    txt = _get(f"https://crt.sh/?q=%25.{urllib.parse.quote(root)}&output=json", log=log)
    out = set()
    try:
        for row in json.loads(txt or "[]"):
            for n in (row.get("name_value") or "").split("\n"):
                n = n.strip().lower().lstrip("*.")
                if n:
                    out.add(n)
    except ValueError:
        pass
    time.sleep(3)  # be polite to crt.sh
    return out


def wayback(root, log):
    q = urllib.parse.urlencode({"url": f"*.{root}/*", "output": "txt", "fl": "original",
                                "collapse": "urlkey", "limit": WAYBACK_LIMIT})
    txt = _get(f"https://web.archive.org/cdx/search/cdx?{q}", timeout=180, log=log)
    time.sleep(3)
    return [u.strip() for u in txt.splitlines() if u.strip()]


def cymru(ips, log):
    """One bulk whois query → {ip: 'AS13335 CLOUDFLARENET'}"""
    if not ips:
        return {}
    out = {}
    try:
        s = socket.create_connection(("whois.cymru.com", 43), timeout=30)
        s.sendall(("begin\nverbose\n" + "\n".join(sorted(ips)[:1000]) + "\nend\n").encode())
        buf = b""
        while chunk := s.recv(65536):
            buf += chunk
        s.close()
        for line in buf.decode("utf-8", "replace").splitlines()[1:]:
            p = [x.strip() for x in line.split("|")]
            if len(p) >= 7 and p[0].isdigit():
                out[p[1]] = f"AS{p[0]} {p[6].split(',')[0]}"
    except OSError as e:
        log(f"      cymru: {e}")
    return out


# --------------------------------------------------------------------------- run
def _prev_dir(handle, day):
    base = os.path.join(RECON, handle)
    if not os.path.isdir(base):
        return None
    runs = sorted(d for d in os.listdir(base) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) and d < day)
    return os.path.join(base, runs[-1]) if runs else None


def _read(path):
    return set(open(path).read().split()) if path and os.path.exists(path) else set()


def recon_program(c, r, day, log=print, active=True):
    h = r["handle"]
    d = os.path.join(RECON, h, day)
    os.makedirs(d, exist_ok=True)
    log = _log_to(os.path.join(d, "run.log"), log)
    t0 = time.time()
    left = lambda: PROGRAM_BUDGET_S - (time.time() - t0)  # noqa: E731
    sc = Scope(c, h)
    roots = sorted(sc.roots)[:MAX_ROOTS]
    log(f"[*] {h}: roots={len(sc.roots)} exact={len(sc.exact)} oos={len(sc.oos_roots | sc.oos_exact)}")
    json.dump({"handle": h, "name": r["name"], "roots": sorted(sc.roots), "exact": sorted(sc.exact),
               "out_of_scope": sorted(sc.oos_roots | sc.oos_exact), "unparsed": sc.skipped},
              open(os.path.join(d, "scope.json"), "w"), indent=1)

    # 1. subdomains from passive sources
    found = set(sc.exact) | set(roots)
    src = Counter()
    if roots:
        open(os.path.join(d, "roots.txt"), "w").write("\n".join(roots) + "\n")
        out = _run(["subfinder", "-dL", os.path.join(d, "roots.txt"), "-silent", "-timeout", "20",
                    "-max-time", "8"], min(600, left()), log)
        s = {x.strip().lower() for x in out.splitlines() if x.strip()}
        src["subfinder"] = len(s)
        found |= s
        for root in roots:
            if left() < 300:
                log("    budget low — skipping remaining crt.sh lookups")
                break
            s = crtsh(root, log)
            src["crt.sh"] += len(s)
            found |= s

    # 2. archived URLs (Wayback) for wildcard roots + exact hosts
    urls = []
    for root in roots + sorted(sc.exact - set(roots)):
        if left() < 180:
            log("    budget low — skipping remaining Wayback lookups")
            break
        u = wayback(root, log) if root in roots else []
        if root not in roots:  # exact host: no wildcard query
            q = urllib.parse.urlencode({"url": f"{root}/*", "output": "txt", "fl": "original",
                                        "collapse": "urlkey", "limit": WAYBACK_LIMIT})
            u = [x for x in _get(f"https://web.archive.org/cdx/search/cdx?{q}", timeout=180,
                                 log=log).splitlines() if x.strip()]
            time.sleep(3)
        urls += u
    src["wayback urls"] = len(urls)
    for u in urls:
        found.add(_host(u))

    hosts = sorted(x for x in found if x and sc.allows(x))
    open(os.path.join(d, "hosts.txt"), "w").write("\n".join(hosts) + "\n")

    # 3. classify archived URLs (in scope only, no fetching). Categories match the PATH/QUERY, never the host,
    #    and near-duplicates collapse to one example per host + path shape + parameter names.
    urls = sorted({u for u in urls if sc.allows(_host(u)) and not SKIP_EXT.search(u)})
    open(os.path.join(d, "urls.txt"), "w").write("\n".join(urls) + "\n")
    cats = {k: {} for k in INTERESTING}
    js = {}
    for u in urls:
        sp = urllib.parse.urlsplit(u)
        path = sp.path or "/"
        params = sorted({kv.split("=")[0] for kv in sp.query.split("&") if kv})
        key = (sp.hostname or "", re.sub(r"[0-9a-f]{8,}|\d+", "N", path.lower()), tuple(params))
        if re.search(r"\.js$", path, re.I):
            js.setdefault(key, u)
            continue
        target = path + ("?" + sp.query if sp.query else "")
        for k, rx in INTERESTING.items():
            if rx.search(target):
                cats[k].setdefault(key, u)
    cats = {k: sorted(v.values()) for k, v in cats.items()}
    js = sorted(js.values())
    for k, v in cats.items():
        open(os.path.join(d, f"urls_{k}.txt"), "w").write("\n".join(v) + "\n")
    open(os.path.join(d, "urls_js.txt"), "w").write("\n".join(js) + "\n")

    # 4. DNS through public resolvers
    dns, dangling, third, ips = {}, [], [], set()
    if hosts and left() > 60:
        _run(["dnsx", "-l", os.path.join(d, "hosts.txt"), "-silent", "-json", "-a", "-cname", "-resp",
              "-r", RESOLVERS, "-t", "10", "-rl", "20", "-o", os.path.join(d, "dns.jsonl")], min(600, left()), log)
        for line in open(os.path.join(d, "dns.jsonl")) if os.path.exists(os.path.join(d, "dns.jsonl")) else []:
            try:
                x = json.loads(line)
            except ValueError:
                continue
            host = x.get("host", "")
            a = x.get("a") or []
            cn = [v.lower().rstrip(".") for v in (x.get("cname") or [])]
            dns[host] = {"a": a, "cname": cn}
            ips.update(a)
            tp = [v for v in cn if any(v == s or v.endswith("." + s) for s in THIRD_PARTY) and not sc.owns(v)]
            if tp:
                third.append({"host": host, "cname": cn, "resolves": bool(a)})
            if cn and not a:
                dangling.append({"host": host, "cname": cn})
    # wildcard DNS: resolve one random label per wildcard root via public resolvers
    wild = {}
    for root in roots:
        lab = f"zz{os.urandom(5).hex()}.{root}"
        out = subprocess.run(["dig", "+short", "+time=3", "+tries=1", "@1.1.1.1", lab, "A"],
                             capture_output=True, text=True).stdout.split()
        ips_w = {x for x in out if re.fullmatch(r"[\d.]+", x)}
        if ips_w:
            wild[root] = {"label": lab, "ips": sorted(ips_w)}
    if wild:
        log(f"    wildcard DNS on: {', '.join(wild)}")

    def wild_noise(host):
        if host in sc.exact:
            return False
        for root, w in wild.items():
            if host.endswith("." + root):
                a_ = set(dns.get(host, {}).get("a") or [])
                return bool(a_) and a_ <= set(w["ips"])
        return False
    asn = cymru(ips, log) if ips else {}
    providers = Counter(asn.get(ip, "unknown") for ip in ips)
    resolved = sorted(h_ for h_, v in dns.items() if v["a"] or v["cname"])
    open(os.path.join(d, "resolved.txt"), "w").write("\n".join(resolved) + "\n")
    noisy = {x for x in resolved if wild_noise(x)}
    json.dump({"third_party": third, "dangling_cname": dangling, "asn": asn, "wildcard_roots": wild,
               "wildcard_lookalikes": sorted(noisy)},
              open(os.path.join(d, "dns_intel.json"), "w"), indent=1)

    # 5. LIGHT active: one GET / per resolving in-scope host (policy rate, research header, no redirects).
    #    Skipped when the policy restricts automation, for third-party-hosted names, or with --passive-only.
    pol = analyze_policy(r["policy"])
    live, fp_base = {}, set()
    tp_hosts = {x["host"] for x in third}
    ok = [x for x in resolved if x not in tp_hosts and sc.allows(x)]
    # priority: listed hosts → names that resolve independently → a few wildcard look-alikes
    probe = ([x for x in ok if x in sc.exact] + [x for x in ok if x not in sc.exact and x not in noisy] +
             [x for x in ok if x in noisy][:WILD_PROBE_CAP])[:MAX_PROBE]
    baseline_hosts = {w["label"]: root for root, w in wild.items()}
    mode = "passive"
    if not active:
        log("    light probe off (--passive-only)")
    elif pol["mode"] != "active":
        log(f"    light probe SKIPPED — policy restricts automation: {pol['notes'][-1][:100]}")
    elif probe and left() > 60:
        mode = "light"
        rps = min(pol["rps"], LIGHT_MAX_RPS)
        hdr = f"{pol['header'][0]}: {pol['header'][1]}"
        open(os.path.join(d, "probe.txt"), "w").write("\n".join(probe + sorted(baseline_hosts)) + "\n")
        log(f"    light probe: {len(probe)} hosts (+{len(baseline_hosts)} wildcard baselines) · 1 req each · "
            f"{rps} req/s · '{hdr}'")
        _run([HTTPX, "-l", os.path.join(d, "probe.txt"), "-silent", "-json", "-o", os.path.join(d, "httpx.jsonl"),
              "-H", hdr, "-rl", str(max(1, int(rps))), "-t", "2", "-timeout", "10", "-retries", "0",
              "-title", "-tech-detect", "-status-code", "-content-length", "-web-server", "-cdn", "-tls-grab",
              "-no-color", "-no-fallback-scheme"], min(1800, left()), log)
        p = os.path.join(d, "httpx.jsonl")
        for line in open(p) if os.path.exists(p) else []:
            try:
                x = json.loads(line)
            except ValueError:
                continue
            if not sc.allows(x.get("input", "")):
                continue
            if x.get("input") in baseline_hosts:
                fp_base.add((baseline_hosts[x["input"]], x.get("status_code"), (x.get("title") or "")[:120],
                             x.get("content_length")))
                continue
            tls = x.get("tls") or {}
            live[x.get("url")] = {"status": x.get("status_code"), "title": (x.get("title") or "")[:120],
                                  "tech": x.get("tech") or [], "server": x.get("webserver"),
                                  "cdn": x.get("cdn_name"), "length": x.get("content_length"),
                                  "tls_cn": tls.get("subject_cn"), "tls_san": (tls.get("subject_an") or [])[:20]}
        # drop responses identical to the wildcard catch-all (same root, status, title, length)
        catchall = [u for u, v in live.items() for root, st, ti, ln in fp_base
                    if (urllib.parse.urlsplit(u).hostname or "").endswith("." + root)
                    and (v["status"], v["title"], v["length"]) == (st, ti, ln)]
        for u in set(catchall):
            live.pop(u, None)
        if catchall:
            log(f"    dropped {len(set(catchall))} wildcard catch-all responses")
    json.dump(live, open(os.path.join(d, "live.json"), "w"), indent=1)

    # 6. diff against the previous run
    prev = _prev_dir(h, day)
    new_live, changed = [], []
    if prev and os.path.exists(os.path.join(prev, "live.json")):
        old = json.load(open(os.path.join(prev, "live.json")))
        for u, v in live.items():
            o = old.get(u)
            if o is None:
                new_live.append(u)
            elif (o.get("status"), o.get("title"), sorted(o.get("tech") or [])) != \
                    (v["status"], v["title"], sorted(v["tech"])):
                changed.append({"url": u, "was": {k: o.get(k) for k in ("status", "title", "tech")},
                                "now": {k: v[k] for k in ("status", "title", "tech")}})
    new_hosts = sorted(set(hosts) - _read(prev and os.path.join(prev, "hosts.txt"))) if prev else []
    new_interesting = []
    if prev:
        old = set()
        for k in INTERESTING:
            old |= _read(os.path.join(prev, f"urls_{k}.txt"))
        new_interesting = sorted({u for v in cats.values() for u in v} - old)
    open(os.path.join(d, "new_hosts.txt"), "w").write("\n".join(new_hosts) + "\n")
    open(os.path.join(d, "new_interesting.txt"), "w").write("\n".join(new_interesting) + "\n")
    json.dump({"new_live": new_live, "changed": changed}, open(os.path.join(d, "live_diff.json"), "w"), indent=1)

    # 7. summary
    n_int = len({u for v in cats.values() for u in v})
    with open(os.path.join(d, "SUMMARY.md"), "w") as f:
        f.write(f"# {r['name']} ({h}) — recon {day}\n\n")
        f.write(("_Passive only: third-party sources + public-resolver DNS. Nothing was sent to the program's servers._"
                 if mode == "passive" else
                 f"_Passive sources + light probe: one `GET /` per host at {min(pol['rps'], LIGHT_MAX_RPS)} req/s with "
                 f"`{pol['header'][0]}: {pol['header'][1]}`._") +
                f" Verify scope and policy with `h1 show {h}` before any deeper testing.\n\n")
        f.write(f"| metric | value |\n|---|---|\n| in-scope hosts | {len(hosts)}"
                f"{f' (+{len(new_hosts)} new)' if prev else ' (baseline)'} |\n| resolving | {len(resolved)} |\n"
                f"| archived in-scope URLs | {len(urls)} |\n| interesting URLs (deduped) | {n_int}"
                f"{f' (+{len(new_interesting)} new)' if prev else ''} |\n| JS files | {len(js)} |\n"
                f"| live web services | {len(live) if mode == 'light' else 'not probed'}"
                f"{f' (+{len(new_live)} new, {len(changed)} changed)' if prev and mode == 'light' else ''} |\n"
                f"| wildcard DNS roots | {', '.join(wild) or 'none'} ({len(noisy)} look-alike names) |\n"
                f"| third-party CNAMEs | {len(third)} |\n| dangling CNAMEs | {len(dangling)} |\n"
                f"| sources | {', '.join(f'{k} {v}' for k, v in src.items())} |\n\n")
        if providers:
            f.write("## Hosting\n" + "".join(f"- {p}: {n} IPs\n" for p, n in providers.most_common(8)) + "\n")
        if new_live or changed:
            f.write("## New / changed live services since last run\n" +
                    "".join(f"- NEW {u} [{live[u]['status']}] {live[u]['title']}\n" for u in new_live[:40]) +
                    "".join(f"- CHANGED {x['url']}: {x['was']} → {x['now']}\n" for x in changed[:40]) + "\n")
        if live:
            notable = sorted(live.items(), key=lambda kv: (kv[1]['status'] not in (200, 401, 403), kv[0]))
            f.write(f"## Live web services ({len(live)})\n| url | status | title | tech |\n|---|---|---|---|\n" +
                    "".join(f"| {u} | {v['status']} | {(v['title'] or '').replace('|', '/')[:60]} | "
                            f"{', '.join(v['tech'][:5])} |\n" for u, v in notable[:80]) + "\n")
        if new_hosts:
            f.write("## New hosts since last run\n" + "".join(f"- `{x}`\n" for x in new_hosts[:60]) +
                    (f"- … {len(new_hosts) - 60} more in new_hosts.txt\n" if len(new_hosts) > 60 else "") + "\n")
        if dangling:
            f.write("## Dangling CNAMEs (subdomain-takeover candidates — verify manually)\n" +
                    "".join(f"- `{x['host']}` → `{x['cname'][-1]}`\n" for x in dangling[:40]) + "\n")
        if third:
            f.write("## Third-party hosted (out of program infra — check policy before touching)\n" +
                    "".join(f"- `{x['host']}` → `{x['cname'][-1]}`{'' if x['resolves'] else ' (no A record)'}\n"
                            for x in third[:40]) + "\n")
        for k, title in (("admin", "Admin / internal / debug paths"), ("api", "API surface"),
                         ("files", "Sensitive-looking files"), ("redirect", "Redirect-style parameters"),
                         ("ssrf", "URL-fetching parameters"), ("id", "Numeric object IDs (IDOR candidates)"),
                         ("auth", "Auth flows")):
            if cats[k]:
                f.write(f"## {title} ({len(cats[k])})\n" + "".join(f"- {u}\n" for u in cats[k][:25]) +
                        (f"- … see urls_{k}.txt\n" if len(cats[k]) > 25 else "") + "\n")
    latest = os.path.join(RECON, h, "latest")
    if os.path.islink(latest) or os.path.exists(latest):
        os.remove(latest)
    os.symlink(day, latest)

    stats = {"hosts": len(hosts), "new_hosts": len(new_hosts), "resolved": len(resolved), "urls": len(urls),
             "interesting": n_int, "new_interesting": len(new_interesting), "dangling": len(dangling),
             "third_party": len(third), "baseline": not prev, "mode": mode, "live": len(live),
             "new_live": len(new_live), "changed": len(changed)}
    c.execute("INSERT OR REPLACE INTO recon VALUES(?,?,?,?,?)", (f"{day}:{h}", h, L.now(), d, json.dumps(stats)))
    detail = (f"{mode} recon: {len(hosts)} hosts" + (" (baseline)" if not prev else f" (+{len(new_hosts)} new)") +
              (f" · {len(live)} live" + (f" (+{len(new_live)} new, {len(changed)} changed)" if prev else "")
               if mode == "light" else " · passive-only") +
              f" · {n_int} interesting URLs" + (f" (+{len(new_interesting)})" if prev else "") +
              (f" · {len(dangling)} dangling CNAME" if dangling else ""))
    L.log_change(c, "recon", h, detail)
    c.commit()
    log(f"    {detail}  [{time.time() - t0:.0f}s]")
    return {"handle": h, "dir": d, **stats}


def ensure_table(c):
    c.execute("CREATE TABLE IF NOT EXISTS recon(id TEXT PRIMARY KEY, handle TEXT, run_at TEXT, dir TEXT, stats TEXT)")


def select(c, n=10, refresh_days=1.0):
    """Top-scored open bounty programs with web scope, skipping ones reconned in the last `refresh_days`."""
    out = []
    for r in L.ranked(c, bounty_only=True, open_only=True):
        if not r["web_assets"]:
            continue
        last = c.execute("SELECT run_at FROM recon WHERE handle=? ORDER BY run_at DESC LIMIT 1",
                         (r["handle"],)).fetchone()
        if last and (L.days_ago(last["run_at"]) or 99) < refresh_days * 0.9:
            continue
        out.append(r)
        if len(out) >= n:
            break
    return out


def run(n=10, only=None, log=print, notify=False, active=True):
    os.makedirs(RECON, exist_ok=True)
    lock = open(os.path.join(RECON, ".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("[!] another recon run is already going")
        return []
    for tool in ("subfinder", "dnsx"):
        if not shutil.which(tool):
            log(f"[!] {tool} not installed — that step will be skipped")
    c = L.db()
    ensure_table(c)
    picks = ([r for r in L.ranked(c, bounty_only=False, open_only=False) if r["handle"] in only]
             if only else select(c, n))
    day = datetime.now().strftime("%Y-%m-%d")
    log(f"[+] recon ({'passive + light probe' if active else 'passive only'}) on {len(picks)}: {', '.join(r['handle'] for r in picks)}")
    results = []
    for r in picks:
        try:
            results.append(recon_program(c, r, day, log, active))
        except Exception as e:  # noqa: BLE001 — one program must not kill the run
            log(f"[!] {r['handle']} failed: {e}")
            L.log_change(c, "recon", r["handle"], f"recon failed: {e}")
            c.commit()
    if results:
        s = os.path.join(RECON, f"RUN-{day}.md")
        with open(s, "w") as f:
            f.write(f"# Recon {day}\n\n| program | mode | hosts | new | resolving | live | new live | interesting URLs | "
                    f"new | dangling |\n|---|---|---|---|---|---|---|---|---|---|\n")
            for x in results:
                f.write(f"| [{x['handle']}]({x['dir']}/SUMMARY.md) | {x['mode']} | {x['hosts']} | "
                        f"{'base' if x['baseline'] else x['new_hosts']} | {x['resolved']} | {x['live']} | "
                        f"{'' if x['baseline'] else x['new_live']} | {x['interesting']} | "
                        f"{'' if x['baseline'] else x['new_interesting']} | {x['dangling']} |\n")
        log(f"[+] run summary → {s}")
    if notify and results:
        hot = [x for x in results if x["new_hosts"] or x["new_interesting"] or x["dangling"] or x["new_live"]]
        body = "\n".join(f"{x['handle']}: +{x['new_hosts']} hosts, +{x['new_live']} live, +{x['new_interesting']} URLs"
                         f"{', ' + str(x['dangling']) + ' dangling' if x['dangling'] else ''}" for x in hot[:8])
        subprocess.run(["notify-send", "-a", "FoxOS H1", "-i", os.path.expanduser("~/foxos/assets/badge.png"),
                        f"Recon done — {len(results)} programs",
                        body or "no new surface since last run"], check=False)
    return results
