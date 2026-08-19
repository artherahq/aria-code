import os
import sys
import asyncio

from ._paths import find_skill_script, missing_script_message

try:
    from arthera_sdk.mcp import mcp_tool
except ImportError:
    def mcp_tool(*args, **kwargs):
        def wrapper(f): return f
        return wrapper

@mcp_tool(description="自动校验量化策略代码的逻辑规范与极端边界条件，生成策略鲁棒性诊断报告。")
async def validate_strategy_spec(strategy_name: str) -> str:
    resolved = find_skill_script("strategy-generation", "validate_strategy_spec.py")
    if resolved is None:
        return missing_script_message("strategy-generation", "validate_strategy_spec.py")
    script_path = str(resolved)
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", script_path, "--demo",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            return stdout.decode()
        return f"策略校验失败:\n{stderr.decode()}"
    except Exception as e:
        return f"执行出错: {e}"
