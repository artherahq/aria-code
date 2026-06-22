"""CLI consumers for runtime/model events.

The runtime core emits typed events and asks for approval decisions.  This
module keeps terminal-specific rendering and prompts at the CLI adapter layer
instead of letting them spread through the agent loop.
"""

from __future__ import annotations

import re
import sys
import time
from typing import Any, Callable

from runtime import (
    AgentEventStatus,
    AgentEventThinking,
    AgentEventToken,
    AgentEventToolCall,
    AgentEventToolResult,
    ApprovalDecision,
)


_REPETITION_MARKER = "*[model stopped — repetition detected]*"
_REPETITION_NOTICE = (
    "\n\n> 已检测到模型开始重复输出，已自动停止展开。"
    "上方结果仍然有效；如需继续，请指定要补充的部分。"
)


class TerminalRuntimeEventConsumer:
    """Consume runtime/provider events and render them to a terminal."""

    def __init__(
        self,
        *,
        terminal: Any,
        console: Any,
        has_rich: bool,
        markdown_cls: type | None,
        live_cls: type | None,
        strip_latex: Callable[[str], str],
        set_robot_state: Callable[[Any], None] | None = None,
        streaming_state: Any = None,
        print_tool_call: Callable[[str, dict], None] | None = None,
        print_tool_done: Callable[[str, int, bool], None] | None = None,
        fallback_from: str = "local",
        live_update_interval: float = 0.08,
    ) -> None:
        self.terminal = terminal
        self.console = console
        self.has_rich = has_rich
        self.markdown_cls = markdown_cls
        self.live_cls = live_cls
        self.strip_latex = strip_latex
        self.set_robot_state = set_robot_state
        self.streaming_state = streaming_state
        self.print_tool_call = print_tool_call
        self.print_tool_done = print_tool_done
        self.fallback_from = fallback_from
        self.live_update_interval = live_update_interval

        self.response_text = ""
        self.streamed_any = False
        self.token_count = 0
        self.thinking_tokens = 0
        self.thinking_shown = False
        self.thinking_start: float | None = None
        self.thinking_finished = False
        self.thinking_preview_buf: list[str] = []
        self.thinking_full_buf: list[str] = []
        self.tool_start_times: dict[str, float] = {}
        self.repetition_stopped = False
        self.repetition_notice_printed = False

        self.live_display = None
        self.spinner = None
        self.first_token_received_ref = [False]
        self.token_start_time: float | None = None
        self.last_live_update = 0.0
        self.use_plain_print_ref = [False]
        self.use_batch_render_ref = [False]
        self.latex_buf_ref = [""]
        self.in_latex_ref = [False]

    @property
    def first_token_received(self) -> bool:
        return self.first_token_received_ref[0]

    @first_token_received.setter
    def first_token_received(self, value: bool) -> None:
        self.first_token_received_ref[0] = value

    @property
    def use_plain_print(self) -> bool:
        return self.use_plain_print_ref[0]

    @use_plain_print.setter
    def use_plain_print(self, value: bool) -> None:
        self.use_plain_print_ref[0] = value

    @property
    def use_batch_render(self) -> bool:
        return self.use_batch_render_ref[0]

    @use_batch_render.setter
    def use_batch_render(self, value: bool) -> None:
        self.use_batch_render_ref[0] = value

    @property
    def latex_buf(self) -> str:
        return self.latex_buf_ref[0]

    @latex_buf.setter
    def latex_buf(self, value: str) -> None:
        self.latex_buf_ref[0] = value

    @property
    def in_latex(self) -> bool:
        return self.in_latex_ref[0]

    @in_latex.setter
    def in_latex(self, value: bool) -> None:
        self.in_latex_ref[0] = value

    def start_spinner(self) -> None:
        if self.has_rich and self.spinner is None and not self.first_token_received:
            self.spinner = self.console.status(
                "[dim]思考中… [/dim][dim italic]esc 取消[/dim italic]",
                spinner="dots",
                spinner_style="dim",
            )
            self.spinner.__enter__()

    def stop_spinner(self) -> None:
        if self.spinner is not None:
            try:
                self.spinner.__exit__(None, None, None)
            except Exception:
                pass
            self.spinner = None

    def stop_live(self, discard: bool = False) -> None:
        self.stop_spinner()
        if self.live_display:
            try:
                if discard:
                    try:
                        from rich.text import Text as _RichText

                        self.live_display.update(_RichText(""))
                        self.live_display.refresh()
                    except Exception:
                        pass
                self.live_display.stop()
            except Exception:
                pass
            self.live_display = None
        elif self.first_token_received and not discard and not self.use_batch_render:
            print(flush=True)

    def set_batch_render_mode(self, enabled: bool = True) -> None:
        self.use_plain_print = enabled
        self.use_batch_render = enabled

    def reset_stream_state(self) -> None:
        self.response_text = ""
        self.streamed_any = False
        self.token_count = 0
        self.first_token_received = False
        self.token_start_time = None
        self.latex_buf = ""
        self.in_latex = False

    def flush_latex_buf(self) -> str:
        raw = self.latex_buf
        self.latex_buf = ""
        self.in_latex = False
        return self.strip_latex(raw) if raw.strip() else raw

    def _show_repetition_notice(self) -> None:
        if self.repetition_notice_printed or self.use_batch_render:
            return
        self.repetition_notice_printed = True
        if self.live_display and self.has_rich and self.markdown_cls is not None:
            clean_text = self.response_text.split(_REPETITION_MARKER, 1)[0].rstrip()
            self.live_display.update(self.markdown_cls(self.strip_latex(clean_text + _REPETITION_NOTICE)))
            self.live_display.refresh()
            return
        print(_REPETITION_NOTICE, end="", flush=True)

    def finalize_text(self, final_text: str) -> str:
        if self.in_latex and self.latex_buf:
            leftover = self.flush_latex_buf()
            final_text = (final_text or "") + leftover
            if self.use_plain_print and not self.use_batch_render:
                print(leftover, end="", flush=True)
        return final_text

    def _finish_thinking(self) -> None:
        if not self.thinking_shown or self.thinking_finished:
            return
        self.thinking_finished = True
        self.stop_spinner()
        elapsed_t = time.time() - self.thinking_start if self.thinking_start else 0
        self.terminal._last_thinking = "".join(self.thinking_full_buf).strip()
        t_info = f"Thought for {elapsed_t:.1f}s"
        if self.thinking_tokens > 0:
            t_info += f" · {self.thinking_tokens:,} tokens"
        ctrlo = "  [dim]· Ctrl+O 展开[/dim]" if self.terminal._last_thinking else ""
        if self.has_rich:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()
            self.console.print(f"  [dim]✻[/dim] [dim]{t_info}[/dim]{ctrlo}")
            if self.terminal.config.get("thinking_preview") and self.thinking_preview_buf:
                preview_text = "".join(self.thinking_preview_buf)[:280].strip()
                if len("".join(self.thinking_preview_buf)) > 280:
                    preview_text += "…"
                self.console.print(f"  [dim italic]{preview_text}[/dim italic]")
        else:
            print(f"\r  ✻ {t_info}")

    def on_token(self, token: str) -> None:
        if not self.first_token_received:
            self.first_token_received = True
            self.token_start_time = time.time()
            if self.set_robot_state is not None:
                self.set_robot_state(self.streaming_state)
            if not self.use_batch_render:
                self.stop_spinner()

        if "<|im_start|>" in token or "<|im_end|>" in token:
            token = token.replace("<|im_start|>", "").replace("<|im_end|>", "")
            if not token.strip():
                return

        meta_artifacts = (
            "(注释：",
            "（注释：",
            "(提示：",
            "（提示：",
            "请使用实际注入的数据",
            "请使用实际数据",
            "实际注入的数据",
            "[system]",
            "[/system]",
            "[INST]",
            "[/INST]",
        )
        if any(a in token for a in meta_artifacts):
            token = re.sub(
                r"\(注[释释]：[^)）]*[)）]|（注[释释]：[^)）]*[)）]"
                r"|\(提示：[^)）]*[)）]|（提示：[^)）]*[)）]"
                r"|请使用实际(?:注入的)?数据[^。\n]*"
                r"|\[/?(?:system|INST)\]",
                "",
                token,
            )
            if not token.strip():
                return

        if _REPETITION_MARKER in token:
            if self.use_batch_render:
                self.response_text += token
                self.streamed_any = True
                self.token_count += 1
                self.repetition_stopped = True
                return
            before, _, _after = token.partition(_REPETITION_MARKER)
            if before:
                self.on_token(before)
            self.response_text += _REPETITION_MARKER
            self.streamed_any = True
            self.token_count += 1
            self.repetition_stopped = True
            self._show_repetition_notice()
            return

        self._finish_thinking()

        if self.use_batch_render:
            self.response_text += token
            self.streamed_any = True
            self.token_count += 1
            return

        open_delims = (r"\(", r"\[", "$$")
        close_delims = (r"\)", r"\]", "$$")
        if not self.in_latex:
            if any(d in token for d in open_delims):
                self.in_latex = True
                self.latex_buf = token
                tail = token
                for od, cd in zip(open_delims, close_delims):
                    if od in tail:
                        after = tail[tail.index(od) + len(od):]
                        if cd in after:
                            token = self.flush_latex_buf()
                            break
                else:
                    self.response_text += self.latex_buf
                    self.streamed_any = True
                    self.token_count += 1
                    return
            else:
                token = self.strip_latex(token)
        else:
            self.latex_buf += token
            if any(d in token for d in close_delims):
                token = self.flush_latex_buf()
            else:
                self.response_text += token
                self.streamed_any = True
                self.token_count += 1
                return

        self.response_text += token
        self.streamed_any = True
        self.token_count += 1
        can_live = (
            self.has_rich
            and not self.use_plain_print
            and getattr(self.console, "is_terminal", False)
            and not getattr(self.console, "is_dumb_terminal", True)
            and self.markdown_cls is not None
            and self.live_cls is not None
        )
        if can_live:
            now = time.time()
            md = self.markdown_cls(self.strip_latex(self.response_text))
            if self.live_display is None:
                self.live_display = self.live_cls(
                    md,
                    console=self.console,
                    refresh_per_second=12,
                    vertical_overflow="visible",
                )
                self.live_display.start()
                self.last_live_update = now
            elif now - self.last_live_update >= self.live_update_interval:
                self.live_display.update(md)
                self.last_live_update = now
        else:
            print(token, end="", flush=True)

    def on_thinking(self, content: str) -> None:
        if not self.thinking_shown:
            self.stop_spinner()
            self.thinking_start = time.time()
            self.thinking_shown = True
        self.thinking_tokens += 1
        if self.thinking_tokens % 30 == 1:
            elapsed = time.time() - self.thinking_start
            sys.stdout.write(
                f"\r  \033[2m✻\033[0m \033[2m思考中  {elapsed:.1f}s  "
                f"({self.thinking_tokens} tokens)\033[0m    "
            )
            sys.stdout.flush()
        if len("".join(self.thinking_preview_buf)) < 300:
            self.thinking_preview_buf.append(content)
        if len("".join(self.thinking_full_buf)) < 8000:
            self.thinking_full_buf.append(content)

    def on_tool_call(self, tool: str, params: dict) -> None:
        self._finish_thinking()
        if self.print_tool_call is not None:
            self.print_tool_call(tool, params if isinstance(params, dict) else {})
        self.tool_start_times[tool] = time.time()

    def on_tool_result(self, tool: str, summary: Any) -> None:
        elapsed_ms = int((time.time() - self.tool_start_times.pop(tool, time.time())) * 1000)
        ok = not (isinstance(summary, dict) and not summary.get("success", True))
        if self.print_tool_done is not None:
            self.print_tool_done(tool, elapsed_ms, success=ok)

        ts = time.strftime("%H:%M:%S")
        entry = f"[{ts}] {tool}: {str(summary)[:100]}"
        self.terminal._transcript_log.append(entry)
        if len(self.terminal._transcript_log) > 100:
            self.terminal._transcript_log = self.terminal._transcript_log[-100:]

        if tool in ("TaskCreate", "TaskUpdate") and isinstance(summary, dict):
            tid = summary.get("id") or summary.get("task_id")
            title = summary.get("title", "")
            status = summary.get("status", "pending")
            if tid:
                existing = next((t for t in self.terminal._task_list if t.get("id") == tid), None)
                if existing:
                    existing["status"] = status
                    if title:
                        existing["title"] = title
                else:
                    self.terminal._task_list.append({"id": tid, "title": title, "status": status})

    def on_status(self, state: str, message: str) -> None:
        if state != "fallback":
            return
        match = re.search(r"(?:from\s+)?(\w+)\s*(?:→|->|to)\s*(\w+)", message or "", re.I)
        if match:
            from_provider, to_provider = match.group(1), match.group(2)
        else:
            from_provider, to_provider = self.fallback_from, "cloud"
        from ui.render.output import print_fallback_toast

        print_fallback_toast(
            from_provider,
            to_provider,
            message or "",
            console=self.console,
            has_rich=self.has_rich,
        )

    def handle_runtime_event(self, event: Any) -> None:
        if isinstance(event, AgentEventToken):
            self.on_token(event.text)
        elif isinstance(event, AgentEventThinking):
            self.on_thinking(event.content)
        elif isinstance(event, AgentEventToolCall):
            self.on_tool_call(event.tool, event.params)
        elif isinstance(event, AgentEventToolResult):
            self.on_tool_result(event.tool, event.result)
        elif isinstance(event, AgentEventStatus):
            self.on_status(event.state, event.message)


