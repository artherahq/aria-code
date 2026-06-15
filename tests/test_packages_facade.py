from pathlib import Path


def test_legacy_tool_registry_builds_manifests():
    from packages.aria_core import PermissionLevel, ServiceKind
    from packages.aria_tools import build_registry_from_legacy

    def handler(_params):
        return {"success": True}

    registry = build_registry_from_legacy(
        {"read_file": (handler, "Read a file"), "broker_order": (handler, "Propose order")},
        [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "parameters": {"type": "object", "required": ["path"]},
                },
            }
        ],
    )

    read_file = registry.get("read_file")
    broker_order = registry.get("broker_order")

    assert read_file is not None
    assert read_file.schema["required"] == ["path"]
    assert read_file.manifest().kind == ServiceKind.TOOL
    assert PermissionLevel.READ_ONLY in read_file.permissions
    assert broker_order is not None
    assert PermissionLevel.BROKER_TRADE in broker_order.permissions


def test_agent_manifests_are_generated_from_existing_registry():
    from packages.aria_agents import list_agent_manifests

    manifests = list_agent_manifests()
    names = {item.name for item in manifests}

    assert "technical" in names
    technical = next(item for item in manifests if item.name == "technical")
    assert "market.technical" in technical.capabilities
    assert technical.manifest().to_dict()["kind"] == "agent"


def test_builtin_skills_connect_tools_and_agents():
    from packages.aria_skills import builtin_skill_specs

    skills = {skill.name: skill for skill in builtin_skill_specs()}

    assert "financial-research" in skills
    assert "get_market_data" in skills["financial-research"].tools
    assert "technical" in skills["financial-research"].agents
    assert "workspace-coding" in skills


def test_service_boundaries_are_registered():
    from packages.aria_core import PermissionLevel
    from packages.aria_services import list_service_specs, required_service_names, service_map

    services = service_map()

    assert {"gateway", "runtime", "data", "reports", "brokers", "safety"}.issubset(services)
    assert "channels" in services
    assert "data.quality" in services["data"].capabilities
    assert PermissionLevel.NETWORK in services["data"].permissions
    assert PermissionLevel.BROKER_READ in services["brokers"].permissions
    assert "channels" not in required_service_names()
    assert len(list_service_specs()) >= 8


def test_default_mcp_exposures_are_stable():
    from packages.aria_mcp import default_exposures

    names = {item.name for item in default_exposures()}

    assert "aria.market.quote" in names
    assert "aria.agent.team" in names
    assert "aria.backtest.run" in names


def test_mcp_tools_convert_to_aria_tool_specs():
    from packages.aria_core import PermissionLevel
    from packages.aria_mcp import mcp_tools_to_specs

    specs = mcp_tools_to_specs(
        [
            {
                "name": "run_backtest",
                "qualified_name": "arthera_quant_engine/run_backtest",
                "description": "Run strategy backtest simulation",
                "inputSchema": {"type": "object", "required": ["symbol"]},
            },
            {
                "name": "place_order",
                "description": "Trading execution order tool",
                "inputSchema": {"type": "object"},
            },
            {
                "name": "calculate_factors",
                "description": "Calculate alpha factor features",
            },
        ],
        "arthera_quant_engine",
    )

    by_name = {spec.name: spec for spec in specs}

    assert by_name["arthera_quant_engine/run_backtest"].schema["required"] == ["symbol"]
    assert "strategy.backtest" in by_name["arthera_quant_engine/run_backtest"].capabilities
    assert PermissionLevel.WORKSPACE_WRITE in by_name["arthera_quant_engine/run_backtest"].permissions
    assert PermissionLevel.BROKER_TRADE in by_name["arthera_quant_engine/place_order"].permissions
    assert "factors" in by_name["arthera_quant_engine/calculate_factors"].capabilities


def test_package_manifest_export_shape(tmp_path):
    import json

    from packages.aria_agents import list_agent_manifests
    from packages.aria_core import build_package_manifest, write_package_manifest
    from packages.aria_infra import aria_code_identity, discover_arthera_packages
    from packages.aria_mcp import default_exposures, mcp_tools_to_specs
    from packages.aria_services import list_service_specs
    from packages.aria_skills import builtin_skill_specs
    from packages.aria_tools import build_registry_from_legacy

    def handler(_params):
        return {"success": True}

    tool_registry = build_registry_from_legacy({"read_file": (handler, "Read a file")}, [])
    arthera_tools = mcp_tools_to_specs(
        [{"name": "run_backtest", "description": "Run strategy backtest"}],
        "arthera_quant_engine",
    )
    manifest = build_package_manifest(
        identity=aria_code_identity("1.2.3"),
        services=list_service_specs(),
        tools=tool_registry.list(),
        agents=list_agent_manifests(),
        skills=builtin_skill_specs(),
        mcp_exposures=default_exposures(),
        arthera_packages=discover_arthera_packages(Path("/missing")),
        arthera_mcp_tools=arthera_tools,
    )

    assert manifest["schema_version"] == "aria.package-manifest.v1"
    assert manifest["product"]["company"] == "Arthera"
    assert manifest["product"]["product"] == "Aria Code"
    assert any(service["name"] == "gateway" for service in manifest["capabilities"]["services"])
    assert manifest["capabilities"]["tools"][0]["name"] == "read_file"
    assert any(agent["name"] == "technical" for agent in manifest["capabilities"]["agents"])
    assert manifest["capabilities"]["arthera_mcp_tools"][0]["name"] == "arthera_quant_engine/run_backtest"

    out = tmp_path / "manifest.json"
    write_package_manifest(out, manifest)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["product"]["version"] == "1.2.3"


