"""stream_chat — Aria cloud SSE provider extracted from aria_cli.py.

Streams AI responses from the Aria backend via Server-Sent Events (SSE).
Supports cancellation, thinking tokens, tool calls, retries, and usage stats.
"""
from __future__ import annotations

import asyncio
import json
from typing import Callable, Optional


async def stream_chat(
    base_url: str,
    message: str,
    history: list,
    model: str = "qwen2.5:7b",
    thinking_mode: str = "auto",
    user_context: Optional[dict] = None,
    auth_token: Optional[str] = None,
    on_token: Optional[Callable[[str], None]] = None,
    on_thinking: Optional[Callable[[str], None]] = None,
    on_tool_call: Optional[Callable[[str, dict], None]] = None,
    on_tool_result: Optional[Callable[[str, str], None]] = None,
    on_status: Optional[Callable[[str, str], None]] = None,
    cancel_event: Optional[asyncio.Event] = None,
    project_context: str = "",
) -> dict:
    """Stream AI chat via SSE with cancel support and user context.

    Parameters
    ----------
    project_context:
        ARIA.md / CLAUDE.md content to inject into user_context.
        Callers pass ``_PROJECT_CONTEXT`` from aria_cli, keeping this
        module free of global state.
    """
    import aiohttp

    url = f"{base_url}/api/v2/ai/chat/stream"

    payload: dict = {
        "message": message,
        "conversation_history": history[-20:],
        "model": model,
        "thinking_mode": thinking_mode,
        "stream": True,
    }
    if user_context:
        if project_context:
            user_context = {**user_context, "project_context": project_context}
        payload["user_context"] = user_context

    headers: dict = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    full_response = ""
    thinking_content = ""
    tools_used: list = []
    sources: list = []
    tool_calls_pending: list = []
    usage: dict = {"prompt_tokens": 0, "completion_tokens": 0, "thinking_tokens": 0}

    _max_connect_retries = 2
    _last_connect_error: Optional[str] = None

    for _attempt in range(_max_connect_retries + 1):
        if cancel_event and cancel_event.is_set():
            return {
                "success": True, "response": "", "cancelled": True,
                "tools_used": [], "sources": [], "usage": usage,
            }
        # Reset per-attempt accumulators
        full_response = ""
        thinking_content = ""
        tools_used = []
        sources = []
        tool_calls_pending = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "thinking_tokens": 0}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        return {"success": False, "error": f"HTTP {resp.status}: {error_text[:200]}"}

                    buffer = ""
                    event_type = "delta"

                    async for chunk in resp.content:
                        if cancel_event and cancel_event.is_set():
                            try:
                                await session.post(
                                    f"{base_url}/api/v2/ai/chat/cancel",
                                    headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=3),
                                )
                            except Exception:
                                pass
                            return {
                                "success": True, "response": full_response,
                                "cancelled": True, "tools_used": tools_used,
                                "sources": sources, "usage": usage,
                            }

                        text = chunk.decode("utf-8", errors="ignore")
                        buffer += text

                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()

                            if not line or line.startswith(":"):
                                continue
                            if line.startswith("event:"):
                                event_type = line[6:].strip()
                                continue
                            if not line.startswith("data:"):
                                continue

                            data_str = line[5:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue

                            # Backend error: {"success": false, "error": "..."}
                            if data.get("success") is False:
                                err_msg = data.get("error", "Backend error")
                                return {"success": False, "error": f"Backend: {err_msg}"}

                            evt = data.get("type", event_type)

                            if evt == "delta":
                                token = data.get("text", data.get("content", ""))
                                if token:
                                    full_response += token
                                    usage["completion_tokens"] += 1
                                    if on_token:
                                        on_token(token)

                            elif evt == "thinking_content":
                                tc = data.get("content", "")
                                if tc:
                                    thinking_content += tc
                                    usage["thinking_tokens"] += 1
                                    if on_thinking:
                                        on_thinking(tc)

                            elif evt == "tool_call":
                                tool = data.get("tool", data.get("name", ""))
                                params = data.get("params", {})
                                tools_used.append(tool)
                                tool_calls_pending.append({"tool": tool, "params": params})
                                if on_tool_call:
                                    on_tool_call(tool, params)

                            elif evt == "tool_result":
                                if on_tool_result:
                                    on_tool_result(data.get("tool", ""), data.get("summary", ""))

                            elif evt == "status":
                                if on_status:
                                    on_status(data.get("state", ""), data.get("message", ""))

                            elif evt == "final":
                                full_response = data.get("answer", full_response)
                                sources = data.get("sources", [])
                                if data.get("usage"):
                                    u = data["usage"]
                                    usage["prompt_tokens"] = u.get("prompt_tokens", usage["prompt_tokens"])
                                    usage["completion_tokens"] = u.get("completion_tokens", usage["completion_tokens"])

                            elif evt == "error":
                                return {"success": False, "error": data.get("message", "Unknown error")}

            return {
                "success": True,
                "response": full_response,
                "thinking": thinking_content,
                "tools_used": tools_used,
                "sources": sources,
                "tool_calls_pending": tool_calls_pending,
                "usage": usage,
            }

        except asyncio.TimeoutError:
            return {"success": False, "error": "Request timed out (120s)"}
        except asyncio.CancelledError:
            return {
                "success": True, "response": full_response, "cancelled": True,
                "tools_used": tools_used, "sources": sources, "usage": usage,
            }
        except aiohttp.ClientConnectorError as exc:
            _last_connect_error = str(exc)
            if _attempt < _max_connect_retries:
                wait = 1.5 * (_attempt + 1)
                await asyncio.sleep(wait)
                if on_status:
                    on_status("retry", f"Connection failed, retrying ({_attempt + 2}/{_max_connect_retries + 1})...")
                continue
            break
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    return {
        "success": False,
        "error": f"Connection failed after {_max_connect_retries + 1} attempts: {_last_connect_error}",
    }
