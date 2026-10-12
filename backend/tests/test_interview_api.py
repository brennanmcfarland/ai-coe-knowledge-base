import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from coe_wizard.app import create_app
from coe_wizard.auth import MemoryCredentialStore
from coe_wizard.config import Settings
from coe_wizard.ontology.model import Edge, Ontology
from coe_wizard.ontology.store import EMPTY_HASH

from .conftest import make_node

ANSWERS: dict[str, Any] = {
    "app_type": "research-tool",
    "domain": ["oncology"],
    "data_types": ["genomic"],
    "data_access": "public",
    "sensitivity": "no-patient-data",
    "deployment": "sandbox",
    "users": ["researchers"],
}


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    chunk = {
        "id": "onc1",
        "doc_id": "d",
        "page_id": "p",
        "doc_title": "Standards",
        "page_title": "Terminology",
        "url": "https://u/onc1",
        "text": "ICD-O-3 codes oncology topography and morphology.",
        "facets": {"domain": ["oncology"]},
    }
    (tmp_path / "chunks.jsonl").write_text(json.dumps(chunk) + "\n")
    app = create_app(
        Settings(data_dir=tmp_path, cache_dir=tmp_path / ".cache", fake_llm=True),
        MemoryCredentialStore(),
    )
    onto = Ontology(
        nodes=[
            make_node("goals"),
            make_node("cardio-data", facets={"domain": ["cardiology"]}),
            make_node("onco-data", facets={"domain": ["oncology"]}),
        ],
        edges=[Edge.of("goals", "onco-data")],
    )
    app.state.services.store.update(EMPTY_HASH, lambda _o: onto)
    with TestClient(app) as c:
        yield c


def wait(client: TestClient, draft_id: str) -> dict[str, Any]:
    for _ in range(200):
        job = client.get(f"/api/interviews/{draft_id}/job").json()
        if job["state"] != "running":
            return job
        time.sleep(0.01)
    raise AssertionError("job never finished")


def run_interview(
    client: TestClient, draft: dict[str, Any], answers: dict[str, Any]
) -> dict[str, Any]:
    """Answer every curated question, skip follow-ups, and return the final job."""
    did = draft["id"]
    for qid, value in answers.items():
        r = client.put(f"/api/interviews/{did}/answers/{qid}", json={"value": value})
        assert r.status_code == 200, r.text
    while True:
        r = client.post(f"/api/interviews/{did}/advance")
        assert r.status_code == 200, r.text
        if r.json()["job_started"]:
            job = wait(client, did)
            if job["state"] != "succeeded" or job["result"].get("project_id"):
                return job


def test_interview_creates_a_tailored_project(client: TestClient) -> None:
    draft = client.post("/api/interviews", json={"name": "Onco app"}).json()
    assert client.get("/api/interviews").json()[0]["id"] == draft["id"]
    job = run_interview(client, draft, ANSWERS)
    assert job["state"] == "succeeded"
    pid = job["result"]["project_id"]
    assert client.get("/api/interviews").json() == []  # the draft is gone

    project = client.get(f"/api/projects/{pid}").json()
    assert project["name"] == "Onco app" and project["tailored"]
    graph = client.get(f"/api/projects/{pid}/graph").json()
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert "cardio-data" not in nodes and {"goals", "onco-data"} <= set(nodes)
    terms = nodes["gen.acquire-oncology-terminologies"]
    assert terms["scope"] == "project" and terms["grounded"]
    assert [c["chunk_id"] for c in terms["citations"]] == ["onc1"]
    licenses = nodes["gen.obtain-terminology-licenses"]
    assert not licenses["grounded"] and licenses["citations"] == []
    assert graph["tailored"] and graph["overlay_hash"]


def test_follow_ups_are_asked_and_saved(client: TestClient) -> None:
    draft = client.post("/api/interviews", json={"name": "F"}).json()
    did = draft["id"]
    client.put(f"/api/interviews/{did}/answers/app_type", json={"value": "ml-model"})
    client.post(f"/api/interviews/{did}/advance")
    wait(client, did)  # no follow-ups for this section in the fake: straight to the next
    d = client.get(f"/api/interviews/{did}").json()
    assert (d["section"], d["stage"]) == (1, "questions")
    r = client.post(f"/api/interviews/{did}/advance")
    assert r.status_code == 422 and "Which clinical" in r.json()["detail"]
    client.put(f"/api/interviews/{did}/answers/domain", json={"value": ["oncology"]})
    client.post(f"/api/interviews/{did}/advance")
    wait(client, did)
    d = client.get(f"/api/interviews/{did}").json()
    assert d["stage"] == "followups"
    fu = d["followups"][0]
    assert fu["kind"] == "single" and "ICD-O-3" in fu["options"]
    bad = client.put(f"/api/interviews/{did}/answers/{fu['id']}", json={"value": "nope"})
    assert bad.status_code == 422
    client.put(f"/api/interviews/{did}/answers/{fu['id']}", json={"value": "ICD-O-3"})
    assert client.get(f"/api/interviews/{did}").json()["followups"][0]["answer"] == "ICD-O-3"


def test_llm_failure_keeps_the_draft_and_retry_works(client: TestClient) -> None:
    draft = client.post("/api/interviews", json={"name": "Retry"}).json()
    answers = {**ANSWERS, "app_description": "simulate-llm-failure please"}
    job = run_interview(client, draft, answers)
    assert job["state"] == "failed" and "Simulated" in job["error"]
    assert client.get(f"/api/interviews/{draft['id']}").json()["answers"]["app_type"]
    job = run_interview(client, draft, {})
    assert job["state"] == "succeeded"
    assert client.get("/api/projects").json()[0]["name"] == "Retry"


