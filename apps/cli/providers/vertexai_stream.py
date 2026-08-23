"""Native Google Cloud Vertex AI LLM Provider using google-genai."""

import asyncio
import json
from typing import AsyncGenerator, Optional

from apps.cli.providers.base import (
    LLMDone,
    LLMEvent,
    LLMProvider,
    LLMThinking,
    LLMToken,
    LLMToolCall,
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

    def _get_client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(vertexai=True)
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
                # For tool results, role should be "user" with Part containing FunctionResponse
                # Wait, google-genai role for function response is "user" or "tool"?
                # Actually, role='user', part=FunctionResponse
                tool_name = msg.get("name", "unknown")
                part = types.Part.from_function_response(
                    name=tool_name,
                    response={"result": content_str}
                )
                contents.append(types.Content(role="user", parts=[part]))
                continue
                
            # Check if previous message has same role
            # (Gemini requires alternating roles: user, model, user, model)
            if contents and contents[-1].role == genai_role:
                contents[-1].parts.append(types.Part.from_text(text=content_str))
            else:
                contents.append(types.Content(role=genai_role, parts=[types.Part.from_text(text=content_str)]))
                
        return contents, system_instruction

    def _tools_to_genai(self, tools: list):
        if not tools:
            return None
        
        from google.genai import types
        
        genai_tools = []
        for tool in tools:
            # Assume tool is a dict adhering to OpenAI JSON schema
            func = tool.get("function", tool)
            name = func.get("name")
            desc = func.get("description", "")
            
            # Map parameters
            params = func.get("parameters", {})
            properties = params.get("properties", {})
            required = params.get("required", [])
            
            schema_props = {}
            for k, v in properties.items():
                prop_type = v.get("type", "string").upper()
                if prop_type == "ARRAY":
                    prop_type = "ARRAY"
                schema_props[k] = types.Schema(
                    type=prop_type,
                    description=v.get("description", ""),
                )
                
            tool_declaration = types.FunctionDeclaration(
                name=name,
                description=desc,
                parameters=types.Schema(
                    type="OBJECT",
                    properties=schema_props,
                    required=required
                ) if properties else None
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
        from google.genai import types
        from google.genai.errors import APIError
        
        try:
            client = self._get_client()
        except ImportError:
            yield LLMDone(
                response="", provider="vertexai", success=False,
                error="google-genai package not found. Please pip install google-genai."
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

