# Install, scrape, build and serve

This is the procedure for getting the CoE Build Wizard from a fresh clone to a running app, and for
keeping it up to date. For how the pieces fit together, see [architecture.md](architecture.md).

```
Install ─▶ Serve ─▶ Log in + Scrape ─▶ Build ontology ─▶ Use / curate
             ▲                                               │
             └────────── re-scrape + rebuild to refresh ◀────┘
```

All commands are run from the repository root.

## 1. Install

### Prerequisites

| Need | Why | Check |
|---|---|---|
| Python 3.14 + [uv](https://docs.astral.sh/uv/) | Backend | `uv --version` |
| Node 22+ and npm | Frontend build | `node --version` |
| OS keyring (GNOME Keyring, KWallet, macOS Keychain) | Only for OAuth login, to store the client id and secret | Not needed for personal-token login |
| Vulkan-capable GPU (tuned for 16 GB VRAM), Linux x64 | Running the bundled local model | `vulkaninfo --summary` |
| About 20 GB free disk in `.cache/` | llama.cpp build plus an 18.8 GB model, with a 1 GB margin | `df -h .` |
| Access to the AI CoE ClickUp space | Source content | — |

Without a suitable GPU or on another OS, point the app at any OpenAI-compatible server that
supports `json_schema` response formats by setting `LLAMA_URL` (see [step 4](#4-build-the-ontology)).

### Install dependencies

```sh
(cd backend && uv sync)
(cd frontend && npm install)
# Only needed to run the end-to-end tests:
(cd frontend && npx playwright install chromium)
```

No `.env` file or secret environment variables are needed.

## 2. Serve the app

```sh
./serve.sh
```

This builds the frontend into `frontend/dist/` and serves the whole app at
**http://127.0.0.1:8765**. Leave it running in its own terminal.

| Env var | Default | Effect |
|---|---|---|
| `COE_PORT` | `8765` | Port to listen on. If you use OAuth, the redirect URL registered in ClickUp must match |
| `COE_RPM` | `60` | ClickUp requests per minute during syncs started from this server |
| `LLAMA_URL` | unset | Use an existing OpenAI-compatible server instead of the managed llama-server |

For frontend development, use `./dev.sh` instead. It runs the backend on `:8765` and Vite with hot
reload on **http://127.0.0.1:5173**, which proxies `/api` to the backend.

**Check:** `curl -s http://127.0.0.1:8765/api/auth/status` returns JSON.

> Start the server before scraping. `scrape.sh` reuses a running server, so your ClickUp login
> stays in memory for the rest of the session. If no server is running, `scrape.sh` starts a
> temporary one and stops it on exit, and the login is lost with it.

## 3. Log in and scrape ClickUp

```sh
./scrape.sh
```

The script:
1. Connects to the backend on `COE_PORT`. If none is running, it builds the frontend if needed,
   then starts a temporary backend that logs to `data/scrape-server.log`.
2. If you aren't logged in, it prompts for a ClickUp **personal API token**. Leave the prompt
   blank to use OAuth in the browser instead (see below).
3. Starts the sync and streams its progress until it finishes. If a sync is already running, it
   follows that one.

You can also start a sync from the app, with the **Sync from ClickUp** button in the header.

### Option A: personal API token (simplest)

Get it in ClickUp under **avatar → Settings → Apps → API Token**. It starts with `pk_`. The token
is passed on stdin, checked against ClickUp, and held only in backend memory. It is never written
to disk, and you paste it again after every backend restart.

Personal tokens don't expire and can access everything your account can. Only use one where that
risk is acceptable, and regenerate it if it leaks.

### Option B: OAuth (needs a workspace admin, once)

1. In ClickUp: **Settings → Integrations → ClickUp API → Create an App**.
2. Add the redirect URL `http://127.0.0.1:8765/api/auth/callback`. If you use `./dev.sh`, also add
   `http://127.0.0.1:5173/api/auth/callback`. If you changed `COE_PORT`, use that port.
3. Leave the token prompt blank (or open **Log in** in the app). The first time, the page asks for
   the client ID and secret and stores them in the OS keyring.
4. Approve access in ClickUp. You are redirected back and logged in.

### What a sync does

- Walks the configured space (ids in `backend/src/coe_wizard/config.py`) and writes each doc page
  as Markdown to `data/corpus/<doc>/<page>.md`, indexed by `data/corpus/manifest.json`.
- It is **incremental**: unchanged docs and pages are skipped, and removed ones are marked
  deleted. The first sync takes longest. Later syncs fetch only what changed.
- It is rate limited to `COE_RPM` per minute and backs off on 429 and 5xx responses.
- An interrupted sync keeps everything fetched so far. Run it again to continue.

**Check:** the script ends with `Sync complete.` and `data/corpus/manifest.json` exists.

## 4. Build the ontology

```sh
./build_ontology.sh
```

This is a batch job. It can run while the app is being served, and edits you make in the app during
the build are preserved.

The first run downloads the pinned llama.cpp Vulkan build and the
`Qwen3-30B-A3B-Instruct-2507-Q4_K_M` model (about 18.8 GB) into `.cache/`, checks their SHA-256,
and resumes if interrupted. After that, the script:

1. Re-chunks the corpus into `data/chunks.jsonl`.
2. Starts the model, or reuses one the running app already has loaded.
3. Runs the pipeline: map → place → consolidate → edges → summarize. Progress is printed, and the
   running step list is visible in `data/ontology-steps.jsonl`.
4. Merges the result into `data/ontology.yaml`. Your curated edits are kept, and new items arrive
   as proposals.
5. Adds proposed interview questions and options to `data/interview.yaml`.

It ends with `Ontology written: N nodes (M proposed)` and a count of interview proposals to review.

| Env var | Default | Effect |
|---|---|---|
| `LLAMA_DEVICE` | `Vulkan0` | llama.cpp device (the discrete GPU) |
| `N_CPU_MOE` | `20` | MoE layers whose experts stay in system RAM. Raise it if you run out of VRAM, lower it if you have more |
| `CTX_SIZE` | `32768` | Context window |
| `LLAMA_URL` | unset | Use an already-running OpenAI-compatible server and skip the download |

Extra arguments are passed to the builder. For a quick smoke test on a few chunks:

```sh
./build_ontology.sh --limit-chunks 20
```

A full build over the corpus can take a long time, because it makes several LLM calls per chunk
and per step.

**Check:** `curl -s http://127.0.0.1:8765/api/ontology/status` shows `"valid": true` and a node
count.

## 5. Use the app

Open http://127.0.0.1:8765.

1. **Create a project.** On **Projects**, name it and click **Start interview**. The interview needs the local
   model. If it isn't downloaded yet, the page offers a **Download** button. Answer each section,
   then answer the model's follow-up questions. When you finish, the app tailors the step graph
   and creates the project.
2. **Work the frontier.** The project's DAG highlights *frontier* steps, whose prerequisites are
   all complete. Open a step to read its cited summary (citations open the source page in a side
   panel), tick its checklist, and mark it complete. Progress is saved per project.
3. **Curate (Edit mode).** Turn on **Edit mode** in the header to rename, add, delete or merge
   steps, edit prerequisites, checklists and citations, and accept or reject proposals from
   rebuilds. Anything you edit is locked against future rebuilds. See *Curating the graph* in the
   [README](../README.md#curating-the-graph).

## 6. Keep it up to date

Repeat steps 3 and 4 whenever the ClickUp content changes:

```sh
./scrape.sh            # with ./serve.sh already running
./build_ontology.sh
```

Then, in Edit mode, review the new proposals in the ontology and the interview questions. To
apply the updated graph to an existing project, use **Re-tailor** on that project and accept or
reject the proposed changes.

## Back up and restore

`data/` is gitignored and not versioned. It holds the scraped corpus, your curated graph, the
interview definition, project overlays, and all progress.

- **Back up** the whole `data/` directory, ideally while the server is stopped, because
  `progress.sqlite3` uses WAL mode.
- Every YAML write saves the previous version to `data/backups/` (the last 100 per file). To roll
  back the ontology, stop the server, copy the chosen `data/backups/ontology-<timestamp>.yaml` over
  `data/ontology.yaml`, and start the server again.
- `.cache/` can be deleted at any time. The model is downloaded again when it is next needed.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Backend failed to start; see data/scrape-server.log` | Read the log. The usual cause is that the port is in use. Set `COE_PORT` or stop the other process |
| Logged out after a restart | Expected, because tokens are held only in memory. Log in again |
| OAuth returns `Invalid or expired OAuth state` | The login took more than 10 minutes, or the server restarted mid-login. Start the login again |
| OAuth redirect mismatch | The redirect URL in the ClickUp app must exactly match `redirect_uri` from `/api/auth/status` |
| `No scraped corpus yet. Run ./scrape.sh first.` | Run step 3 |
| Model fails to start or runs out of memory | See `.cache/llama-server.log`. Raise `N_CPU_MOE`, lower `CTX_SIZE`, or check the device name with `.cache/llama-b11430/llama-server --list-devices` |
| App shows `invalid_ontology` | A hand edit to `data/ontology.yaml` broke validation (duplicate id, unknown node, or cycle). Fix the file, or restore from `data/backups/` |
| An edit fails with "Ontology changed since it was last read" | Another edit or a build landed first. The UI refetches, so retry the edit |
| Vite says port 5173 is in use (`./dev.sh`) | Stop the other Vite instance. The port is strict so the OAuth redirect stays valid |

## Development checks

```sh
cd backend && uv run pytest && uv run ruff check && uv run pyright
cd frontend && npm run lint && npm run typecheck && npm run test:e2e
```

The e2e suite runs the real backend against fixture data with a scripted model. It needs no
ClickUp access and no GPU. `./graphify.sh` rebuilds the code knowledge graph in `graphify-out/`.
