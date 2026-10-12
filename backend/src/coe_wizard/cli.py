"""Command-line entry points used by the repo's shell scripts."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from .config import REPO_ROOT, Settings


def _settings(args: argparse.Namespace) -> Settings:
    return Settings(
        data_dir=Path(args.data_dir).resolve(),
        host="127.0.0.1",
        port=args.port,
        rpm=args.rpm,
        static_dir=Path(args.static_dir).resolve() if args.static_dir else None,
        public_url=args.public_url,
        cache_dir=Path(args.cache_dir).resolve(),
        llama_url=args.llama_url or os.environ.get("LLAMA_URL") or None,
        llm_idle_timeout=args.llm_idle_timeout,
        fake_llm=getattr(args, "fake_llm", False),
    )


def serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .app import create_app
    from .auth import MemoryCredentialStore

    creds = MemoryCredentialStore() if args.memory_credentials else None
    app = create_app(_settings(args), creds)
    # Bind to loopback only: nothing here should be reachable from the network.
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


def build_ontology(args: argparse.Namespace) -> None:
    from .chunks import chunk_corpus, read_chunks, with_facets, write_chunks
    from .interview.definition import (
        InterviewDef,
        ProposedQuestion,
        ensure_seeded,
        merge_proposals,
    )
    from .llm_server import ExternalProvider, ManagedProvider, ServerConfig, provision
    from .ontology.merge import merge_ontology
    from .ontology.pipeline import build_ontology as run_pipeline
    from .ontology.pipeline import propose_questions
    from .ontology.store import OntologyStore, YamlStore

    settings = _settings(args)
    if not settings.manifest_path.exists():
        sys.exit("No scraped corpus found; run ./scrape.sh first")
    # Re-chunking keeps the facet tags of unchanged chunks (ids are content hashes).
    previous = {cid: c.facets for cid, c in read_chunks(settings.chunks_path).items() if c.facets}
    count = write_chunks(
        settings.chunks_path, with_facets(chunk_corpus(settings.corpus_dir), previous)
    )
    print(f"{count} chunks written to {settings.chunks_path}", flush=True)
    all_chunks = read_chunks(settings.chunks_path)
    chunks = all_chunks
    if args.limit_chunks:
        chunks = dict(list(chunks.items())[: args.limit_chunks])

    store = OntologyStore(settings.ontology_path, settings.backups_dir)
    existing, _ = store.load()
    ensure_seeded(settings.interview_path)
    interview = YamlStore(settings.interview_path, settings.backups_dir, InterviewDef, "interview")
    defn, _ = interview.load()

    if settings.llama_url:
        provider = ExternalProvider(settings.llama_url)
        cfg = None
    else:
        # The CLI stops the server as soon as it's done, unless the app is still using it.
        cfg = ServerConfig.from_env(settings.cache_dir, idle_timeout=0)
        provider = ManagedProvider(cfg)

    async def run() -> None:
        if cfg is not None and not cfg.files_ready():
            last = [""]

            def progress(name: str, done: int, total: int) -> None:
                pct = f"{done * 100 // total}%" if total else f"{done // 1_000_000} MB"
                if pct != last[0]:
                    last[0] = pct
                    print(f"\rDownloading {name}: {pct}   ", end="", flush=True)

            await provision(cfg, progress)
            print(flush=True)
        print("Starting the model (shared with the app if it's running)…", flush=True)
        try:
            async with provider.lease() as llm:
                print(f"Running step doc: {settings.steps_path}", flush=True)
                built = await run_pipeline(
                    llm,
                    chunks,
                    existing,
                    lambda m: print(m, flush=True),
                    settings.steps_path,
                    defn.facets(),
                )
                print("Proposing interview questions", flush=True)
                questions = await propose_questions(
                    llm,
                    [q.text for q in defn.questions],
                    [s.id for s in defn.sections],
                    sorted({c.doc_title for c in all_chunks.values() if c.doc_title}),
                )
        finally:
            await provider.aclose()

        write_chunks(settings.chunks_path, with_facets(all_chunks.values(), built.chunk_facets))
        # Merge against the file as it is *now*, so edits made during the build survive.
        merged, _ = store.update(None, lambda cur: merge_ontology(cur, built.ontology))
        proposed = sum(n.status == "proposed" for n in merged.nodes)
        print(f"Ontology written: {len(merged.nodes)} nodes ({proposed} proposed)", flush=True)
        updated, _ = interview.update(
            None,
            lambda cur: merge_proposals(
                cur,
                built.new_values,
                [ProposedQuestion.model_validate(q) for q in questions],
            ),
        )
        print(f"Interview questions: {updated.proposals()} proposals to review", flush=True)

    asyncio.run(run())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="coe-wizard")
    parser.add_argument("--data-dir", default=str(REPO_ROOT / "data"))
    parser.add_argument("--cache-dir", default=str(REPO_ROOT / ".cache"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--rpm", type=int, default=60, help="ClickUp requests per minute")
    parser.add_argument("--public-url", default=None, help="Browser-facing origin (dev: Vite)")
    parser.add_argument("--static-dir", default=None, help="Built frontend to serve")
    parser.add_argument(
        "--llama-url",
        default=None,
        help="Use this running OpenAI-compatible server (default: $LLAMA_URL, else managed)",
    )
    parser.add_argument(
        "--llm-idle-timeout",
        type=float,
        default=600,
        help="Seconds the managed llama-server stays loaded after the last interview step",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="Run the web app")
    p_serve.add_argument(
        "--memory-credentials",
        action="store_true",
        help="Keep OAuth client credentials in memory instead of the keyring (tests)",
    )
    p_serve.add_argument(
        "--fake-llm", action="store_true", help="Use a scripted model instead of llama.cpp (tests)"
    )
    p_serve.set_defaults(func=serve)

    p_build = sub.add_parser("build-ontology", help="Chunk the corpus and (re)build the DAG")
    p_build.add_argument("--limit-chunks", type=int, default=0, help="Debug: only use N chunks")
    p_build.set_defaults(func=build_ontology)

    args = parser.parse_args(argv)
    args.func(args)
