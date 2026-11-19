---
name: kali-vuln-analysis
description: "Kali menu 02 — Vulnerability Analysis. Compound workflow that takes an nmap service baseline and chains nmap NSE vuln scripts, nikto, nuclei and searchsploit to identify, enrich and triage likely vulnerabilities on authorized hosts. Use when the user wants to find weaknesses, run vuln checks, or map services to known CVEs/exploits on a target they are authorized to test."
---

# Vulnerability Analysis (Kali 02)

Goal: from a list of live hosts/services, produce a triaged list of candidate
vulnerabilities with evidence and a pointer to proof-of-concept — without exploiting yet.

## Authorization gate
Vuln scanning is active and noisy. Confirm the target is owned / in a sanctioned engagement /
a lab before running. These checks can crash fragile services — warn the user before scanning
production and prefer a maintenance window.

## Phase 1 — Service baseline (reuse recon output)
Start from a version scan so checks are targeted, not blind:
```
nmap -sV -p- -T4 <target> -oA full_services
```
If you already have `services.xml` from **kali-info-gathering**, reuse it.

## Phase 2 — NSE vulnerability scripts (service-aware)
```
nmap -sV --script vuln <target> -oA nse_vuln            # broad safe-ish vuln category
nmap -p445 --script smb-vuln-* <target>                 # SMB (EternalBlue etc.)
nmap -p443 --script ssl-enum-ciphers,ssl-heartbleed <target>
nmap --script http-enum,http-vuln-* -p80,443 <target>
```
List/explain any script: `nmap --script-help "<name>"`. 613 scripts live in
`/usr/share/nmap/scripts/`.

## Phase 3 — Web server vuln sweep
```
nikto -h http://<target> -o nikto.txt                   # misconfig, old software, default files
nuclei -u http://<target> -o nuclei.txt                 # templated CVE/misconfig checks
nuclei -l web_urls.txt -severity high,critical          # batch, high-signal only
```
Update nuclei templates first if network allows: `nuclei -update-templates`.

## Phase 4 — Map findings to exploits
```
searchsploit apache 2.4.49                               # find PoCs by product+version
searchsploit --cve 2021-41773
searchsploit -x <path>                                   # view a PoC
searchsploit -m <path>                                   # copy PoC to cwd for review
```
Record the exact version string that matched — that is the evidence linking service → CVE.

## Triage output
For each finding record: host:port, service/version, finding, source (NSE/nikto/nuclei),
severity, CVE if any, exploit availability (searchsploit/MSF), and a confidence note
(confirmed vs. version-inferred). Hand confirmed, exploitable items to **kali-exploitation**;
web findings to **kali-web-analysis**; DB findings to **kali-db-assessment**.

## Gotchas
- `--script vuln` includes intrusive checks; read `--script-help` before running on fragile hosts.
- Version-inferred findings are candidates, not confirmations — note the difference in the report.
- nuclei/nikto are loud; they will light up any IDS. That is expected in an authorized test.
