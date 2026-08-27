"""Native Google Cloud Vertex AI LLM Provider using google-genai.

google-genai is an optional dependency: most users run Ollama or an
OpenAI-compatible endpoint and should not have to install a Google SDK. That
makes the "it is not installed" path a normal one to land on, so it has to say
what to do rather than leaking a ModuleNotFoundError.
"""

import asyncio
import json
import os
from typing import AsyncGenerator, Optional

from aria_code.apps.cli.providers.base import (
    LLMDone,
    LLMEvent,
    LLMProvider,
    LLMThinking,
    LLMToken,
    LLMToolCall,
)

_MISSING_SDK_MESSAGE = (
    "Gemini/Vertex AI 需要 google-genai，当前未安装。\n"
    "  安装：pip install google-genai\n"
    "  或改用其他模型：/model  （Ollama 本地模型无需额外依赖）"
)


class VertexAIProvider(LLMProvider):
    """Native Vertex AI provider using google-genai."""
    
    def __init__(
        self,
        model: str,
        config: Optional[dict] = None,
        system_override: Optional[str] = None,
    ):
        self.model = model
        self.config = config or {}
        self.system_override = system_override
        self._client = None

    def _api_key(self) -> str:
        """Gemini API key from config, falling back to the standard env vars."""
        for value in (
            self.config.get("api_key"),
            self.config.get("gemini_key"),
            os.getenv("GEMINI_API_KEY"),
            os.getenv("GOOGLE_API_KEY"),
        ):
            key = str(value or "").strip()
            if key:
                return key
        return ""

    def _use_vertex(self) -> bool:
        """Decide between Vertex AI (ADC) and the Gemini API-key endpoint.

        ``use_vertexai`` used to default to True unconditionally, so a developer
        holding only a GEMINI_API_KEY got ``genai.Client(vertexai=True)`` and a
        credentials error — Vertex needs application-default credentials and a
        project.  An explicit config value still wins; otherwise pick whichever
        set of credentials is actually present.
        """
        configured = self.config.get("use_vertexai")
        if configured is not None:
            return bool(configured)
        env_flag = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower()
        if env_flag in {"1", "true", "yes", "on"}:
            return True
        if env_flag in {"0", "false", "no", "off"}:
            return False
        has_vertex_creds = bool(
            os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        )
        if has_vertex_creds:
            return True
        # No project/ADC configured: an API key is the only usable path.
        return not self._api_key()

    def _get_client(self):
        if self._client is None:
            from google import genai

            if self._use_vertex():
                project = os.getenv("GOOGLE_CLOUD_PROJECT") or self.config.get("gcp_project")
                location = (
                    os.getenv("GOOGLE_CLOUD_LOCATION")
                    or self.config.get("gcp_location")
                    or "us-central1"
                )
                kwargs = {"vertexai": True, "location": location}
                if project:
                    kwargs["project"] = str(project)
                self._client = genai.Client(**kwargs)
            else:
                api_key = self._api_key()
                if not api_key:
                    raise RuntimeError(
                        "Gemini 需要凭据：设置 GEMINI_API_KEY，或配置 Vertex AI "
                        "(GOOGLE_CLOUD_PROJECT + gcloud auth application-default login)。"
                    )
                self._client = genai.Client(api_key=api_key)
        return self._client
        
    def _messages_to_contents(self, messages: list):
        # Convert aria chat messages to genai Content objects
        from google.genai import types
        
        system_instruction = self.system_override or ""
        contents = []
        
        for msg in messages:
            role = msg.get("role", "user")
            content_str = msg.get("content", "")
            
            if role == "system":
                if system_instruction:
                    system_instruction += "\n\n" + content_str
                else:
                    system_instruction = content_str
                continue
                
            genai_role = "user" if role == "user" else "model"
            
            if role == "tool":
                # Rendered as text, not as a FunctionResponse part.
                #
                # Gemini only accepts a function_response that answers a
                # function_call it can see in the preceding model turn, and the
                # agent loop does not preserve those: it records the assistant
                # turn as plain text. Sending an unanswered function_response
                # made the conversation malformed, and Gemini replied with a
                # single whitespace character and no tool call — the turn died
                # as "empty_response" a round or two in, every time.
                #
                # The information is not lost by doing this: the loop already
                # puts the same results in the follow-up user message that
                # comes next, in a form written to be read.
                tool_name = msg.get("name") or "tool"
                text = f"[{tool_name}] {content_str}".strip()
                if not text:
                    continue
                if contents and contents[-1].role == "user":
                    contents[-1].parts.append(types.Part.from_text(text=text))
                else:
                    contents.append(types.Content(
                        role="user", parts=[types.Part.from_text(text=text)]))
                continue
                
            # An empty part is worse than no part. When a model answers a turn
            # with nothing but a function call — which Gemini does routinely,
            # and which the agent loop records as an assistant message whose
            # text is "" — this used to send Content(role="model", parts=[""]).
            # Gemini responds to that with a single whitespace character and no
            # tool call, so the second round of every tool-using turn came back
            # as "empty_response" and the task died after one step.
            if not str(content_str or "").strip():
                continue

            # Check if previous message has same role
            # (Gemini requires alternating roles: user, model, user, model)
            if contents and contents[-1].role == genai_role:
                contents[-1].parts.append(types.Part.from_text(text=content_str))
            else:
                contents.append(types.Content(role=genai_role, parts=[types.Part.from_text(text=content_str)]))
                
        return contents, system_instruction

    def _schema_from_dict(self, d: dict, types):
        if not d:
            return None
        t = d.get("type", "string").upper()
        if t == "ARRAY":
            items = d.get("items", {})
            return types.Schema(
                type="ARRAY",
                description=d.get("description", ""),
                items=self._schema_from_dict(items, types) if items else types.Schema(type="STRING")
            )
        elif t == "OBJECT":
            props = d.get("properties", {})
            req = d.get("required", [])
            schema_props = {k: self._schema_from_dict(v, types) for k, v in props.items()}
            return types.Schema(
                type="OBJECT",
                description=d.get("description", ""),
                properties=schema_props if schema_props else None,
                required=req if req else None
            )
        else:
            return types.Schema(
                type=t,
                description=d.get("description", "")
            )

    def _tools_to_genai(self, tools: list):
        if not tools:
            return None
        from google.genai import types
        genai_tools = []
        # Vertex rejects the entire request when two declarations share a name
        # ("Duplicate function declaration found: web_fetch"), where
        # OpenAI-compatible backends just take the last one. The registries
        # upstream should not produce duplicates, but this is the boundary
        # where a duplicate becomes a hard 400 for the whole turn, so it is
        # also the boundary that has to be certain.
        seen: set = set()
        for tool in tools:
            func = tool.get("function", tool)
            name = func.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            desc = func.get("description", "")
            
            # Map parameters recursively
            params = func.get("parameters", {})
            schema = self._schema_from_dict(params, types) if params else None
            
            tool_declaration = types.FunctionDeclaration(
                name=name,
                description=desc,
                parameters=schema
            )
            genai_tools.append(types.Tool(function_declarations=[tool_declaration]))
            
        return genai_tools

    async def stream(
        self,
        messages: list,
        tools: list,
        *,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> AsyncGenerator[LLMEvent, None]:
        # These imports must sit INSIDE the try. They were above it, so when
        # google-genai was not installed they raised first and the handler
        # below — the one that explains how to fix it — was unreachable. The
        # user saw a bare "No module named 'google.genai'" and no way forward.
        try:
            from google.genai import types
            from google.genai.errors import APIError

            client = self._get_client()
        except ImportError:
            yield LLMDone(
                response="", provider="vertexai", success=False,
                error=_MISSING_SDK_MESSAGE,
            )
            return
        except Exception as e:
            yield LLMDone(response="", provider="vertexai", success=False, error=str(e))
            return
            
        contents, system_instruction = self._messages_to_contents(messages)
        genai_tools = self._tools_to_genai(tools)
        
        config = types.GenerateContentConfig(
            system_instruction=system_instruction if system_instruction else None,
            tools=genai_tools if genai_tools else None,
            temperature=self.config.get("temperature", 0.7),
        )
        
        try:
            # We must use asyncio.to_thread because the google-genai async client might not be used here
            # Or we can just use the sync stream generator wrapped in an async generator.
            # google-genai provides AsyncClient as well:
            # async_client = genai.Client(vertexai=True).aio
            response_stream = await client.aio.models.generate_content_stream(
                model=self.model,
                contents=contents,
                config=config,
            )
            
            full_response = ""
            usage = {}
            tool_calls = []
            
            async for chunk in response_stream:
                if cancel_event and cancel_event.is_set():
                    yield LLMDone(response=full_response, provider="vertexai", success=True, cancelled=True)
                    return
                    
                if chunk.text:
                    full_response += chunk.text
                    yield LLMToken(text=chunk.text)
                    
                if chunk.function_calls:
                    for fc in chunk.function_calls:
                        args = {k: v for k, v in fc.args.items()} if fc.args else {}
                        yield LLMToolCall(tool=fc.name, params=args)
                        tool_calls.append({"tool": fc.name, "params": args})
                        
                if chunk.usage_metadata:
                    usage = {
                        "prompt_tokens": chunk.usage_metadata.prompt_token_count,
                        "completion_tokens": chunk.usage_metadata.candidates_token_count,
                    }
                    
            yield LLMDone(
                response=full_response,
                tool_calls_pending=tool_calls,
                usage=usage,
                provider="vertexai",
                success=True,
                cancelled=False,
            )
            
        except APIError as e:
            yield LLMDone(response="", provider="vertexai", success=False, error=f"Vertex AI API Error: {e.message}")
        except Exception as e:
            yield LLMDone(response="", provider="vertexai", success=False, error=f"Vertex AI Error: {str(e)}")

