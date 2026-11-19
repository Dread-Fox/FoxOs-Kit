---
name: kali-social-engineering
description: "Kali menu 13 — Social Engineering Tools. Compound workflow for AUTHORIZED security-awareness testing that chains OSINT (theHarvester) for target pretext data with the Social-Engineer Toolkit (setoolkit) to run sanctioned phishing-awareness and credential-harvesting simulations against an organization's own employees. Use ONLY when the user is running an authorized phishing/awareness engagement for an organization that has signed off, or a closed lab."
---

# Social Engineering (Kali 13)

Goal: run a sanctioned security-awareness / phishing simulation that measures how an
organization's own people respond, and produces training outcomes — never to deceive or harm
real third parties.

## Authorization gate (strictest)
This is the most abuse-prone category. Proceed ONLY with: (a) written authorization from the
organization whose employees are the audience, (b) a defined target list of that org's own
people, and (c) a stated awareness/training purpose. Refuse if the aim is to phish people who
have not consented via their employer, to impersonate a real third party to defraud, or to
harvest credentials outside a sanctioned test. If any of that is unclear, stop and ask.

What stays in bounds: simulations against the client's own staff, landing pages hosted on
infrastructure the client authorizes, and metrics that go into a training report. What is out:
targeting individuals privately, cloning a real bank/brand to collect live credentials, or any
campaign without the employer's sign-off.

## Phase 1 — Scoped OSINT for pretext (authorized target org only)
```
theHarvester -d clientcompany.com -b bing,duckduckgo,crtsh -f se_osint
```
Use results (email format, department names) to design a realistic but clearly-scoped pretext.
Keep the list to the authorized employee set only.

## Phase 2 — SET campaign (setoolkit)
```
sudo setoolkit
# 1) Social-Engineering Attacks
#   2) Website Attack Vectors
#     3) Credential Harvester Attack Method
#       2) Site Cloner  (clone the CLIENT's OWN login portal, hosted on authorized infra)
# Set the listener to your authorized host; SET logs submitted credentials for the report.
```
Alternative modules SET offers: spear-phishing email (with the client's mail authorization),
mass mailer, and QRCode generator — all under the same authorization constraint.

## Phase 3 — Deliver (within the engagement's rules)
Send only to the authorized list, using infrastructure/sender the engagement permits. Track
click-through and submission rates as metrics, not as a way to actually use captured passwords.

## Phase 4 — Measure & debrief
Record: emails sent, opens, clicks, credential-submissions, time-to-report, and who reported it
to security. These become the awareness metrics.

## Output and handoff
A metrics summary (rates, not individuals shamed) plus recommended training actions, handed to
**kali-reporting**. Credentials captured in the sim are reported as "N users submitted" and then
securely destroyed — not reused.

## Gotchas
- Immediately redirect submitters to a short training page; never leave captured creds usable.
- Never clone a real external brand to collect live credentials — that is fraud, not a test.
- Coordinate with the client's IT/security so the sim isn't mistaken for a real incident.
