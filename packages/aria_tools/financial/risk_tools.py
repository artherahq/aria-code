import os
import sys
import asyncio

SDK_PATH = "/Users/mac/Desktop/Arthera/sdks/python"
if SDK_PATH not in sys.path:
    sys.path.append(SDK_PATH)

try:
    from arthera_sdk.mcp import mcp_tool
except ImportError:
    def mcp_tool(*args, **kwargs):
        def wrapper(f): return f
        return wrapper

@mcp_tool(description="运行黑天鹅风险、相关性与下行压力测试。调用 risk_profile.py 进行验证。")
async def run_risk_profile(symbol: str) -> str:
    script_path = "/Users/mac/Desktop/aria-skills/skills/risk-assessment/scripts/risk_profile.py"
    if not os.path.exists(script_path):
        return f"Error: 风险压力测试脚本不存在 ({script_path})"
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
