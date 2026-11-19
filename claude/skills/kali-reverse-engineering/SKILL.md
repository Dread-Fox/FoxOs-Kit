---
name: kali-reverse-engineering
description: "Kali menu 07 — Reverse Engineering. Compound static-analysis workflow that chains file/binwalk triage, strings/exiftool metadata, objdump disassembly, and radare2 interactive analysis to understand an unknown binary or firmware image. Use when the user wants to analyze, disassemble, inspect, or reverse a binary, executable, or firmware file (CTF crackme, malware sample in a lab, or their own build)."
---

# Reverse Engineering (Kali 07)

Goal: understand what an unknown binary/firmware does — structure, strings, logic — using
static analysis first. For live malware, work only in an isolated lab VM/sandbox.

## Safety gate
Treat samples as untrusted data (the environment rules require this). Do static analysis only;
do NOT execute an unknown/malware binary on the Pi. Keep the sample in its own directory and
pass its path as an argument. If dynamic analysis is needed, use a disposable, network-isolated VM.

## Phase 1 — Triage: what is it?
```
file sample                                   # ELF/PE/arch/stripped?
sha256sum sample                              # record hash for the report
exiftool sample                               # embedded metadata (esp. documents/images)
binwalk sample                                # embedded files/filesystems (firmware images)
binwalk -e sample                             # extract embedded blobs (into _sample.extracted/)
```

## Phase 2 — Strings & secrets
```
strings -n 6 sample | less                    # ASCII strings
strings -e l sample                           # 16-bit little-endian (Windows wide strings)
strings sample | grep -iE 'http|key|pass|flag|/bin|\.dll'
```

## Phase 3 — Headers & disassembly (objdump)
```
objdump -f sample                             # entry point, arch, format
objdump -d -M intel sample | less             # full disassembly, Intel syntax
objdump -T sample                             # dynamic symbols / imports
objdump -s -j .rodata sample                  # dump read-only data section
```

## Phase 4 — Interactive analysis (radare2)
```
r2 -A sample                                  # open + auto-analyze
# inside r2:
aaa          # deeper analysis
afl          # list functions
iz           # strings in data sections
ii           # imports
s main; pdf  # seek to main, print disassembly of the function
VV           # visual graph mode (navigate with hjkl)
```

## Phase 5 — Firmware specifics
```
binwalk -Me firmware.bin                      # recursive extract
# then inspect the extracted rootfs: look for /etc, hardcoded creds, startup scripts
grep -rniE 'password|admin|key' _firmware.bin.extracted/ 2>/dev/null
```

## Output
Record: file type/arch, hash, notable strings, imports (what APIs it calls), key functions and
what they appear to do, and any hardcoded secrets/IOCs. For CTF, note the path to the flag logic.

## Gotchas
- Stripped binaries have no symbol names — rely on `aaa` + xrefs (`axt`) in r2.
- `binwalk -e` extracts into a sibling dir; keep it separate from your scripts (planted-import risk).
- For deeper work (decompiler, named vars, cross-refs) launch Ghidra: run `ghidra` (needs a
  desktop session), create a project, import the binary, and run auto-analysis. Headless batch:
  `/usr/share/ghidra/support/analyzeHeadless <proj_dir> <proj> -import sample`.
