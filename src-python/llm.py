from __future__ import annotations
import asyncio
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
TITLE_EXTRACT_RE = re.compile(
    r"<(?:metis|session)_title\b[^>]*>(.*?)</(?:metis|session)_title>",
    re.IGNORECASE | re.DOTALL,
)
TITLE_STRIP_RE = re.compile(
    r"<(?:metis|session)_title\b[^>]*>.*?</(?:metis|session)_title>\s*",
    re.IGNORECASE | re.DOTALL,
)
UNCLOSED_TITLE_RE = re.compile(
    r"<(?:metis|session)_title\b[^>]*>.*$",
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
    r"<{5,}\s*SEARCH\r?\n(.*?)\r?\n={5,}\r?\n(.*?)\r?\n>{5,}\s*REPLACE",
    re.IGNORECASE | re.DOTALL,
)
CMD_TAG_RE = re.compile(
    r"<(?:metis|surgical|pinpoint)_(?:cmd|terminal|verify)[^>]*>\r?\n?(.*?)\r?\n?</(?:metis|surgical|pinpoint)_(?:cmd|terminal|verify)>",
    re.IGNORECASE | re.DOTALL,
)
SEARCH_TAG_RE = re.compile(
    r"<(?:metis|surgical|pinpoint)?_?search[^>]*>\r?\n?(.*?)\r?\n?</(?:metis|surgical|pinpoint)?_?search>",
    re.IGNORECASE | re.DOTALL,
)


