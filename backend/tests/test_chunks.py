import json
from pathlib import Path

from coe_wizard.chunks import chunk_corpus, chunk_id, read_chunks, split_markdown, write_chunks


def test_chunk_id_ignores_whitespace_and_case_but_not_content() -> None:
    assert chunk_id("d", "p", "Hello   World\n") == chunk_id("d", "p", "hello world")
    assert chunk_id("d", "p", "hello world") != chunk_id("d", "p", "hello there")
    assert chunk_id("d", "p", "x") != chunk_id("d", "q", "x")


def test_split_on_headings_and_packs_paragraphs() -> None:
    md = "# A\n\npara one\n\npara two\n\n# B\n\n" + ("word " * 400)
    parts = split_markdown(md, target=200)
    assert parts[0] == "# A\n\npara one\n\npara two"
    assert parts[1].startswith("# B")


def test_chunks_are_stable_across_rebuilds_and_skip_deleted(tmp_path: Path) -> None:
    (tmp_path / "d1").mkdir()
    (tmp_path / "d1" / "p1.md").write_text("# Title\n\nSome text")
    (tmp_path / "d1" / "p2.md").write_text("gone")
    manifest = {
        "docs": {
            "d1": {
                "name": "Doc",
                "pages": {
                    "p1": {"name": "P1", "path": "d1/p1.md", "url": "u1"},
                    "p2": {"name": "P2", "path": "d1/p2.md", "url": "u2", "deleted": True},
                },
            }
        }
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    first = [c.id for c in chunk_corpus(tmp_path)]
    second = [c.id for c in chunk_corpus(tmp_path)]
    assert first == second and len(first) == 1

    out = tmp_path / "chunks.jsonl"
    write_chunks(out, chunk_corpus(tmp_path))
    loaded = read_chunks(out)
    assert loaded[first[0]].url == "u1"
