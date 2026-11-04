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
# Single source of truth for action-tag syntax.
# Invariant: one tag per line — the line, after trimming leading/trailing whitespace,
# must consist of exactly the tag and nothing else. A tag mentioned inside a sentence
# is plain text and is never executed.
ACTION_OPEN_RE = re.compile(
    r'^<metis_(?P<kind>replace|write|read)\s+(?:file|path|name)\s*=\s*"(?P<file>[^"]+)"\s*(?P<self>/?)>$',
    re.IGNORECASE,
)
ACTION_CLOSE_RE = re.compile(
    r'^</metis_(?P<kind>replace|write|read)\s*>$',
    re.IGNORECASE,
)

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
                    "relativePath": f.relative_path or f.file_name,
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
                    rel_label = m.get("relativePath") or m["fileName"]
                    if m["mediaKind"] == "image":
                        content_blocks.append({
                            "type": "input_text",
                            "text": f"[Attached Base64 Image: {rel_label}]",
                        })
                        content_blocks.append({
                            "type": "input_image",
                            "image_url": f"data:{m['mimeType']};base64,{m['base64']}",
                        })
                    elif m["mediaKind"] == "pdf":
                        content_blocks.append({
                            "type": "input_text",
                            "text": f"[Attached Base64 PDF: {rel_label}]",
                        })
                        content_blocks.append({
                            "type": "input_file",
                            "filename": m["fileName"],
                            "file_data": f"data:application/pdf;base64,{m['base64']}",
                        })
                if any(b.get("type") in ("input_image", "input_file") for b in content_blocks):
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
                    rel_label = m.get("relativePath") or m["fileName"]
                    if m["mediaKind"] == "image":
                        content_blocks.append({
                            "type": "text",
                            "text": f"[Attached Base64 Image: {rel_label}]",
                        })
                        content_blocks.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{m['mimeType']};base64,{m['base64']}"
                            }
                        })
                    elif m["mediaKind"] == "pdf":
                        content_blocks.append({
                            "type": "text",
                            "text": f"[Attached Base64 PDF: {rel_label}]",
                        })
                        content_blocks.append({
                            "type": "file",
                            "file": {
                                "filename": m["fileName"],
                                "file_data": f"data:application/pdf;base64,{m['base64']}",
                            }
                        })
                if any(b.get("type") in ("image_url", "file") for b in content_blocks):
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

        def open_chat_stream(req_payload: dict):
            data_bytes = json.dumps(req_payload).encode("utf-8")
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            return urllib.request.urlopen(req, timeout=180, context=ctx)

        try:
            try:
                stream_resp = open_chat_stream(payload)
            except urllib.error.HTTPError as he_first:
                if media_items and he_first.code in (400, 415, 422):
                    # If the OpenAI-compatible provider does not support multimodal/PDF blocks, fallback to text messages
                    plain_messages = cls.prepare_chat_completions_messages(system_prompt, history)
                    payload["messages"] = plain_messages
                    stream_resp = open_chat_stream(payload)
                else:
                    raise he_first

            with stream_resp:
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
        Line-oriented scanner for action tags. One tag per line: a line is an action tag only
        when its entire content, after trimming surrounding whitespace, is exactly the tag.
        - Markdown fenced code blocks (``` or ~~~) are never scanned, so example tags inside
          them are neither executed nor stripped from display.
        - A tag mentioned inside a sentence is plain text, never an action.
        - Extracts <metis_replace file="...">...</metis_replace>, <metis_write file="...">
          ...</metis_write> blocks, and <metis_read file="..." /> one-liners into `blocks`,
          and removes the tag lines themselves from `visible_text`.
        - A block left unterminated at EOF is discarded entirely (never half-applied).
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
                m_close = ACTION_CLOSE_RE.match(line_stripped.strip())
                if m_close and m_close.group("kind").lower() == active_tag:
                    body_str = "".join(active_body)
                    if body_str.endswith("\r\n"):
                        body_str = body_str[:-2]
                    elif body_str.endswith("\n"):
                        body_str = body_str[:-1]
                    blocks.append((active_tag, active_file, body_str))
                    active_tag = None
                    active_file = ""
                    active_body = []
                else:
                    # Body lines are taken verbatim; nested tag-like text is file content.
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

            m_open = ACTION_OPEN_RE.match(line_stripped.strip())
            if m_open:
                kind = m_open.group("kind").lower()
                file_attr = (m_open.group("file") or "").strip()
                is_self_closing = m_open.group("self") == "/"
                if kind == "read":
                    if file_attr:
                        blocks.append(("read", file_attr, ""))
                    continue
                if is_self_closing:
                    # <metis_write .../> carries no body -> not an action, render as text.
                    visible_lines.append(line)
                    continue
                active_tag = kind
                active_file = file_attr
                active_body = []
                continue

            if ACTION_CLOSE_RE.match(line_stripped.strip()):
                # Stray closing tag outside any block: drop from display, do nothing.
                continue

            if idx == len(lines) - 1:
                pending = line_stripped.strip()
                if pending.startswith("<metis_") and not pending.endswith(">"):
                    # Half-written tag during streaming: show nothing, execute nothing.
                    continue

            visible_lines.append(line)

        if active_tag is not None:
            # Unterminated block at EOF: discard entirely, never half-apply.
            active_tag = None
            active_file = ""
            active_body = []

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
            if kind == "read":
                if not any(r.lower() == file_attr.lower() for r in parsed.file_reads):
                    parsed.file_reads.append(file_attr)
            elif kind == "replace":
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
