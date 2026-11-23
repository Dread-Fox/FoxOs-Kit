"""h1lib — HackerOne Hacker API client, local intel DB, and target scoring.

Everything the `h1` CLI and the `h1-widget` desktop window share lives here.
Data lives in ~/h1/data/h1.db (SQLite). Creds come from ~/.config/hackerone/creds
(H1_USER=..., H1_TOKEN=...).
"""
import base64
import json
import math
import os
import re
import sqlite3
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HOME = os.path.expanduser("~/h1")
DATA = os.path.join(HOME, "data")
DB_PATH = os.path.join(DATA, "h1.db")
CREDS = os.path.expanduser("~/.config/hackerone/creds")
API = "https://api.hackerone.com/v1"
UA = "dreadfox-h1-toolkit/1.0"

# Asset types our Kali web/network toolchain can actually work on.
WEB_TYPES = {"URL", "WILDCARD", "API", "CIDR", "IP_ADDRESS", "OTHER"}
SEV_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


# --------------------------------------------------------------------------- API
class H1Error(RuntimeError):
    pass


def _creds():
    try:
        env = dict(l.strip().split("=", 1) for l in open(CREDS) if "=" in l)
        return env["H1_USER"].strip(), env["H1_TOKEN"].strip()
    except (OSError, KeyError) as e:
        raise H1Error(f"missing creds in {CREDS} (need H1_USER / H1_TOKEN): {e}")


def h1_user():
    """This machine's HackerOne username (from the creds file), used to tag research traffic."""
    user = os.environ.get("H1_USER", "").strip()
    if user:
        return user
    try:
        return _creds()[0]
    except H1Error:
        raise H1Error(f"no HackerOne username: set H1_USER in {CREDS} before running recon/hunt")


class Client:
    def __init__(self, delay=0.12):
        user, tok = _creds()
        self.auth = "Basic " + base64.b64encode(f"{user}:{tok}".encode()).decode()
        self.delay = delay
        self.calls = 0

    def request(self, method, path, body=None, params=None):
        url = path if path.startswith("http") else API + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(6):
            req = urllib.request.Request(url, data=data, method=method, headers={
                "Authorization": self.auth, "Accept": "application/json",
                "Content-Type": "application/json", "User-Agent": UA})
            try:
                self.calls += 1
                with urllib.request.urlopen(req, timeout=40) as r:
                    time.sleep(self.delay)
                    raw = r.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    wait = int(e.headers.get("Retry-After") or 0) or 2 ** attempt * 3
                    time.sleep(min(wait, 90))
                    continue
                detail = e.read().decode(errors="replace")[:600]
                raise H1Error(f"{method} {url} -> HTTP {e.code}: {detail}")
            except urllib.error.URLError as e:
                time.sleep(2 ** attempt)
                last = e
        raise H1Error(f"{method} {url} failed after retries: {locals().get('last')}")

    def get(self, path, **params):
        return self.request("GET", path, params=params or None)

    def paginate(self, path, size=100, max_pages=None, **params):
        params = {"page[size]": size, **params}
        url = API + path + "?" + urllib.parse.urlencode(params)
        pages = 0
        while url:
            d = self.request("GET", url)
            yield from d.get("data", [])
            pages += 1
            if max_pages and pages >= max_pages:
                break
            url = (d.get("links") or {}).get("next")


