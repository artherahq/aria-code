"""
e2e/conftest.py — 端到端测试专用配置
=====================================
默认跳过所有 e2e 测试，需 --e2e 标志才激活。
可用 --provider 指定只跑某个 provider。

用法:
    # 跑所有 e2e（需要在环境变量或 providers.json 里有 key）
    python3 -m pytest tests/e2e/ --e2e -v

    # 只跑 siliconflow
    python3 -m pytest tests/e2e/ --e2e --provider siliconflow -v
"""
from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--e2e",
        action="store_true",
        default=False,
        help="开启端到端真实 API 测试（需要有效的 API key）",
    )
    parser.addoption(
        "--provider",
        default=None,
        metavar="NAME",
        help="只测指定 provider（如 siliconflow / deepseek / moonshot）",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """若未传 --e2e，自动 skip 所有 e2e 目录下的测试。"""
    if config.getoption("--e2e"):
        return
    skip_marker = pytest.mark.skip(reason="e2e 测试需要 --e2e 标志")
    for item in items:
        if "e2e" in str(item.fspath):
            item.add_marker(skip_marker)
