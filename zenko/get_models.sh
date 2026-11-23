#!/bin/bash
# Downloads Zenko's models into LM Studio's models folder (resumable).
M=~/.lmstudio/models
get() { repo=$1 file=$2; mkdir -p "$M/$repo"; echo "START $repo/$file $(date +%T)"
  curl -fL --retry 5 --retry-delay 5 -C - -s -o "$M/$repo/$file" "https://huggingface.co/$repo/resolve/main/$file" \
    && echo "DONE $file $(date +%T)" || echo "FAILED $file (exit $?)"; }
get nomic-ai/nomic-embed-text-v1.5-GGUF nomic-embed-text-v1.5.Q8_0.gguf
get huihui-ai/Huihui-Qwen3.6-35B-A3B-abliterated-MTP-GGUF Huihui-Qwen3.6-35B-A3B-abliterated-ggml-model-Q4_K.gguf
get unsloth/Qwen3.8-27B-GGUF Qwen3.8-27B-UD-Q4_K_XL.gguf
get DavidAU/OpenAi-GPT-oss-20b-abliterated-uncensored-NEO-Imatrix-gguf OpenAI-20B-NEOPlus-Uncensored-IQ4_NL.gguf
echo ALL_FINISHED
