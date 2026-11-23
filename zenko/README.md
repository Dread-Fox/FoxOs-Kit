# Zenko — local abliterated models through LM Studio

Zenko is the FoxWorx agent running on local abliterated models, so it doesn't refuse security tool calls.
FoxWorx talks to LM Studio over the built-in `lmstudio-local` endpoint (`http://127.0.0.1:1234/v1`).

## 1. LM Studio
Install LM Studio **0.4.x** (0.4.28 tested) from https://lmstudio.ai — the AppImage works on Kali.
Older llama.cpp runtimes (LM Studio 0.3.x, Ollama 0.11) cannot load the `qwen35` / `qwen35moe` architectures
these models use. Open it once so it installs the `lms` CLI at `~/.lmstudio/bin/lms`.

## 2. Models (~45 GB)
```bash
~/zenko-scripts/get_models.sh        # resumable curl download into ~/.lmstudio/models
~/zenko-scripts/aria_models.sh       # faster alternative: 16 connections with aria2c, verifies sizes
~/zenko-scripts/resume_models.sh     # finishes/validates any partial download
```
| model | role |
|---|---|
| Huihui-Qwen3.6-35B-A3B-abliterated Q4_K | main agent (MoE, ~45 tok/s on 3x RTX 3060 Ti) |
| Qwen3.8-27B UD-Q4_K_XL | deeper, slower reasoning |
| OpenAI gpt-oss-20b abliterated NEOPlus IQ4_NL | fallback (harmony format, reasoning in `reasoning_content`) |
| nomic-embed-text v1.5 Q8_0 | embeddings for Zenko memory |

## 3. Load and connect
```bash
~/zenko-scripts/load_zenko.sh                         # ZENKO_GPU / ZENKO_CTX / ZENKO_MODEL to tune
python3 -I ~/zenko-scripts/talk.py /tmp/h.json "hi"   # quick chat test (keeps history in h.json)
```
`--gpu max` at a 128K context runs out of memory when the desktop also uses GPU 0. These models think before
answering, so give direct API calls at least 4K output tokens.

In FoxWorx: **AI copilot → Provider & model → LM Studio → lmstudio-local → model `zenko-main` → Use this model**.
The DreadFox pet chat and the desktop companion's TALK window use the same selection.

Known behaviour: Zenko treats "the IP is on the scope list" as decisive even when other evidence says the list is
wrong. Keep engagement scope accurate; the Rabbit Hole guardrails enforce it regardless of the model.
