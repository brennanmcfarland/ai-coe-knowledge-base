"""Command-line entry points used by the repo's shell scripts."""

import argparse
import asyncio
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
    from .chunks import chunk_corpus, read_chunks, write_chunks
    from .llm import LlamaServerBackend
    from .ontology.merge import merge_ontology
    from .ontology.pipeline import build_ontology as run_pipeline
    from .ontology.store import OntologyStore

    settings = _settings(args)
    if not settings.manifest_path.exists():
        sys.exit("No scraped corpus found; run ./scrape.sh first")
    count = write_chunks(settings.chunks_path, chunk_corpus(settings.corpus_dir))
    print(f"{count} chunks written to {settings.chunks_path}", flush=True)
    chunks = read_chunks(settings.chunks_path)
    if args.limit_chunks:
        chunks = dict(list(chunks.items())[: args.limit_chunks])

    store = OntologyStore(settings.ontology_path, settings.backups_dir)
    existing, _ = store.load()

    async def run() -> None:
        llm = LlamaServerBackend(args.llama_url)
        try:
            built = await run_pipeline(llm, chunks, existing, lambda m: print(m, flush=True))
        finally:
            await llm.aclose()
        # Merge against the file as it is *now*, so edits made during the build survive.
        merged, _ = store.update(None, lambda cur: merge_ontology(cur, built))
        proposed = sum(n.status == "proposed" for n in merged.nodes)
        print(f"Ontology written: {len(merged.nodes)} nodes ({proposed} proposed)", flush=True)

    asyncio.run(run())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="coe-wizard")
    parser.add_argument("--data-dir", default=str(REPO_ROOT / "data"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--rpm", type=int, default=60, help="ClickUp requests per minute")
    parser.add_argument("--public-url", default=None, help="Browser-facing origin (dev: Vite)")
    parser.add_argument("--static-dir", default=None, help="Built frontend to serve")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="Run the web app")
    p_serve.add_argument(
        "--memory-credentials",
        action="store_true",
        help="Keep OAuth client credentials in memory instead of the keyring (tests)",
    )
    p_serve.set_defaults(func=serve)

    p_build = sub.add_parser("build-ontology", help="Chunk the corpus and (re)build the DAG")
    p_build.add_argument("--llama-url", required=True)
    p_build.add_argument("--limit-chunks", type=int, default=0, help="Debug: only use N chunks")
    p_build.set_defaults(func=build_ontology)

    args = parser.parse_args(argv)
    args.func(args)
