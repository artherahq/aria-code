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
        """Dynamic Agent Routing (Orchestrator) using LLM and interactive streaming."""
        from aria_code.runtime.orchestrator import dynamic_agent_orchestration
        from aria_code.apps.cli.providers.base import ConfiguredProvider
        from aria_code.agents.registry import get_registry
        
        request = args.strip()
        if not request:
            message = "Usage: /route <任务描述>"
            self.context.console.print(f"[yellow]{message}[/yellow]") if self.context.has_rich else print(message)
            return
            
        if self.context.has_rich:
            self.context.console.print("[dim]Orchestrator 正在思考最佳智能体组合...[/dim]")
            
        provider = ConfiguredProvider(self.context.config, "gemini-2.5-flash")
        
        result = await dynamic_agent_orchestration(request, provider)
        agents_to_run = result.get("agents", [])
        
        if self.context.has_rich:
            from rich.panel import Panel
            from rich.live import Live
            from rich.console import Group
            from rich.text import Text
            
            plan = result.get("plan", "No plan provided.")
            content = f"""[bold cyan]路由计划:[/bold cyan]
{plan}

[bold green]调度的智能体:[/bold green]
"""
            for a in agents_to_run:
                content += f" - {a}\n"
                
            self.context.console.print(Panel(content, title="🚀 动态路由编排结果 (Orchestrator)", border_style="blue"))
            
            if not agents_to_run:
                self.context.console.print("[yellow]没有可用的智能体处理此请求。[/yellow]")
                return

            registry = get_registry()
            import re as _re
            tickers = _re.findall(r'[A-Z]{2,5}', request)
            symbol = tickers[0] if tickers else ""
            
            for agent_name in agents_to_run:
                agent_cls = registry.get(agent_name)
                if not agent_cls:
                    self.context.console.print(f"[red]找不到智能体: {agent_name}[/red]")
                    continue
                
                self.context.console.print(f"\n[bold magenta]▶ 正在执行智能体: {agent_name}[/bold magenta]")
                
                lines = []
                def update_display(live_context, new_line=None):
                    if new_line:
                        lines.append(new_line)
                    display_lines = lines[-15:]
                    group = Group(*[Text.from_markup(l) for l in display_lines])
                    live_context.update(group)
                
                with Live(auto_refresh=True, console=self.context.console) as live:
                    def on_thought(text):
                        update_display(live, f"[dim cyan]🤔 思考中:[/dim cyan] {text.strip()}")
                        
                    def on_tool_start(name, params):
                        update_display(live, f"[bold yellow]🛠️ 调用工具:[/bold yellow] {name} {params}")
                        
                    def on_tool_end(name, result):
                        res_str = str(result)[:60] + "..." if len(str(result)) > 60 else str(result)
                        update_display(live, f"[green]✓ 工具返回:[/green] {name} -> {res_str}")
                        
                    def on_token(token):
                        pass
                        
                    agent = agent_cls(
                        llm_provider=provider,
                        on_thought=on_thought,
                        on_tool_start=on_tool_start,
                        on_tool_end=on_tool_end,
                        on_token=on_token
                    )
                    
                    try:
                        data = {"company_name": symbol, "request": request}
                        agent_result = await agent.analyze(symbol, data)
                        update_display(live, f"[bold green]✨ 执行完成![/bold green]")
                    except Exception as e:
                        update_display(live, f"[bold red]❌ 执行失败: {e}[/bold red]")
                        continue
                        
                self.context.console.print(Panel(
                    agent_result.analysis, 
                    title=f"📊 {agent_name} 分析报告", 
                    border_style="green"
                ))

        else:
            print("动态路由编排结果:")
            print(f"计划: {result.get('plan')}")
            print(f"智能体: {result.get('agents')}")
