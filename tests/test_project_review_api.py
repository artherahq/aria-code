"""HTTP-level coverage for the internal project-review/workspace service.

Requires the optional "service" extra (fastapi, uvicorn, python-multipart);
skips cleanly when it is not installed, matching how the local CLI keeps
this dependency optional.
"""

from __future__ import annotations

import io
import os
import tempfile
import zipfile

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

TOKEN = "test-token-for-project-review-api"


@pytest.fixture()
def client():
    os.environ["ARIA_CODE_REVIEW_TOKEN"] = TOKEN
    os.environ["ARIA_CODE_WORKSPACE_ROOT"] = tempfile.mkdtemp()
    # project_review_server imports project_review_api at module load time,
    # both of which read these env vars lazily (per-call), so a fresh import
    # is not required — but do it anyway in case an earlier test imported
    # the module before the env vars above were set.
    import importlib

    import aria_code.project_review_server as server_module

    importlib.reload(server_module)
    return fastapi_testclient.TestClient(server_module.app)


def _zip(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for path, content in entries.items():
            archive.writestr(path, content)
    return buf.getvalue()


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_health_needs_no_auth(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_review_endpoint_rejects_missing_and_wrong_token(client):
    payload = _zip({"app.py": "print('ok')"})
    response = client.post("/v1/project-reviews", files={"project": ("p.zip", payload)})
    assert response.status_code == 401

    response = client.post(
        "/v1/project-reviews",
        files={"project": ("p.zip", payload)},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401


def test_review_endpoint_returns_a_report(client):
    payload = _zip({"app.py": "API_KEY = 'super-secret-value'\n"})
    response = client.post(
        "/v1/project-reviews", files={"project": ("p.zip", payload)}, headers=_auth()
    )
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "aria.project-review.v1"
    assert body["summary"]["reviewed_files"] == 1


def test_review_endpoint_rejects_bad_archive(client):
    response = client.post(
        "/v1/project-reviews",
        files={"project": ("p.zip", b"not a zip")},
        headers=_auth(),
    )
    assert response.status_code == 400


def test_workspace_lifecycle_read_propose_apply(client):
    payload = _zip({"app.py": "value = 1\n"})
    create = client.post(
        "/v1/workspaces",
        files={"project": ("p.zip", payload)},
        data={"owner_id": "owner-a", "run_id": "run-a"},
        headers=_auth(),
    )
    assert create.status_code == 201
    workspace_id = create.json()["workspace_id"]
    assert "owner_hash" not in create.json()

    fetched = client.get(f"/v1/workspaces/{workspace_id}?owner_id=owner-a", headers=_auth())
    assert fetched.status_code == 200

    wrong_owner = client.get(f"/v1/workspaces/{workspace_id}?owner_id=owner-b", headers=_auth())
    assert wrong_owner.status_code == 404

    read = client.post(
        f"/v1/workspaces/{workspace_id}/files/read",
        json={"owner_id": "owner-a", "path": "app.py"},
        headers=_auth(),
    )
    assert read.status_code == 200
    assert "value = 1" in read.json()["content"]

    listing = client.post(
        f"/v1/workspaces/{workspace_id}/files/list",
        json={"owner_id": "owner-a", "path": "."},
        headers=_auth(),
    )
    assert listing.status_code == 200

    search = client.post(
        f"/v1/workspaces/{workspace_id}/search",
        json={"owner_id": "owner-a", "pattern": "value"},
        headers=_auth(),
    )
    assert search.status_code == 200

    propose = client.post(
        f"/v1/workspaces/{workspace_id}/changes",
        json={"owner_id": "owner-a", "path": "app.py", "content": "value = 2\n"},
        headers=_auth(),
    )
    assert propose.status_code == 201
    change_id = propose.json()["change_id"]
    assert propose.json()["status"] == "proposed"

    fetched_change = client.get(
        f"/v1/workspaces/{workspace_id}/changes/{change_id}?owner_id=owner-a",
        headers=_auth(),
    )
    assert fetched_change.status_code == 200

    apply = client.post(
        f"/v1/workspaces/{workspace_id}/changes/{change_id}/apply",
        json={"owner_id": "owner-a"},
        headers=_auth(),
    )
    assert apply.status_code == 200
    assert apply.json()["status"] == "applied"

    rollback = client.post(
        f"/v1/workspaces/{workspace_id}/changes/{change_id}/rollback",
        json={"owner_id": "owner-a"},
        headers=_auth(),
    )
    assert rollback.status_code == 200
    assert rollback.json()["status"] == "rolled_back"

    deleted = client.delete(f"/v1/workspaces/{workspace_id}?owner_id=owner-a", headers=_auth())
    assert deleted.status_code == 204

    gone = client.get(f"/v1/workspaces/{workspace_id}?owner_id=owner-a", headers=_auth())
    assert gone.status_code == 404


def test_service_refuses_to_start_without_a_configured_token(client, monkeypatch):
    monkeypatch.delenv("ARIA_CODE_REVIEW_TOKEN", raising=False)
    payload = _zip({"app.py": "print('ok')"})
    response = client.post(
        "/v1/project-reviews",
        files={"project": ("p.zip", payload)},
        headers=_auth(),
    )
    assert response.status_code == 503
