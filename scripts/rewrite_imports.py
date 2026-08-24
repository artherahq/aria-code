import os
import re

top_level_modules = {
    "aliyun_data_client", "aria_cli", "aria_daemon", "aria_feishu_bot", "aria_mcp_server",
    "aria_relay_client", "aria_relay_server", "aria_telegram_bot", "ariarc", "artifacts",
    "backtest_engine", "backtest_report", "canva_client", "change_store", "command_safety",
    "computer_use_tools", "dashboard_generator", "data_analysis_tools", "data_cleaner", "data_service",
    "demo_player", "doctor", "external_agent_runner", "figma_client", "file_analysis_tools",
    "finance_formulas", "football_data_client", "image_gen_tools", "intent_classifier", "kling_video_client",
    "licensing", "local_finance_tools", "local_image_provider", "local_llm_provider", "macro_tools",
    "markdown_pdf", "market_data_client", "mcp_client", "memory_manager", "model_capability",
    "notification_tools", "openai_image_client", "plan_utils", "plugin_loader", "portfolio_ledger",
    "preview_server", "project_tools", "realty_data_tools", "report_exporters", "report_generator",
    "runway_video_client", "setup_wizard", "spreadsheet_tools", "strategy_vault", "video_analysis",
    "video_editor", "workspace_context",
    "adk_apps", "agents", "apps", "brokers", "clients", "datasources", "domain",
    "packages", "privacy", "providers", "runtime", "safety", "tools", "ui", "workspace"
}

def rewrite_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # match "from <top_level>..." -> "from aria_code.<top_level>..."
    for mod in top_level_modules:
        content = re.sub(rf'^from {mod}(\s|\.)', rf'from aria_code.{mod}\1', content, flags=re.MULTILINE)
        content = re.sub(rf'^import {mod}(\s|\n)', rf'import aria_code.{mod}\1', content, flags=re.MULTILINE)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

for root, _, files in os.walk("src/aria_code"):
    for file in files:
        if file.endswith(".py"):
            rewrite_file(os.path.join(root, file))

for root, _, files in os.walk("tests"):
    for file in files:
        if file.endswith(".py"):
            rewrite_file(os.path.join(root, file))
            
# Also fix pyproject.toml
with open("pyproject.toml", "r", encoding="utf-8") as f:
    pyproject = f.read()
    
# Remove py-modules and set packages = ["aria_code"]
import re
pyproject = re.sub(r'py-modules = \[.*?\]\n', '', pyproject, flags=re.DOTALL)
pyproject = re.sub(r'include = \[.*?\]\n', 'include = ["aria_code*"]\n', pyproject, flags=re.DOTALL)
pyproject = re.sub(r'where = "."\n', 'where = "src"\n', pyproject)

with open("pyproject.toml", "w", encoding="utf-8") as f:
    f.write(pyproject)