# ---------------------------------------------------------------------------- DB
SCHEMA = """
CREATE TABLE IF NOT EXISTS programs(
  handle TEXT PRIMARY KEY, id TEXT, name TEXT, submission_state TEXT, state TEXT,
  offers_bounties INT, fast_payments INT, gold_standard INT, triage_active INT,
  open_scope INT, started_at TEXT, my_reports INT, my_valid INT, my_bounty REAL,
  bookmarked INT, currency TEXT, policy TEXT, raw TEXT,
  first_seen TEXT, last_seen TEXT, scopes_synced TEXT);
CREATE TABLE IF NOT EXISTS scopes(
  id TEXT PRIMARY KEY, handle TEXT, asset_type TEXT, identifier TEXT,
  bounty INT, submission INT, max_severity TEXT, instruction TEXT,
  created_at TEXT, updated_at TEXT, first_seen TEXT, last_seen TEXT);
CREATE INDEX IF NOT EXISTS scopes_handle ON scopes(handle);
CREATE TABLE IF NOT EXISTS payouts(
  report_id TEXT PRIMARY KEY, handle TEXT, amount REAL, severity TEXT,
  title TEXT, cwe TEXT, disclosed_at TEXT, url TEXT);
CREATE INDEX IF NOT EXISTS payouts_handle ON payouts(handle);
CREATE TABLE IF NOT EXISTS changes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, handle TEXT,
  detail TEXT, seen INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS scores(
  handle TEXT PRIMARY KEY, score REAL, reasons TEXT, web_assets INT, wildcards INT,
  bounty_assets INT, max_sev TEXT, pay_count INT, pay_median REAL, pay_max REAL,
  new_assets_30d INT, computed TEXT);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS hunts(id TEXT PRIMARY KEY, handle TEXT, run_at TEXT, mode TEXT, dir TEXT,
  hosts INT, live INT, findings INT, leads TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS recon(id TEXT PRIMARY KEY, handle TEXT, run_at TEXT, dir TEXT, stats TEXT);
"""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------- sync progress
# A running sync writes data/sync_progress.json so the Radar can draw a live progress bar.
PROGRESS_PATH = os.path.join(DATA, "sync_progress.json")
# share of the overall bar each phase owns (programs, scopes, payouts, scoring)
PHASES = {"programs": (0.00, 0.05), "scopes": (0.05, 0.75), "payouts": (0.80, 0.17), "scoring": (0.97, 0.03)}


def progress(phase, done=0, total=0, note=""):
    """Record sync progress atomically. phase='done' / 'failed' marks the end."""
    base, span = PHASES.get(phase, (1.0, 0.0))
    frac = min(1.0, base + span * (done / total if total else 0)) if phase in PHASES else 1.0
    st = {"phase": phase, "done": done, "total": total, "note": note, "pct": round(frac * 100, 1),
          "pid": os.getpid(), "updated": time.time()}
    try:
        tmp = PROGRESS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f)
        os.replace(tmp, PROGRESS_PATH)
    except OSError:
        pass  # progress is cosmetic; never break a sync over it


def read_progress():
    """Progress of a sync that is running right now, or None."""
    try:
        with open(PROGRESS_PATH) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return None
    if st.get("phase") in ("done", "failed"):
        return None
    try:
        os.kill(int(st["pid"]), 0)  # writer still alive?
    except (OSError, KeyError, ValueError, TypeError):
        return None
    return st


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def days_ago(s):
    t = parse_ts(s)
    return None if t is None else (datetime.now(timezone.utc) - t).total_seconds() / 86400


def db():
    os.makedirs(DATA, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=120)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    return c


def meta_get(c, k, default=None):
    r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def meta_set(c, k, v):
    c.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


def log_change(c, kind, handle, detail):
    c.execute("INSERT INTO changes(ts,kind,handle,detail) VALUES(?,?,?,?)", (now(), kind, handle, detail))


