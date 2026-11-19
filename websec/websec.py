#!/usr/bin/env python3
"""
websec - a headless web-app authorization/testing framework for this Kali box.

A Burp-equivalent capability driven entirely from the CLI (no GUI). It ties the
installed tools together and adds a config-driven authorization engine (the
Autorize equivalent): replay every endpoint as every role and flag anything that
was NOT denied when it should have been.

Burp component  -> here
  Proxy          -> mitmdump            (websec proxy)
  Repeater       -> curl / this tool    (websec req)
  Intruder       -> ffuf                (websec fuzz)
  Scanner        -> nuclei              (websec scan)
  Target/crawl   -> katana + httpx      (websec crawl)
  Decoder        -> this tool           (websec jwt)
  Autorize       -> this tool           (websec authz)   <-- centerpiece
  Collaborator   -> interactsh-client   (websec oob)
  Subdomains/DNS -> subfinder + dnsx    (websec recon)
  Param discover -> arjun               (websec params)

SAFETY: authz sends only safe methods (GET/HEAD/OPTIONS) unless --allow-writes
is passed, so it never mutates a target by accident.
"""
import argparse, json, os, ssl, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path

HOME = Path(__file__).resolve().parent
PROFILES = HOME / "profiles"
LOOT = HOME / "loot"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

C = {"g":"\033[32m","r":"\033[31m","y":"\033[33m","b":"\033[34m","d":"\033[2m","x":"\033[0m"}
def col(s,c): return f"{C.get(c,'')}{s}{C['x']}" if sys.stdout.isatty() else str(s)

