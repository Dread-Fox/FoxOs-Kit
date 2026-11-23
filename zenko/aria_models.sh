#!/bin/bash
# Multi-connection, resumable download of Zenko's remaining models; verifies final sizes against Hugging Face.
M=~/.lmstudio/models
get() { repo=$1 file=$2; url="https://huggingface.co/$repo/resolve/main/$file"
  want=$(curl -sIL "$url" | grep -i '^content-length' | tail -1 | tr -dc 0-9)
  for try in 1 2 3 4 5; do
    echo "START $file try $try have=$(stat -c %s "$M/$repo/$file" 2>/dev/null || echo 0) want=$want $(date +%T)"
    aria2c -c -x 16 -s 16 -k 20M --max-tries=0 --retry-wait=10 --file-allocation=none \
      --auto-file-renaming=false --summary-interval=0 --console-log-level=warn \
      -d "$M/$repo" -o "$file" "$url"
    have=$(stat -c %s "$M/$repo/$file" 2>/dev/null || echo 0)
    [ -n "$want" ] && [ "$have" = "$want" ] && [ ! -e "$M/$repo/$file.aria2" ] && { echo "DONE $file verified $have bytes $(date +%T)"; return; }
  done; echo "FAILED $file have=$have want=$want"; }
get huihui-ai/Huihui-Qwen3.6-35B-A3B-abliterated-MTP-GGUF Huihui-Qwen3.6-35B-A3B-abliterated-ggml-model-Q4_K.gguf
get unsloth/Qwen3.8-27B-GGUF Qwen3.8-27B-UD-Q4_K_XL.gguf
get DavidAU/OpenAi-GPT-oss-20b-abliterated-uncensored-NEO-Imatrix-gguf OpenAI-20B-NEOPlus-Uncensored-IQ4_NL.gguf
echo ALL_FINISHED
