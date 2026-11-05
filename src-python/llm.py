from __future__ import annotations
import asyncio
import base64
import json
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from models import (
    ChatHistoryItem,
    FileEdit,
    FileEditActionType,
    FileReadRequest,
    FileSearchRequest,
    ParsedAgentResponse,
    VendorProfile,
    WhitelistedFile,
)


# 匹配开头可选的联网搜索提示前缀
LEADING_PREFIX_RE = re.compile(
    r"^(\s*(?:🔍 Searching web via vendor grounding\.\.\.\s*)?)",
    re.IGNORECASE,
)

# 匹配开头的 <think>...</think>（支持流式未闭合状态）
# 注意：不加行尾 $ 锚点，否则闭合块后若紧跟正文会回退到未闭合分支而吞掉正文。
LEADING_THINK_RE = re.compile(
    r"^<think\b[^>]*>(?:(?P<closed>[\s\S]*?)</think>|(?P<unclosed>[\s\S]*))[ \t]*(?:\r?\n)?",
    re.IGNORECASE,
)

# 匹配开头的标题标签 <metis_title>...</metis_title>（支持流式未闭合状态）
LEADING_TITLE_RE = re.compile(
    r"^<(?P<tag>metis|session|chat)_title\b[^>]*>(?:(?P<title>[^\r\n<]*)</(?P=tag)_title>|[^\r\n]*)[ \t]*(?:\r?\n)?",
    re.IGNORECASE,
)

# Markdown 围栏代码块（``` 或 ~~~），info 用于区分开/闭（闭合围栏必须无语言标识）
FENCE_RE = re.compile(r"^[ \t]*(?P<fence>`{3,}|~{3,})(?P<info>.*)$")

# 动作标签（整行独立匹配，兼容双引号/单引号、多属性、自闭合与单行闭合）
ACTION_OPEN_RE = re.compile(
    r"""^<(?P<tag>metis_(?:replace|write|read|search|grep)|read_file|search_file|write_file|replace_file|grep)\b(?P<attrs>(?:"[^"]*"|'[^']*'|[^>"'])*?)(?P<self>/?)>(?:(?P<inline_body>.*)</(?P=tag)\s*>)?$""",
    re.IGNORECASE,
)
ACTION_CLOSE_RE = re.compile(
    r"""^</(?P<tag>metis_(?:replace|write|read|search|grep)|read_file|search_file|write_file|replace_file|grep)\s*>$""",
    re.IGNORECASE,
)
ATTR_PAIR_RE = re.compile(
    r"""([a-zA-Z_][a-zA-Z0-9_-]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`/]+))"""
)

# 流式输出末尾可能出现的未闭合残缺标签前缀（如 <、<<、</、<metis、</metis_write...）
# 注意：末尾保证完整的标签行（含 '>'）永远不会被隐藏。
STREAMING_PARTIAL_TAG_RE = re.compile(
    r"""^(?:<+|</|<{0,2}/?(?:m(?:e(?:t(?:i(?:s(?:_[a-z]*)?)?)?)?)?|r(?:e(?:a(?:d(?:_file)?)?)?)?|s(?:e(?:a(?:r(?:c(?:h(?:_file)?)?)?)?)?)?|w(?:r(?:i(?:t(?:e(?:_file)?)?)?)?)?|g(?:r(?:e(?:p)?)?)?|think|session_title|chat_title)(?:\s+(?:"[^"]*"|'[^']*'|[^>])*)?)$""",
    re.IGNORECASE,
)

