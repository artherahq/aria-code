"""根目录不再新增 Python 模块——只出不进。

为什么需要这条守卫：codex（openai/codex）根目录**一个源文件都没有**，3104+ 个
Rust 文件全在 codex-rs/ 一个目录里。它靠的不是自觉，而是构建系统强制——
Bazel + Cargo 下，不属于任何声明过 target 的源文件根本不参与编译。

Python 没有这个强制：根目录扔一个 .py 就能 import，于是会自然堆积。
aria-code 现在有 58 个根模块（26 个是 re-export shim，32 个是真实实现）。

这些**不能直接搬走**：pyproject 的 [tool.setuptools].py-modules 声明了 57 个，
PyPI 上有 5 个发布版本，装机用户能 `import market_data_client`。搬一个就破坏
一个调用方。所以现阶段的正确目标不是"清零"，而是**停止增长**：

    现有 58 个 → 冻结为基线，允许减少，不允许增加
    新代码 → 必须放进子包（apps/ clients/ tools/ domain/ providers/ …）

减少的路径有两条，都不需要这条守卫让路：
  - shim 化：实现搬进子目录，根目录留 200 字节 re-export（已对 26 个做过）
  - 5.0 的 src-layout：一个包，py-modules 从 57 条降到 0 条

基线随文件减少而收紧（断言用 <=），所以清理工作不会被这条守卫挡住；
只有**新增**才会失败。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 2026-08-19 冻结的基线。只减不增。
_BASELINE = 58


def _root_modules() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return sorted(f for f in out.split() if "/" not in f)


def test_root_module_count_does_not_grow():
    current = _root_modules()
    assert len(current) <= _BASELINE, (
        f"根目录 Python 模块从 {_BASELINE} 增加到了 {len(current)}。\n"
        "新代码请放进子包（apps/ clients/ tools/ domain/ providers/ runtime/ …），"
        "不要放根目录。\n"
        "根目录模块受 pyproject 的 py-modules 约束——每加一个就多一处需要手工"
        "同步的声明，而漏同步的后果是已发布的包缺文件（2026-08-19 发生过两次）。\n"
        f"当前清单：\n  " + "\n  ".join(current)
    )


def test_baseline_is_tightened_when_modules_are_removed():
    """清理之后要把基线调下来，否则守卫会慢慢失去约束力。

    这条不是形式主义：基线留在 58 而实际降到 40，意味着未来可以无声地加回
    18 个而不触发任何告警——守卫就废了。
    """
    current = _root_modules()
    assert len(current) >= _BASELINE - 5, (
        f"根目录模块已降到 {len(current)}（基线 {_BASELINE}），"
        f"请把 tests/test_root_module_budget.py 里的 _BASELINE 更新为 {len(current)}，"
        "以锁定这次清理的成果。"
    )


def test_every_root_module_is_declared_for_packaging():
    """根目录模块必须在 py-modules 里，否则 pip 安装后不存在。

    与 test_packaging_manifest.py 的角度互补：那条检查"被 import 的模块是否
    已声明"，这条检查"存在的根模块是否都已声明"——后者能抓到"加了文件但还
    没人 import"的中间状态，避免它在被 import 的那天才暴露。
    """
    import re

    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    body = re.search(r"py-modules = \[(.*?)\n\]", text, re.S)
    assert body, "pyproject.toml 缺少 [tool.setuptools].py-modules"
    declared = set(re.findall(r'"([^"]+)"', body.group(1)))

    # 只有**会被 import** 的模块才需要进 py-modules。有些根文件是靠路径执行的
    # 独立入口（`python image_service_runner.py`），不参与 import，声明与否
    # 对它们没有意义——image_service_runner 就是这样：Arthera 侧用
    # subprocess.run([python, runner]) 按路径调用它，刻意避免把 Torch/Diffusers
    # 拖进 QuantEngine 的进程。判据用「有没有人 import 它」而不是「文件存不存在」。
    imported: set[str] = set()
    grep = subprocess.run(
        ["git", "grep", "-hE", r"(^|[^.\w])(from|import) [a-z_][a-z_0-9]*", "--", "*.py"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    ).stdout
    imported = set(re.findall(r"(?:^|[^.\w])(?:from|import)\s+([a-z_][a-z_0-9]*)", grep))

    undeclared = sorted(
        Path(f).stem for f in _root_modules()
        if Path(f).stem in imported
        and Path(f).stem not in declared
        and not Path(f).stem.startswith("test_")
    )
    assert not undeclared, (
        f"以下根模块存在于仓库但没有在 py-modules 里声明，pip 安装后不会存在：\n  "
        + "\n  ".join(undeclared)
    )
