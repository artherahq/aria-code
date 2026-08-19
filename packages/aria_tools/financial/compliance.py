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

@mcp_tool(description="运行合规审计拦截网关。验证投资组合是否违反合规风控限制（如流动性、黑名单、集中度）。")
async def run_compliance_audit(strategy_name: str, assets: list[str]) -> str:
    resolved = find_skill_script("compliance-audit-trail", "governance_manifest_gate.py")
    if resolved is None:
        return missing_script_message("compliance-audit-trail", "governance_manifest_gate.py")
    script_path = str(resolved)
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
