from __future__ import annotations
import asyncio
import base64
import json
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from models import (
    ChatHistoryItem,
    FileEdit,
    FileEditActionType,
    ParsedAgentResponse,
    VendorProfile,
    VendorType,
    WhitelistedFile,
)


LEADING_THINK_OPEN_RE = re.compile(
    r"^(\s*(?:🔍 Searching web via vendor grounding\.\.\.\s*)?)<think\b[^>]*>",
    re.IGNORECASE | re.DOTALL,
)
THINK_CLOSE_BEFORE_TITLE_RE = re.compile(
    r"</think>\s*(?=<(?:metis|session|chat)_title\b)",
    re.IGNORECASE,
)
THINK_CLOSE_LINE_RE = re.compile(
    r"</think>[ \t]*(?:\r?\n|$)",
    re.IGNORECASE,
)
THINK_CLOSE_ANY_RE = re.compile(
    r"</think>\s*",
    re.IGNORECASE,
)

LEADING_TITLE_EXTRACT_RE = re.compile(
    r"^(?:\s*(?:🔍 Searching web via vendor grounding\.\.\.\s*)?)<(?:metis|session|chat)_title\b[^>]*>([^\r\n<]{1,120})</(?:metis|session|chat)_title>",
    re.IGNORECASE,
)
LEADING_TITLE_STRIP_RE = re.compile(
    r"^(\s*(?:🔍 Searching web via vendor grounding\.\.\.\s*)?)<(?:metis|session|chat)_title\b[^>]*>[^\r\n<]*</(?:metis|session|chat)_title>[ \t]*\r?\n?",
    re.IGNORECASE,
)
LEADING_UNCLOSED_TITLE_RE = re.compile(
    r"^(\s*(?:🔍 Searching web via vendor grounding\.\.\.\s*)?)<(?:metis|session|chat)_title\b[^>]*>[^\r\n]*$",
    re.IGNORECASE,
)

FENCE_OPEN_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
SINGLE_LINE_TAG_RE = re.compile(
    r"^[ \t]*<metis_(replace|write)\s+(?:file|path|name)\s*=\s*(?:\"([^\">]+)\"|'([^'>]+)'|([^\"'>\s]+))\s*>(.*)</metis_\1>[ \t]*$",
    re.IGNORECASE,
)
OPEN_TAG_RE = re.compile(
    r"^[ \t]*<metis_(replace|write)\s+(?:file|path|name)\s*=\s*(?:\"([^\">]+)\"|'([^'>]+)'|([^\"'>\s]+))\s*>[ \t]*$",
    re.IGNORECASE,
)
CLOSE_REPLACE_RE = re.compile(r"^[ \t]*</metis_replace>[ \t]*$", re.IGNORECASE)
CLOSE_WRITE_RE = re.compile(r"^[ \t]*</metis_write>[ \t]*$", re.IGNORECASE)
STREAMING_PARTIAL_TAG_RE = re.compile(r"^[ \t]*<metis_(?:replace|write)\b[^>]*$", re.IGNORECASE)

SR_BLOCK_RE = re.compile(
    r"<{5,}\s*SEARCH\r?\n((?:(?!={5,}|<{5,}\s*SEARCH)[\s\S])*?)\r?\n={5,}(?:\r?\n((?:(?!>{5,}\s*REPLACE|={5,})[\s\S])*?))?\r?\n>{5,}\s*REPLACE",
    re.IGNORECASE,
)


