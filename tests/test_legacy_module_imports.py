"""Regression coverage for the gradual module-layout migration."""

import importlib


def test_legacy_module_paths_resolve_to_the_new_packages():
    mappings = {
        "computer_use_tools": "tools.computer_use_tools",
        "file_analysis_tools": "tools.file_analysis_tools",
        "market_data_client": "clients.market_data_client",
        "local_llm_provider": "providers.local_llm_provider",
        "backtest_engine": "domain.backtest_engine",
        "finance_formulas": "domain.finance_formulas",
    }

    for legacy_name, canonical_name in mappings.items():
        assert importlib.import_module(legacy_name) is importlib.import_module(canonical_name)
