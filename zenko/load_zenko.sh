#!/bin/bash
# Starts LM Studio's server and loads Zenko's main model as `zenko-main` (what FoxWorx and talk.py call).
# Defaults fit 3x RTX 3060 Ti 8GB with the desktop using ~1.5 GB of GPU 0; override per machine:
#   ZENKO_GPU=0.65  ZENKO_CTX=131072  ZENKO_MODEL=huihui-qwen3.6-35b-a3b-abliterated-mtp
L=$(command -v lms || echo ~/.lmstudio/bin/lms)
[ -x "$L" ] || { echo "lms not found: open LM Studio once (it installs ~/.lmstudio/bin/lms)"; exit 1; }
$L server start
$L unload zenko-main 2>/dev/null
$L load "${ZENKO_MODEL:-huihui-qwen3.6-35b-a3b-abliterated-mtp}" --gpu "${ZENKO_GPU:-0.65}" \
  -c "${ZENKO_CTX:-131072}" --identifier zenko-main -y
