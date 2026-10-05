#!/usr/bin/env bash
# Chunk the scraped corpus and (re)build the wizard DAG with a local llama.cpp model.
#
# Downloads a pinned llama.cpp Vulkan build and a pinned GGUF (SHA256-verified) into .cache/,
# runs llama-server on a free localhost port for the duration of the build, then merges the
# result into data/ontology.yaml (your curated edits are kept; new items arrive as proposals).
#
# Env overrides:
#   LLAMA_URL      use an already-running OpenAI-compatible server instead of provisioning one
#   LLAMA_DEVICE   llama.cpp device (default Vulkan0 = the discrete GPU; see --list-devices)
#   N_CPU_MOE      MoE layers whose experts stay in system RAM (default 20, fits 16 GB VRAM)
#   CTX_SIZE       context window (default 32768)
# Extra args are passed to `coe-wizard build-ontology` (e.g. --limit-chunks 20 for a smoke test).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE="$ROOT/.cache"

LLAMA_TAG="b11430"
LLAMA_ASSET="llama-${LLAMA_TAG}-bin-ubuntu-vulkan-x64.tar.gz"
LLAMA_SHA256="99652a5a753b4045a885c5c3f5565c05a59d0391ac2cb207a327c7d0f72917f2"
LLAMA_URL_DL="https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_TAG}/${LLAMA_ASSET}"

MODEL_FILE="Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf"
MODEL_SHA256="6c997b8af17debdfb01d890214400ccbab00db6acc0ba8da5de1cc906c4774d0"
MODEL_URL="https://huggingface.co/unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF/resolve/main/${MODEL_FILE}"

fetch_verified() { # url dest sha256
  local url="$1" dest="$2" sha="$3"
  if [[ -f "$dest" ]] && echo "$sha  $dest" | sha256sum -c --status; then
    return
  fi
  echo "Downloading $(basename "$dest")…"
  curl -L --fail --retry 3 -C - -o "$dest.part" "$url"
  if ! echo "$sha  $dest.part" | sha256sum -c --status; then
    rm -f "$dest.part"
    echo "SHA256 mismatch for $(basename "$dest"); refusing to use it" >&2
    exit 1
  fi
  mv "$dest.part" "$dest"
}

if [[ ! -f "$ROOT/data/corpus/manifest.json" ]]; then
  echo "No scraped corpus yet. Run ./scrape.sh first." >&2
  exit 1
fi

if [[ -z "${LLAMA_URL:-}" ]]; then
  mkdir -p "$CACHE/models"
  fetch_verified "$LLAMA_URL_DL" "$CACHE/$LLAMA_ASSET" "$LLAMA_SHA256"
  if [[ ! -x "$CACHE/llama-$LLAMA_TAG/llama-server" ]]; then
    tar xzf "$CACHE/$LLAMA_ASSET" -C "$CACHE"
  fi
  fetch_verified "$MODEL_URL" "$CACHE/models/$MODEL_FILE" "$MODEL_SHA256"

  PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
  LOG="$CACHE/llama-server.log"
  echo "Starting llama-server on 127.0.0.1:$PORT (log: $LOG)"
  "$CACHE/llama-$LLAMA_TAG/llama-server" \
    -m "$CACHE/models/$MODEL_FILE" \
    --host 127.0.0.1 --port "$PORT" \
    --device "${LLAMA_DEVICE:-Vulkan0}" \
    -ngl 999 --n-cpu-moe "${N_CPU_MOE:-20}" \
    -c "${CTX_SIZE:-32768}" \
    >"$LOG" 2>&1 &
  SERVER_PID=$!
  trap 'kill "$SERVER_PID" 2>/dev/null; wait "$SERVER_PID" 2>/dev/null || true' EXIT

  echo -n "Waiting for the model to load"
  for _ in $(seq 1 600); do
    if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null; then break; fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
      echo; echo "llama-server exited; see $LOG" >&2; tail -20 "$LOG" >&2; exit 1
    fi
    echo -n "."; sleep 1
  done
  echo
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null || { echo "llama-server never became healthy" >&2; exit 1; }
  LLAMA_URL="http://127.0.0.1:$PORT"
fi

uv run --project "$ROOT/backend" coe-wizard --data-dir "$ROOT/data" build-ontology --llama-url "$LLAMA_URL" "$@"
