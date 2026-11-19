---
name: kali-web-analysis
description: "Kali menu 03 — Web Application Analysis. Compound workflow that fingerprints a web app and chains whatweb, httpx, wpscan, gobuster/feroxbuster/ffuf, nikto and sqlmap to enumerate technology, content, and injectable inputs on an authorized web target. Use when the user wants to test, enumerate, fuzz, or assess a website or web API they are authorized to test."
---

# Web Application Analysis (Kali 03)

Goal: from a URL, fingerprint the stack, discover hidden content and parameters, and test
inputs for common web flaws (injection, auth, misconfig) — on an authorized app only.

## Authorization gate
Confirm the app/domain is in scope. Directory fuzzing and sqlmap generate heavy traffic and
can modify data (sqlmap `--batch` can submit forms / run injected queries). Never point these
at a site you do not own or are not contracted to test.

## Phase 1 — Fingerprint
```
whatweb -a 3 http://<target>                         # CMS, frameworks, server, headers
httpx -u http://<target> -title -tech-detect -status-code -server
curl -sI http://<target>                              # raw headers, redirects
```

## Phase 2 — Content & path discovery
```
feroxbuster -u http://<target> -w /usr/share/wordlists/dirb/common.txt -x php,txt,html
gobuster dir -u http://<target> -w /usr/share/wordlists/dirbuster/directory-list-2.3-medium.txt -t 40
ffuf -u http://<target>/FUZZ -w /usr/share/wordlists/dirb/common.txt -mc 200,204,301,302,401,403
```
Virtual-host / subdomain fuzzing:
`ffuf -u http://<target> -H "Host: FUZZ.<target>" -w subs.txt -fs <baseline-size>`

## Phase 3 — CMS-specific (if WordPress etc.)
```
wpscan --url http://<target> --enumerate vp,vt,u          # vuln plugins/themes, users
wpscan --url http://<target> --passwords wordlist.txt --usernames admin   # login test
```
(Add `--api-token` for CVE data if the user has a WPScan token.)

## Phase 4 — Parameter & injection testing
```
nikto -h http://<target>                                   # quick misconfig/known-issue sweep
sqlmap -u "http://<target>/page?id=1" --batch --level 2 --risk 2    # SQLi detect
sqlmap -u "http://<target>/page?id=1" --dbs                 # enumerate DBs once injectable
sqlmap -r request.txt --batch                               # feed a saved Burp/HTTP request
```
Capture a real request to a file (`request.txt`) when the input is a POST or needs cookies/auth.

## Output and handoff
Record per endpoint: method, parameters, tech stack, interesting paths (admin, backups,
`.git`, upload), auth mechanism, and any confirmed injection with the exact payload. Hand
database access to **kali-db-assessment**; credential cracking to **kali-password-attacks**.

## Gotchas
- Choose wordlist size to match time budget; `directory-list-2.3-medium` is large for the Pi.
- Set `-mc`/`-fs` filters or you drown in 200/soft-404 noise.
- sqlmap: start low (`--level 1 --risk 1`), raise only as needed; `--risk 3` can issue updates.
- Respect robots.txt only for stealth, not for scope — scope is set by authorization, not the site.
