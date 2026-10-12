# Plan: Project interview → tailored step graph

## 1. Context

**Current state**
- `ai-coe-knowledge-base` builds a single global step DAG, `data/ontology.yaml`, from the scraped ClickUp corpus. It does this with an offline map → place → consolidate → edges → summarize pipeline (`build_ontology.sh`, Qwen3-30B on llama-server).
- Every project sees the same graph. A project is just `{id, name, created_at}` plus its progress rows in SQLite.
- The LLM exists only while `build_ontology.sh` runs. Nodes have no notion of domain or app type.
- The corpus is small (9 docs, 104 chunks). It already covers what the oncology example needs: ICD-O-3, SNOMED, OMOP and CDISC oncology TAUGs in *Healthcare Data & Interoperability Standards Reference*, the cancer genomics sources in *Public Healthcare & Genomics Data Sources*, and the cBioPortal how-to.

**What changes**
- Creating a project now requires an in-app interview: app/service type, domain, data types and sources, sensitivity and deployment. It runs on a live LLM that asks adaptive follow-ups.
- The answers produce a **per-project tailored step graph**:
  - the global steps that apply, chosen by facet tags plus LLM selection;
  - plus **generated project-specific steps** built from KB chunks found through facets. An oncology project, for example, gets "Acquire oncology terminologies (ICD-O-3, …)" with citations.
- The interview questions live in a curated `data/interview.yaml`. It starts from a committed seed, and builds propose additions.
- The build tags chunks and nodes with facet values.
- llama-server provisioning moves into shared backend code, so the app can start the model on demand, download it with the user's consent, and share one server with builds.

## 2. Decisions

