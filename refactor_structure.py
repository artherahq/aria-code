import os
import shutil
import re
from pathlib import Path

# 定义移动映射字典
MOVE_MAP = {
    "tools": [
        "computer_use_tools.py",
        "data_analysis_tools.py",
        "file_analysis_tools.py",
        "image_gen_tools.py",
        "local_finance_tools.py",
        "macro_tools.py",
        "notification_tools.py",
        "project_tools.py",
        "realty_data_tools.py",
        "spreadsheet_tools.py"
    ],
    "clients": [
        "aliyun_data_client.py",
        "canva_client.py",
        "figma_client.py",
        "football_data_client.py",
        "kling_video_client.py",
        "market_data_client.py",
        "openai_image_client.py",
        "runway_video_client.py",
        "aria_relay_client.py"
    ],
    "providers": [
        "local_llm_provider.py",
        "local_image_provider.py"
    ],
    "domain": [
        "backtest_engine.py",
        "backtest_report.py",
        "portfolio_ledger.py",
        "finance_formulas.py",
        "strategy_vault.py"
    ]
}

def update_imports(file_path: Path, module_mapping: dict):
    """
    使用安全的正则替换文件内的 import 语句
    """
    if not file_path.exists() or file_path.suffix != '.py':
        return

    try:
        content = file_path.read_text(encoding="utf-8")
        original_content = content

        for module_name, new_prefix in module_mapping.items():
            mod_no_ext = module_name.replace('.py', '')

            # 替换 from module import ... -> from prefix.module import ...
            pattern_from = rf"^(from\s+){mod_no_ext}(\s+import)"
            content = re.sub(pattern_from, rf"\1{new_prefix}.{mod_no_ext}\2", content, flags=re.MULTILINE)

            # 替换 import module -> from prefix import module
            pattern_import = rf"^(import\s+){mod_no_ext}\b"
            content = re.sub(pattern_import, rf"from {new_prefix} import {mod_no_ext}", content, flags=re.MULTILINE)

        if content != original_content:
            file_path.write_text(content, encoding="utf-8")
            print(f"  [Updated Imports] {file_path.name}")
    except Exception as e:
        print(f"  [Error] updating {file_path.name}: {e}")

def main():
    # 相对本脚本定位仓库根目录；写死开发者本机路径会让别人 clone 后直接
    # 在错误的目录上执行搬移操作。允许用 ARIA_CODE_PATH 覆盖。
    base_dir = Path(os.getenv("ARIA_CODE_PATH", "")).expanduser() if os.getenv("ARIA_CODE_PATH") \
        else Path(__file__).resolve().parent

    # 1. 创建目标文件夹
    for folder in MOVE_MAP.keys():
        (base_dir / folder).mkdir(exist_ok=True)
        # 确保新文件夹是合法的 Python 包
        init_file = base_dir / folder / "__init__.py"
        if not init_file.exists():
            init_file.write_text("")

    # 2. 构建 import 映射表 (e.g. {"macro_tools.py": "tools"})
    module_mapping = {}
    for folder, files in MOVE_MAP.items():
        for file in files:
            module_mapping[file] = folder

    # 3. 物理移动文件
    print("🚀 开始移动文件并整理架构...")
    moved_count = 0
    for folder, files in MOVE_MAP.items():
        for file_name in files:
            src = base_dir / file_name
            dst = base_dir / folder / file_name
            if src.exists():
                shutil.move(str(src), str(dst))
                print(f"  [Moved] {file_name} -> {folder}/")
                moved_count += 1

    if moved_count == 0:
        print("✅ 文件之前已经移动过了，直接进行 import 刷新。")

    # 4. 遍历所有 Python 文件并更新 imports
    print("\n🚀 开始更新全局 Import 引用路径...")
    for py_file in base_dir.rglob("*.py"):
        # 排除外部依赖或者不需要修改的目录
        if ".venv" in py_file.parts or "node_modules" in py_file.parts or "build" in py_file.parts:
            continue
        update_imports(py_file, module_mapping)

    print("\n🎉 重构完成！根目录文件减少，模块已被归档至相应的层级。")

if __name__ == "__main__":
    main()
