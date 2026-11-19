---
name: kali-forensics
description: "Kali menu 11 — Forensics. Compound workflow for digital forensics and file analysis that chains hashing/imaging discipline, exiftool metadata, binwalk/foremost carving, strings, and Autopsy/Sleuth Kit for disk and image investigation. Use when the user wants to analyze a disk image, carve/recover files, inspect file metadata, or investigate an artifact in a forensically sound way."
---

# Forensics (Kali 11)

Goal: examine disks, images, and files to recover data and establish facts — preserving
integrity so findings hold up.

## Integrity gate (do this first)
Work on a COPY, never the original. Hash before and after to prove nothing changed. Treat all
evidence as untrusted data (own directory, pass paths as args, don't execute anything found).
```
sha256sum evidence.img | tee evidence.img.sha256         # baseline hash
cp evidence.img work.img                                 # operate on the copy
```
For a live disk, image it read-only first: `sudo dd if=/dev/sdX of=evidence.img bs=4M conv=noerror,sync status=progress`
(prefer a write-blocker / `dcfldd` with hashing when available).

## Phase 1 — Identify & inspect metadata
```
file work.img
exiftool suspicious.jpg                                   # camera, GPS, timestamps, software
exiftool -a -G1 document.pdf                              # all tags, grouped
```

## Phase 2 — Carve embedded/deleted files
```
binwalk work.img                                          # show embedded signatures
binwalk -e work.img                                       # extract known embedded files
foremost -t all -i work.img -o carved/                    # signature-based file carving
```

## Phase 3 — String & keyword sweep
```
strings -a -t x work.img | grep -iE 'password|BEGIN RSA|flag\{|@gmail'
grep -a -o -bE 'flag\{[^}]+\}' work.img                   # byte-offset keyword hits
```

## Phase 4 — Filesystem analysis (Sleuth Kit / Autopsy)
```
mmls work.img                                             # partition layout + offsets
fls -r -o <offset> work.img | less                        # list files incl. deleted (* marks)
icat -o <offset> work.img <inode> > recovered_file        # extract a file by inode
```
GUI investigation (needs a desktop session):
```
autopsy                                                    # browse to http://localhost:9999, new case
```

## Phase 5 — Timeline (optional)
```
fls -r -m "/" -o <offset> work.img > body.txt
mactime -b body.txt -d > timeline.csv                     # MAC-time timeline
```

## Output
Record: evidence hash (before/after), tools and versions used, what was recovered (files,
metadata, keywords) with offsets/inodes, and a chain-of-custody note (who, when, from what).
Keep the methodology reproducible for **kali-reporting**.

## Gotchas
- Never mount evidence read-write; use `mount -o ro,loop,offset=$((512*<sector>))` if you must mount.
- Memory analysis: Volatility 3 is installed via pipx as `vol` (~/.local/bin/vol). E.g.
  `vol -f memdump.raw windows.info` then `windows.pslist`, `windows.netscan`, `linux.bash`.
- Carving yields many false positives — validate recovered files with `file` before reporting.
