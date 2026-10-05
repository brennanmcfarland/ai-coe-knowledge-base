from typing import Any

from coe_wizard.chunks import Chunk
from coe_wizard.ontology.model import Ontology
from coe_wizard.ontology.pipeline import (
    EDGE_SCHEMA,
    MAP_SCHEMA,
    REDUCE_SCHEMA,
    SUMMARY_SCHEMA,
    build_ontology,
    render_citations,
)

from .conftest import make_onto


class FakeLLM:
    """Scripted stand-in for llama-server, keyed by which schema is requested."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, prompt: str, json_schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        if json_schema is MAP_SCHEMA:
            if "security" in prompt:
                return {
                    "steps": [
                        {
                            "title": "Security review",
                            "description": "Threat model",
                            "checklist": ["Run threat model", "Run threat model "],
                        }
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
        if json_schema is REDUCE_SCHEMA:
            return {
                "nodes": [
                    {
                        "id": "Procure Data",
                        "title": "Procure data",
                        "description": "d",
                        "checklist": ["Find sources"],
                        "members": [0],
                    },
                    {
                        "id": "security-review",
                        "title": "Security review",
                        "description": "d",
                        "checklist": ["Run threat model"],
                        "members": [1, 99],
                    },
                ]
            }
        if json_schema is EDGE_SCHEMA:
            return {
                "edges": [
                    {"from": "procure-data", "to": "security-review", "confidence": 0.9},
                    {"from": "security-review", "to": "procure-data", "confidence": 0.2},
                    {"from": "ghost", "to": "procure-data", "confidence": 1},
                ]
            }
        if json_schema is SUMMARY_SCHEMA:
            return {"summary": "Do it [S1]. Again [S1][S9]."}
        raise AssertionError("unexpected schema")


CHUNKS = {
    "c1": Chunk("c1", "d", "p1", "Doc", "Data", "https://u/1", "procure data from sources"),
    "c2": Chunk("c2", "d", "p2", "Doc", "Sec", "https://u/2", "security review process"),
}


async def test_pipeline_builds_valid_dag_with_citations() -> None:
    llm = FakeLLM()
    onto = await build_ontology(llm, CHUNKS, Ontology())
    assert [n.id for n in onto.nodes] == ["procure-data", "security-review"]
    assert [e.key for e in onto.edges] == [("procure-data", "security-review")]  # cycle dropped
    sec = onto.nodes[1]
    assert [i.text for i in sec.checklist] == ["Run threat model"]
    assert sec.summary == "Do it [1]. Again [1]."
    assert [(c.n, c.chunk_id, c.url) for c in sec.citations] == [(1, "c2", "https://u/2")]


async def test_existing_ids_are_offered_to_reducer() -> None:
    llm = FakeLLM()
    await build_ontology(llm, CHUNKS, make_onto(["procure-data"], []))
    reduce_prompt = next(p for p in llm.prompts if "Merge these candidate" in p)
    assert "- procure-data: Procure-Data" in reduce_prompt


def test_render_citations_numbers_by_first_use() -> None:
    sources = [CHUNKS["c1"], CHUNKS["c2"]]
    text, cites = render_citations("b [S2] a [S1] b again [S2]", sources)
    assert text == "b [1] a [2] b again [1]"
    assert [c.chunk_id for c in cites] == ["c2", "c1"]
