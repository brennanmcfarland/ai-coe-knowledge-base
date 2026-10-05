# Plan: ClickUp-backed AI CoE Wizard

## 1. Context

**Current state:** `ai-coe-knowledge-base` has no commits. It contains `human-plan.md`, a `README.md` that describes a broad API token held in an env var, a `.env` (left alone), and a `.claude/settings.json` that blocks reading `.env`. The remote is `github.com/brennanmcfarland/ai-coe-knowledge-base`, and its visibility wasn't checked. The machine has Python 3.14.5, uv, Node 26, graphify 0.8.47, an AMD RDNA4 GPU with about 16 GB VRAM (Vulkan and ROCm both present), and 30 GB RAM. llama.cpp is not installed.

**What changes:** A local-only web app built in three parts:
- **Scraper** (`scrape.sh`): pulls ClickUp Docs from space `90176814220` (workspace `9017310967`) into a gitignored local store, using OAuth.
- **Ontology builder** (`build_ontology.sh`): uses a local llama.cpp model to propose a DAG of development steps, each with a summary, a checklist, and citations.
- **Wizard UI** (FastAPI + React/Vite 8): you work through the DAG frontier and tick off nodes for each project. An in-app edit mode lets you curate the DAG.

The ClickUp content couldn't be inspected while planning, so the node breakdown comes from the corpus at build time and is then curated by hand.

## 2. Decisions

### Security and data handling
| Decision | Choice | Consequence |
|---|---|---|
| ClickUp credentials | A first-run setup page stores client_id/secret in the OS keyring (`keyring`). The access token stays in backend memory only. | Nothing secret sits on disk in plaintext. You log in again after every backend restart. |
| Old env-var token | Removed. No code reads env tokens, and the README is rewritten for OAuth + keyring. | You need to revoke the old personal token in ClickUp. `.env` is never read or edited. |
| Login scope | Login is only needed to sync (scrape). The wizard works offline from local data. | `scrape.sh` starts the backend if needed, opens the login page, and triggers the scrape through the backend API, so the token never leaves backend memory. |
| Network exposure | The backend binds to `127.0.0.1` only. There is no app auth beyond ClickUp, and nothing is pushed anywhere. | Meets the local-only requirement. Anyone with local shell access can use the app, including the editor. |
| Ontology in git | Gitignored, stored in `data/` next to the corpus. | No ClickUp-derived text reaches GitHub. Curation edits aren't versioned, so the builder and the editor write timestamped backups before every write. |

### Content and LLM
| Decision | Choice | Consequence |
|---|---|---|
| Scrape scope | ClickUp Docs and their pages only, via API v3 with `content_format=text/md` | Small scope with no attachment parsing. Material that exists only in tasks, attachments or comments is invisible to the wizard. |
| Refresh | Incremental, by page `date_updated`, tracked in a manifest. Deleted docs are flagged and their chunks retired. | Fewer API calls. Citations pointing at retired chunks show up as "broken" in edit mode. |
| Rate limit | Token bucket at 60/min (flag `--rpm`). Also honors `X-RateLimit-Remaining/Reset` and backs off exponentially with jitter on 429/5xx. | Safe on any ClickUp plan. A large space takes minutes to scrape the first time. |
| How nodes are defined | The LLM proposes nodes, edges and checklists, and a human curates them in the app. | Deterministic and editable. You have to review the first build. |
| Large-corpus strategy | Chunk the docs into stable IDs (a hash of doc/page ID plus normalized chunk text). Map: extract candidate steps, checklist items and dependencies per chunk, constrained by a JSON schema. Reduce: merge and dedupe into nodes and edges, then write per-node summaries that cite chunk IDs. | One model and no vector DB. Citation quality depends on the merge step, and RAG can be added behind the same interface later. |
| llama.cpp integration | Talk to `llama-server`'s OpenAI-compatible HTTP API with `httpx`, behind an `LLMBackend` protocol (`complete(prompt, json_schema) -> dict`). | Nothing native to compile on 3.14. Another backend can be swapped in later. |
| Default model | Qwen3-30B-A3B Q4_K_M (about 18 GB), with partial MoE CPU offload (`--n-cpu-moe`) | Higher quality than 14B. It overflows 16 GB VRAM and uses about 6+ GB of system RAM (see Risks). |
| Provisioning | `build_ontology.sh` downloads a pinned llama.cpp Vulkan release and the pinned GGUF (checked by SHA256) into gitignored `.cache/`. It starts llama-server on a free localhost port and stops it on exit. | Reproducible with no system install. The first run downloads about 19 GB. |

