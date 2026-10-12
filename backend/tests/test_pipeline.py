import json
from pathlib import Path
from typing import Any

from coe_wizard.chunks import Chunk
from coe_wizard.ontology.model import Ontology
from coe_wizard.ontology.pipeline import (
    MAP_SCHEMA,
    MERGE_SCHEMA,
    SUMMARY_SCHEMA,
    build_ontology,
    render_citations,
)

from .conftest import make_onto


class FakeLLM:
    """Scripted stand-in for llama-server, keyed by which schema is requested.

    `place`, `duplicate` and `reuse` map a substring of the prompt to the answer to give.
    """

    def __init__(
        self,
        place: dict[str, str] | None = None,
        duplicate: dict[str, str] | None = None,
        reuse: dict[str, str] | None = None,
    ) -> None:
        self.prompts: list[str] = []
        self.place = place or {}
        self.duplicate = duplicate or {}
        self.reuse = reuse or {}

    @staticmethod
    def _pick(table: dict[str, str], prompt: str, default: str) -> str:
        return next((v for k, v in table.items() if k in prompt), default)

    async def complete(self, prompt: str, json_schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        props = json_schema["properties"]
        if "facets" in props:  # map step with facet tagging
            result = await self.complete(prompt, MAP_SCHEMA)
            if "security review process" in prompt:
                result["facets"] = {"domain": ["oncology", "not-a-value"]}
                result["new_values"] = [{"facet": "domain", "value": "Dermatology"}]
            else:
                result["facets"] = {"domain": []}
                result["new_values"] = []
            return result
        if json_schema is MAP_SCHEMA:
            if "security review process" in prompt:
                return {
                    "steps": [
                        {
                            "title": "Security review",
                            "description": "Threat model",
                            "checklist": ["Run threat model", "Run threat model "],
                        }
                    ]
                }
            if "audit trail" in prompt:
                return {
                    "steps": [
                        {"title": "Audit", "description": "Audit it", "checklist": ["Log access"]}
                    ]
                }
            return {
                "steps": [
                    {
                        "title": "Procure data",
                        "description": "Get data",
                        "checklist": ["Find sources"],
                    }
                ]
            }
        candidate = prompt.rsplit("\n\n", 1)[-1]
        if "match" in props:
            out = {"match": self._pick(self.place, candidate, "new")}
            assert out["match"] in props["match"]["enum"]
            if "reuse_id" in props:
                out["reuse_id"] = self._pick(self.reuse, candidate, "none")
            return out
        if "duplicate_of" in props:
            choice = self._pick(self.duplicate, candidate, "none")
            assert choice in props["duplicate_of"]["enum"]
            return {"duplicate_of": choice}
        if json_schema is MERGE_SCHEMA:
            return {"title": "Merged", "description": "m", "checklist": ["Merged item"]}
        if "prerequisites" in props:
            allowed = props["prerequisites"]["items"]["properties"]["id"]["enum"]
            if '"procure-data" (' in prompt:
                prereqs = [{"id": "security-review", "confidence": 0.2}]
            elif '"security-review" (' in prompt:
                prereqs = [{"id": "procure-data", "confidence": 0.9}]
            else:
                prereqs = []
            return {"prerequisites": [p for p in prereqs if p["id"] in allowed]}
        if json_schema is SUMMARY_SCHEMA:
            return {"summary": "Do it [S1]. Again [S1][S9]."}
        raise AssertionError("unexpected schema")


CHUNKS = {
    "c1": Chunk("c1", "d", "p1", "Doc", "Data", "https://u/1", "procure data from sources"),
    "c2": Chunk("c2", "d", "p2", "Doc", "Sec", "https://u/2", "security review process"),
}
AUDIT = Chunk("c3", "d", "p3", "Doc", "Audit", "https://u/3", "audit trail")


async def test_pipeline_builds_valid_dag_with_citations() -> None:
    llm = FakeLLM()
    onto = (await build_ontology(llm, CHUNKS, Ontology())).ontology
    assert [n.id for n in onto.nodes] == ["procure-data", "security-review"]
    assert [e.key for e in onto.edges] == [("procure-data", "security-review")]  # cycle dropped
    sec = onto.nodes[1]
    assert [i.text for i in sec.checklist] == ["Run threat model"]
    assert sec.summary == "Do it [1]. Again [1]."
    assert [(c.n, c.chunk_id, c.url) for c in sec.citations] == [(1, "c2", "https://u/2")]
    assert all(n.facets == {} for n in onto.nodes)  # no facets configured: untagged


async def test_map_step_tags_chunks_and_nodes_with_facets() -> None:
    llm = FakeLLM()
    facets = {"domain": ["oncology", "cardiology"]}
    built = await build_ontology(llm, CHUNKS, Ontology(), facets=facets)
    map_prompt = next(p for p in llm.prompts if "security review process" in p)
    assert "- domain: oncology, cardiology" in map_prompt
    # Unknown values are dropped; untagged chunks get no entry for the facet.
    assert built.chunk_facets == {"c1": {}, "c2": {"domain": ["oncology"]}}
    assert built.new_values == {"domain": ["Dermatology"]}
    tags = {n.id: n.facets for n in built.ontology.nodes}
    assert tags == {"procure-data": {}, "security-review": {"domain": ["oncology"]}}


async def test_existing_ids_are_offered_for_reuse() -> None:
    llm = FakeLLM(reuse={"Security review": "sec"})
    onto = (await build_ontology(llm, CHUNKS, make_onto(["procure-data", "sec"], []))).ontology
    place_prompt = next(p for p in llm.prompts if "Decide where the candidate" in p)
    assert "- procure-data: Procure-Data" in place_prompt
    assert [n.id for n in onto.nodes] == ["procure-data", "sec"]


async def test_matching_candidate_is_merged_into_step(tmp_path: Path) -> None:
    doc_path = tmp_path / "steps.jsonl"
    llm = FakeLLM(place={"Audit": "security-review"})
    built = await build_ontology(llm, {**CHUNKS, "c3": AUDIT}, Ontology(), doc_path=doc_path)
    onto = built.ontology
    assert [n.id for n in onto.nodes] == ["procure-data", "security-review"]
    assert onto.nodes[1].title == "Merged"
    steps = [json.loads(line) for line in doc_path.read_text().splitlines()]
    assert [s["id"] for s in steps] == ["procure-data", "security-review"]
    assert steps[1]["chunk_ids"] == ["c2", "c3"]


async def test_consolidation_merges_duplicates() -> None:
    llm = FakeLLM(duplicate={'"id": "audit"': "security-review"})
    onto = (await build_ontology(llm, {**CHUNKS, "c3": AUDIT}, Ontology())).ontology
    assert [n.id for n in onto.nodes] == ["procure-data", "security-review"]
    assert [c.chunk_id for c in onto.nodes[1].citations] == ["c2"]
    assert onto.nodes[1].title == "Merged"


def test_render_citations_numbers_by_first_use() -> None:
    sources = [CHUNKS["c1"], CHUNKS["c2"]]
    text, cites = render_citations("b [S2] a [S1] b again [S2]", sources)
    assert text == "b [1] a [2] b again [1]"
    assert [c.chunk_id for c in cites] == ["c2", "c1"]
