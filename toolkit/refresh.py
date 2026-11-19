#!/usr/bin/env python3
"""
toolkit refresh — generates a ROLLING catalog of the CLI tools on this Kali box
that are most useful to Claude Code as your direct interface to the machine.

Run `toolkit` (or `python3 refresh.py`) any time. Each run:
  * verifies which curated tools are installed, with version + path
  * diffs against the previous snapshot and reports NEW / REMOVED tools
  * rewrites CATALOG.md and saves a JSON snapshot

So you never need the Kali GUI — this is the map of what I can drive for you.
"""
import json, re, shutil, subprocess, time
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*[mK]")
BAD = re.compile(r"(usage:|illegal option|invalid option|unrecognized|message from kali|options:)", re.I)

HERE = Path(__file__).resolve().parent
CATALOG = HERE / "CATALOG.md"
SNAP = HERE / ".snapshot.json"

# category -> list of (tool, one-line purpose). Curated for agent usefulness.
CATEGORIES = {
    "01 Information Gathering": [
        ("nmap","port/service/OS scan (-sV -sC -oA)"), ("masscan","mass fast port scan"),
        ("arp-scan","LAN host discovery"), ("netdiscover","passive/active LAN discovery"),
        ("fping","bulk ICMP sweep"), ("hping3","custom packet crafting"),
        ("dig","DNS queries"), ("host","DNS lookup"), ("whois","registration lookup"),
        ("dnsrecon","DNS enum + AXFR"), ("dnsenum","DNS enum"), ("dnsx","fast DNS resolver toolkit"),
        ("fierce","DNS recon"), ("theharvester","emails/hosts OSINT"), ("amass","subdomain OSINT"),
        ("subfinder","passive subdomain enum"), ("recon-ng","recon framework"), ("spiderfoot","OSINT automation"),
    ],
    "02 Vulnerability Analysis": [
        ("nuclei","templated vuln/misconfig scanner"), ("nikto","web server vuln scan"),
        ("wafw00f","WAF fingerprint"), ("searchsploit","offline exploit-db search"),
    ],
    "03 Web Application": [
        ("whatweb","web tech fingerprint"), ("httpx","fast HTTP prober"), ("katana","crawler/site-map"),
        ("wpscan","WordPress scan"), ("joomscan","Joomla scan"),
        ("gobuster","dir/dns/vhost brute"), ("feroxbuster","recursive content discovery"),
        ("dirb","dir brute"), ("ffuf","fast fuzzer (Intruder)"), ("wfuzz","web fuzzer"),
        ("arjun","hidden HTTP param discovery"), ("paramspider","archived param mining"),
        ("sqlmap","SQL injection"), ("commix","command injection"), ("dalfox","XSS scanner"),
    ],
    "04 Database Assessment": [
        ("sqlmap","SQLi + DB dump"), ("sqlite3","local SQLite"),
    ],
    "05 Password Attacks": [
        ("hydra","online login brute"), ("medusa","online brute"), ("ncrack","network auth brute"),
        ("patator","multi-protocol brute"), ("john","offline hash crack"), ("hashcat","GPU hash crack"),
        ("hashid","hash type id"), ("hash-identifier","hash type id"),
        ("cewl","wordlist from site"), ("crunch","wordlist generator"), ("chntpw","Windows pw/reg edit"),
    ],
    "Auth / AD / SMB": [
        ("netexec","SMB/WinRM/LDAP swiss-army (CME successor)"), ("evil-winrm","WinRM shell"),
        ("responder","LLMNR/NBT-NS poisoning"), ("smbclient","SMB client"), ("smbmap","SMB share enum"),
        ("enum4linux","SMB enum"), ("rpcclient","MS-RPC client"), ("ldapsearch","LDAP queries"),
    ],
    "08 Exploitation": [
        ("msfconsole","Metasploit framework"), ("msfvenom","payload generator"),
        ("searchsploit","find PoC exploits"), ("setoolkit","social-engineering toolkit"),
    ],
    "06 Wireless": [
        ("aircrack-ng","WPA/WEP crack"), ("airmon-ng","monitor mode"), ("airodump-ng","capture"),
        ("aireplay-ng","deauth/inject"), ("wifite","automated wifi"), ("reaver","WPS"), ("bully","WPS"),
        ("kismet","wireless detector"),
    ],
    "09 Sniffing & Spoofing": [
        ("tcpdump","packet capture"), ("tshark","CLI wireshark"), ("wireshark","GUI capture (headless: tshark)"),
        ("ettercap","ARP MITM"), ("bettercap","MITM framework"),
        ("mitmproxy","interactive HTTPS proxy"), ("mitmdump","scriptable proxy (headless)"),
        ("interactsh-client","out-of-band interaction (Collaborator)"),
    ],
    "07 Reverse Engineering": [
        ("radare2","disassembler/debugger"), ("gdb","debugger"), ("objdump","disassemble"),
        ("readelf","ELF headers"), ("ltrace","library call trace"), ("strace","syscall trace"),
        ("strings","printable strings"), ("file","file type id"),
    ],
    "11 Forensics": [
        ("binwalk","firmware carve"), ("foremost","file carve"), ("scalpel","file carve"),
        ("exiftool","metadata"), ("steghide","stego"), ("zsteg","PNG/BMP stego"),
        ("autopsy","forensic suite"), ("mmls","partition layout"), ("fls","filesystem listing"),
        ("testdisk","partition recovery"), ("photorec","file recovery"),
        ("dd","imaging"), ("ddrescue","robust imaging"), ("hashdeep","recursive hashing"),
        ("volatility3","memory forensics"),
    ],
    "Utility / Transport / Dev": [
        ("curl","HTTP client (Repeater)"), ("wget","downloader"), ("jq","JSON processor"),
        ("xxd","hexdump"), ("openssl","TLS/crypto"), ("socat","socket relay"), ("ncat","netcat (nmap)"),
        ("proxychains4","proxy chaining"), ("tor","onion routing"),
        ("git","vcs"), ("python3","scripting"), ("pipx","python apps"), ("ruby","scripting"),
        ("go","build go tools"), ("gcc","compile"), ("make","build"),
        ("docker","containers"), ("podman","containers"), ("sqlite3","db cli"),
    ],
    "Local framework": [
        ("websec","headless authz testing framework (this box) — see ~/websec"),
    ],
}