class TerminalApprovalEventConsumer:
    """Terminal-side approval prompt consumer for runtime tool execution."""

    def __init__(
        self,
        *,
        terminal: Any,
        console: Any,
        has_rich: bool,
        confirm_decision: Callable[..., ApprovalDecision],
        apply_decision: Callable[[dict, ApprovalDecision], dict],
        save_config: Callable[[dict], None],
    ) -> None:
        self.terminal = terminal
        self.console = console
        self.has_rich = has_rich
        self.confirm_decision = confirm_decision
        self.apply_decision = apply_decision
        self.save_config = save_config

    async def approve(
        self,
        tool_name: str,
        tool_params: dict,
        *,
        stop_before_prompt: Callable[[], None] | None = None,
    ) -> ApprovalDecision:
        if stop_before_prompt is not None:
            stop_before_prompt()
        try:
            approval = self.confirm_decision(
                tool_name,
                tool_params,
                config_policy=self.terminal.config.get("command_policy", "safe"),
            )
        except KeyboardInterrupt:
            approval = ApprovalDecision.deny("KeyboardInterrupt")

        self.terminal._record_feedback(
            "tool_accept" if approval.approved else "tool_reject",
            tool_name,
        )
        if not approval.approved:
            from ui.render.output import print_tool_blocked

            print_tool_blocked(tool_name, "用户取消", console=self.console, has_rich=self.has_rich)
        return approval

    def apply(self, tool_params: dict, approval: ApprovalDecision) -> dict:
        self.apply_decision(tool_params, approval)
        if approval.upgrade_policy:
            tool_params.pop("_upgrade_policy", None)
            self.terminal.config["command_policy"] = "balanced"
            try:
                self.save_config(self.terminal.config)
                if self.has_rich:
                    self.console.print("  [dim]策略已升级为 balanced 并保存[/dim]")
            except Exception:
                pass
        return tool_params


__all__ = ["TerminalApprovalEventConsumer", "TerminalRuntimeEventConsumer"]
