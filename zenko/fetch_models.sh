#!/bin/bash
# Download Zenko's models into LM Studio's models folder, resumable, verified against Hugging Face sizes.
#   fetch_models.sh main   main agent model + embeddings (~21 GB)   [default]
#   fetch_models.sh all    + Qwen3.8-27B reasoning + gpt-oss-20b fallback (~49 GB)
# Uses aria2c (16 connections) when installed, else curl. Safe to re-run: finished files are skipped.
set -u
M="${LMSTUDIO_MODELS:-$HOME/.lmstudio/models}"
SET="${1:-main}"
MAIN=(nomic-ai/nomic-embed-text-v1.5-GGUF nomic-embed-text-v1.5.Q8_0.gguf
      huihui-ai/Huihui-Qwen3.6-35B-A3B-abliterated-MTP-GGUF Huihui-Qwen3.6-35B-A3B-abliterated-ggml-model-Q4_K.gguf)
EXTRA=(unsloth/Qwen3.8-27B-GGUF Qwen3.8-27B-UD-Q4_K_XL.gguf
       DavidAU/OpenAi-GPT-oss-20b-abliterated-uncensored-NEO-Imatrix-gguf OpenAI-20B-NEOPlus-Uncensored-IQ4_NL.gguf)
case "$SET" in main) LIST=("${MAIN[@]}") ;; all) LIST=("${MAIN[@]}" "${EXTRA[@]}") ;;
  *) echo "usage: $0 [main|all]"; exit 1 ;; esac
fail=0
get() { local repo=$1 file=$2 url="https://huggingface.co/$1/resolve/main/$2" want have
  mkdir -p "$M/$repo"
  want=$(curl -sIL --max-time 30 "$url" | grep -i '^content-length' | tail -1 | tr -dc 0-9)
  for try in 1 2 3 4 5 6; do
    have=$(stat -c %s "$M/$repo/$file" 2>/dev/null || echo 0)
    if [ -n "$want" ] && [ "$have" = "$want" ] && [ ! -e "$M/$repo/$file.aria2" ]; then
      echo "  ✓ $file ($((want/1024/1024)) MiB)"; return 0; fi
    echo "  ↓ $file  try $try  $((have/1024/1024))/$(( ${want:-0}/1024/1024 )) MiB"
    if command -v aria2c >/dev/null; then
      aria2c -c -x 16 -s 16 -k 20M --max-tries=0 --retry-wait=10 --file-allocation=none \
        --auto-file-renaming=false --summary-interval=30 --console-log-level=warn \
        -d "$M/$repo" -o "$file" "$url"
    else
      curl -fL --retry 5 --retry-all-errors --retry-delay 10 -C - --progress-bar -o "$M/$repo/$file" "$url"
    fi
  done
  echo "  ✗ $file did not finish — re-run $0 $SET"; fail=1; }
echo "Zenko models ($SET) → $M"
for ((i=0; i<${#LIST[@]}; i+=2)); do get "${LIST[i]}" "${LIST[i+1]}"; done
exit $fail
