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

@mcp_tool(description="运行黑天鹅风险、相关性与下行压力测试。调用 risk_profile.py 进行验证。")
async def run_risk_profile(symbol: str) -> str:
    resolved = find_skill_script("risk-assessment", "risk_profile.py")
    if resolved is None:
        return missing_script_message("risk-assessment", "risk_profile.py")
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
        return f"脚本执行失败:\n{stderr.decode()}"
    except Exception as e:
        return f"执行出错: {e}"
