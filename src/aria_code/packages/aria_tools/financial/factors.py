import os
import sys
import asyncio

from ._paths import ensure_arthera_sdk, find_skill_script, missing_script_message

ensure_arthera_sdk()

try:
    from arthera_sdk.mcp import mcp_tool
except ImportError:
    def mcp_tool(*args, **kwargs):
        def wrapper(f): return f
        return wrapper

@mcp_tool(description="运行核心财务因子的量化验证和研究分析。调用 factor_evaluate.py 进行验证。")
async def run_factor_research(symbol: str, factors: list[str]) -> str:
    resolved = find_skill_script("factor-research", "factor_evaluate.py")
    if resolved is None:
        return missing_script_message("factor-research", "factor_evaluate.py")
    script_path = str(resolved)
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", script_path, "--symbol", symbol, "--factors", ",".join(factors),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            return stdout.decode()
        return f"脚本执行失败:\n{stderr.decode()}"
    except Exception as e:
        return f"执行出错: {e}"
