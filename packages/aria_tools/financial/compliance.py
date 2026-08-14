import os
import sys
import asyncio

try:
    from arthera_sdk.mcp import mcp_tool
except ImportError:
    def mcp_tool(*args, **kwargs):
        def wrapper(f): return f
        return wrapper

@mcp_tool(description="运行合规审计拦截网关。验证投资组合是否违反合规风控限制（如流动性、黑名单、集中度）。")
async def run_compliance_audit(strategy_name: str, assets: list[str]) -> str:
    script_path = "/Users/mac/Desktop/aria-skills/skills/compliance-audit-trail/scripts/governance_manifest_gate.py"
    if not os.path.exists(script_path):
        return f"Error: 合规网关脚本不存在 ({script_path})"
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", script_path, "--demo", "--strategy", strategy_name, "--assets", ",".join(assets),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            return stdout.decode()
        return f"合规验证拦截:\n{stderr.decode()}"
    except Exception as e:
        return f"执行出错: {e}"
