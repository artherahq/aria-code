"""User-visible task graph previews for complex work."""

from __future__ import annotations


import json
import asyncio
import datetime
import time
import shlex
from typing import Dict, Any, Optional


import json
import asyncio
import datetime
import time
import shlex
import sys
import os
from typing import Dict, Any, Optional


import json
import asyncio
import datetime
import time
import shlex
import sys
import os
from typing import Dict, Any, Optional


class OrchestratorCommandsMixin:
    def cmd_orchestrate(self, args: str):
        """Preview the verifiable Agent task graph for a request."""
        from runtime.task_orchestrator import build_task_graph

        request = args.strip()
        if not request:
            message = "Usage: /orchestrate <任务描述>"
            self.context.console.print(f"[yellow]{message}[/yellow]") if self.context.has_rich else print(message)
            return
        graph = build_task_graph(request)
        if self.context.has_rich:
            from rich.table import Table

            table = Table(title=f"任务编排 · {graph.kind}", header_style="bold cyan")
            table.add_column("步骤", no_wrap=True)
            table.add_column("目标")
            table.add_column("依赖")
            table.add_column("验证门禁")
            for stage in graph.stages:
                table.add_row(
                    stage.title, stage.objective, "、".join(stage.depends_on) or "—",
                    stage.verification or "—",
                )
            self.context.console.print(table)
            ready = "、".join(stage.title for stage in graph.ready(()))
            self.context.console.print(f"[dim]可立即并行：{ready}。写入操作仍受权限与 worktree 隔离约束。[/dim]")
        else:
            for stage in graph.stages:
                deps = ", ".join(stage.depends_on) or "none"
                print(f"- {stage.title} [{deps}]: {stage.objective}")

    async def cmd_route(self, args: str):
        """Dynamic Agent Routing (Orchestrator) using LLM."""
        from runtime.orchestrator import dynamic_agent_orchestration
        from apps.cli.providers.base import ConfiguredProvider
        
        request = args.strip()
        if not request:
            message = "Usage: /route <任务描述>"
            self.context.console.print(f"[yellow]{message}[/yellow]") if self.context.has_rich else print(message)
            return
            
        if self.context.has_rich:
            self.context.console.print("[dim]Orchestrator 正在思考最佳智能体组合...[/dim]")
            
        provider = ConfiguredProvider(self.context.config.get("local_provider", "vertexai"))
        
        result = await dynamic_agent_orchestration(request, provider)
        
        if self.context.has_rich:
            from rich.panel import Panel
            
            plan = result.get("plan", "No plan provided.")
            agents = result.get("agents", [])
            
            content = f"[bold cyan]路由计划:[/bold cyan]\n{plan}\n\n[bold green]调度的智能体:[/bold green]\n"
            for a in agents:
                content += f" - {a}\n"
                
            self.context.console.print(Panel(content, title="🚀 动态路由编排结果 (Orchestrator)", border_style="blue"))
        else:
            print("动态路由编排结果:")
            print(f"计划: {result.get('plan')}")
            print(f"智能体: {result.get('agents')}")