def test_package_doctor_report_statuses(tmp_path):
    from packages.aria_infra import discover_arthera_packages, build_package_doctor_report
    from packages.aria_services import list_service_specs, required_service_names

    root = tmp_path / "packages"
    (root / "contracts").mkdir(parents=True)
    (root / "quant_engine" / "mcp_server.py").parent.mkdir(parents=True)
    (root / "quant_engine" / "mcp_server.py").write_text("# server", encoding="utf-8")
    arthera = discover_arthera_packages(root)

    ok_report = build_package_doctor_report(
        arthera=arthera,
        mcp_status={
            "configured": True,
            "config_path": str(tmp_path / "mcp.json"),
            "server_path": str(root / "quant_engine" / "mcp_server.py"),
            "server_file_exists": True,
            "running": True,
            "tool_count": 2,
        },
        tool_count=2,
        manifest_can_export=True,
        manifest_path=tmp_path / "manifest.json",
        services=list_service_specs(),
        required_services=required_service_names(),
        provider_health=[{"provider": "yfinance", "status": "ok"}],
    )
    assert ok_report.status == "ok"
    assert any(check.name == "service_boundaries" and check.status == "ok" for check in ok_report.checks)
    assert any(check.name == "data_provider_health" and check.status == "ok" for check in ok_report.checks)

    warn_report = build_package_doctor_report(
        arthera=discover_arthera_packages(Path("/missing")),
        mcp_status={
            "configured": False,
            "config_path": str(tmp_path / "mcp.json"),
            "server_path": "",
            "server_file_exists": False,
            "running": False,
            "tool_count": 0,
        },
        tool_count=0,
        manifest_can_export=True,
        manifest_path=tmp_path / "manifest.json",
        services=[],
        required_services=required_service_names(),
        provider_health=[{"provider": "yfinance", "status": "rate_limited"}],
    )
    assert warn_report.status == "fail"
    assert any(check.name == "mcp_config" and check.status == "warn" for check in warn_report.checks)
    assert any(check.name == "service_boundaries" and check.status == "fail" for check in warn_report.checks)
    assert any(check.name == "data_provider_health" and check.status == "warn" for check in warn_report.checks)


def test_arthera_package_discovery_finds_quant_engine(tmp_path):
    from packages.aria_infra import discover_arthera_packages

    root = tmp_path / "packages"
    (root / "contracts").mkdir(parents=True)
    (root / "quant_engine" / "tools").mkdir(parents=True)
    (root / "quant_engine" / "mcp_server.py").write_text("# test", encoding="utf-8")

    found = discover_arthera_packages(root)

    assert found.available is True
    assert found.packages["contracts"] == root / "contracts"
    assert root / "quant_engine" / "mcp_server.py" in found.mcp_servers
    assert root / "quant_engine" / "tools" in found.tool_dirs


def test_real_arthera_packages_path_is_optional():
    from packages.aria_infra import discover_arthera_packages

    found = discover_arthera_packages(Path("/path/that/does/not/exist"))

    assert found.available is False
    assert found.packages == {}


def test_product_identity_marks_aria_code_as_arthera_product():
    from packages.aria_infra import aria_code_identity

    identity = aria_code_identity("9.9.9")

    assert identity.company == "Arthera"
    assert identity.product == "Aria Code"
    assert identity.package_name == "aria-code"
    assert "Arthera Quant Engine" in identity.product_family
    assert identity.to_dict()["version"] == "9.9.9"


def test_arthera_mcp_server_config_and_merge(tmp_path):
    from packages.aria_mcp import (
        arthera_quant_engine_server_config,
        load_mcp_config,
        merge_server_config,
        mcp_server_status,
        write_mcp_config,
    )

    root = tmp_path / "Arthera"
    config = arthera_quant_engine_server_config(root)

    assert config["name"] == "arthera_quant_engine"
    assert config["args"] == [str(root / "packages" / "quant_engine" / "mcp_server.py")]
    assert config["env"]["PYTHONPATH"] == str(root)

    merged = merge_server_config({"servers": [{"name": "other"}]}, config)
    assert [server["name"] for server in merged["servers"]] == ["other", "arthera_quant_engine"]

    replaced = merge_server_config(merged, {**config, "description": "updated"})
    matches = [server for server in replaced["servers"] if server["name"] == "arthera_quant_engine"]
    assert len(matches) == 1
    assert matches[0]["description"] == "updated"

    config_path = tmp_path / ".arthera" / "mcp_servers.json"
    write_mcp_config(config_path, replaced)
    loaded = load_mcp_config(config_path)
    assert loaded["servers"][1]["name"] == "arthera_quant_engine"

    missing = mcp_server_status(config_path, "missing_server")
    assert missing["configured"] is False
    assert missing["running"] is False

    status = mcp_server_status(
        config_path,
        "arthera_quant_engine",
        runtime_status=[
            {
                "name": "arthera_quant_engine",
                "running": True,
                "tool_count": 3,
                "tools": ["calculate_factors", "run_backtest", "detect_regime"],
            }
        ],
    )
    assert status["configured"] is True
    assert status["running"] is True
    assert status["tool_count"] == 3
    assert status["tools"][0] == "calculate_factors"