class MultiVendorClient:
    @staticmethod
    def extract_media_items(whitelist: List[WhitelistedFile]) -> List[dict]:
        items: List[dict] = []
        for f in whitelist:
            if not (f.is_binary_media or f.media_kind in ("image", "pdf")):
                continue
            p = Path(f.full_path)
            if not p.is_file():
                continue
            try:
                raw_bytes = p.read_bytes()
                if not raw_bytes:
                    continue
                b64 = base64.b64encode(raw_bytes).decode("ascii")
                mime = f.mime_type
                if not mime or mime == "text/plain":
                    mime = "application/pdf" if f.media_kind == "pdf" else "image/png"
                items.append({
                    "fileName": f.file_name,
                    "mediaKind": f.media_kind,
                    "mimeType": mime,
                    "base64": b64,
                })
            except Exception:
                continue
        return items

    @classmethod
    async def send_chat(
        cls,
        vendor: VendorProfile,
        system_prompt: str,
        files_snapshot: Optional[str] = None,
        history: Optional[List[ChatHistoryItem]] = None,
        whitelist: Optional[List[WhitelistedFile]] = None,
        thinking_intensity: str = "medium",
        on_chunk: Optional[Callable[[str], None]] = None,
    ) -> Tuple[bool, str]:
        """Returns (success, full_response_or_error_message)."""
        chat_history = list(history) if history is not None else []
        file_list = list(whitelist) if whitelist is not None else []
        chunk_cb = on_chunk if on_chunk is not None else (lambda _: None)

        # 1. Decouple System Prompt from mutable files_snapshot.
        # System prompt remains strictly static and deterministic as Prefix Cache Anchor.
        full_system = system_prompt.strip()

        # If history is empty but a snapshot was passed directly, seed it as the initial user context
        if files_snapshot and files_snapshot.strip() and not chat_history:
            chat_history.append(
                ChatHistoryItem(
                    role="user",
                    content=f"[Workspace Initial Files Snapshot]\n{files_snapshot.strip()}",
                )
            )

        base_url = vendor.base_url.rstrip("/")
        media_items = cls.extract_media_items(file_list)

        if vendor.vendor_type == VendorType.Gemini:
            return await asyncio.to_thread(
                cls._call_gemini_sync,
                vendor,
                base_url,
                full_system,
                chat_history,
                thinking_intensity,
                chunk_cb,
                media_items,
            )
        elif vendor.vendor_type == VendorType.Claude:
            return await asyncio.to_thread(
                cls._call_claude_sync,
                vendor,
                base_url,
                full_system,
                chat_history,
                thinking_intensity,
                chunk_cb,
                media_items,
            )
        else:
            return await asyncio.to_thread(
                cls._call_responses_api_sync,
                vendor,
                base_url,
                full_system,
                chat_history,
                chunk_cb,
                media_items,
                thinking_intensity,
            )

    @staticmethod
    def build_responses_tools() -> List[dict]:
        """
        OpenAI and DeepSeek standard Responses API tool specification for live web search.
        Allows models to dynamically evaluate search on the provider cluster without forcing it.
        """
        return [
            {
                "type": "web_search",
            }
        ]

    @staticmethod
    def build_deepseek_tools() -> List[dict]:
        """
        DeepSeek live web search specification for Responses API & Chat Completions fallback.
        """
        return [
            {
                "type": "web_search",
            }
        ]

    @staticmethod
    def resolve_responses_url(base_url: str) -> str:
        """
        Resolve base URL to the standardized /v1/responses endpoint.
        """
        clean = base_url.rstrip("/")
        if clean.endswith("/chat/completions"):
            clean = clean[:-17]
        if clean.endswith("/responses"):
            return clean
        if clean.endswith("/v1"):
            return f"{clean}/responses"
        return f"{clean}/v1/responses"

    @classmethod
    def prepare_responses_inputs(cls, history: List[ChatHistoryItem]) -> List[dict]:
        """
        Sanitize and format chat history for OpenAI / DeepSeek Responses API:
        1. Map 'assistant' -> 'assistant', everything else -> 'user'.
        2. Merge consecutive turns of the same role to maintain a clean alternating turn sequence.
        3. Ensure the sequence starts with a 'user' turn.
        4. Ensure the sequence does not end with an 'assistant' turn.
        """
        input_items: List[dict] = []
        for item in history:
            role = "assistant" if item.role == "assistant" else "user"
            content = (item.content or "").strip()
            if not content:
                continue

            if not input_items:
                input_items.append({"role": role, "content": content})
            else:
                if input_items[-1]["role"] == role:
                    prev_content = input_items[-1]["content"]
                    if isinstance(prev_content, str):
                        input_items[-1]["content"] = f"{prev_content}\n\n{content}"
                    else:
                        input_items.append({"role": role, "content": content})
                else:
                    input_items.append({"role": role, "content": content})

        while input_items and input_items[0]["role"] == "assistant":
            input_items.pop(0)

        if not input_items:
            input_items.append({"role": "user", "content": "Please proceed."})

        if input_items[-1]["role"] == "assistant":
            input_items.append({"role": "user", "content": "Please proceed."})

        return input_items

    @classmethod
    def _call_responses_api_sync(
        cls,
        vendor: VendorProfile,
        base_url: str,
        system_prompt: str,
        history: List[ChatHistoryItem],
        on_chunk: Callable[[str], None],
        media_items: Optional[List[dict]] = None,
        thinking_intensity: str = "high",
    ) -> Tuple[bool, str]:
        url = cls.resolve_responses_url(base_url)

        input_items = cls.prepare_responses_inputs(history)

        if media_items and input_items:
            last_user = None
            for itm in reversed(input_items):
                if itm.get("role") == "user":
                    last_user = itm
                    break
            if not last_user:
                last_user = input_items[-1]

            raw_user_content = last_user.get("content", "")
            if isinstance(raw_user_content, str):
                content_blocks: List[dict] = []
                if raw_user_content:
                    content_blocks.append({"type": "input_text", "text": raw_user_content})
                for m in media_items:
                    if m["mediaKind"] == "image":
                        content_blocks.append({
                            "type": "input_image",
                            "image_url": f"data:{m['mimeType']};base64,{m['base64']}",
                        })
                if any(b.get("type") == "input_image" for b in content_blocks):
                    last_user["content"] = content_blocks

        payload: dict = {
            "model": vendor.selected_model,
            "instructions": system_prompt,
            "input": input_items,
            "stream": True,
            "temperature": 0.2,
        }

        # Allow vendor API live search dynamically without forcing it
        if vendor.api_key and vendor.api_key.strip():
            payload["tools"] = cls.build_responses_tools()

        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "Metis-Agent/1.3",
        }
        if vendor.api_key and vendor.api_key.strip():
            headers["Authorization"] = f"Bearer {vendor.api_key.strip()}"

        ctx = ssl.create_default_context()

        def open_responses_stream(req_payload: dict):
            data_bytes = json.dumps(req_payload).encode("utf-8")
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            return urllib.request.urlopen(req, timeout=180, context=ctx)

        resp = None
        try:
            resp = open_responses_stream(payload)
        except urllib.error.HTTPError as he:
            body = ""
            try:
                body = he.read().decode("utf-8", errors="replace")
            except Exception:
                pass

            # If provider endpoint returns 404 / 405 / 501, seamlessly fallback to Chat Completions
            if he.code in (404, 405, 501):
                return cls._call_chat_completions_fallback_sync(
                    vendor,
                    base_url,
                    system_prompt,
                    history,
                    on_chunk,
                    media_items,
                    thinking_intensity,
                )

            body_lower = body.lower()
            # If tools / web_search not supported by model or endpoint, or rate limited/unsupported (429/400/403/404), retry without tools
            if "tools" in payload and (
                he.code in (400, 403, 404, 429)
                or any(term in body_lower for term in ("tool", "tools", "web_search", "not supported", "unknown", "unsupported", "quota", "exhausted"))
            ):
                payload.pop("tools", None)
                try:
                    resp = open_responses_stream(payload)
                except urllib.error.HTTPError as he2:
                    if he2.code in (404, 405, 501):
                        return cls._call_chat_completions_fallback_sync(
                            vendor,
                            base_url,
                            system_prompt,
                            history,
                            on_chunk,
                            media_items,
                            thinking_intensity,
                        )
                    body2 = ""
                    try:
                        body2 = he2.read().decode("utf-8", errors="replace")
                    except Exception:
                        pass
                    body2_lower = body2.lower()
                    if (he2.code in (400, 422)) and any(term in body2_lower for term in ("instructions", "unknown parameter", "unrecognized field", "input", "deserialize", "target type", "unknown variant")):
                        return cls._call_chat_completions_fallback_sync(
                            vendor,
                            base_url,
                            system_prompt,
                            history,
                            on_chunk,
                            media_items,
                            thinking_intensity,
                        )
                    return False, f"Responses API Error (HTTP {he2.code}): {body2}"
                except Exception as e2:
                    return False, f"HTTP request error on retry: {e2}"
            elif (he.code in (400, 422)) and any(term in body_lower for term in ("instructions", "unknown parameter", "unrecognized field", "input", "deserialize", "target type", "unknown variant")):
                # Schema/variant deserialization failure in Responses API; fallback to standard /chat/completions
                return cls._call_chat_completions_fallback_sync(
                    vendor,
                    base_url,
                    system_prompt,
                    history,
                    on_chunk,
                    media_items,
                    thinking_intensity,
                )
            else:
                return False, f"Responses API Error (HTTP {he.code}): {body}"
        except Exception as e:
            return False, f"HTTP request error: {e}"

        output_parts: List[str] = []
        has_shown_search = False

        try:
            with resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    json_str = line[5:].strip()
                    if json_str == "[DONE]":
                        break
                    try:
                        v = json.loads(json_str)
                    except Exception:
                        continue

                    event_type = v.get("type", "")
                    if event_type in (
                        "response.done",
                        "response.completed",
                        "response.incomplete",
                        "response.cancelled",
                        "done",
                    ):
                        break

                    # 1. Server-side web search status indicators
                    if event_type == "response.output_item.added":
                        item = v.get("item", {})
                        if isinstance(item, dict) and item.get("type") == "web_search_call":
                            if not has_shown_search:
                                has_shown_search = True
                                notice = "🔍 Searching web via vendor grounding...\n\n"
                                output_parts.append(notice)
                                on_chunk(notice)
                    elif "web_search_call" in event_type or event_type.endswith(".searching"):
                        if not has_shown_search:
                            has_shown_search = True
                            notice = "🔍 Searching web via vendor grounding...\n\n"
                            output_parts.append(notice)
                            on_chunk(notice)

                    # 2. Reasoning / Thinking deltas (DeepSeek-R1 / OpenAI o-series)
                    # Do not mix structured reasoning into output_parts to prevent thought leakage.
                    if event_type in (
                        "response.reasoning_text.delta",
                        "response.reasoning_summary_text.delta",
                        "response.reasoning.delta",
                        "response.reasoning_text.done",
                        "response.reasoning_summary_text.done",
                        "response.reasoning.done",
                    ):
                        continue

                    # 3. Output text deltas
                    elif event_type in (
                        "response.output_text.delta",
                        "response.text.delta",
                    ):
                        delta = v.get("delta")
                        content_txt = delta if isinstance(delta, str) else (delta.get("text") if isinstance(delta, dict) else "")
                        if content_txt:
                            output_parts.append(content_txt)
                            on_chunk(content_txt)

                    # 4. Standard choices fallback if proxy wraps completion chunks
                    elif "choices" in v:
                        choices = v.get("choices")
                        if isinstance(choices, list) and choices:
                            c_delta = choices[0].get("delta", {})
                            if isinstance(c_delta, dict):
                                content = c_delta.get("content")
                                if isinstance(content, str) and content:
                                    output_parts.append(content)
                                    on_chunk(content)

                            finish_reason = choices[0].get("finish_reason")
                            if finish_reason and finish_reason in ("stop", "length", "tool_calls", "content_filter"):
                                break

            return True, "".join(output_parts)
        except Exception as e:
            return False, f"Responses API stream read error: {e}"

    @classmethod
    def prepare_chat_completions_messages(
        cls, system_prompt: str, history: List[ChatHistoryItem]
    ) -> List[dict]:
        """
        Sanitize and format chat history for OpenAI / DeepSeek Chat Completions API:
        1. Keep system prompt strictly static as message 0 (Prefix Cache anchor).
        2. Map 'assistant' -> 'assistant', everything else -> 'user'.
        3. Merge consecutive turns of the same role to maintain strict alternation.
        4. Ensure sequence starts with 'user' turn after system prompt and does not end with 'assistant'.
        """
        messages: List[dict] = []
        if system_prompt and system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt.strip()})

        user_or_assistant: List[dict] = []
        for item in history:
            role = "assistant" if item.role == "assistant" else "user"
            content = (item.content or "").strip()
            if not content:
                continue

            if not user_or_assistant:
                user_or_assistant.append({"role": role, "content": content})
            else:
                if user_or_assistant[-1]["role"] == role:
                    prev_content = user_or_assistant[-1]["content"]
                    if isinstance(prev_content, str):
                        user_or_assistant[-1]["content"] = f"{prev_content}\n\n{content}"
                    else:
                        user_or_assistant.append({"role": role, "content": content})
                else:
                    user_or_assistant.append({"role": role, "content": content})

        while user_or_assistant and user_or_assistant[0]["role"] == "assistant":
            user_or_assistant.pop(0)

        if not user_or_assistant:
            user_or_assistant.append({"role": "user", "content": "Please proceed."})

        if user_or_assistant[-1]["role"] == "assistant":
            user_or_assistant.append({"role": "user", "content": "Please proceed."})

        messages.extend(user_or_assistant)
        return messages

    @classmethod
    def _call_chat_completions_fallback_sync(
        cls,
        vendor: VendorProfile,
        base_url: str,
        system_prompt: str,
        history: List[ChatHistoryItem],
        on_chunk: Callable[[str], None],
        media_items: Optional[List[dict]] = None,
        thinking_intensity: str = "high",
    ) -> Tuple[bool, str]:
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"

        messages = cls.prepare_chat_completions_messages(system_prompt, history)

        if media_items and messages:
            last_msg = None
            for m_item in reversed(messages):
                if m_item.get("role") == "user":
                    last_msg = m_item
                    break
            if not last_msg:
                last_msg = messages[-1]

            raw_text = last_msg.get("content", "")
            if isinstance(raw_text, str):
                content_blocks = []
                if raw_text:
                    content_blocks.append({"type": "text", "text": raw_text})
                for m in media_items:
                    if m["mediaKind"] == "image":
                        content_blocks.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{m['mimeType']};base64,{m['base64']}"
                            }
                        })
                if any(b.get("type") == "image_url" for b in content_blocks):
                    last_msg["content"] = content_blocks

        payload: dict = {
            "model": vendor.selected_model,
            "messages": messages,
            "stream": True,
            "temperature": 0.2,
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "Metis-Agent/1.3",
        }
        if vendor.api_key and vendor.api_key.strip():
            headers["Authorization"] = f"Bearer {vendor.api_key.strip()}"

        ctx = ssl.create_default_context()

        try:
            data_bytes = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=180, context=ctx) as stream_resp:
                output_parts: List[str] = []

                for raw_line in stream_resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    json_str = line[5:].strip()
                    if json_str == "[DONE]":
                        break
                    try:
                        v = json.loads(json_str)
                    except Exception:
                        continue

                    choices = v.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    delta = choices[0].get("delta")
                    if not isinstance(delta, dict):
                        continue

                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        output_parts.append(content)
                        on_chunk(content)

                    finish_reason = choices[0].get("finish_reason")
                    if finish_reason and finish_reason in ("stop", "length", "tool_calls", "content_filter"):
                        break

                return True, "".join(output_parts)
        except urllib.error.HTTPError as he:
            body = ""
            try:
                body = he.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            return False, f"LLM Provider Error (HTTP {he.code}): {body}"
        except Exception as e:
            return False, f"Stream read error: {e}"

    # Backward compatibility alias
    _call_openai_compatible_sync = _call_responses_api_sync

    @classmethod
    def prepare_claude_messages(cls, history: List[ChatHistoryItem]) -> List[dict]:
        """
        Sanitize and format chat history for Claude API:
        1. Map 'assistant' -> 'assistant', everything else -> 'user'.
        2. Merge consecutive turns of the same role.
        3. Ensure the sequence starts with a 'user' turn.
        4. Ensure the sequence does not end with an 'assistant' turn (prevents thinking mode prefill errors).
        """
        messages: List[dict] = []
        for item in history:
            role = "assistant" if item.role == "assistant" else "user"
            text = (item.content or "").strip()
            if not text:
                continue

            if not messages:
                messages.append({"role": role, "content": text})
            else:
                if messages[-1]["role"] == role:
                    prev_text = messages[-1]["content"]
                    messages[-1]["content"] = f"{prev_text}\n\n{text}" if prev_text else text
                else:
                    messages.append({"role": role, "content": text})

        while messages and messages[0]["role"] == "assistant":
            messages.pop(0)

        if not messages:
            messages.append({"role": "user", "content": "Please proceed."})

        if messages[-1]["role"] == "assistant":
            messages.append({"role": "user", "content": "Please proceed."})

        return messages

    @classmethod
    def prepare_gemini_contents(cls, history: List[ChatHistoryItem]) -> List[dict]:
        """
        Sanitize and format chat history for Gemini API:
        1. Map 'assistant' -> 'model', everything else -> 'user'.
        2. Merge consecutive turns of the same role.
        3. Ensure the sequence starts with a 'user' turn.
        4. Ensure the sequence does NOT end with a 'model' turn (Gemini strictly rejects
           requests ending with a model turn with HTTP 400).
        """
        raw_turns: List[dict] = []
        for item in history:
            role = "model" if item.role == "assistant" else "user"
            text = (item.content or "").strip()
            if not text:
                continue

            if not raw_turns:
                raw_turns.append({"role": role, "parts": [{"text": text}]})
            else:
                if raw_turns[-1]["role"] == role:
                    prev_text = raw_turns[-1]["parts"][0].get("text", "")
                    merged_text = f"{prev_text}\n\n{text}" if prev_text else text
                    raw_turns[-1]["parts"] = [{"text": merged_text}]
                else:
                    raw_turns.append({"role": role, "parts": [{"text": text}]})

        while raw_turns and raw_turns[0]["role"] == "model":
            raw_turns.pop(0)

        if not raw_turns:
            raw_turns.append({"role": "user", "parts": [{"text": "Please proceed."}]})

        if raw_turns[-1]["role"] == "model":
            raw_turns.append({"role": "user", "parts": [{"text": "Please proceed."}]})

        return raw_turns

    @staticmethod
    def build_claude_tools() -> List[dict]:
        """
        Claude native server-side Web Search specification:
        Allows Claude to perform live web research dynamically when needed without forcing it.
        """
        return [
            {
                "type": "web_search_20250101",
                "name": "web_search",
            }
        ]

    @classmethod
    def _call_claude_sync(
        cls,
        vendor: VendorProfile,
        base_url: str,
        system_prompt: str,
        history: List[ChatHistoryItem],
        thinking_intensity: str,
        on_chunk: Callable[[str], None],
        media_items: Optional[List[dict]] = None,
    ) -> Tuple[bool, str]:
        url = base_url if base_url.endswith("/messages") else f"{base_url}/messages"

        messages = cls.prepare_claude_messages(history)
        has_pdf = False
        if media_items and messages:
            last_msg = messages[-1]
            raw_text = last_msg.get("content", "")
            blocks = []
            if isinstance(raw_text, str) and raw_text:
                blocks.append({"type": "text", "text": raw_text})
            elif isinstance(raw_text, list):
                blocks.extend(raw_text)

            for item in media_items:
                if item["mediaKind"] == "image":
                    blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": item["mimeType"],
                            "data": item["base64"],
                        },
                    })
                elif item["mediaKind"] == "pdf":
                    has_pdf = True
                    blocks.append({
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": item["base64"],
                        },
                    })
            if blocks:
                last_msg["content"] = blocks

        intensity = (thinking_intensity or "high").lower()
        budget_tokens = {
            "low": 2048,
            "medium": 8000,
            "high": 16000,
            "max": 32000,
        }.get(intensity, 16000)

        payload = {
            "model": vendor.selected_model,
            "max_tokens": 8192,
            "system": system_prompt,
            "messages": messages,
            "stream": True,
            "tools": cls.build_claude_tools(),
        }
        if budget_tokens > 0 and "3-7" in vendor.selected_model:
            payload["max_tokens"] = max(8192, budget_tokens + 4096)
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget_tokens,
            }

        headers = {
            "Content-Type": "application/json",
            "x-api-key": vendor.api_key.strip(),
            "anthropic-version": "2023-06-01",
            "Accept": "text/event-stream",
            "User-Agent": "Metis-Agent/1.3",
        }
        if has_pdf:
            headers["anthropic-beta"] = "pdfs-2024-09-25"

        ctx = ssl.create_default_context()

        def open_claude_stream(req_payload: dict):
            data_bytes = json.dumps(req_payload).encode("utf-8")
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            return urllib.request.urlopen(req, timeout=180, context=ctx)

        resp = None
        max_attempts = 2
        last_error = ""

        for attempt in range(max_attempts):
            try:
                resp = open_claude_stream(payload)
                break
            except urllib.error.HTTPError as he:
                body = ""
                try:
                    body = he.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                body_lower = body.lower()
                last_error = f"Claude API Error (HTTP {he.code}): {body}"

                if "tools" in payload and (
                    he.code in (400, 403, 404, 429)
                    or any(term in body_lower for term in ("tool", "web_search", "unknown", "invalid", "unsupported", "quota", "exhausted"))
                ):
                    payload.pop("tools", None)
                    continue

                return False, last_error
            except Exception as e:
                return False, f"HTTP request error: {e}"
        else:
            return False, last_error

        try:
            with resp:
                full_output: List[str] = []

                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    json_str = line[5:].strip()
                    try:
                        v = json.loads(json_str)
                    except Exception:
                        continue

                    ev_type = v.get("type")
                    if ev_type in ("message_stop", "done"):
                        break

                    if ev_type == "content_block_delta":
                        delta = v.get("delta", {})
                        if isinstance(delta, dict):
                            dtype = delta.get("type", "")
                            if dtype == "thinking_delta":
                                continue
                            txt = delta.get("text", "")
                            if txt:
                                full_output.append(txt)
                                on_chunk(txt)

                return True, "".join(full_output)
        except urllib.error.HTTPError as he:
            body = ""
            try:
                body = he.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            return False, f"Claude API Error (HTTP {he.code}): {body}"
        except Exception as e:
            return False, f"HTTP request error: {e}"

    @staticmethod
    def build_gemini_generation_config(clean_model: str, thinking_intensity: str) -> Tuple[dict, bool]:
        intensity = (thinking_intensity or "medium").lower()
        budget_tokens = {
            "low": 2048,
            "medium": 8192,
            "high": 24576,
            "max": 24576,
        }.get(intensity, 8192)

        gen_config: dict = {"temperature": 0.2}
        supports_thinking = "1.5" not in clean_model
        if supports_thinking:
            gen_config["thinkingConfig"] = {"thinkingBudget": budget_tokens}
        return gen_config, supports_thinking

    @staticmethod
    def build_gemini_tools() -> List[dict]:
        """
        Gemini native Google Search Grounding tool specification:
        Allows Gemini to dynamically assess whether search is required without forcing it.
        """
        return [
            {
                "google_search": {}
            }
        ]

    @classmethod
    def _call_gemini_sync(
        cls,
        vendor: VendorProfile,
        base_url: str,
        system_prompt: str,
        history: List[ChatHistoryItem],
        thinking_intensity: str,
        on_chunk: Callable[[str], None],
        media_items: Optional[List[dict]] = None,
    ) -> Tuple[bool, str]:
        raw_model = vendor.selected_model.strip()
        clean_model = raw_model[7:] if raw_model.startswith("models/") else (raw_model or "gemini-2.5-flash")

        api_key = vendor.api_key.strip()
        if not api_key:
            url = f"{base_url}/models/{clean_model}:streamGenerateContent?alt=sse"
        else:
            url = f"{base_url}/models/{clean_model}:streamGenerateContent?alt=sse&key={api_key}"

        contents = cls.prepare_gemini_contents(history)
        if media_items and contents:
            for item in media_items:
                contents[-1]["parts"].append({
                    "inlineData": {
                        "mimeType": item["mimeType"],
                        "data": item["base64"],
                    }
                })

        gen_config, supports_thinking = cls.build_gemini_generation_config(clean_model, thinking_intensity)

        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": gen_config,
            "tools": cls.build_gemini_tools(),
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "Metis-Agent/1.3",
        }
        if api_key:
            headers["x-goog-api-key"] = api_key

        ctx = ssl.create_default_context()

        def open_gemini_stream(req_payload: dict):
            data_bytes = json.dumps(req_payload).encode("utf-8")
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            return urllib.request.urlopen(req, timeout=180, context=ctx)

        resp = None
        max_attempts = 3
        last_error = ""

        for attempt in range(max_attempts):
            try:
                resp = open_gemini_stream(payload)
                break
            except urllib.error.HTTPError as he:
                body = ""
                try:
                    body = he.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                body_lower = body.lower()
                last_error = f"Gemini API Error (HTTP {he.code}): {body}"

                # Step 1: If tools (google_search) caused 429 (quota / free tier limit), 400, 403, 404, or grounding error, drop tools
                if "tools" in payload and (
                    he.code in (429, 400, 403, 404)
                    or any(term in body_lower for term in ("tools", "google_search", "googlesearch", "grounding", "quota", "exhausted", "resource_exhausted", "rate", "unsupported", "permission"))
                ):
                    payload.pop("tools", None)
                    continue

                # Step 2: If thinkingConfig not supported (400, 404), drop thinkingConfig
                if supports_thinking and "thinkingConfig" in payload.get("generationConfig", {}) and (
                    he.code in (400, 404)
                    or any(term in body_lower for term in ("thinkingconfig", "thinkingbudget", "not supported", "unknown field", "unrecognized"))
                ):
                    payload["generationConfig"] = {"temperature": 0.2}
                    continue

                return False, last_error
            except Exception as e:
                return False, f"HTTP request error: {e}"
        else:
            return False, last_error

        try:
            with resp:
                full_output: List[str] = []

                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    json_str = line[5:].strip()
                    try:
                        v = json.loads(json_str)
                    except Exception:
                        continue

                    candidates = v.get("candidates")
                    if not isinstance(candidates, list) or not candidates:
                        continue
                    content_obj = candidates[0].get("content")
                    if not isinstance(content_obj, dict):
                        continue
                    parts = content_obj.get("parts")
                    if not isinstance(parts, list):
                        continue

                    for part in parts:
                        if not isinstance(part, dict):
                            continue
                        if bool(part.get("thought", False)):
                            continue
                        txt = part.get("text")
                        if isinstance(txt, str) and txt:
                            full_output.append(txt)
                            on_chunk(txt)

                    finish_reason = candidates[0].get("finishReason")
                    if finish_reason and finish_reason in ("STOP", "MAX_TOKENS", "SAFETY", "RECITATION"):
                        break

                return True, "".join(full_output)
        except Exception as e:
            return False, f"Stream read error: {e}"

    @staticmethod
    def strip_leading_think(text: str) -> str:
        """
        Strips a leading <think>...</think> block (or an unclosed leading <think>... during streaming)
        anchored strictly to the start of the assistant response. Never strips inline <think> mentions
        in the middle of the assistant's reply.
        """
        m_open = LEADING_THINK_OPEN_RE.match(text)
        if not m_open:
            return text

        prefix = m_open.group(1) or ""
        rest = text[m_open.end():]

        m_close = THINK_CLOSE_BEFORE_TITLE_RE.search(rest)
        if not m_close:
            m_close = THINK_CLOSE_LINE_RE.search(rest)
        if not m_close:
            m_close = THINK_CLOSE_ANY_RE.search(rest)

        if m_close:
            return prefix + rest[m_close.end():]
        # Still streaming the leading <think> block (unclosed)
        return prefix

    @staticmethod
    def strip_leading_title(text: str) -> str:
        """
        Strips a leading <metis_title>...</metis_title> tag (or unclosed on the first line during streaming)
        anchored strictly to the start of the assistant response.
        """
        cleaned = LEADING_TITLE_STRIP_RE.sub(r"\1", text, count=1)
        cleaned = LEADING_UNCLOSED_TITLE_RE.sub(r"\1", cleaned, count=1)
        return cleaned

    @classmethod
    def extract_session_title(cls, raw_output: str) -> Optional[str]:
        cleaned = cls.strip_leading_think(raw_output)
        m = LEADING_TITLE_EXTRACT_RE.match(cleaned)
        if m:
            first_line = m.group(1).strip().splitlines()[0] if m.group(1).strip() else ""
            title = first_line.strip().strip("\"'`#*").strip()
            if title:
                return f"{title[:40]}..." if len(title) > 40 else title
        return None

    @staticmethod
    def _scan_agent_blocks(text: str) -> Tuple[str, List[Tuple[str, str, str]]]:
        """
        Fence-aware top-level line scanner for <metis_replace> and <metis_write>:
        - Skips Markdown fenced code blocks (``` or ~~~) opened at top level so example tags
          inside code blocks are neither executed as file edits nor stripped from display.
        - Extracts only top-level closed <metis_replace file="...">...</metis_replace> and
          <metis_write file="...">...</metis_write> blocks into `blocks`.
        - Removes top-level edit blocks (including unclosed top-level blocks at EOF while streaming)
          from `visible_text`.
        """
        lines = text.splitlines(keepends=True)
        in_fence = False
        fence_char = ""
        fence_len = 0

        active_tag: Optional[str] = None
        active_file: str = ""
        active_body: List[str] = []

        visible_lines: List[str] = []
        blocks: List[Tuple[str, str, str]] = []

        for idx, line in enumerate(lines):
            line_stripped = line.rstrip("\r\n")

            if active_tag is not None:
                if active_tag == "replace" and CLOSE_REPLACE_RE.match(line_stripped):
                    body_str = "".join(active_body)
                    if body_str.endswith("\r\n"):
                        body_str = body_str[:-2]
                    elif body_str.endswith("\n"):
                        body_str = body_str[:-1]
                    blocks.append(("replace", active_file, body_str))
                    active_tag = None
                    active_file = ""
                    active_body = []
                elif active_tag == "write" and CLOSE_WRITE_RE.match(line_stripped):
                    body_str = "".join(active_body)
                    if body_str.endswith("\r\n"):
                        body_str = body_str[:-2]
                    elif body_str.endswith("\n"):
                        body_str = body_str[:-1]
                    blocks.append(("write", active_file, body_str))
                    active_tag = None
                    active_file = ""
                    active_body = []
                else:
                    active_body.append(line)
                continue

            if in_fence:
                close_fence_re = re.compile(
                    r"^[ \t]*" + re.escape(fence_char) + r"{" + str(fence_len) + r",}[ \t]*$"
                )
                if close_fence_re.match(line_stripped):
                    in_fence = False
                    fence_char = ""
                    fence_len = 0
                visible_lines.append(line)
                continue

            m_fence = FENCE_OPEN_RE.match(line_stripped)
            if m_fence:
                marker = m_fence.group(1)
                in_fence = True
                fence_char = marker[0]
                fence_len = len(marker)
                visible_lines.append(line)
                continue

            m_single = SINGLE_LINE_TAG_RE.match(line_stripped)
            if m_single:
                kind = m_single.group(1).lower()
                file_attr = (m_single.group(2) or m_single.group(3) or m_single.group(4) or "").strip()
                body_str = m_single.group(5) or ""
                if file_attr:
                    blocks.append((kind, file_attr, body_str))
                continue

            m_open = OPEN_TAG_RE.match(line_stripped)
            if m_open:
                file_attr = (m_open.group(2) or m_open.group(3) or m_open.group(4) or "").strip()
                if file_attr:
                    active_tag = m_open.group(1).lower()
                    active_file = file_attr
                    active_body = []
                    continue

            if idx == len(lines) - 1 and STREAMING_PARTIAL_TAG_RE.match(line_stripped):
                continue

            visible_lines.append(line)

        return "".join(visible_lines), blocks

    @classmethod
    def parse_agent_output(cls, raw_output: str, whitelist: List[WhitelistedFile]) -> ParsedAgentResponse:
        parsed = ParsedAgentResponse(raw_text=raw_output)

        cleaned = cls.strip_leading_think(raw_output)
        cleaned = cls.strip_leading_title(cleaned)
        visible_text, blocks = cls._scan_agent_blocks(cleaned)

        for kind, file_attr, body in blocks:
            if not file_attr:
                continue
            if kind == "replace":
                for sr in SR_BLOCK_RE.finditer(body):
                    search = sr.group(1) or ""
                    replace = sr.group(2) or ""
                    parsed.file_edits.append(
                        FileEdit(
                            action_type=FileEditActionType.SearchReplace,
                            target_file=file_attr,
                            new_content="",
                            search_block=search,
                            replace_block=replace,
                        )
                    )
            elif kind == "write":
                if not any(e.target_file.lower() == file_attr.lower() for e in parsed.file_edits):
                    parsed.file_edits.append(
                        FileEdit(
                            action_type=FileEditActionType.WriteFull,
                            target_file=file_attr,
                            new_content=body,
                            search_block="",
                            replace_block="",
                        )
                    )

        cleaned_display = re.sub(r"<[｜|]{1,2}\s*DSML[\s\S]*$", "", visible_text)
        cleaned_display = re.sub(r"\n{3,}", "\n\n", cleaned_display)
        parsed.explanation_text = cleaned_display.strip()
        return parsed

    @classmethod
    def format_display_text(cls, text: str) -> str:
        cleaned = cls.strip_leading_think(text)
        cleaned = cls.strip_leading_title(cleaned)
        visible_text, _ = cls._scan_agent_blocks(cleaned)
        cleaned_display = re.sub(r"<[｜|]{1,2}\s*DSML[\s\S]*$", "", visible_text)
        cleaned_display = re.sub(r"\n{3,}", "\n\n", cleaned_display)
        return cleaned_display.strip()

    @staticmethod
    async def fetch_ollama_models(base_url: str) -> List[str]:
        def _fetch() -> List[str]:
            clean = base_url.rstrip("/")
            if clean.endswith("/v1"):
                clean = clean[:-3]
            url = f"{clean}/api/tags" if not clean.endswith("/api") else f"{clean}/tags"
            for candidate_url in (url, f"{base_url.rstrip('/').removesuffix('/v1')}/tags"):
                try:
                    req = urllib.request.Request(candidate_url, method="GET")
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        data = json.loads(resp.read().decode("utf-8", errors="replace"))
                        models = data.get("models", [])
                        if isinstance(models, list):
                            names = [
                                str(m.get("name"))
                                for m in models
                                if isinstance(m, dict) and m.get("name")
                            ]
                            if names:
                                return names
                except Exception:
                    continue
            return []

        return await asyncio.to_thread(_fetch)
