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
    ParsedAgentResponse,
    SurgicalActionType,
    SurgicalFileEdit,
    VendorProfile,
    VendorType,
    WhitelistedFile,
)


THINK_RE = re.compile(r"<think\b[^>]*>.*?</think>\s*", re.IGNORECASE | re.DOTALL)
UNCLOSED_THINK_RE = re.compile(r"<think\b[^>]*>.*$", re.IGNORECASE | re.DOTALL)
ORPHAN_THINK_RE = re.compile(r"<\/?(?:think|thought|reasoning)\b[^>]*>\s*", re.IGNORECASE)
TITLE_EXTRACT_RE = re.compile(
    r"<(?:metis|session|chat)_title\b[^>]*>(.*?)</(?:metis|session|chat)_title>",
    re.IGNORECASE | re.DOTALL,
)
TITLE_STRIP_RE = re.compile(
    r"<(?:metis|session|chat)_title\b[^>]*>.*?</(?:metis|session|chat)_title>\s*",
    re.IGNORECASE | re.DOTALL,
)
UNCLOSED_TITLE_RE = re.compile(
    r"<(?:metis|session|chat)_title\b[^>]*>.*$",
    re.IGNORECASE | re.DOTALL,
)

REPLACE_TAG_RE = re.compile(
    r"<(?:metis|surgical|pinpoint)_replace(?:\s+(?:file|path|name)?\s*=?\s*[\"']?([^\"'>\r\n]+)[\"']?)?\s*>\r?\n?(.*?)(?:</(?:metis|surgical|pinpoint)_replace>|$)",
    re.IGNORECASE | re.DOTALL,
)
WRITE_TAG_RE = re.compile(
    r"<(?:metis|surgical|pinpoint)_write(?:\s+(?:file|path|name)?\s*=?\s*[\"']?([^\"'>\r\n]+)[\"']?)?\s*>\r?\n?(.*?)(?:</(?:metis|surgical|pinpoint)_write>|$)",
    re.IGNORECASE | re.DOTALL,
)
SR_BLOCK_RE = re.compile(
    r"<{5,}\s*SEARCH\r?\n((?:(?!={5,}|<{5,}\s*SEARCH)[\s\S])*?)\r?\n={5,}(?:\r?\n((?:(?!>{5,}\s*REPLACE|={5,})[\s\S])*?))?\r?\n>{5,}\s*REPLACE",
    re.IGNORECASE,
)
RAW_SR_STREAMING_RE = re.compile(r"<{5,}\s*SEARCH\b.*$", re.IGNORECASE | re.DOTALL)
CMD_TAG_RE = re.compile(
    r"<(?:metis|surgical|pinpoint)_(?:cmd|terminal|verify)[^>]*>\r?\n?(.*?)\r?\n?</(?:metis|surgical|pinpoint)_(?:cmd|terminal|verify)>\r?\n?",
    re.IGNORECASE | re.DOTALL,
)
CMD_STREAMING_RE = re.compile(
    r"<(?:metis|surgical|pinpoint)_(?:cmd|terminal|verify)[^>]*>.*$",
    re.IGNORECASE | re.DOTALL,
)
DSML_CMD_RE = re.compile(
    r'''<[｜|]{1,2}\s*DSML\s*[｜|]{1,2}\s*invoke\s+name=["']?(?:metis|pinpoint|surgical)?_?(?:cmd|terminal|verify)["']?[^>]*>([\s\S]*?)(?:<[｜|]{1,2}\s*/?\s*DSML|\Z)''',
    re.IGNORECASE,
)
SEARCH_TAG_RE = re.compile(
    r"<(?:metis|surgical|pinpoint)?_?search[^>]*>\r?\n?(.*?)\r?\n?</(?:metis|surgical|pinpoint)?_?search>",
    re.IGNORECASE | re.DOTALL,
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

        if vendor.vendor_type == VendorType.LocalSandbox:
            return await cls._run_local_sandbox(files_snapshot or "", chat_history, file_list, chunk_cb)

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
                )
            else:
                return False, f"Responses API Error (HTTP {he.code}): {body}"
        except Exception as e:
            return False, f"HTTP request error: {e}"

        output_parts: List[str] = []
        is_in_reasoning = False
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
                    if event_type in (
                        "response.reasoning_text.delta",
                        "response.reasoning_summary_text.delta",
                        "response.reasoning.delta",
                    ):
                        delta = v.get("delta")
                        reasoning_txt = delta if isinstance(delta, str) else (delta.get("text") if isinstance(delta, dict) else "")
                        if reasoning_txt:
                            if not is_in_reasoning:
                                is_in_reasoning = True
                                output_parts.append("<think>")
                                on_chunk("<think>")
                            output_parts.append(reasoning_txt)
                            on_chunk(reasoning_txt)

                    elif event_type in (
                        "response.reasoning_text.done",
                        "response.reasoning_summary_text.done",
                        "response.reasoning.done",
                    ):
                        if is_in_reasoning:
                            is_in_reasoning = False
                            output_parts.append("</think>\n")
                            on_chunk("</think>\n")

                    # 3. Output text deltas
                    elif event_type in (
                        "response.output_text.delta",
                        "response.text.delta",
                    ):
                        delta = v.get("delta")
                        content_txt = delta if isinstance(delta, str) else (delta.get("text") if isinstance(delta, dict) else "")
                        if content_txt:
                            if is_in_reasoning:
                                is_in_reasoning = False
                                output_parts.append("</think>\n")
                                on_chunk("</think>\n")
                            output_parts.append(content_txt)
                            on_chunk(content_txt)

                    # 4. Standard choices fallback if proxy wraps completion chunks
                    elif "choices" in v:
                        choices = v.get("choices")
                        if isinstance(choices, list) and choices:
                            c_delta = choices[0].get("delta", {})
                            if isinstance(c_delta, dict):
                                content = c_delta.get("content")
                                reasoning = c_delta.get("reasoning_content") or c_delta.get("reasoning")
                                if isinstance(content, str) and content:
                                    if is_in_reasoning:
                                        is_in_reasoning = False
                                        output_parts.append("</think>\n")
                                        on_chunk("</think>\n")
                                    output_parts.append(content)
                                    on_chunk(content)
                                elif isinstance(reasoning, str) and reasoning:
                                    if not is_in_reasoning:
                                        is_in_reasoning = True
                                        output_parts.append("<think>")
                                        on_chunk("<think>")
                                    output_parts.append(reasoning)
                                    on_chunk(reasoning)

                            finish_reason = choices[0].get("finish_reason")
                            if finish_reason and finish_reason in ("stop", "length", "tool_calls", "content_filter"):
                                break

            if is_in_reasoning:
                output_parts.append("</think>\n")
                on_chunk("</think>\n")

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
                is_in_reasoning = False

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
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if isinstance(content, str) and content:
                        if is_in_reasoning:
                            is_in_reasoning = False
                            output_parts.append("</think>\n")
                            on_chunk("</think>\n")
                        output_parts.append(content)
                        on_chunk(content)
                    elif isinstance(reasoning, str) and reasoning:
                        if not is_in_reasoning:
                            is_in_reasoning = True
                            output_parts.append("<think>")
                            on_chunk("<think>")
                        output_parts.append(reasoning)
                        on_chunk(reasoning)

                    finish_reason = choices[0].get("finish_reason")
                    if finish_reason and finish_reason in ("stop", "length", "tool_calls", "content_filter"):
                        break

                if is_in_reasoning:
                    output_parts.append("</think>\n")
                    on_chunk("</think>\n")

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

        intensity = (thinking_intensity or "medium").lower()
        budget_tokens = {
            "low": 2048,
            "medium": 6000,
            "high": 16000,
        }.get(intensity, 6000)

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
                is_in_thought = False

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
                                thinking_txt = delta.get("thinking", "")
                                if thinking_txt:
                                    if not is_in_thought:
                                        is_in_thought = True
                                        full_output.append("<think>")
                                        on_chunk("<think>")
                                    full_output.append(thinking_txt)
                                    on_chunk(thinking_txt)
                            else:
                                txt = delta.get("text", "")
                                if txt:
                                    if is_in_thought:
                                        is_in_thought = False
                                        full_output.append("</think>\n")
                                        on_chunk("</think>\n")
                                    full_output.append(txt)
                                    on_chunk(txt)

                if is_in_thought:
                    full_output.append("</think>\n")
                    on_chunk("</think>\n")

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
                is_in_thought = False

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
                        is_thought = bool(part.get("thought", False))
                        txt = part.get("text")
                        if isinstance(txt, str):
                            if is_thought:
                                if not is_in_thought:
                                    is_in_thought = True
                                    full_output.append("<think>")
                                    on_chunk("<think>")
                            elif is_in_thought:
                                is_in_thought = False
                                full_output.append("</think>\n")
                                on_chunk("</think>\n")
                            full_output.append(txt)
                            on_chunk(txt)

                    finish_reason = candidates[0].get("finishReason")
                    if finish_reason and finish_reason in ("STOP", "MAX_TOKENS", "SAFETY", "RECITATION"):
                        break

                if is_in_thought:
                    full_output.append("</think>\n")
                    on_chunk("</think>\n")

                return True, "".join(full_output)
        except Exception as e:
            return False, f"Stream read error: {e}"

    @staticmethod
    def extract_session_title(raw_output: str) -> Optional[str]:
        m = TITLE_EXTRACT_RE.search(raw_output)
        if m:
            first_line = m.group(1).strip().splitlines()[0] if m.group(1).strip() else ""
            cleaned = first_line.strip().strip("\"'`#*").strip()
            if cleaned:
                return f"{cleaned[:40]}..." if len(cleaned) > 40 else cleaned
        return None

    @classmethod
    def parse_agent_output(cls, raw_output: str, whitelist: List[WhitelistedFile]) -> ParsedAgentResponse:
        parsed = ParsedAgentResponse(raw_text=raw_output)

        cleaned_think = THINK_RE.sub("", raw_output)
        cleaned_think = UNCLOSED_THINK_RE.sub("", cleaned_think)
        cleaned_think = ORPHAN_THINK_RE.sub("", cleaned_think)
        cleaned = TITLE_STRIP_RE.sub("", cleaned_think)

        # 1. Parse <metis_replace file="...">
        for cap in REPLACE_TAG_RE.finditer(cleaned):
            file_attr = (cap.group(1) or "").strip()
            body = cap.group(2) or ""
            for sr in SR_BLOCK_RE.finditer(body):
                search = sr.group(1) or ""
                replace = sr.group(2) or ""
                parsed.file_edits.append(
                    SurgicalFileEdit(
                        action_type=SurgicalActionType.SearchReplace,
                        target_file=file_attr,
                        new_content="",
                        search_block=search,
                        replace_block=replace,
                    )
                )

        # 2. Parse <metis_write file="..."> (for empty files or complete rewrites)
        for cap in WRITE_TAG_RE.finditer(cleaned):
            file_attr = (cap.group(1) or "").strip()
            body = cap.group(2) or ""
            if not any(e.target_file.lower() == file_attr.lower() for e in parsed.file_edits):
                parsed.file_edits.append(
                    SurgicalFileEdit(
                        action_type=SurgicalActionType.WriteFull,
                        target_file=file_attr,
                        new_content=body,
                        search_block="",
                        replace_block="",
                    )
                )

        # 3. Parse raw SEARCH/REPLACE blocks outside tags
        for sr in SR_BLOCK_RE.finditer(cleaned):
            search = sr.group(1) or ""
            replace = sr.group(2) or ""
            already = any(
                e.search_block == search and e.replace_block == replace for e in parsed.file_edits
            )
            if not already:
                target_file = whitelist[0].file_name if whitelist else ""
                parsed.file_edits.append(
                    SurgicalFileEdit(
                        action_type=SurgicalActionType.SearchReplace,
                        target_file=target_file,
                        new_content="",
                        search_block=search,
                        replace_block=replace,
                    )
                )

        parsed.explanation_text = cls.format_display_text(raw_output)
        return parsed

    @staticmethod
    def format_display_text(text: str) -> str:
        step1 = THINK_RE.sub("", text)
        step2 = UNCLOSED_THINK_RE.sub("", step1)
        step2 = ORPHAN_THINK_RE.sub("", step2)
        step3 = TITLE_STRIP_RE.sub("", step2)
        step4 = UNCLOSED_TITLE_RE.sub("", step3)
        cleaned = SEARCH_TAG_RE.sub("", step4)
        cleaned = REPLACE_TAG_RE.sub("", cleaned)
        cleaned = WRITE_TAG_RE.sub("", cleaned)
        cleaned = SR_BLOCK_RE.sub("", cleaned)
        cleaned = RAW_SR_STREAMING_RE.sub("", cleaned)
        cleaned = CMD_TAG_RE.sub("", cleaned)
        cleaned = CMD_STREAMING_RE.sub("", cleaned)
        cleaned = re.sub(r"<[｜|]{1,2}[\s\S]*?[｜|]{1,2}>", "", cleaned)
        cleaned = re.sub(r"<[｜|]{1,2}[\s\S]*$", "", cleaned)
        cleaned = re.sub(r"<result\b[^>]*>[\s\S]*?<\/result>", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"<result\b[^>]*>[\s\S]*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

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

    @staticmethod
    async def _run_local_sandbox(
        _files_snapshot: str,
        history: List[ChatHistoryItem],
        whitelist: List[WhitelistedFile],
        on_chunk: Callable[[str], None],
    ) -> Tuple[bool, str]:
        await asyncio.sleep(0.15)
        last_user_msg = ""
        for m in reversed(history):
            if m.role == "user":
                last_user_msg = m.content
                break

        if last_user_msg.startswith("[Terminal Execution Feedback") or "Exit Code: 0" in last_user_msg:
            summary = "✅ Terminal execution completed (Exit Code 0). Output matches expectations, all tests verified successfully!"
            on_chunk(summary)
            return True, summary

        if not whitelist:
            msg = "<metis_title>Workspace File Setup</metis_title>\nNo files are currently attached to this workspace. Please drop files to edit."
            on_chunk(msg)
            return True, msg

        target = whitelist[0]
        p = Path(target.full_path)
        if p.exists():
            try:
                code = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                code = target.last_saved_content
        else:
            code = target.last_saved_content

        parts = ["<metis_title>Fix Token Expiration Bug</metis_title>\n"]
        if "is_token_expired" in code:
            parts.append(f"Inspected whitelisted file `{target.file_name}`:\n")
            parts.append("- **Issue Identified**: Inverted operator in `is_token_expired`.\n")
            parts.append(f"<metis_replace file=\"{target.file_name}\">\n")
            parts.append("<<<<<<< SEARCH\n")
            parts.append("    return current_time < expires_at\n")
            parts.append("=======\n")
            parts.append("    return current_time >= expires_at\n")
            parts.append(">>>>>>> REPLACE\n")
            parts.append("</metis_replace>\n")
        else:
            parts.append(f"[Mock Sandbox Engine] Inspected `{target.file_name}`.\n")

        resp = "".join(parts)
        on_chunk(resp)
        return True, resp
