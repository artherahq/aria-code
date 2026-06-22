from apps.cli.lifecycle_hooks import run_event_hook


def test_run_event_hook_invokes_global_and_local_scripts(tmp_path):
    config_dir = tmp_path / "config"
    cwd = tmp_path / "project"
    global_hooks = config_dir / "hooks"
    local_hooks = cwd / ".aria" / "hooks"
    output = tmp_path / "hook.log"
    global_hooks.mkdir(parents=True)
    local_hooks.mkdir(parents=True)

    global_script = global_hooks / "session_start.sh"
    local_script = local_hooks / "session_start.sh"
    global_script.write_text(f"#!/bin/sh\necho global:$ARIA_EVENT:$ARIA_SESSION >> {output}\n", encoding="utf-8")
    local_script.write_text(f"#!/bin/sh\necho local:$ARIA_EVENT:$ARIA_SESSION >> {output}\n", encoding="utf-8")
    global_script.chmod(0o755)
    local_script.chmod(0o755)

    run_event_hook(
        "session_start",
        config_dir=config_dir,
        cwd=cwd,
        env_extra={"ARIA_SESSION": "abc123"},
    )

    assert output.read_text(encoding="utf-8").splitlines() == [
        "global:session_start:abc123",
        "local:session_start:abc123",
    ]
