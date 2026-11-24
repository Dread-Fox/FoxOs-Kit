#!/usr/bin/env bash
# Runs inside the test container as the installed user: checks what the installer produced.
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
fail=0
check() { if eval "$2" >/dev/null 2>&1; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }
FW=~/FoxWorx-DreadFoxHacker
check python      'python3 -c "import sys; assert sys.version_info >= (3, 8)"'
check h1          'python3 -m py_compile ~/h1/h1.py ~/h1/h1lib.py ~/h1/recon.py ~/h1/hunt.py ~/h1/dreadfox_widget.py ~/h1/fox_art.py'
# Core tools a distro doesn't package at all come from the standard Kali toolset (skipped in these tests).
. /etc/os-release
case "$ID" in void) KNOWN_GAPS="nmap" ;; *) KNOWN_GAPS="" ;; esac
missing_core=$(foxos-tools check 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' | awk '/\(core\)$/ && /✗/ {print $2}' | sort | tr '\n' ' ')
unexpected=$(for t in $missing_core; do case " $KNOWN_GAPS " in *" $t "*) ;; *) echo "$t";; esac; done | tr '\n' ' ')
[ -n "$KNOWN_GAPS" ] && echo "INFO known gap on $ID (from the Kali toolset instead): $KNOWN_GAPS"
check core-tools "[ -z '${unexpected// /}' ]"
check foxworx-venv "test -x $FW/.venv/bin/python && cd $FW && .venv/bin/python -c \"import foxworx, fastapi, litellm\""
check foxworx-no-login "grep -q AUTH_MODE=.open ~/.config/foxworx-workbench/settings.env"
# Start FoxWorx the way a non-systemd system would, and ask it for its tool inventory.
(cd $FW && setsid .venv/bin/python -m foxworx.cli serve --config ~/.config/foxworx-workbench/settings.env > /tmp/fw.log 2>&1 &)
for i in $(seq 1 60); do curl -s -o /dev/null http://127.0.0.1:8765/api/auth-mode && break; sleep 1; done
check foxworx-runs 'curl -sf http://127.0.0.1:8765/api/auth-mode | grep -q "\"mode\":\"open\""'
check foxworx-catalog 'curl -sf -H "Origin: http://127.0.0.1:8765" http://127.0.0.1:8765/api/tools/inventory | python3 -c "import json,sys; d=json.load(sys.stdin); assert d[\"totals\"][\"executables\"] > 100"'
foxos-tools check 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' | grep -E '✗|missing' | sed 's/^/INFO /'
grep -iE 'error|failed' /tmp/fw.log | head -5 | sed 's/^/INFO fw: /'
[ $fail = 0 ] && echo "RESULT PASS" || echo "RESULT FAIL"
