#!/usr/bin/env bash
# Chunk the scraped corpus and (re)build the wizard DAG with a local llama.cpp model.
#
# The first run downloads a pinned llama.cpp Vulkan build and a pinned GGUF (SHA256-verified)
# into .cache/. The llama-server is shared with the running app (interviews use it too): if the
# app already has it loaded it's reused, otherwise it's started for the build and stopped after,
# unless the app is still using it. The result is merged into data/ontology.yaml (your curated
# edits are kept; new items arrive as proposals), chunks get facet tags, and new interview
# questions/options are proposed in data/interview.yaml.
#
# Env overrides:
#   LLAMA_URL      use an already-running OpenAI-compatible server instead of the managed one
#   LLAMA_DEVICE   llama.cpp device (default Vulkan0 = the discrete GPU; see --list-devices)
#   N_CPU_MOE      MoE layers whose experts stay in system RAM (default 20, fits 16 GB VRAM)
#   CTX_SIZE       context window (default 32768)
# Extra args are passed to `coe-wizard build-ontology` (e.g. --limit-chunks 20 for a smoke test).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$ROOT/data/corpus/manifest.json" ]]; then
  echo "No scraped corpus yet. Run ./scrape.sh first." >&2
  exit 1
fi

exec uv run --project "$ROOT/backend" coe-wizard --data-dir "$ROOT/data" --cache-dir "$ROOT/.cache" \
  build-ontology "$@"
