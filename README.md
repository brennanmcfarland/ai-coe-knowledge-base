# CoE Build Wizard

A local-only wizard that turns the AI CoE ClickUp knowledge base into a curated graph of
development steps (procure data → system design → security review → …). Each step has a summary
with citations back to the source ClickUp doc, a checklist, and a completion box. You work through
the frontier (steps whose prerequisites are done), and each project tracks its own progress.

Nothing leaves your machine: the backend binds to `127.0.0.1`, serves only its own frontend, and
the only network calls are to ClickUp (when you sync) and to download the pinned model.

## Prototype caveats
This app is heavily prototype/vibe coded and should be treated as such with no guarantees of
quality.

## Requirements
- Python 3.14 + [uv](https://docs.astral.sh/uv/), Node 22+
- An OS keyring (GNOME Keyring / KWallet / macOS Keychain) for the ClickUp OAuth app credentials
- For building the ontology: a Vulkan-capable GPU (tuned for 16 GB VRAM) and ~19 GB of disk

```sh
(cd backend && uv sync)
(cd frontend && npm install && npx playwright install chromium)
```

## ClickUp OAuth setup (once)
1. In ClickUp: **Settings → Integrations → ClickUp API → Create an App**.
2. Redirect URL: `http://127.0.0.1:8765/api/auth/callback`. If you use `./dev.sh`, also add
   `http://127.0.0.1:5173/api/auth/callback`.
3. Start the app and open **Log in**. The first time, the page asks for the client ID and secret
   and stores them in your OS keyring. They're never written to a file.

The access token is kept **only in the backend's memory**: you log in again after every restart.
No environment variables or `.env` files are read. If you previously used a personal API token,
revoke it in ClickUp.

## Usage
| Command | What it does |
|---|---|
| `./serve.sh` | Build the frontend and serve the app at http://127.0.0.1:8765 |
| `./dev.sh` | Backend + Vite dev server with hot reload at http://127.0.0.1:5173 |
| `./scrape.sh` | Log in if needed (opens the browser), then sync ClickUp Docs into `data/corpus/`. Incremental; rate limited to 60 req/min (`COE_RPM` to change) |
| `./build_ontology.sh` | Chunk the corpus and (re)build the step graph with a local LLM. First run downloads llama.cpp (Vulkan) and Qwen3-30B-A3B-Instruct-2507 Q4_K_M into `.cache/` |
| `./graphify.sh` | Rebuild the code knowledge graph in `graphify-out/` (code only) |

Typical first run: `./scrape.sh`, then `./build_ontology.sh`, then `./serve.sh`. Afterwards, curate
the graph in **Edit mode**.

`build_ontology.sh` knobs: `LLAMA_DEVICE` (default `Vulkan0`, the discrete GPU), `N_CPU_MOE`
(default 20 MoE layers kept in system RAM), `CTX_SIZE` (default 32768), `LLAMA_URL` (use an
already-running OpenAI-compatible server). Extra args go to the builder, e.g.
`./build_ontology.sh --limit-chunks 20` for a quick smoke test.

## Curating the graph
Turn on **Edit mode** in the header. You can:
- add, rename, delete, and merge steps
- drag between node handles to add a prerequisite, or select an edge and press Delete to remove it
- edit checklists, and remove or re-attach citations
- accept or reject proposals

Anything you edit is **locked** so rebuilds don't overwrite it. Rebuilds add new steps and edges as
dashed *proposals*, and anything you deleted or rejected stays gone. Summaries always come from
the latest build.

Merging moves every project's progress to the surviving step. Deleting hides progress, and
re-creating a step with the same name restores it.

## Where data lives (all gitignored)
- `data/corpus/`: scraped Markdown + `manifest.json`
- `data/chunks.jsonl`: citeable chunks
- `data/ontology.yaml`: the curated graph. Hand-editable: the app validates it and shows errors,
  and your comments survive app edits.
- `data/backups/`: timestamped ontology backups (last 100). **Back up `data/` yourself** if your
  curation matters, because it isn't versioned in git.
- `data/progress.sqlite3`: projects and progress

## Development
```sh
cd backend && uv run pytest && uv run ruff check && uv run pyright
cd frontend && npm run lint && npm run typecheck && npm run test:e2e
```
The Playwright suite runs the real backend against fixture data in `frontend/e2e/fixtures`, with no
ClickUp or LLM. The Playwright MCP server (`.mcp.json`) is pinned to Playwright 1.60.0, the same
version as the test runner.