# Search / Replace 分隔符行匹配（状态机按行解析，彻底避免复杂正则回溯）
SR_SEARCH_START_RE = re.compile(r"^[ \t]*<{5,}\s*SEARCH[ \t]*$", re.IGNORECASE)
SR_DIVIDER_RE = re.compile(r"^[ \t]*={5,}[ \t]*$")
SR_REPLACE_END_RE = re.compile(r"^[ \t]*>{5,}\s*REPLACE[ \t]*$", re.IGNORECASE)


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

    @staticmethod
    def _split_leading_prefix(text: str) -> Tuple[str, str]:
        """分离文本开头的联网提示前缀与正文。"""
        m = LEADING_PREFIX_RE.match(text)
        if m:
            return m.group(1), text[m.end():]
        return "", text

    @classmethod
    def strip_leading_think(cls, text: str) -> str:
        """剥离紧贴开头的 <think>...</think> 块（流式未闭合时隐藏思考内容），绝不误删正文中的 <think> 提及。"""
        prefix, rest = cls._split_leading_prefix(text)
        m = LEADING_THINK_RE.match(rest)
        if not m:
            return text
        if m.group("closed") is not None:
            return prefix + rest[m.end():]
        # 仍在流式输出、尚未闭合的思考块：只保留前缀（隐藏思考内容）
        return prefix

    @classmethod
    def strip_leading_title(cls, text: str) -> str:
        """剥离紧贴开头的 <metis_title>...</metis_title> 标签（包含流式未闭合状态）。"""
        prefix, rest = cls._split_leading_prefix(text)
        m = LEADING_TITLE_RE.match(rest)
        if not m:
            return text
        return prefix + rest[m.end():]

    @classmethod
    def extract_session_title(cls, raw_output: str) -> Optional[str]:
        """从模型响应开头提取会话标题。"""
        cleaned = cls.strip_leading_think(raw_output)
        _, rest = cls._split_leading_prefix(cleaned)
        m = LEADING_TITLE_RE.match(rest)
        if m and m.group("title") is not None:
            raw_title = (m.group("title") or "").strip()
            first_line = raw_title.splitlines()[0] if raw_title else ""
            title = first_line.strip().strip("\"'`#* \t")
            if title:
                return f"{title[:40]}..." if len(title) > 40 else title
        return None


    @staticmethod
    def _parse_search_replace_blocks(body: str) -> List[Tuple[str, str]]:
        """
        基于状态机的逐行解析器，从 <metis_replace> 块内容中提取 (search_block, replace_block) 列表。
        """
        results: List[Tuple[str, str]] = []
        state = "OUTSIDE"  # 状态: OUTSIDE -> IN_SEARCH -> IN_REPLACE
        search_lines: List[str] = []
        replace_lines: List[str] = []

        for line in body.splitlines(keepends=True):
            stripped = line.rstrip("\r\n")

            if state == "OUTSIDE":
                if SR_SEARCH_START_RE.match(stripped):
                    state = "IN_SEARCH"
                    search_lines = []
                    replace_lines = []
            elif state == "IN_SEARCH":
                if SR_DIVIDER_RE.match(stripped):
                    state = "IN_REPLACE"
                elif SR_SEARCH_START_RE.match(stripped):
                    search_lines = []
                else:
                    search_lines.append(line)
            elif state == "IN_REPLACE":
                if SR_REPLACE_END_RE.match(stripped):
                    s_text = re.sub(r"\r?\n$", "", "".join(search_lines))
                    r_text = re.sub(r"\r?\n$", "", "".join(replace_lines))
                    results.append((s_text, r_text))
                    state = "OUTSIDE"
                else:
                    replace_lines.append(line)

        return results


    @staticmethod
    def _normalize_action_kind(raw_tag: str) -> str:
        t = (raw_tag or "").strip().lower()
        if t in ("metis_read", "read_file"):
            return "read"
        if t in ("metis_search", "metis_grep", "search_file", "grep"):
            return "search"
        if t in ("metis_write", "write_file"):
            return "write"
        if t in ("metis_replace", "replace_file"):
            return "replace"
        return t

    @staticmethod
    def _parse_tag_attrs(attr_str: str) -> Dict[str, str]:
        attrs: Dict[str, str] = {}
        for m in ATTR_PAIR_RE.finditer(attr_str or ""):
            key = m.group(1).lower().replace("-", "_")
            val = (
                m.group(2)
                if m.group(2) is not None
                else (m.group(3) if m.group(3) is not None else (m.group(4) or ""))
            )
            attrs[key] = val.strip()
        return attrs

    @staticmethod
    def _parse_optional_int(val: Optional[str]) -> Optional[int]:
        if val is None or not str(val).strip():
            return None
        try:
            return int(str(val).strip())
        except Exception:
            return None

    @classmethod
    def _scan_agent_blocks(cls, text: str) -> Tuple[str, List[Tuple[str, Dict[str, Any], str]]]:
        """
        基于行状态机的动作标签扫描器：
        1. 围栏代码块（``` 或 ~~~）内的任何标签均视为普通示例文本，不执行也不剔除。
        2. 闭合围栏要求：同种字符、长度不小于开启围栏、且行尾无其它非空字符（遵循 CommonMark 规范）。
        3. 标签必须独立占一行；句中提及的标签原样保留。
        4. 在块内部时，除匹配的闭合标签外，所有内容原样收集（允许被写入的代码里包含其它标签字符串）。
        5. 末尾未闭合的块或流式半截标签自动隐藏并丢弃，绝不半途执行。
        """
        lines = text.splitlines(keepends=True)
        in_fence = False
        fence_char = ""
        fence_len = 0

        active_kind: Optional[str] = None
        active_attrs: Dict[str, Any] = {}
        active_body: List[str] = []

        visible_lines: List[str] = []
        blocks: List[Tuple[str, Dict[str, Any], str]] = []

        for idx, line in enumerate(lines):
            stripped = line.strip()

            # 状态 1：处于多行块内部（write / replace / 多行 search）
            if active_kind is not None:
                m_close = ACTION_CLOSE_RE.match(stripped)
                if m_close and cls._normalize_action_kind(m_close.group("tag")) == active_kind:
                    body_str = re.sub(r"\r?\n$", "", "".join(active_body))
                    blocks.append((active_kind, active_attrs, body_str))
                    active_kind = None
                    active_attrs = {}
                    active_body = []
                else:
                    active_body.append(line)
                continue

            # 状态 2：处于 Markdown 代码块内部（忽略所有标签）
            if in_fence:
                m_fence = FENCE_RE.match(line.rstrip("\r\n"))
                if m_fence:
                    marker = m_fence.group("fence")
                    info = m_fence.group("info").strip()
                    if marker[0] == fence_char and len(marker) >= fence_len and not info:
                        in_fence = False
                        fence_char = ""
                        fence_len = 0
                visible_lines.append(line)
                continue

            # 检查是否进入 Markdown 代码块
            m_fence = FENCE_RE.match(line.rstrip("\r\n"))
            if m_fence:
                marker = m_fence.group("fence")
                in_fence = True
                fence_char = marker[0]
                fence_len = len(marker)
                visible_lines.append(line)
                continue

            # 状态 3：普通文本行，检测动作开启标签
            m_open = ACTION_OPEN_RE.match(stripped)
            if m_open:
                kind = cls._normalize_action_kind(m_open.group("tag"))
                attrs = cls._parse_tag_attrs(m_open.group("attrs") or "")
                is_self_closing = bool(m_open.group("self"))
                inline_body = m_open.group("inline_body")

                file_attr = (
                    attrs.get("file")
                    or attrs.get("path")
                    or attrs.get("name")
                    or attrs.get("target")
                    or ""
                ).strip()

                if kind == "read":
                    target_f = file_attr or (inline_body or "").strip()
                    if target_f:
                        attrs["file"] = target_f
                        blocks.append(("read", attrs, ""))
                    continue

                if kind == "search":
                    pattern_val = (
                        attrs.get("pattern")
                        or attrs.get("regex")
                        or attrs.get("query")
                        or (inline_body if inline_body is not None else "")
                    ).strip()
                    if pattern_val or is_self_closing or inline_body is not None:
                        if pattern_val:
                            attrs["pattern"] = pattern_val
                            blocks.append(("search", attrs, pattern_val))
                        continue
                    active_kind = "search"
                    active_attrs = attrs
                    active_body = []
                    continue

                if not file_attr:
                    continue

                attrs["file"] = file_attr

                if inline_body is not None:
                    blocks.append((kind, attrs, inline_body))
                    continue

                if is_self_closing:
                    if kind == "write":
                        blocks.append(("write", attrs, ""))
                    else:
                        visible_lines.append(line)
                    continue

                active_kind = kind
                active_attrs = attrs
                active_body = []
                continue

            # 过滤游离的闭合标签
            if ACTION_CLOSE_RE.match(stripped):
                continue

            # 流式输出保护：如果最后一行是正在生成中的半截标签，暂不显示
            if idx == len(lines) - 1 and STREAMING_PARTIAL_TAG_RE.match(stripped):
                continue

            visible_lines.append(line)

        return "".join(visible_lines), blocks


    @classmethod
    def _clean_visible_text(cls, visible_text: str) -> str:
        """清理模型底层泄漏标记与多余空行。"""
        cleaned = re.sub(r"<[｜|]{1,2}\s*DSML[\s\S]*$", "", visible_text)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    @classmethod
    def parse_agent_output(cls, raw_output: str, whitelist: List[WhitelistedFile]) -> ParsedAgentResponse:
        parsed = ParsedAgentResponse(raw_text=raw_output)

        cleaned = cls.strip_leading_think(raw_output)
        cleaned = cls.strip_leading_title(cleaned)
        visible_text, blocks = cls._scan_agent_blocks(cleaned)

        seen_reads = set()
        seen_searches = set()
        seen_writes = set()

        for kind, attrs, body in blocks:
            file_attr = str(attrs.get("file", "")).strip()
            file_key = file_attr.lower()

            if kind == "read":
                s_line = cls._parse_optional_int(
                    attrs.get("start_line") or attrs.get("start") or attrs.get("from_line")
                )
                e_line = cls._parse_optional_int(
                    attrs.get("end_line") or attrs.get("end") or attrs.get("to_line")
                )
                read_key = (file_key, s_line, e_line)
                if file_attr and read_key not in seen_reads:
                    seen_reads.add(read_key)
                    parsed.file_reads.append(
                        FileReadRequest(
                            target_file=file_attr,
                            start_line=s_line,
                            end_line=e_line,
                        )
                    )

            elif kind == "search":
                pattern_str = str(attrs.get("pattern") or body or "").strip()
                search_key = (file_key, pattern_str)
                if pattern_str and search_key not in seen_searches:
                    seen_searches.add(search_key)
                    parsed.file_searches.append(
                        FileSearchRequest(
                            pattern=pattern_str,
                            target_file=file_attr,
                        )
                    )

            elif kind == "replace":
                sr_pairs = cls._parse_search_replace_blocks(body)
                if not sr_pairs:
                    # 标签内容缺少分隔行，未解析出任何 SEARCH/REPLACE 对。
                    # 主动塞入一个空块，交由下游 apply_edit_in_place 给出格式错误反馈并触发下一轮重试，
                    # 避免"标签被剥离、文件未改、界面无提示"的静默失败。
                    sr_pairs = [("", "")]
                for search_str, replace_str in sr_pairs:
                    parsed.file_edits.append(
                        FileEdit(
                            action_type=FileEditActionType.SearchReplace,
                            target_file=file_attr,
                            new_content="",
                            search_block=search_str,
                            replace_block=replace_str,
                        )
                    )

            elif kind == "write":
                if file_key not in seen_writes:
                    seen_writes.add(file_key)
                    parsed.file_edits.append(
                        FileEdit(
                            action_type=FileEditActionType.WriteFull,
                            target_file=file_attr,
                            new_content=body,
                            search_block="",
                            replace_block="",
                        )
                    )

        parsed.explanation_text = cls._clean_visible_text(visible_text)
        return parsed

    @classmethod
    def format_display_text(cls, text: str) -> str:
        cleaned = cls.strip_leading_think(text)
        cleaned = cls.strip_leading_title(cleaned)
        visible_text, _ = cls._scan_agent_blocks(cleaned)
        return cls._clean_visible_text(visible_text)

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
