from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coe_wizard.app import create_app
from coe_wizard.auth import MemoryCredentialStore
from coe_wizard.config import Settings
from coe_wizard.ontology.model import Ontology
from coe_wizard.ontology.store import EMPTY_HASH

from .conftest import item, make_onto


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(Settings(data_dir=tmp_path), MemoryCredentialStore())
    svc = app.state.services

    def seed(_o: Ontology) -> Ontology:
        onto = make_onto(["a", "b"], [("a", "b")])
        onto.nodes[0].checklist = [item("x", "ix")]
        return onto

    svc.store.update(EMPTY_HASH, seed)
    with TestClient(app) as c:
        yield c


def test_progress_round_trip_and_frontier(client: TestClient) -> None:
    pid = client.post("/api/projects", json={"name": "App"}).json()["id"]
    graph = client.get(f"/api/projects/{pid}/graph").json()
    assert {n["id"]: n["state"] for n in graph["nodes"]} == {"a": "frontier", "b": "locked"}

    client.put(f"/api/projects/{pid}/nodes/a/checklist/ix", json={"checked": True})
    client.put(f"/api/projects/{pid}/nodes/a/complete", json={"completed": True})
    graph = client.get(f"/api/projects/{pid}/graph").json()
    a = next(n for n in graph["nodes"] if n["id"] == "a")
    assert a["checklist"][0]["checked"] and a["state"] == "complete"
    assert next(n for n in graph["nodes"] if n["id"] == "b")["state"] == "frontier"


def test_projects_are_isolated(client: TestClient) -> None:
    p1 = client.post("/api/projects", json={"name": "One"}).json()["id"]
    p2 = client.post("/api/projects", json={"name": "Two"}).json()["id"]
    client.put(f"/api/projects/{p1}/nodes/a/complete", json={"completed": True})
    states = {n["id"]: n["state"] for n in client.get(f"/api/projects/{p2}/graph").json()["nodes"]}
    assert states["a"] == "frontier"


def test_edit_requires_current_hash(client: TestClient) -> None:
    pid = client.post("/api/projects", json={"name": "App"}).json()["id"]
    h = client.get(f"/api/projects/{pid}/graph?edit=true").json()["hash"]
    r = client.patch("/api/ontology/nodes/a", json={"title": "A2"}, headers={"If-Match": h})
    assert r.status_code == 200
    r = client.patch("/api/ontology/nodes/a", json={"title": "A3"}, headers={"If-Match": h})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale"
    r = client.post(
        "/api/ontology/edges",
        json={"source": "b", "target": "a"},
        headers={"If-Match": r.json()["detail"]["hash"]},
    )
    assert r.status_code == 409 and "cycle" in r.json()["detail"]["message"]


def test_invalid_ontology_is_reported(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "ontology.yaml").write_text("nodes:\n  - {title: no id}\n")
    pid = client.post("/api/projects", json={"name": "App"}).json()["id"]
    r = client.get(f"/api/projects/{pid}/graph")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "invalid_ontology"
    assert client.get("/api/ontology/status").json()["valid"] is False


def test_sync_requires_login(client: TestClient) -> None:
    assert client.post("/api/sync").status_code == 401


def test_auth_setup_and_login_redirect(client: TestClient) -> None:
    assert client.get("/api/auth/status").json()["configured"] is False
    client.post("/api/auth/setup", json={"client_id": "cid", "client_secret": "s"})
    r = client.get("/api/auth/login", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("https://app.clickup.com/api")
    r = client.get("/api/auth/callback?code=x&state=forged", follow_redirects=False)
    assert "error=" in r.headers["location"]
