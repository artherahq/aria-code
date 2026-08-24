import json
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aria_code.workspace_context import (
    context_file_path,
    load_workspace_context,
    normalize_workspace_context,
    record_workspace_artifact,
    save_workspace_context,
    set_workspace_activity,
)


def test_normalize_context_drops_sensitive_and_unknown_fields(tmp_path):
    context = normalize_workspace_context(
        {
            "provider": "private-provider",
            "token": "secret-token",
            "project": {"name": " Quant Lab ", "symbol": " AAPL ", "apiKey": "sk-secret"},
            "activity": {
                "state": "running",
                "title": " Build an earnings brief ",
                "progress": 160,
                "requiresApproval": True,
                "endpoint": "https://internal.example",
            },
            "artifacts": [
                {"id": "brief-1", "title": " Earnings brief ", "url": "https://private.example"},
                {"id": "ignored", "title": "   "},
            ],
        },
        tmp_path,
    )

    assert context["schemaVersion"] == 1
    assert context["project"] == {"name": "Quant Lab", "symbol": "AAPL"}
    assert context["activity"] == {
        "state": "running",
        "title": "Build an earnings brief",
        "progress": 100,
        "requiresApproval": True,
    }
    assert context["artifacts"] == [{"id": "brief-1", "title": "Earnings brief", "status": "ready"}]
    serialized = json.dumps(context)
    assert "secret" not in serialized
    assert "provider" not in serialized
    assert "endpoint" not in serialized
    assert "url" not in serialized


def test_save_and_load_context_is_private_and_atomic(tmp_path):
    target = save_workspace_context(
        {"project": {"name": "ARIA"}, "activity": {"state": "queued"}},
        tmp_path,
    )

    assert target == context_file_path(tmp_path)
    assert target.exists()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert load_workspace_context(tmp_path)["activity"]["state"] == "queued"


def test_activity_and_artifacts_are_capped(tmp_path):
    set_workspace_activity(
        state="needs_approval",
        title="Confirm execution",
        requires_approval=True,
        workspace_root=tmp_path,
    )
    for index in range(8):
        record_workspace_artifact(
            artifact_id=f"artifact-{index}",
            title=f"Artifact {index}",
            kind="report",
            workspace_root=tmp_path,
        )

    context = load_workspace_context(tmp_path)
    assert context["activity"]["state"] == "needs_approval"
    assert context["activity"]["requiresApproval"] is True
    assert [item["id"] for item in context["artifacts"]] == [
        "artifact-2",
        "artifact-3",
        "artifact-4",
        "artifact-5",
        "artifact-6",
        "artifact-7",
    ]
