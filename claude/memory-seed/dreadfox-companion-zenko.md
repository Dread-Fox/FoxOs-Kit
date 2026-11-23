---
name: dreadfox-companion-zenko
description: DreadFox desktop companion widget, FoxWorx pet link, and Zenko local abliterated models via LM Studio
metadata:
  type: project
---
**DreadFox Companion** = `~/h1/dreadfox_widget.py` (+ `~/h1/fox_art.py`): Tk desktop kitsune in Bounty Radar's crimson/steel
theme (BG #0c0e12, CRIMSON #E11D2A, STEEL #b8c0cc). Art is the DreadFox redesign: 9 stages (tails 1→9), 8 moods.
It reads `~/h1/data/pet.json`, which FoxWorx writes when settings.env has `FOXWORX_COMPANION_FILE` +
`FOXWORX_COMPANION_OWNER` (foxworx/companion_bridge.py, aggregates only). TALK window calls FoxWorx's API and only
works without a browser sign-in in open mode. The web sanctuary (static/fox-art.js) uses the same art and mood.

**Zenko** = FoxWorx agent on local abliterated models through LM Studio 0.4.x (`~/zenko-scripts`, README there).
Main model loads as `zenko-main` at http://127.0.0.1:1234/v1; FoxWorx selection: LM Studio / lmstudio-local / zenko-main.
Zenko memory (foxworx/zenko_memory.py) embeds with nomic-embed via LM Studio.

**Why:** the user wants one pet across web + desktop and uncensored local models for security tool calls.
**How to apply:** verify LM Studio is running and `zenko-main` is loaded before debugging AI failures in FoxWorx.
Related: [[hackerone-api]], [[foxos-rice]].