def ver(tool):
    p = shutil.which(tool)
    if not p:
        return None, None
    v = ""
    for flag in ("--version","-V","version","-v"):
        try:
            out = subprocess.run([tool, flag], capture_output=True, text=True, timeout=6,
                                 stdin=subprocess.DEVNULL)
            text = ANSI.sub("", (out.stdout or "") + "\n" + (out.stderr or ""))
            # prefer a line that has a digit, isn't an error/usage/banner line
            cand = [ln.strip() for ln in text.splitlines()
                    if any(c.isdigit() for c in ln) and not BAD.search(ln) and len(ln.strip()) < 60]
            if cand:
                v = cand[0][:48]; break
        except Exception:
            continue
    return p, v

def main():
    prev = {}
    if SNAP.exists():
        try: prev = json.loads(SNAP.read_text())
        except Exception: prev = {}
    prev_present = set(prev.get("present", []))

    now_present = {}
    lines = []
    lines.append("# Kali CLI Catalog — Claude Code's direct interface to this box")
    lines.append("")
    lines.append(f"_Rolling catalog. Last refreshed: {time.strftime('%Y-%m-%d %H:%M')} "
                 f"(run `toolkit` to update). You drive these for the user so they never need the Kali GUI._")
    lines.append("")
    total_present = 0; total = 0
    body = []
    for cat, tools in CATEGORIES.items():
        body.append(f"## {cat}")
        body.append("")
        body.append("| Tool | ✓ | Purpose | Version |")
        body.append("|------|---|---------|---------|")
        for tool, purpose in tools:
            total += 1
            path, v = ver(tool)
            ok = path is not None
            if ok:
                total_present += 1; now_present[tool] = path
            mark = "✅" if ok else "—"
            body.append(f"| `{tool}` | {mark} | {purpose} | {v or ''} |")
        body.append("")

    # change tracking
    now_set = set(now_present)
    added = sorted(now_set - prev_present)
    removed = sorted(prev_present - now_set) if prev_present else []
    lines.append(f"**Coverage:** {total_present}/{total} curated tools present.")
    lines.append("")
    if prev_present:
        if added:  lines.append(f"**🟢 New since last refresh:** {', '.join('`'+t+'`' for t in added)}")
        if removed:lines.append(f"**🔴 Removed since last refresh:** {', '.join('`'+t+'`' for t in removed)}")
        if not added and not removed: lines.append("_No changes since last refresh._")
    else:
        lines.append("_First refresh — baseline snapshot saved._")
    lines.append("")
    lines.append("> Absent tools are installable: `sudo apt install <tool>` (most), or a release binary. "
                 "Ask me and I'll install + verify. Burp Suite is intentionally omitted (GUI-only; use `websec`).")
    lines.append("")
    lines += body

    CATALOG.write_text("\n".join(lines))
    SNAP.write_text(json.dumps({"ts": time.time(), "present": sorted(now_set)}, indent=2))
    print(f"catalog -> {CATALOG}  ({total_present}/{total} present)")
    if prev_present and (added or removed):
        if added:   print("  NEW:", ", ".join(added))
        if removed: print("  REMOVED:", ", ".join(removed))

if __name__ == "__main__":
    main()