### Wizard behavior
| Decision | Choice | Consequence |
|---|---|---|
| Projects | Multiple projects, each with its own progress | Needs a project picker, and all progress is keyed by `project_id`. |
| Completion | A manual "mark complete" box per node. Checklist items are guidance only. | Checking every item doesn't complete a node on its own. |
| Frontier and gating | The frontier is incomplete nodes whose predecessors are all complete, and it's highlighted. Locked nodes are dimmed but can still be opened. | Behaves like a wiki and doesn't block reading ahead. |
| Persistence | SQLite in `data/` via stdlib `sqlite3`, with a version-table migration | Survives restarts and browser changes. Navigating never resets state because it's all server-side. |
| Citations | Clicking a citation opens a right-hand side panel with the cited excerpt and an "Open in ClickUp" link (new tab). The local doc isn't rendered. | Simpler, and works around ClickUp blocking iframes. Each citation stores the excerpt text and the ClickUp page URL. |

### Curation editor
| Decision | Choice | Consequence |
|---|---|---|
| Location | A global "Edit mode" toggle that makes the normal graph and node pages editable inline | No separate admin route. Edits apply to all projects. |
| Store | The ontology stays a YAML file, which the editor rewrites atomically (temp file plus rename) using **ruamel.yaml** round-trip | Human-readable and keeps hand-added comments and key order. Ontology (YAML) and progress (SQLite) live in two stores, so merges must update both (see Changes). |
| What can be edited | Nodes (add, rename, delete, merge), edges (drag in React Flow, with cycles rejected), checklist items (add, remove, reorder), and citations (remove or re-attach to a chunk) | Summary text isn't editable. It is LLM-owned and refreshes on every build. |
| Merge on re-run | Any field you've edited is locked and never overwritten. New LLM nodes and edges come in as `proposed` (dashed in edit mode) for you to accept or reject. Untouched fields and all summaries refresh. | Edits survive rebuilds. A summary can drift away from a node you've hand-curated. |
| Orphaned progress | A merge moves progress to the surviving node (completion is OR'd, checklist ticks are unioned by item ID). A delete keeps the rows but hides them. | Re-creating a node with the same ID brings its progress back. The DB keeps some dead rows. |
| Concurrent edits | The YAML carries a content hash. Every editor write sends the hash it last saw, and a stale hash gets a 409 that asks the UI to reload. | Stops two tabs, or a rebuild and an edit, from silently overwriting each other. |

### Dependencies
| Area | Chosen | Alternatives considered |
|---|---|---|
| Backend framework | **FastAPI** (+ uvicorn, pydantic) | Litestar, Starlette only, Flask |
| HTTP client | **httpx** (async; ClickUp and llama-server) | — |
| Secrets | **keyring** | — |
| YAML | **ruamel.yaml** | PyYAML |
| DB | **stdlib sqlite3** | SQLModel, SQLAlchemy 2 core |
| Python tooling | **uv, ruff, pyright, pytest, respx** | ty, mypy |
| LLM runtime | **llama-server (pinned Vulkan release)** | llama-cpp-python, Ollama |
| Frontend | **React + TS on Vite 8.x** | Svelte 5, Vue 3, vanilla TS |
| DAG | **@xyflow/react 12 + @dagrejs/dagre** | elkjs, precomputed positions |
| Routing / data | **React Router 7 + TanStack Query** | TanStack Router, plain fetch |
| UI | **Tailwind v4 (Vite plugin) + shadcn/ui** (Radix Sheet, Checkbox and similar) | Tailwind only, Mantine, CSS modules |
| Markdown | **react-markdown + rehype-sanitize** (node summaries) | — |
| E2E | **@playwright/test 1.60.0 (exact pin) + @playwright/mcp** | pytest-playwright |
| Code graph | **graphifyy** as a uv dev dependency, output gitignored | committed output, MCP server |
| JS package manager | **npm** (already installed) | — |

## 3. Changes

**Repo layout and config**
- `backend/` is a uv project with `requires-python = ">=3.14,<3.15"`, the dependencies above, and graphifyy in the `dev` group.
- `frontend/` is a Vite 8 React-TS app.
- `scrape.sh`, `build_ontology.sh` and `graphify.sh` sit at the root, along with `dev.sh` (backend plus Vite dev server with an `/api` proxy) and `serve.sh` (builds the frontend, then FastAPI serves `frontend/dist`).
- `.gitignore` explicitly lists `data/` (corpus, manifest, ontology YAML and backups, SQLite), `.cache/` (llama binaries and models), `graphify-out/`, `node_modules/`, `.venv/`, `frontend/dist/`, `test-results/` and `.env*`.
- `.mcp.json` registers `@playwright/mcp`.
- README is rewritten: setup, registering a ClickUp OAuth app with redirect `http://127.0.0.1:<port>/api/auth/callback`, keyring, the scripts, and the prototype caveats. The env-token text is removed.

**Auth (backend `auth` module)**
- `GET /api/auth/status` reports configured, logged in, and the user.
- `POST /api/auth/setup` stores client_id/secret in the keyring.
- `GET /api/auth/login` redirects to ClickUp's authorize URL with a random `state`.
- `GET /api/auth/callback` validates `state`, exchanges the code at `/api/v2/oauth/token`, and keeps the token in process memory.
- `POST /api/auth/logout` drops the token.
- Frontend `/login` page: a setup form if credentials aren't configured, otherwise a "Log in with ClickUp" button.

**Scraper (backend `scraper` package plus `scrape.sh`)**
- `ClickUpClient` combines the rate limiter, retry/backoff and header-aware throttling.
- It enumerates the space, its folders and its lists, then queries v3 docs for each parent.
- For each doc it fetches the pages as Markdown and writes `data/corpus/<doc_id>/<page_id>.md`, with metadata (title, ClickUp URL, `date_updated`) recorded in `data/corpus/manifest.json`.
- Incremental runs compare `date_updated`.
- `POST /api/sync` starts the job and `GET /api/sync` reports progress. The job requires login.
- `scrape.sh` does the following:
  1. Ensures the backend is running.
  2. Opens `/login?next=sync` in the browser.
  3. Polls until logged in.
  4. Triggers the sync and streams its progress.
  5. Exits non-zero on failure.

**Ontology builder (backend `ontology` package plus `build_ontology.sh`)**
- The `LLMBackend` protocol has a `LlamaServerBackend` implementation that uses the `json_schema` / grammar response format.
- The chunker produces stable chunk IDs, stored in `data/chunks.jsonl`.
- The pipeline runs map, then reduce (merge and dedupe nodes, infer edges, break cycles by dropping the lowest-confidence edge), then summarize. Each summary cites chunk IDs, which resolve to an excerpt plus a ClickUp URL.
- The merge step follows the lock and proposal rules above and writes a backup before writing `data/ontology.yaml`.
- YAML schema: a node has `id`, `title`, `summary`, `checklist[{id, text}]`, `citations[{chunk_id, excerpt, url}]`, `status: accepted|proposed`, and `locked_fields[]`. An edge has `from`, `to`, `status` and `locked`.
- The loader validates the file with pydantic, checks that the graph is acyclic and that edge endpoints exist, and reports errors in the UI.
- `build_ontology.sh` provisions `.cache/`, verifies the SHA256, runs llama-server with a trap that stops it on exit, then runs the pipeline.

**Progress (backend `state` module)**
- SQLite tables: `projects`, `node_progress(project_id, node_id, completed, hidden)`, `checklist_progress(project_id, node_id, item_id, checked)` and `schema_version`.
- REST endpoints:
  - Projects: create, list, rename, delete.
  - `GET /api/projects/{id}/graph` returns nodes and edges with completion and frontier flags computed server-side.
  - `PUT` endpoints toggle completion and checklist items.

**Editor API**
- `PATCH`/`POST`/`DELETE` endpoints cover nodes, edges, checklist items, citations, merges, and accepting or rejecting proposals.
- Every write carries the YAML hash, sets the locked field, writes a backup, and does an atomic replace.
- A merge also migrates SQLite progress inside one transaction. If the YAML write fails, the transaction rolls back.
- Edge creation rejects cycles.

**Frontend**
- **Routes:** `/login`, `/projects` (picker), `/projects/:pid` (DAG view) and `/projects/:pid/nodes/:nid` (node page).
- **DAG view:** React Flow laid out by dagre, top to bottom. Nodes are styled as complete, frontier or locked, and clicking one navigates to its page.
- **Node page:** the rendered summary with citation markers, the checklist, the "mark complete" box, and previous/next frontier navigation.
- **Citation panel:** a shadcn Sheet on the right with the excerpt and "Open in ClickUp".
- **Edit mode:** a header toggle turns on inline editors, edge drag-connect and delete on the graph, a merge dialog, a proposals badge with accept and reject, and broken-citation flags.
- **Data:** TanStack Query with optimistic checklist and completion toggles.
- **Header:** a sync status indicator with a "Sync from ClickUp" button.

**Tests**
- **pytest:**
  - ClickUp client, using respx to cover pagination, 429 backoff, header throttling and incremental skips.
  - OAuth `state` validation.
  - Chunk ID stability.
  - Ontology merge rules: locked fields survive, new items become proposals.
  - Cycle rejection and YAML validation errors.
  - Progress merge migration and hidden-on-delete.
  - The 409 on a stale hash.
  - Frontier computation.
  - The pipeline run against a fake `LLMBackend`.
- **Playwright** (webServer boots the backend against a fixture `data/` dir with no ClickUp or LLM):
  - Creating a project.
  - Clicking a DAG node to open its page.
  - Checking items, navigating away and back, and confirming they persist.
  - Marking a node complete and seeing the frontier advance.
  - Opening a citation and seeing the side panel with the external link.
  - Edit mode: rename, add an edge, reject a cyclic edge, merge nodes and check that progress migrates, accept a proposal.
  - The login page renders its setup and login states.

**Graphify**
- `graphify.sh` runs graphify over `backend/src`, `frontend/src` and the scripts only, never `data/`. Its output goes to gitignored `graphify-out/`.

## 4. Risks and notes

- **Model memory:** Qwen3-30B-A3B Q4_K_M is about 18.6 GB. With 16 GB VRAM it needs `--n-cpu-moe` offload, and the system was already using about 20 of its 30 GB RAM. Close other workloads before building. Swapping the model is a one-line pin change to Qwen3-14B if it OOMs.
- **Vulkan on RDNA4:** the support is new. If the pinned Vulkan release misbehaves, the fallback is a ROCm build (ROCm is present).
- **ClickUp API details not verified without access:**
  - whether the v3 docs `parent` filter catches docs nested in folders and lists, which is why the scraper enumerates every level;
  - whether ClickUp accepts an `http://127.0.0.1` OAuth redirect;
  - whether the workspace's plan raises the rate limit.

  Check all three on the first run.
- **Docs-only scope:** if most of the CoE material actually lives in tasks or attachments, the graph will be thin. You'd need to widen the scrape.
- **Two stores:** ontology YAML and progress SQLite can drift if something edits the YAML outside the app. Node IDs are the contract, and orphaned progress is harmless because it's hidden.
- **Summaries aren't editable** and refresh on every build, so a summary can contradict a node's curated title or checklist until you rebuild.
- **Gitignored curation:** your editor work exists only on this machine, apart from the local backups. Back up `data/` if it matters.
- **No app auth:** anything that can reach `127.0.0.1:<port>` can edit the ontology. That's acceptable for a local single-user setup.
- **Playwright pin:** 1.60.0 is pinned exactly as requested, although 1.63 is current. The `@playwright/mcp` version is pinned separately.
- **Revoke the old token:** revoke the old personal ClickUp token after OAuth works. Its value is in `.env`, which hasn't been read.