def test_retailor_writes_proposals(client: TestClient) -> None:
    svc = client.app.state.services  # type: ignore[attr-defined]
    pid = svc.db.create_project("Old").id  # untailored: sees every global step
    before = {n["id"] for n in client.get(f"/api/projects/{pid}/graph").json()["nodes"]}
    assert before == {"goals", "cardio-data", "onco-data"}

    draft = client.post(f"/api/projects/{pid}/retailor").json()
    assert draft["project_id"] == pid and draft["name"] == "Old"
    assert client.post(f"/api/projects/{pid}/retailor").json()["id"] == draft["id"]
    job = run_interview(client, draft, ANSWERS)
    assert job["result"] == {"project_id": pid}

    graph = client.get(f"/api/projects/{pid}/graph").json()
    assert {n["id"] for n in graph["nodes"]} == before  # nothing changes until accepted
    keys = {p["key"] for p in graph["project_proposals"]}
    assert "exclude:cardio-data" in keys
    assert "add_node:gen.acquire-oncology-terminologies" in keys

    h = graph["overlay_hash"]
    r = client.post(
        f"/api/projects/{pid}/proposals/exclude:cardio-data/resolve",
        json={"accept": True},
        headers={"If-Match": h},
    )
    h = r.json()["hash"]
    r = client.post(
        f"/api/projects/{pid}/proposals/add_node:gen.acquire-oncology-terminologies/resolve",
        json={"accept": True},
        headers={"If-Match": h},
    )
    assert r.status_code == 200
    graph = client.get(f"/api/projects/{pid}/graph").json()
    ids = {n["id"] for n in graph["nodes"]}
    assert "cardio-data" not in ids and "gen.acquire-oncology-terminologies" in ids


def test_new_matching_global_steps_are_flagged(client: TestClient) -> None:
    job = run_interview(client, client.post("/api/interviews", json={"name": "N"}).json(), ANSWERS)
    pid = job["result"]["project_id"]
    svc = client.app.state.services  # type: ignore[attr-defined]
    _, h = svc.store.load()
    svc.store.update(
        h,
        lambda o: (
            o.nodes.extend(
                [
                    make_node("onco-imaging", facets={"domain": ["oncology"]}),
                    make_node("x-cardio", facets={"domain": ["cardiology"]}),
                ]
            )
            or o
        ),
    )
    graph = client.get(f"/api/projects/{pid}/graph").json()
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert nodes["onco-imaging"]["is_new"] and "x-cardio" not in nodes
    assert [p["key"] for p in graph["project_proposals"]] == ["new:onco-imaging"]


def test_promote_through_the_api(client: TestClient) -> None:
    job = run_interview(client, client.post("/api/interviews", json={"name": "P"}).json(), ANSWERS)
    pid = job["result"]["project_id"]
    graph = client.get(f"/api/projects/{pid}/graph").json()
    r = client.post(
        f"/api/projects/{pid}/nodes/gen.obtain-terminology-licenses/promote",
        headers={"If-Match": graph["overlay_hash"]},
    )
    assert r.status_code == 200, r.text
    gid = r.json()["id"]
    edit_graph = client.get(f"/api/projects/{pid}/graph?edit=true").json()
    node = next(n for n in edit_graph["nodes"] if n["id"] == "gen.obtain-terminology-licenses")
    assert node["promoted_to"] == gid
    assert gid not in {n["id"] for n in edit_graph["nodes"]}  # hidden in its own project
    onto, _ = client.app.state.services.store.load()  # type: ignore[attr-defined]
    promoted = onto.node(gid)
    assert promoted is not None and promoted.status == "proposed" and not promoted.grounded


def test_deleting_a_project_removes_its_overlay(client: TestClient, tmp_path: Path) -> None:
    job = run_interview(client, client.post("/api/interviews", json={"name": "D"}).json(), ANSWERS)
    pid = job["result"]["project_id"]
    assert (tmp_path / "projects" / f"{pid}.yaml").exists()
    client.delete(f"/api/projects/{pid}")
    assert not (tmp_path / "projects" / f"{pid}.yaml").exists()


def test_interview_definition_curation(client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "interview.yaml"
    path.write_text(
        path.read_text().replace(
            "      - {value: general, label: General or multi-domain}",
            "      - {value: general, label: General or multi-domain}\n"
            "      - {value: dermatology, label: Dermatology, status: proposed}",
        )
    )
    public = client.get("/api/interview-def").json()
    domain = next(q for q in public["questions"] if q["id"] == "domain")
    assert "dermatology" not in [o["value"] for o in domain["options"]]
    curated = client.get("/api/interview-def?edit=true").json()
    assert curated["proposals"] == 1
    r = client.post(
        "/api/interview-def/questions/domain/options/dermatology/resolve",
        json={"accept": False},
        headers={"If-Match": curated["hash"]},
    )
    assert r.status_code == 200
    assert client.get("/api/interview-def?edit=true").json()["proposals"] == 0
    assert "dermatology" in path.read_text()  # remembered as rejected


def test_llm_status_with_fake(client: TestClient) -> None:
    assert client.get("/api/llm/status").json()["state"] == "ready"