class MultiVendorClient:
    @classmethod
    async def send_chat(
        cls,
        vendor: VendorProfile,
        system_prompt: str,
        files_snapshot: str,
        history: List[ChatHistoryItem],
        whitelist: List[WhitelistedFile],
        thinking_intensity: str,
        on_chunk: Callable[[str], None],
    ) -> Tuple[bool, str]:
        """Returns (success, full_response_or_error_message)."""
        if vendor.vendor_type == VendorType.LocalSandbox:
            return await cls._run_local_sandbox(files_snapshot, history, whitelist, on_chunk)

        full_system = f"{system_prompt}\n\n{files_snapshot}"
        base_url = vendor.base_url.rstrip("/")

        if vendor.vendor_type == VendorType.Gemini:
            return await asyncio.to_thread(
                cls._call_gemini_sync,
                vendor,
                base_url,
                full_system,
                history,
                thinking_intensity,
                on_chunk,
            )
        elif vendor.vendor_type == VendorType.Claude:
            return await asyncio.to_thread(
                cls._call_claude_sync,
                vendor,
                base_url,
                full_system,
                history,
                thinking_intensity,
                on_chunk,
            )
        else:
            return await asyncio.to_thread(
                cls._call_openai_compatible_sync,
                vendor,
                base_url,
                full_system,
                history,
                on_chunk,
            )

    @classmethod
    def _call_openai_compatible_sync(
        cls,
        vendor: VendorProfile,
        base_url: str,
        system_prompt: str,
        history: List[ChatHistoryItem],
        on_chunk: Callable[[str], None],
    ) -> Tuple[bool, str]:
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"

        messages = [{"role": "system", "content": system_prompt}]
        for item in history:
            messages.append(
                {
                    "role": "assistant" if item.role == "assistant" else "user",
                    "content": item.content,
                }
            )

        payload = {
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

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
        ctx = ssl.create_default_context()

        try:
            with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
                full_output: List[str] = []
                is_in_reasoning = False

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

                    choices = v.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    delta = choices[0].get("delta")
                    if not isinstance(delta, dict):
                        continue

                    content = delta.get("content")
                    reasoning = delta.get("reasoning_content")
                    if isinstance(content, str) and content:
                        if is_in_reasoning:
                            is_in_reasoning = False
                            full_output.append("</think>\n")
                            on_chunk("</think>\n")
                        full_output.append(content)
                        on_chunk(content)
                    elif isinstance(reasoning, str) and reasoning:
                        if not is_in_reasoning:
                            is_in_reasoning = True
                            full_output.append("<think>")
                            on_chunk("<think>")
                        full_output.append(reasoning)
                        on_chunk(reasoning)

                if is_in_reasoning:
                    full_output.append("</think>\n")
                    on_chunk("</think>\n")

                return True, "".join(full_output)
        except urllib.error.HTTPError as he:
            body = ""
            try:
                body = he.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            return False, f"LLM Provider Error (HTTP {he.code}): {body}"
        except Exception as e:
            return False, f"HTTP request error: {e}"

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
    ) -> Tuple[bool, str]:
        url = base_url if base_url.endswith("/messages") else f"{base_url}/messages"

        messages = cls.prepare_claude_messages(history)

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

        ctx = ssl.create_default_context()

        def open_claude_stream(req_payload: dict):
            data_bytes = json.dumps(req_payload).encode("utf-8")
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            return urllib.request.urlopen(req, timeout=180, context=ctx)

        resp = None
        try:
            resp = open_claude_stream(payload)
        except urllib.error.HTTPError as he:
            body = ""
            try:
                body = he.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            body_lower = body.lower()
            if "tools" in payload and any(term in body_lower for term in ("tool", "web_search", "unknown", "invalid")):
                payload.pop("tools", None)
                try:
                    resp = open_claude_stream(payload)
                except urllib.error.HTTPError as he2:
                    body2 = ""
                    try:
                        body2 = he2.read().decode("utf-8", errors="replace")
                    except Exception:
                        pass
                    return False, f"Claude API Error (HTTP {he2.code}): {body2}"
                except Exception as e2:
                    return False, f"HTTP request error on retry: {e2}"
            else:
                return False, f"Claude API Error (HTTP {he.code}): {body}"
        except Exception as e:
            return False, f"HTTP request error: {e}"

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

                    if v.get("type") == "content_block_delta":
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
    ) -> Tuple[bool, str]:
        raw_model = vendor.selected_model.strip()
        clean_model = raw_model[7:] if raw_model.startswith("models/") else (raw_model or "gemini-2.5-flash")

        api_key = vendor.api_key.strip()
        if not api_key:
            url = f"{base_url}/models/{clean_model}:streamGenerateContent?alt=sse"
        else:
            url = f"{base_url}/models/{clean_model}:streamGenerateContent?alt=sse&key={api_key}"

        contents = cls.prepare_gemini_contents(history)

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
        try:
            resp = open_gemini_stream(payload)
        except urllib.error.HTTPError as he:
            body = ""
            try:
                body = he.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            body_lower = body.lower()
            needs_retry = False
            if "tools" in payload and any(term in body_lower for term in ("tools", "google_search", "googlesearch", "grounding")):
                payload.pop("tools", None)
                needs_retry = True
            if supports_thinking and any(term in body_lower for term in ("thinkingconfig", "thinkingbudget", "not supported")):
                payload["generationConfig"] = {"temperature": 0.2}
                needs_retry = True

            if needs_retry:
                try:
                    resp = open_gemini_stream(payload)
                except urllib.error.HTTPError as he2:
                    body2 = ""
                    try:
                        body2 = he2.read().decode("utf-8", errors="replace")
                    except Exception:
                        pass
                    if "tools" in payload:
                        payload.pop("tools", None)
                        try:
                            resp = open_gemini_stream(payload)
                        except urllib.error.HTTPError as he3:
                            body3 = ""
                            try:
                                body3 = he3.read().decode("utf-8", errors="replace")
                            except Exception:
                                pass
                            return False, f"Gemini API Error (HTTP {he3.code}): {body3}"
                        except Exception as e3:
                            return False, f"HTTP request error: {e3}"
                    else:
                        return False, f"Gemini API Error (HTTP {he2.code}): {body2}"
                except Exception as e2:
                    return False, f"HTTP request error on retry: {e2}"
            else:
                return False, f"Gemini API Error (HTTP {he.code}): {body}"
        except Exception as e:
            return False, f"HTTP request error: {e}"

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

        # 4. Parse <metis_cmd>
        for cap in CMD_TAG_RE.finditer(cleaned):
            raw_cmd = (cap.group(1) or "").strip()
            if raw_cmd:
                valid_lines = [
                    line.strip()
                    for line in raw_cmd.splitlines()
                    if line.strip()
                    and not line.strip().startswith("#")
                    and not line.strip().startswith("//")
                    and not line.strip().lower().startswith("rem ")
                ]
                if valid_lines:
                    combined = " && ".join(valid_lines)
                    if combined not in parsed.terminal_commands:
                        parsed.terminal_commands.append(combined)

        parsed.explanation_text = cls.format_display_text(raw_output)
        return parsed

    @staticmethod
    def format_display_text(text: str) -> str:
        step1 = THINK_RE.sub("", text)
        step2 = UNCLOSED_THINK_RE.sub("", step1)
        step3 = TITLE_STRIP_RE.sub("", step2)
        step4 = UNCLOSED_TITLE_RE.sub("", step3)
        cleaned = SEARCH_TAG_RE.sub("", step4).strip()

        def _fmt_write(m: re.Match) -> str:
            f_name = m.group(1) or ""
            body = (m.group(2) or "").rstrip()
            return f"📄 **`{f_name}`**\n```\n{body}\n```"

        formatted = WRITE_TAG_RE.sub(_fmt_write, cleaned)

        def _fmt_replace(m: re.Match) -> str:
            f_name = m.group(1) or ""
            body = (m.group(2) or "").strip()
            return f"📄 **`{f_name}` (Surgical Update)**\n```\n{body}\n```"

        return REPLACE_TAG_RE.sub(_fmt_replace, formatted)

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
            parts.append("<metis_cmd>\npython -m unittest test_auth.py\n</metis_cmd>\n")
        else:
            parts.append(f"[Mock Sandbox Engine] Inspected `{target.file_name}`.\n")

        resp = "".join(parts)
        on_chunk(resp)
        return True, resp
