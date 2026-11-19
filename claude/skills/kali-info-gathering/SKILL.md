---
name: kali-info-gathering
description: "Kali menu 01 — Information Gathering. Compound recon workflow that chains passive and active OSINT and network-discovery tools (whois, dig, theHarvester, amass, dnsenum, dnsrecon, fierce, netdiscover, arp-scan, nmap -sn/-sV) to build a target map of domains, hosts, services and people. Use when the user wants to enumerate, footprint, map, or fingerprint an authorized target (their own domain/LAN, a sanctioned engagement, or a CTF)."
---

# Information Gathering (Kali 01)

Goal: turn a scope (a domain, an IP range, or an org name) into a structured map of
live hosts, open services, DNS records and public footprint — passive first, then active.

## Authorization gate (do this first, every time)
Before any ACTIVE step (DNS brute force, host discovery, port scan), confirm the target is
in scope: the user owns it, it is a sanctioned engagement with written authorization, or it
is a CTF/lab. If scope is unclear, ask. Passive OSINT (whois, public DNS lookups,
theHarvester) is lower-risk but still only for a named target. Never scan third parties.

## Phase 1 — Passive footprint (no packets to target infra)
```
whois example.com                              # registrar, org, name servers, dates
dig example.com ANY +noall +answer             # A/AAAA/MX/NS/TXT in one shot
dig +short txt example.com                      # SPF/DMARC hints
theHarvester -d example.com -b bing,duckduckgo,crtsh -f harvest   # emails, subdomains, hosts
```
`crtsh` (certificate transparency) is the single best passive subdomain source.

## Phase 2 — Subdomain enumeration
```
amass enum -passive -d example.com -o amass_passive.txt     # passive, broad
dnsenum --enum example.com -o dnsenum.xml                   # records + brute + zone transfer try
dnsrecon -d example.com -t std,brt                          # std records + brute
fierce --domain example.com                                 # quick DNS recon + nearby hosts
```
Merge results: `sort -u amass_passive.txt dnsenum*.txt > subs.txt`. Resolve them:
`dig +short -f subs.txt | sort -u > resolved_ips.txt`.

## Phase 3 — Local network discovery (LAN engagements)
```
sudo netdiscover -r 192.168.1.0/24             # passive/active ARP sweep, vendor names
sudo arp-scan --localnet                        # fast ARP host list on local subnet
nmap -sn 192.168.1.0/24 -oA hosts_ping          # ICMP/ARP ping sweep, no port scan
```

## Phase 4 — Live-host and service confirmation
```
nmap -sn -iL resolved_ips.txt -oA live          # which resolved IPs are up
nmap -sV -F -iL live.gnmap -oA services         # top-100 ports + versions on live hosts
```
Pull just the up-hosts from a ping scan: `grep Up live.gnmap | cut -d' ' -f2 > up.txt`.

## Output and handoff
- Always save with `-oA <base>` (normal + XML + grepable) so later skills can parse it.
- Produce a short inventory table: host, hostname, open ports, service/version, notes.
- Hand the service list to **kali-vuln-analysis**; hand web services to **kali-web-analysis**.

## Gotchas
- `-sn` and ARP scans on a LAN need root (`sudo`).
- Rate-limit passive sources; theHarvester/amass can get you throttled.
- Zone transfers (`dig axfr @ns example.com`) rarely work but are worth one try per NS.
- On the Pi, keep nmap at `-T3`/`-T4`; `-T5` drops packets on slow links.
