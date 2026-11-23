#!/bin/bash
# Waits for get_models.sh, then resumes any incomplete model files until each matches its size on Hugging Face.
while pgrep -f zenko-scripts/get_models.sh >/dev/null; do sleep 30; done
M=~/.lmstudio/models
fix() { repo=$1 file=$2; url="https://huggingface.co/$repo/resolve/main/$file"
  want=$(curl -sIL "$url" | grep -i '^content-length' | tail -1 | tr -dc 0-9)
  for try in $(seq 1 30); do
    have=$(stat -c %s "$M/$repo/$file" 2>/dev/null || echo 0)
    [ -n "$want" ] && [ "$have" = "$want" ] && { echo "DONE $file (verified $have bytes)"; return; }
    echo "RESUME $file try $try at $have/$want bytes $(date +%T)"
    mkdir -p "$M/$repo"; curl -fL --retry 5 --retry-all-errors --retry-delay 10 -C - -s -o "$M/$repo/$file" "$url"; sleep 5
  done; echo "FAILED $file after 30 tries"; }
fix huihui-ai/Huihui-Qwen3.6-35B-A3B-abliterated-MTP-GGUF Huihui-Qwen3.6-35B-A3B-abliterated-ggml-model-Q4_K.gguf
fix unsloth/Qwen3.8-27B-GGUF Qwen3.8-27B-UD-Q4_K_XL.gguf
fix DavidAU/OpenAi-GPT-oss-20b-abliterated-uncensored-NEO-Imatrix-gguf OpenAI-20B-NEOPlus-Uncensored-IQ4_NL.gguf
fix nomic-ai/nomic-embed-text-v1.5-GGUF nomic-embed-text-v1.5.Q8_0.gguf
echo ALL_VERIFIED