# ---------------- HTTP ----------------
def http(method, url, token=None, body=None, timeout=20):
    headers = {"Accept": "application/json", "User-Agent": "websec/1.0"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, r.read(65536)
    except urllib.error.HTTPError as e:
        return e.code, e.read(65536)
    except Exception as e:
        return 0, str(e).encode()

def load_profile(name):
    p = PROFILES / (name if name.endswith(".json") else name + ".json")
    if not p.exists():
        sys.exit(f"profile not found: {p}")
    return json.loads(p.read_text())

def get_token(base, login_path, spec):
    """spec may carry a static 'token' or 'login' creds. Returns token or None (anon)."""
    if not spec:            # anon role
        return None
    if spec.get("token"):
        return spec["token"]
    login = spec.get("login")
    if login:
        st, raw = http("POST", base + login_path, body=login)
        try:
            d = json.loads(raw)
        except Exception:
            d = {}
        tok = d.get("token")
        if not tok:
            print(col(f"  ! login failed for {login.get('email')}: {st} {raw[:120]!r}", "y"))
        return tok
    return None

# ---------------- authz engine (Autorize equivalent) ----------------
def cmd_authz(a):
    prof = load_profile(a.profile)
    base = prof["base"]; login_path = prof.get("login_path", "/auth/login")
    roles = prof["roles"]; endpoints = prof["endpoints"]

    tokens = {}
    print(col("== acquiring role tokens ==", "b"))
    for rname, spec in roles.items():
        tok = get_token(base, login_path, spec)
        tokens[rname] = tok
        state = "anon" if spec == {} or spec is None else ("token" if tok else "NO-TOKEN")
        print(f"  {rname:<12} {state}")

    role_names = list(roles.keys())
    print("\n" + col("== authorization matrix ==", "b"))
    hdr = f"{'METHOD':<7}{'PATH':<26}" + "".join(f"{rn[:10]:<12}" for rn in role_names)
    print(col(hdr, "d"))

    findings = []
    for ep in endpoints:
        method = ep.get("method", "GET").upper()
        path = ep["path"]; allow = set(ep.get("allow", []))
        if method not in SAFE_METHODS and not a.allow_writes:
            print(f"{method:<7}{path:<26}" + col("skipped (write; use --allow-writes)", "d"))
            continue
        cells = []
        for rn in role_names:
            tok = tokens.get(rn)
            if roles[rn] not in ({}, None) and tok is None:
                cells.append(col(f"{'(no tok)':<12}", "d")); continue
            st, _ = http(method, base + path, token=tok)
            allowed_role = rn in allow
            ok_allow = 200 <= st < 300
            denied = st in (401, 403, 404)
            if allowed_role:
                tag = col(f"{st:<12}", "g") if ok_allow else col(f"{st} !low{'':<4}", "y")
            else:
                if ok_allow:
                    tag = col(f"{st} VIOL{'':<4}", "r")
                    findings.append((method, path, rn, st, "accessible to role that should be denied"))
                elif denied:
                    tag = col(f"{st:<12}", "g")
                else:
                    tag = col(f"{st:<12}", "y")
            cells.append(tag)
            time.sleep(a.delay)
        print(f"{method:<7}{path:<26}" + "".join(cells))

    print("\n" + col("== findings ==", "b"))
    if not findings:
        print(col("  none — every endpoint enforced its role model", "g"))
    else:
        for m, p, rn, st, why in findings:
            print(col(f"  [BROKEN ACCESS CONTROL] {m} {p} as {rn} -> {st} ({why})", "r"))
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = LOOT / f"authz-{a.profile}-{ts}.json"
    out.write_text(json.dumps({"profile": a.profile, "findings": findings}, indent=2))
    print(col(f"\nsaved {out}", "d"))

# ---------------- thin wrappers over installed tools ----------------
def run(cmd):
    print(col("$ " + " ".join(cmd), "d")); sys.stdout.flush()
    return subprocess.call(cmd)

def cmd_recon(a):
    dom = a.domain
    subs = LOOT / f"subs-{dom}.txt"
    run(["subfinder", "-silent", "-d", dom, "-o", str(subs)])
    run(["sh", "-c", f"dnsx -silent -a -resp -l {subs} | tee {LOOT}/dns-{dom}.txt"])

def cmd_crawl(a):
    run(["katana", "-silent", "-jc", "-d", str(a.depth), "-u", a.url, "-o", str(LOOT / "crawl.txt")])

def cmd_scan(a):
    cmd = ["nuclei", "-u", a.url]
    if a.tags: cmd += ["-tags", a.tags]
    run(cmd)

def cmd_fuzz(a):
    run(["ffuf", "-u", a.url, "-w", a.wordlist, "-mc", a.match, "-rate", str(a.rate)])

def cmd_params(a):
    run(["arjun", "-u", a.url])

def cmd_oob(a):
    print(col("Starting interactsh client. Paste the generated URL into a payload;", "y"))
    print(col("any DNS/HTTP hit on it proves out-of-band interaction (SSRF/blind).", "y"))
    run(["interactsh-client"])

def cmd_proxy(a):
    run(["mitmdump", "-p", str(a.port)])

def cmd_jwt(a):
    import base64
    parts = a.token.split(".")
    if len(parts) < 2: sys.exit("not a JWT")
    def dec(seg):
        seg += "=" * (-len(seg) % 4)
        return base64.urlsafe_b64decode(seg)
    print(col("header: ", "b") + dec(parts[0]).decode("utf-8", "replace"))
    print(col("payload:", "b") + dec(parts[1]).decode("utf-8", "replace"))
    print(col("(signature not verified — this is a decoder, like Burp Decoder)", "d"))

def cmd_req(a):
    prof = load_profile(a.profile) if a.profile else None
    token = None
    if prof and a.role:
        token = get_token(prof["base"], prof.get("login_path", "/auth/login"), prof["roles"].get(a.role))
    base = prof["base"] if prof else ""
    body = json.loads(a.data) if a.data else None
    st, raw = http(a.method.upper(), (base + a.path) if prof else a.path, token=token, body=body)
    print(col(f"HTTP {st}", "g" if 200 <= st < 300 else "y"))
    try: print(json.dumps(json.loads(raw), indent=2)[:4000])
    except Exception: print(raw[:4000].decode("utf-8", "replace"))

# ---------------- CLI ----------------
def main():
    ap = argparse.ArgumentParser(prog="websec", description="headless web-app authz testing framework")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("authz", help="role x endpoint authorization matrix (Autorize equivalent)")
    s.add_argument("profile"); s.add_argument("--delay", type=float, default=0.3)
    s.add_argument("--allow-writes", action="store_true", help="also send PATCH/POST/DELETE (mutates target!)")
    s.set_defaults(func=cmd_authz)

    s = sub.add_parser("req", help="single request (Repeater), optionally as a profile role")
    s.add_argument("method"); s.add_argument("path")
    s.add_argument("--profile"); s.add_argument("--role"); s.add_argument("--data")
    s.set_defaults(func=cmd_req)

    s = sub.add_parser("recon", help="subdomains + DNS (subfinder/dnsx)")
    s.add_argument("domain"); s.set_defaults(func=cmd_recon)

    s = sub.add_parser("crawl", help="crawl/site-map (katana)")
    s.add_argument("url"); s.add_argument("--depth", type=int, default=3); s.set_defaults(func=cmd_crawl)

    s = sub.add_parser("scan", help="templated vuln scan (nuclei)")
    s.add_argument("url"); s.add_argument("--tags"); s.set_defaults(func=cmd_scan)

    s = sub.add_parser("fuzz", help="fuzz (ffuf / Intruder)")
    s.add_argument("url", help="use FUZZ keyword"); s.add_argument("wordlist")
    s.add_argument("--match", default="200,301,302,401,403"); s.add_argument("--rate", type=int, default=20)
    s.set_defaults(func=cmd_fuzz)

    s = sub.add_parser("params", help="hidden parameter discovery (arjun)")
    s.add_argument("url"); s.set_defaults(func=cmd_params)

    s = sub.add_parser("oob", help="out-of-band interaction server (interactsh / Collaborator)")
    s.set_defaults(func=cmd_oob)

    s = sub.add_parser("proxy", help="intercepting proxy (mitmdump / Repeater+Proxy)")
    s.add_argument("--port", type=int, default=8080); s.set_defaults(func=cmd_proxy)

    s = sub.add_parser("jwt", help="decode a JWT (Decoder)")
    s.add_argument("token"); s.set_defaults(func=cmd_jwt)

    a = ap.parse_args(); a.func(a)

if __name__ == "__main__":
    LOOT.mkdir(exist_ok=True)
    main()