| # | Decision | Choice | Consequence |
|---|---|---|---|
| 1 | Meaning of "ontology" output | Tailored per-project step graph | Reuses the DAG, node page and progress UI. Projects no longer share one identical graph, so the view has to be computed per project. |
| 2 | Interview surface | In-app, at project creation | New interview route and page. The existing Playwright flows that create projects must go through the interview. |
| 3 | LLM at interview time | Yes, live | The app needs a running llama-server while interviewing. Responses take seconds to minutes, so LLM steps run as background jobs with progress. |
| 4 | Question source | Curated `data/interview.yaml`, seeded by the LLM | Adapts as the KB grows. Proposals need curator review, using the same proposed/accepted/locked/rejected rules as the ontology. |
| 5 | Provisioning | The backend spawns llama-server on demand and stops it after an idle timeout (10 min, configurable). `LLAMA_URL` overrides. | No GPU is held while you're only browsing. The first LLM step waits about 1–2 min for the model to load. |
| 6 | Tailoring | Select + generate | The LLM chooses which global steps apply and generates project-specific steps from retrieved chunks. This needs per-project node storage, and summarization now also runs at interview time. |
| 7 | LLM's role in the conversation | Curated questions plus adaptive follow-ups. The default is up to 3 follow-ups per section, settable per section in `interview.yaml`. | The interview stays a bounded length but adapts to the answers. Follow-ups are grounded in the facet values and chunks the KB actually has. |
| 8 | Retrieval | Facet tags assigned at build time | Retrieval is a filter on chunk/node facets, and the LLM then ranks and uses the hits. Fast and inspectable, but only as good as the tags. |
| 9 | LLM failure | Block with an error | The interview cannot finish without the LLM. The user retries. A draft (#17) keeps the answers. |
| 10 | Mandatory | Required for new projects. Existing projects keep the full global graph and get an optional "Tailor this project" button. | Every new project is tailored. Existing progress needs no migration. |
| 11 | GPU sharing | One shared llama-server, managed through a lock/lease file in `.cache/` | The build and interviews can run at the same time without loading the model twice. Interleaved requests slow both down. |
| 12 | Downloads | The app can download llama.cpp and the model after a consent prompt | Download and SHA256 logic moves from `build_ontology.sh` into the backend. The interview start screen shows the ~19 GB size and a progress bar. |
| 13 | Escape hatch | Hard block: no project exists until the interview completes | Combined with #9, a project can't be created at all on a machine where the model won't run. That includes CI and e2e, which therefore use a fake LLM backend. |
| 14 | Overlay storage | `data/projects/<id>.yaml` per project | `ontology.yaml` stays global. The overlay uses the existing ruamel, hash (409), backup and atomic-write machinery. The project view is the filtered global graph combined with the generated nodes. |
| 15 | Global changes | New global nodes are auto-included when their facets match, and marked "new". A "Re-tailor" button reruns LLM selection. | Projects pick up curator fixes without unexpected LLM calls. Steps the user explicitly excluded stay excluded. |
| 16 | Facet definition | Each choice question in `interview.yaml` is a facet, and its options are the facet's values | Questions, tags and retrieval share one vocabulary. Nodes with empty facets count as **universal** (for example security review). |
| 17 | Draft storage | A server-side draft in `data/interviews/<uuid>.yaml` | Survives refreshes and restarts. The Projects page lists "Resume interview" entries. Completing a draft creates the project and its overlay atomically, then deletes the draft. |
| 18 | Re-tailoring an existing project | The result is shown as a diff of proposals that the user accepts or rejects | Steps the user is working on never vanish without warning. Progress on removed steps is hidden, not deleted, the same as global deletes today. |
| 19 | Curation of generated steps | Editable inside the overlay, and can be promoted to global | Edit-mode locks apply within the overlay. "Promote to global" adds a copy to `ontology.yaml` as a proposal carrying the project's facets. |
| 20 | Grounding | Uncited generated steps are allowed but flagged "Not from KB" | Fills gaps in the KB, at the risk of confidently wrong advice. The flag is visible on the node page and graph. |
| 21 | Seed | A committed generic seed, copied to `data/interview.yaml` if that file is missing, plus LLM proposals from builds | Works on day one and in tests. No ClickUp text goes into git. |
| 22 | Seed topics | App/service type, clinical/scientific domain, data types and sources, sensitivity and deployment | Four seed sections. Domain drives terminology resources, data types drive data-source steps, and sensitivity drives security steps. |
| 23 | Promoting uncited steps | Allowed. The "Not from KB" flag carries over until a curator attaches a citation, which clears it. | Curators can record gaps in the KB as to-dos in the global graph. |
| 24 | Facet tagging cost | Facet tags are extracted inside the existing map call (the map schema gains a `facets` field), not in a separate pass. Node facets are the union of their chunks' tags, and a curator can lock them. | Adds no LLM calls per chunk. A node can collect broad tags through merges, which is why curators can lock them. |
| 25 | Project proposals UX | Re-tailor diffs and newly auto-included nodes appear in a "Review changes" panel on the project graph, without needing global edit mode | Users manage their own project. Global edit mode stays the curator's tool. |
| 26 | LLM warm-up | Starting an interview checks whether the model is present (and offers the consent download if not), then loads the server in the background while the user answers the first section | Hides most of the model load time. |

## 3. Changes

**LLM provisioning (backend `llm_server` module)**
- Move the pinned llama.cpp and GGUF URLs and SHA256s, `fetch_verified`, free-port selection, startup, the health wait and shutdown out of `build_ontology.sh` and into Python.
- Add a `LlamaServerManager` with these responsibilities:
  - `status()` reports one of: missing, downloading, stopped, starting, ready, failed.
  - `download(progress)` fetches the files after the user consents.
  - `acquire()` returns a lease and the URL of a ready server. It starts the server if needed and otherwise reuses the one recorded in `.cache/llama.lock` (pid, port, owner, lease count).
  - `release()` ends a lease. An idle timer stops the server once there are no leases left.
- `LLAMA_URL`, `LLAMA_DEVICE`, `N_CPU_MOE` and `CTX_SIZE` behave as they do today.
- `build_ontology.sh` becomes a thin wrapper: `coe-wizard build-ontology` acquires a lease through the same manager. Running it from the CLI keeps the current behavior of downloading without a prompt.
- Add a `FakeLLMBackend` (scripted responses keyed by prompt kind) and select it with `serve --fake-llm` for Playwright.

**Interview definition (`data/interview.yaml`, backend `interview` package)**
- The seed is committed at `backend/src/coe_wizard/interview/seed.yaml` and copied into `data/` on startup if `data/interview.yaml` is missing.
- Schema: `sections[{id, title, max_followups}]`, and `questions[{id, section, text, kind: single|multi|text, facet: bool, options[{value, label, status, locked}], status, locked}]`. Choice questions with `facet: true` define the facet dimensions.
- The loader validates the file the same way `ontology.yaml` is validated, and errors show in the UI.
- During a build, the LLM proposes new option values and questions. These arrive as `proposed`, follow the existing merge rules (locked items are kept, rejected items stay gone), and appear in edit mode for accept/reject.

**Build pipeline changes**
- The map schema gains `facets: {<facet_id>: [values]}`, constrained to the accepted facet values from `interview.yaml`, plus an optional `new_values` proposal list.
- `Chunk` gains a `facets` field in `chunks.jsonl`.
- `Node` gains:
  - `facets: dict[str, list[str]]`, lockable;
  - `grounded: bool = True`, which is false only for promoted uncited steps.
- The merge rules extend to `facets`. A node with empty facets is universal.

**Overlay model (`data/projects/<id>.yaml`)**
- Holds:
  - `answers` (curated and follow-up);
  - `included` and `excluded` global node ids;
  - `generated` nodes (the `Node` schema, plus `grounded`);
  - `edges` (any combination of global and generated endpoints);
  - `proposals` for re-tailor diffs and new auto-includes;
  - `seen` global ids, used to mark auto-includes as "new";
  - `rejected`.
- Reads and writes go through an `OverlayStore` that reuses `OntologyStore`'s hash, backup and atomic-write machinery.
- The project view is the accepted global nodes, keeping those that are (explicitly included) OR (facet-match OR universal) AND NOT excluded, plus the accepted generated nodes. Edges are the accepted global edges between visible nodes plus the overlay edges.
- Cycles are checked across this combined graph on every write.
- `build_view` takes this combined graph. Frontier and gating work as before.
- Projects with no overlay (existing ones) see the full global graph.

**Interview engine (backend `interview` package, plus a job runner like `SyncJob`)**
- `InterviewSession` (the draft file) records the current section, answers and follow-ups.
- **Steps:**
  1. **Answer:** a curated section is saved immediately.
  2. **Follow-ups:** an LLM call per section, given the answers so far and the facet values and chunk titles the KB has. It returns up to `max_followups` questions (text or choice), which are saved to the draft.
  3. **Select:** pick candidate global nodes with the facet filter, then make one enum-constrained LLM call per candidate batch to include or exclude it in light of all the answers.
  4. **Retrieve:** collect the chunks whose facets match the answers.
  5. **Generate:**
     - The LLM proposes project-specific steps that the selected global steps don't cover, using a "covered_by" enum check the same way the pipeline's place step does.
     - Each step is summarized with `[S#]` citations through the existing `summarize`/`render_citations` code.
     - A step with no supporting chunks is kept with `grounded: false`, its summary written from general knowledge, and no citations.
  6. **Edges:** the existing `PREREQ_PROMPT` approach, run over the combined node set. Cycles are broken by confidence.
- Completing an interview atomically creates the project row and the overlay file and deletes the draft. If any LLM step fails, the job fails, the draft stays, and the UI offers a retry (#9, #13).
- **Re-tailor:** for a project with an overlay, rerunning the interview (with its answers prefilled and editable) produces a diff that is written as overlay `proposals`.
- **Propagation:** whenever the global ontology changes, the project view computes facet-matching global nodes that aren't in `seen` and shows them as "new" proposals. No LLM is involved.

**Edit and promote**
- In edit mode on a project, generated nodes are editable through the existing editor actions (rename, checklist, citations, delete, edges), routed to the `OverlayStore`, with locks applying inside the overlay. Global nodes still edit the global file.
- **Promote to global** copies a generated node into `ontology.yaml` as `proposed`, with the project's answer facets and `grounded` carried over. Attaching a citation to an ungrounded global node sets `grounded: true`.

**API (new routes)**
- `GET/POST /api/llm/status|download|warm`: manager status, consented download with progress, and warm-up.
- `POST /api/interviews` creates a draft. `GET /api/interviews` lists drafts. `GET/DELETE /api/interviews/{id}`.
- `PUT /api/interviews/{id}/answers/{question_id}` and `POST /api/interviews/{id}/advance` (triggers follow-ups or tailoring as a job). `GET /api/interviews/{id}/job?since=` streams progress, the same way `/api/sync` does.
- `POST /api/projects/{id}/retailor` creates a prefilled draft linked to the project. Completing it writes proposals instead of creating a project.
- `POST /api/projects/{id}/proposals/{key}/resolve` accepts or rejects a project proposal.
- `POST /api/projects/{id}/nodes/{nid}/promote` promotes a generated node.
- Overlay editor routes mirror `/api/ontology/*` under `/api/projects/{id}/overlay/*` and use the overlay hash in `If-Match`.
- `POST /api/projects` (direct create) is removed. Projects are created only by completing an interview.

**Frontend**
- **ProjectsPage:** "Start a new application" opens the interview. A "Resume interview" list shows drafts. Existing untailored projects show a "Tailor this project" button.
- **New `/interviews/:iid` page**, a section-by-section stepper:
  - an LLM readiness banner, with consent-to-download and load progress;
  - curated questions, then a "thinking…" state, then the follow-ups;
  - a final tailoring progress log;
  - on failure, an error with a Retry button.
- **Project graph:** a "Review changes" panel for re-tailor and auto-include proposals. "New" badges. Generated nodes are styled differently, and ungrounded ones carry a "Not from KB" flag.
- **Node page:** a "Not from KB" banner on ungrounded nodes, and a "Promote to global" action on generated nodes in edit mode.
- **Edit mode:** an interview-question curation view that lists proposed question and option values to accept or reject.

**Tests**
- **pytest:**
  - Seed copy and `interview.yaml` validation.
  - Facet extraction in the map step and node facet union and locking, run against the fake LLM.
  - The overlay view: facet filter, universal nodes, exclusions, cycle check across global and overlay.
  - Interview job happy path, and failure leaving the draft intact.
  - Atomic project and overlay creation.
  - Re-tailor diff becoming proposals.
  - Auto-include "new" detection.
  - Promoting carries `grounded` over, and attaching a citation clears it.
  - `LlamaServerManager` lease, reuse and idle shutdown (with a fake process), and SHA mismatch rejection.
  - Overlay 409 on a stale hash.
- **Playwright** (`--fake-llm`):
  - Complete an interview choosing oncology, then see a generated, cited "oncology terminologies" step.
  - Resume a draft after a reload.
  - Interview failure shows Retry.
  - Re-tailor shows proposals, and accepting one updates the graph.
  - Promote to global.
  - "Not from KB" flag visible.
  - Existing flows updated to create projects through the interview.

## 4. Risks/Notes

- **No project without an LLM.** Required interview + block on failure + hard block (#9, #10, #13) means machines that can't run the 30B model can't create projects. Only e2e/CI is covered, through `--fake-llm`. Revisit if non-GPU users appear.
- **Memory pressure.** A shared server avoids loading the model twice, but holding it in the app (until the idle timeout) while you also run other workloads on 30 GB RAM risks OOM. Builds and interviews running together are slow.
- **Uncited steps** (#20) can be confidently wrong. The flag is the only safeguard, so make it prominent.
- **Facet quality.** Selection and retrieval depend on build-time tags from a corpus that is currently tiny and noisy (test docs, duplicate cBioPortal copies). Expect thin results until the KB grows and the tags are curated. The interview requires a build that includes facet tagging, and the current `ontology.yaml` predates facets: until a rebuild, every node is universal.
- **Three stores now** (global YAML, overlay YAMLs, SQLite progress). Node ids remain the contract. A global merge or delete must also update overlay references (included, excluded, edges, seen) inside the same guarded write, or generated-to-global edges can dangle. The view drops dangling edges defensively.
- **Interview latency.** Follow-ups plus select, generate, summarize and edges can take several minutes per project on this hardware. It's surfaced as a progress log, not hidden.
- **App-initiated 19 GB download** (#12). It needs resumable downloads (`-C -` behavior) and a clear cancel. Disk space isn't checked today, so add a free-space check before starting.
- **Removing `POST /api/projects`** breaks any external scripts that create projects directly. There aren't any in the repo besides e2e.