# -------------------------------------------------------------------------- sync
def sync_programs(c, cl, log=print):
    ts = now()
    known = c.execute("SELECT COUNT(*) FROM programs").fetchone()[0]
    first_run = known == 0
    seen = 0
    progress("programs", 0, known or 600, "listing programs")
    for p in cl.paginate("/hackers/programs"):
        a = p["attributes"]
        h = a["handle"]
        seen += 1
        if seen % 100 == 0:
            progress("programs", seen, max(seen, known or 600), f"{seen} programs listed")
        old = c.execute("SELECT * FROM programs WHERE handle=?", (h,)).fetchone()
        row = dict(
            handle=h, id=p["id"], name=a.get("name"), submission_state=a.get("submission_state"),
            state=a.get("state"), offers_bounties=int(bool(a.get("offers_bounties"))),
            fast_payments=int(bool(a.get("fast_payments"))),
            gold_standard=int(bool(a.get("gold_standard_safe_harbor"))),
            triage_active=int(bool(a.get("triage_active"))), open_scope=int(bool(a.get("open_scope"))),
            started_at=a.get("started_accepting_at"), my_reports=a.get("number_of_reports_for_user") or 0,
            my_valid=a.get("number_of_valid_reports_for_user") or 0,
            my_bounty=a.get("bounty_earned_for_user") or 0, bookmarked=int(bool(a.get("bookmarked"))),
            currency=a.get("currency"), policy=a.get("policy"), raw=json.dumps(a), last_seen=ts)
        if old is None:
            row["first_seen"] = ts
            if not first_run:
                log_change(c, "new_program", h,
                           f"{row['name']} appeared ({'BOUNTY' if row['offers_bounties'] else 'VDP'}, {row['submission_state']})")
        else:
            row["first_seen"] = old["first_seen"]
            if old["submission_state"] != row["submission_state"]:
                log_change(c, "state", h, f"{old['submission_state']} -> {row['submission_state']}")
            if not old["offers_bounties"] and row["offers_bounties"]:
                log_change(c, "bounties_on", h, "program started offering bounties")
        cols = ",".join(row)
        c.execute(f"INSERT INTO programs({cols}) VALUES({','.join('?' * len(row))}) "
                  f"ON CONFLICT(handle) DO UPDATE SET " + ",".join(f"{k}=excluded.{k}" for k in row if k != "handle"),
                  tuple(row.values()))
    c.commit()
    log(f"[+] programs: {seen} eligible")
    return seen


