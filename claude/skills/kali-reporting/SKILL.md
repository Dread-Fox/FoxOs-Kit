---
name: kali-reporting
description: "Kali menu 12 — Reporting Tools. Compound workflow that collates evidence from the other kali-* skills (nmap -oA XML/grepable, tool logs, screenshots, loot) into a structured penetration-test report: findings table with severity/CVSS, reproduction steps, and remediation, output as Markdown and optionally published as a Claude artifact or doc. Use when the user wants to write up, document, summarize, or report the results of a security assessment."
---

# Reporting (Kali 12)

Goal: turn scattered tool output into a clear, defensible report an owner can act on —
findings, evidence, impact, and remediation.

## Phase 1 — Gather the evidence
Collect in one working dir:
- nmap `-oA` sets (`.nmap/.xml/.gnmap`)
- tool logs (nikto.txt, nuclei.txt, wpscan, responder logs, sqlmap session)
- screenshots / terminal captures that prove each finding
- loot (hashes, creds) — redact or vault secrets in the deliverable
```
mkdir -p report/evidence && cp *.nmap *.xml *.gnmap *.txt report/evidence/
```

## Phase 2 — Normalize scan data
```
xsltproc report/evidence/scan.xml -o report/scan.html      # nmap XML → readable HTML
# quick findings extract from grepable output:
grep -h open report/evidence/*.gnmap | tr ',' '\n' | grep open | sort | uniq -c | sort -rn
```
For multi-host nmap XML, `nmap-parse-output` or a short script can pivot host↔service.

## Phase 3 — Build the findings table
For each finding capture: ID, title, affected asset(s), severity (and CVSS v3.1 vector if used),
status (confirmed/suspected), evidence reference, reproduction steps, and remediation.

Severity guide: Critical = unauth RCE / full compromise; High = priv-esc, sensitive data
exposure; Medium = meaningful but constrained; Low = info leak / hardening; Info = note.

## Phase 4 — Assemble the report (standard structure)
1. Executive summary (plain-language risk, 1 paragraph + top findings)
2. Scope & rules of engagement (targets, dates, authorization reference)
3. Methodology (which kali-* phases were run)
4. Findings (the table above, ordered by severity) with evidence
5. Remediation roadmap (prioritized, with effort estimate)
6. Appendices (raw tool output, full host/port inventory)

## Phase 5 — Deliver
- Draft as Markdown in `report/report.md`.
- To hand to the user as a polished, shareable document, offer to publish it as a Claude
  **artifact** (HTML page) or a **Claude Doc** (editable/exportable to PDF/Word). Do NOT put
  live credentials/secrets in a published artifact — redact first.
- For an offline file, the `docx`/`pdf` skills can produce a Word/PDF deliverable.

## Output
A single report.md (+ evidence/) that any later reader can follow end to end, plus the
published link if the user wants one.

## Gotchas
- Redact secrets in anything shareable; keep the unredacted set local and access-controlled.
- Tie every finding to concrete evidence — "confirmed" needs a reproducible step, not an inference.
- Keep the executive summary non-technical; the owner's decision-maker reads only that.
