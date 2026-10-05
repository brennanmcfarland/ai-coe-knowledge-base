#!/usr/bin/env bash
# Rebuild the code knowledge graph in graphify-out/ (gitignored). Code only: .graphifyignore
# keeps data/ (scraped ClickUp content) and dependencies out of the graph. No LLM needed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
uv run --project backend graphify update . "$@"