def sync_scopes(c, cl, handles, log=print):
    first_run = meta_get(c, "scopes_initialized") is None
    for i, h in enumerate(handles, 1):
        progress("scopes", i - 1, len(handles), h)
        ts = now()
        try:
            items = list(cl.paginate(f"/hackers/programs/{urllib.parse.quote(h)}/structured_scopes"))
        except H1Error as e:
            log(f"[!] scopes {h}: {e}")
            continue
        had_any = c.execute("SELECT COUNT(*) FROM scopes WHERE handle=?", (h,)).fetchone()[0] > 0
        ids = set()
        for s in items:
            a = s["attributes"]
            ids.add(s["id"])
            old = c.execute("SELECT * FROM scopes WHERE id=?", (s["id"],)).fetchone()
            vals = (h, a.get("asset_type"), a.get("asset_identifier"), int(bool(a.get("eligible_for_bounty"))),
                    int(bool(a.get("eligible_for_submission"))), a.get("max_severity"), a.get("instruction"),
                    a.get("created_at"), a.get("updated_at"))
            if old is None:
                c.execute("INSERT INTO scopes(handle,asset_type,identifier,bounty,submission,max_severity,instruction,"
                          "created_at,updated_at,id,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                          vals + (s["id"], ts, ts))
                if had_any and not first_run and a.get("eligible_for_submission"):
                    log_change(c, "new_scope", h,
                               f"+ {a.get('asset_type')} {a.get('asset_identifier')}"
                               f"{' [bounty]' if a.get('eligible_for_bounty') else ''} max={a.get('max_severity')}")
            else:
                if not old["bounty"] and a.get("eligible_for_bounty"):
                    log_change(c, "scope_bounty", h, f"{a.get('asset_identifier')} is now bounty-eligible")
                c.execute("UPDATE scopes SET handle=?,asset_type=?,identifier=?,bounty=?,submission=?,max_severity=?,"
                          "instruction=?,created_at=?,updated_at=?,last_seen=? WHERE id=?", vals + (ts, s["id"]))
        gone = [r["id"] for r in c.execute("SELECT id FROM scopes WHERE handle=?", (h,)) if r["id"] not in ids]
        for gid in gone:
            r = c.execute("SELECT identifier FROM scopes WHERE id=?", (gid,)).fetchone()
            log_change(c, "scope_removed", h, f"- {r['identifier']}")
            c.execute("DELETE FROM scopes WHERE id=?", (gid,))
        c.execute("UPDATE programs SET scopes_synced=? WHERE handle=?", (ts, h))
        c.commit()  # per program: keep write locks short so hunt/widget never wait long
        if i % 25 == 0:
            log(f"    scopes {i}/{len(handles)}")
    meta_set(c, "scopes_initialized", now())
    c.commit()


def _hacktivity_pages(cl, pages):
    # The hacktivity `next` link dies after page 2 and deep pages cap at 10 items,
    # so walk page numbers explicitly at size 10 and stop on an empty page.
    for num in range(1, pages + 1):
        d = cl.request("GET", API + "/hackers/hacktivity?" + urllib.parse.urlencode(
            {"page[size]": 10, "page[number]": num, "queryString": "total_awarded_amount:>0"}))
        items = d.get("data") or []
        if not items:
            break
        yield from items


def sync_payouts(c, cl, pages=150, log=print):
    """Pull recently disclosed, paid hacktivity to estimate what each program actually pays."""
    n = 0
    progress("payouts", 0, pages, "paid disclosures")
    for k, it in enumerate(_hacktivity_pages(cl, pages)):
        if k % 10 == 0:  # pages are 10 items each
            progress("payouts", k // 10, pages, f"page {k // 10 + 1}")
        a = it["attributes"]
        prog = ((it.get("relationships") or {}).get("program") or {}).get("data") or {}
        h = (prog.get("attributes") or {}).get("handle")
        if not h or a.get("total_awarded_amount") is None:
            continue
        c.execute("INSERT OR REPLACE INTO payouts VALUES(?,?,?,?,?,?,?,?)",
                  (str(it["id"]), h, float(a["total_awarded_amount"]), a.get("severity_rating"),
                   a.get("title"), a.get("cwe"), a.get("disclosed_at") or a.get("latest_disclosable_activity_at"), a.get("url")))
        n += 1
        if n % 10 == 0:
            c.commit()
    c.commit()
    log(f"[+] payouts: {n} paid disclosures indexed "
        f"({c.execute('SELECT COUNT(*) FROM payouts').fetchone()[0]} total)")


def full_sync(scopes="bounty", payout_pages=150, log=print):
    try:
        new = _full_sync(scopes, payout_pages, log)
    except BaseException as e:
        progress("failed", note=str(e)[:200])
        raise
    progress("done")
    return new


def _full_sync(scopes, payout_pages, log):
    c, cl = db(), Client()
    t0 = time.time()
    before = c.execute("SELECT COALESCE(MAX(id),0) FROM changes").fetchone()[0]
    sync_programs(c, cl, log)
    if scopes != "none":
        q = "SELECT handle FROM programs WHERE last_seen=(SELECT MAX(last_seen) FROM programs)"
        if scopes == "bounty":
            q += " AND offers_bounties=1"
        handles = [r["handle"] for r in c.execute(q)]
        log(f"[+] syncing scopes for {len(handles)} programs")
        sync_scopes(c, cl, handles, log)
    if payout_pages:
        sync_payouts(c, cl, payout_pages, log)
    progress("scoring", 0, 1, "ranking programs")
    n = compute_scores(c)
    meta_set(c, "last_sync", now())
    c.commit()
    new = [dict(r) for r in c.execute("SELECT * FROM changes WHERE id>? ORDER BY id", (before,))]
    log(f"[+] scored {n} programs · {len(new)} changes · {cl.calls} API calls · {time.time() - t0:.0f}s")
    return new


# ----------------------------------------------------------------------- scoring
def compute_scores(c):
    """Rank programs by how promising they are for *us* (web/network toolchain, payouts, freshness)."""
    pays = {}
    for r in c.execute("SELECT handle, amount, disclosed_at FROM payouts"):
        pays.setdefault(r["handle"], []).append((r["amount"], r["disclosed_at"]))
    ts = now()
    n = 0
    c.execute("DELETE FROM scores")
    for p in c.execute("SELECT * FROM programs WHERE last_seen=(SELECT MAX(last_seen) FROM programs)").fetchall():
        h = p["handle"]
        sc = c.execute("SELECT * FROM scopes WHERE handle=? AND submission=1", (h,)).fetchall()
        bounty_sc = [s for s in sc if s["bounty"]]
        web = [s for s in bounty_sc if s["asset_type"] in WEB_TYPES]
        wild = [s for s in web if s["asset_type"] == "WILDCARD" or (s["identifier"] or "").startswith("*")]
        max_sev = max((s["max_severity"] or "none" for s in bounty_sc), key=lambda x: SEV_RANK.get(x, 0), default="none")
        new30 = sum(1 for s in sc if (days_ago(s["created_at"]) or 999) <= 30)
        amounts = [a for a, _ in pays.get(h, [])]
        recent_pay = sum(1 for _, d in pays.get(h, []) if (days_ago(d) or 999) <= 120)
        med = statistics.median(amounts) if amounts else 0
        mx = max(amounts) if amounts else 0
        age = days_ago(p["started_at"])

        s, why = 0.0, []
        if p["offers_bounties"]:
            s += 15
            why.append("pays bounties")
        if p["submission_state"] != "open":
            s -= 40
            why.append(f"submissions {p['submission_state']}")
        if web:
            pts = min(20, 7 * math.log2(1 + len(web)))
            s += pts
            why.append(f"{len(web)} bounty web/net assets")
        elif bounty_sc:
            why.append("bounty scope is mobile/hardware/other only")
        if wild:
            s += min(15, 6 * math.log2(1 + len(wild)))
            why.append(f"{len(wild)} wildcard(s) → big recon surface")
        if SEV_RANK.get(max_sev, 0) >= 4:
            s += 8
            why.append("critical-severity eligible")
        elif SEV_RANK.get(max_sev, 0) == 3:
            s += 4
        if amounts:
            s += min(20, 3.2 * math.log10(1 + med) + 1.5 * math.log10(1 + mx))
            why.append(f"paid {len(amounts)}x (median ${med:,.0f}, max ${mx:,.0f})")
        if recent_pay:
            s += min(6, 2 * recent_pay)
        if age is not None and age <= 120:
            pts = 15 * (1 - age / 120)
            s += pts
            why.append(f"launched {age:.0f}d ago — fresh")
        if new30:
            s += min(10, 4 * math.log2(1 + new30))
            why.append(f"{new30} asset(s) added in last 30d")
        if p["fast_payments"]:
            s += 3
            why.append("fast payments")
        if p["gold_standard"]:
            s += 4
            why.append("gold-standard safe harbor")
        if p["triage_active"]:
            s += 2
        if p["open_scope"]:
            s += 2
        if p["bookmarked"]:
            s += 3
        if p["my_valid"]:
            s += 2
            why.append(f"you have {p['my_valid']} valid report(s) here")
        c.execute("INSERT INTO scores VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (h, round(max(s, 0), 1), json.dumps(why), len(web), len(wild), len(bounty_sc), max_sev,
                   len(amounts), med, mx, new30, ts))
        n += 1
    c.commit()
    return n


def ranked(c, bounty_only=True, open_only=True, query=None, limit=None):
    q = ("SELECT p.*, s.score, s.reasons, s.web_assets, s.wildcards, s.bounty_assets, s.max_sev, "
         "s.pay_count, s.pay_median, s.pay_max, s.new_assets_30d FROM programs p JOIN scores s USING(handle) WHERE 1=1")
    args = []
    if bounty_only:
        q += " AND p.offers_bounties=1"
    if open_only:
        q += " AND p.submission_state='open'"
    if query:
        q += (" AND (p.handle LIKE ? OR p.name LIKE ? OR p.handle IN "
              "(SELECT handle FROM scopes WHERE identifier LIKE ?))")
        args += [f"%{query}%"] * 3
    q += " ORDER BY s.score DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    return c.execute(q, args).fetchall()


# ----------------------------------------------------------------------- targets
def targets(c, handle, bounty_only=True):
    q = "SELECT * FROM scopes WHERE handle=? AND submission=1" + (" AND bounty=1" if bounty_only else "")
    rows = c.execute(q, (handle,)).fetchall()
    out = {"wildcards": [], "urls": [], "cidrs": [], "other": [], "mobile": []}
    for s in rows:
        ident = (s["identifier"] or "").strip()
        t = s["asset_type"]
        parts = re.split(r"[,\s]+", ident) if t in ("URL", "WILDCARD", "API", "CIDR", "IP_ADDRESS") else [ident]
        for part in parts:
            if not part:
                continue
            if t == "WILDCARD" or part.startswith("*"):
                out["wildcards"].append(re.sub(r"^\*\.?", "", part).lstrip("."))
            elif t in ("URL", "API"):
                out["urls"].append(part)
            elif t in ("CIDR", "IP_ADDRESS"):
                out["cidrs"].append(part)
            elif t in ("GOOGLE_PLAY_APP_ID", "APPLE_STORE_APP_ID", "OTHER_APK", "OTHER_IPA", "TESTFLIGHT"):
                out["mobile"].append(part)
            else:
                out["other"].append(part)
    return {k: sorted(set(v)) for k, v in out.items()}


def export_targets(c, handle):
    t = targets(c, handle)
    d = os.path.join(HOME, "targets", handle)
    os.makedirs(d, exist_ok=True)
    files = {}
    for k, v in t.items():
        if v:
            p = os.path.join(d, f"{k}.txt")
            open(p, "w").write("\n".join(v) + "\n")
            files[k] = p
    # One combined list of hosts, handy for `subfinder -dL`, httpx, nuclei, websec recon.
    roots = t["wildcards"] + [re.sub(r"^https?://", "", u).split("/")[0] for u in t["urls"]]
    p = os.path.join(d, "hosts.txt")
    open(p, "w").write("\n".join(sorted(set(roots))) + "\n")
    files["hosts"] = p
    return d, files


HDR_NAME = r"X-(?:[A-Za-z0-9]+-)*(?:BB|Bug-?Bounty|Bounty|HackerOne|H1|Research\w*|Hacker\w*|Intigriti|Pentest\w*)(?:-[A-Za-z0-9]+)*"
HDR_RE = re.compile(r"(" + HDR_NAME + r")\s*:\s*`*\s*((?:\[[^\]\n]*\]|<[^>\n]*>|\{[^}\n]*\}|[^\s`|\"\u201c\u201d*,;])*)", re.I)
PLACEHOLDER = re.compile(r"\[[^\]]*\]|<[^>]*>|\{[^}]*\}|\b(?:YOUR[-_ ]?)?(?:H1[-_ ]?|HACKERONE[-_ ]?)?(?:USERNAME|HANDLE)\b", re.I)


def policy_header(policy, user=None):
    """Find the program's required research header and fill in our username.

    Prefers a 'format' occurrence (one with a placeholder) over an 'example' one; returns (name, value) or None."""
    user = user or h1_user()
    hits = [(m.group(1), m.group(2).replace("\\", "").strip(" .:")) for m in HDR_RE.finditer(policy or "")]
    if not hits:
        return None
    name, val = next((h for h in hits if PLACEHOLDER.search(h[1])), hits[0])
    val = PLACEHOLDER.sub(user, val)
    if not re.search(r"[A-Za-z0-9]", val) or (user not in val and len(hits) > 1 and val == hits[-1][1]):
        val = user if not re.search(r"[A-Za-z0-9]", val) else val
    return name, val
