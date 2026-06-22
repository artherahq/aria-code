from apps.cli.config_paths import resolve_paths
from apps.cli.config_store import load_cli_config, save_cli_config


def test_load_cli_config_merges_saved_values_and_syncs(monkeypatch, tmp_path):
    paths = resolve_paths(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text(
        '{"model": "aria-opus-old", "ui_lang": "", "permission_mode": "read-only"}',
        encoding="utf-8",
    )
    monkeypatch.setattr("apps.cli.i18n.detect_system_lang", lambda: "zh")
    synced = []

    cfg = load_cli_config(
        paths,
        {"model": "qwen2.5-coder:1.5b", "ui_lang": "", "permission_mode": "workspace-write"},
        sync_policy=synced.append,
    )

    assert cfg["model"] == "qwen2.5-coder:1.5b"
    assert cfg["ui_lang"] == "zh"
    assert cfg["permission_mode"] == "read-only"
    assert synced == [cfg]


def test_save_cli_config_excludes_conversation_history(tmp_path):
    paths = resolve_paths(tmp_path)

    save_cli_config(paths, {"model": "test-model", "conversation_history": [{"role": "user"}]})

    text = paths.config_file.read_text(encoding="utf-8")
    assert "test-model" in text
    assert "conversation_history" not in text
