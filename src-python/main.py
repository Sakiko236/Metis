from __future__ import annotations
import asyncio
import copy
import json
import os
import sys
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure local modules in src-python are importable and standard streams use UTF-8
sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from agent import AgentController, AgentLoop
from config import ConfigManager
from llm import MultiVendorClient
from models import (
    AppConfig,
    ChatHistoryItem,
    ChatSession,
    SessionStoreData,
    SurgicalActionType,
    SurgicalFileEdit,
    UiStreamEvent,
)
from session import SessionManager
from shell import ShellRunner
from whitelist import WhitelistManager


_stdout_lock = threading.Lock()


def emit_host_message(payload: Dict[str, Any]) -> None:
    try:
        line = "METIS_IPC:" + json.dumps(payload, ensure_ascii=False) + "\n"
        encoded = line.encode("utf-8", errors="replace")
        with _stdout_lock:
            sys.stdout.buffer.write(encoded)
            sys.stdout.buffer.flush()
    except Exception:
        pass


class SidecarEngine:
    def __init__(self) -> None:
        self.config: AppConfig = ConfigManager.load_or_create_default()
        self.session_store: SessionStoreData = SessionManager.load_or_create_default()
        self.session_store.prune_redundant_empty_sessions()
        SessionManager.save(self.session_store)

        self.whitelist = WhitelistManager()
        active = self.session_store.get_active_session()
        if active is not None:
            self.whitelist.bind_session_list(active.whitelist, active.allowed_directories)

        self.agent = AgentController()
        self.status_text: str = "Ready"
        self.active_loop_task: Optional[asyncio.Task] = None

    def broadcast_full_state(
        self,
        toast: Optional[str] = None,
        prefill_prompt: Optional[str] = None,
    ) -> None:
        active = self.session_store.get_active_session()
        if active is not None:
            active.whitelist = list(self.whitelist.get_files())
            active.allowed_directories = list(self.whitelist.get_directories())
        SessionManager.save(self.session_store)

        payload: Dict[str, Any] = {
            "type": "fullState",
            "config": self.config.to_dict(),
            "sessions": [s.to_dict() for s in self.session_store.sessions],
            "activeSessionId": self.session_store.active_session_id,
            "activeWorkspaceDir": self.whitelist.get_active_workspace_directory(),
            "isBusy": self.agent.is_busy,
            "statusText": self.status_text,
            "configFilePath": str(ConfigManager.get_config_path()),
            "isMaximized": False,
        }
        if toast is not None:
            payload["toast"] = toast
        if prefill_prompt is not None:
            payload["prefillPrompt"] = prefill_prompt

        emit_host_message(payload)

    async def handle_command(self, data: Dict[str, Any]) -> None:
        action = str(data.get("action", ""))

        if action == "ping":
            emit_host_message(
                {
                    "type": "pong",
                    "engine": "python-sidecar",
                    "version": "1.3.0",
                }
            )
            return

        if action == "uiReady":
            self.broadcast_full_state()

        elif action == "createSession":
            self.session_store.prune_redundant_empty_sessions()
            existing_empty = self.session_store.find_empty_session()
            if existing_empty is not None:
                is_already_active = self.session_store.active_session_id == existing_empty.id
                self.session_store.active_session_id = existing_empty.id
                self.whitelist.bind_session_list(
                    existing_empty.whitelist,
                    existing_empty.allowed_directories,
                )
                msg = "Already in an empty session." if is_already_active else "Switched to empty session."
                self.broadcast_full_state(toast=msg)
            else:
                ns = ChatSession()
                content_count = sum(1 for s in self.session_store.sessions if not s.is_empty())
                ns.title = f"Session {content_count + 1}"
                self.session_store.active_session_id = ns.id
                self.session_store.sessions.insert(0, ns)
                self.whitelist.clear()
                self.broadcast_full_state(toast="New session created.")

        elif action == "selectSession":
            sid = str(data.get("sessionId", ""))
            if sid:
                self.session_store.active_session_id = sid
                active = self.session_store.get_active_session()
                if active is not None:
                    self.whitelist.bind_session_list(active.whitelist, active.allowed_directories)
                self.broadcast_full_state()

        elif action == "renameSession":
            sid = str(data.get("sessionId", ""))
            title = str(data.get("title", "")).strip()
            if sid and title:
                for s in self.session_store.sessions:
                    if s.id == sid:
                        s.title = title
                        s.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        break
                self.broadcast_full_state(toast="Session renamed.")

        elif action == "deleteSession":
            sid = str(data.get("sessionId", ""))
            self.session_store.sessions = [s for s in self.session_store.sessions if s.id != sid]
            if not self.session_store.sessions:
                self.session_store.sessions.append(ChatSession())
            if self.session_store.active_session_id == sid:
                self.session_store.active_session_id = self.session_store.sessions[0].id
            self.session_store.prune_redundant_empty_sessions()
            active = self.session_store.get_active_session()
            if active is not None:
                self.whitelist.bind_session_list(active.whitelist, active.allowed_directories)
            self.broadcast_full_state(toast="Session deleted.")

        elif action == "saveConfig":
            cfg_val = data.get("config")
            if isinstance(cfg_val, dict):
                self.config = AppConfig.from_dict(cfg_val)
                ConfigManager.save(self.config)
                self.broadcast_full_state(toast="Settings saved.")

        elif action == "setTheme":
            theme = str(data.get("theme", "dark"))
            self.config.theme = theme
            ConfigManager.save(self.config)
            self.broadcast_full_state()

        elif action == "setThinkingIntensity":
            raw_intensity = str(data.get("intensity", "medium")).lower()
            intensity = raw_intensity if raw_intensity in ("low", "medium", "high") else "medium"
            self.config.thinking_intensity = intensity
            ConfigManager.save(self.config)
            self.broadcast_full_state()

        elif action == "probeOllama":
            base_url = str(data.get("baseUrl", "http://localhost:11434/v1"))
            models = await MultiVendorClient.fetch_ollama_models(base_url)
            toast = (
                f"Detected {len(models)} local Ollama models."
                if models
                else "No Ollama service detected. Ensure 'ollama serve' is running."
            )
            emit_host_message(
                {
                    "type": "ollamaModels",
                    "models": models,
                    "toast": toast,
                }
            )

        elif action == "addFiles":
            paths = data.get("paths", [])
            if isinstance(paths, list):
                added = 0
                for p in paths:
                    if isinstance(p, str) and self.whitelist.add_file(p):
                        added += 1
                self.broadcast_full_state(toast=f"Added {added} files to workspace.")

        elif action == "addDirectory":
            folder_path = str(data.get("path", ""))
            if folder_path:
                loaded, _ = self.whitelist.add_directory(folder_path)
                self.broadcast_full_state(toast=f"Mounted folder with {loaded} files.")

        elif action == "removeFile":
            path = str(data.get("path", ""))
            if path:
                self.whitelist.remove_file(path)
                self.broadcast_full_state(toast="Removed file from session.")

        elif action == "removeDirectory":
            path = str(data.get("path", ""))
            if path:
                self.whitelist.remove_directory(path)
                self.broadcast_full_state(toast="Unmounted folder.")

        elif action == "clearWhitelist":
            self.whitelist.clear()
            self.broadcast_full_state(toast="Cleared all attached files.")

        elif action == "inspectFile":
            path = str(data.get("path", ""))
            if path:
                wf = self.whitelist.resolve_whitelisted_file(path)
                if wf is not None:
                    p = Path(wf.full_path)
                    if p.exists():
                        try:
                            content = p.read_text(encoding="utf-8", errors="replace")
                        except Exception:
                            content = wf.last_saved_content
                    else:
                        content = wf.last_saved_content

                    emit_host_message(
                        {
                            "type": "fileContentPreview",
                            "fileName": wf.file_name,
                            "fullPath": wf.full_path,
                            "editCount": wf.edit_count,
                            "mediaKind": wf.media_kind,
                            "mimeType": wf.mime_type,
                            "isBinaryMedia": wf.is_binary_media,
                            "content": content,
                        }
                    )

        elif action == "revertFile":
            path = str(data.get("path", ""))
            if path:
                ok, msg = self.whitelist.revert_file_to_initial(path)
                if ok:
                    self.broadcast_full_state(toast=msg)

        elif action == "pauseLoop":
            self.agent.pause()
            self.status_text = "⏸ Paused"
            emit_host_message(
                {
                    "type": "busyStatus",
                    "isBusy": True,
                    "statusText": "⏸ Paused",
                }
            )

        elif action == "resumeLoop":
            self.agent.resume()
            self.status_text = "⚡ Working..."
            emit_host_message(
                {
                    "type": "busyStatus",
                    "isBusy": True,
                    "statusText": "⚡ Working...",
                }
            )

        elif action == "stopLoop":
            self.agent.stop()
            self.status_text = "Ready"
            emit_host_message(
                {
                    "type": "busyStatus",
                    "isBusy": False,
                    "statusText": "Ready",
                }
            )

        elif action == "sendPrompt":
            prompt = str(data.get("prompt", "")).strip()
            if prompt and not self.agent.is_busy:
                self.active_loop_task = asyncio.create_task(self.run_autonomous_loop(prompt))

    async def run_autonomous_loop(self, user_prompt: str) -> None:
        self.agent.is_busy = True
        self.agent.stop_requested = False
        self.agent.is_paused = False
        self.status_text = "Thinking..."

        emit_host_message(
            {
                "type": "busyStatus",
                "isBusy": True,
                "statusText": "Thinking...",
            }
        )

        needs_session_title = False
        active = self.session_store.get_active_session()
        if active is not None:
            is_default_title = (
                active.title in ("Default Session", "Untitled Session")
                or active.title.startswith("Session ")
            )
            if not active.api_history or is_default_title:
                needs_session_title = True

            active.api_history.append(ChatHistoryItem(role="user", content=user_prompt))
            active.ui_events.append(UiStreamEvent(event_type="user", content=user_prompt))

        max_rounds = max(1, self.config.max_verify_loops)
        timeout_sec = self.config.command_timeout_seconds
        vendor = next(
            (v for v in self.config.vendors if v.id == self.config.active_vendor_id),
            self.config.vendors[0] if self.config.vendors else None,
        )

        if vendor is None:
            self.agent.is_busy = False
            self.status_text = "Ready"
            self.broadcast_full_state()
            return

        current_round = 1
        retry_delays_secs = [2, 4, 8]

        while current_round <= max_rounds:
            if self.agent.stop_requested:
                break

            while self.agent.is_paused:
                await asyncio.sleep(0.2)
                if self.agent.stop_requested:
                    break

            if self.agent.stop_requested:
                break

            round_status = (
                "Thinking..."
                if current_round == 1
                else f"Round {current_round}/{max_rounds} — Thinking..."
            )
            self.status_text = round_status
            emit_host_message(
                {
                    "type": "busyStatus",
                    "isBusy": True,
                    "statusText": round_status,
                }
            )

            assistant_event_id = uuid.uuid4().hex
            active = self.session_store.get_active_session()
            if active is not None:
                ev = UiStreamEvent(
                    id=assistant_event_id,
                    event_type="assistant",
                    content="Thinking...",
                    vendor_name=vendor.display_name,
                    model_name=vendor.selected_model,
                    round=current_round,
                    max_rounds=max_rounds,
                )
                active.ui_events.append(ev)

            self.broadcast_full_state()

            history_snapshot = (
                copy.deepcopy(active.api_history) if active is not None else []
            )
            if needs_session_title and current_round == 1 and history_snapshot:
                last_msg = history_snapshot[-1]
                if last_msg.role == "user":
                    last_msg.content = (
                        f"{last_msg.content}\n\n"
                        "[System Instruction: Since this is the first turn of the session, please summarize a concise session title (2-6 words, in the same language as the user's request) at the very beginning of your reply wrapped in <metis_title>...</metis_title> tags, then provide your normal response.]"
                    )

            whitelist_snapshot = list(self.whitelist.get_files())
            files_block = AgentLoop.build_file_snapshot(whitelist_snapshot)
            system_prompt = AgentLoop.build_system_prompt()

            attempt = 0
            chat_ok = False
            raw_response_or_err = ""

            while True:
                streamed_chunks: list[str] = []

                def on_chunk(chunk: str) -> None:
                    streamed_chunks.append(chunk)
                    full_so_far = "".join(streamed_chunks)
                    display = MultiVendorClient.format_display_text(full_so_far)
                    display_or_thinking = display if display.strip() else "Thinking..."
                    emit_host_message(
                        {
                            "type": "streamUpdate",
                            "eventId": assistant_event_id,
                            "content": display_or_thinking,
                        }
                    )

                ok, result_text = await MultiVendorClient.send_chat(
                    vendor=vendor,
                    system_prompt=system_prompt,
                    files_snapshot=files_block,
                    history=history_snapshot,
                    whitelist=whitelist_snapshot,
                    thinking_intensity=self.config.thinking_intensity,
                    on_chunk=on_chunk,
                )

                if ok:
                    chat_ok = True
                    raw_response_or_err = result_text
                    break

                err_lower = result_text.lower()
                is_retryable = (
                    any(code in result_text for code in ("503", "502", "504", "429", "500"))
                    or "overloaded" in err_lower
                    or "unavailable" in err_lower
                    or "http request error" in err_lower
                    or "stream read error" in err_lower
                )

                if (
                    is_retryable
                    and attempt < len(retry_delays_secs)
                    and not self.agent.stop_requested
                ):
                    delay_secs = retry_delays_secs[attempt]
                    attempt += 1

                    retry_status = f"API returned 503/busy, retrying in {delay_secs}s ({attempt}/3)..."
                    self.status_text = retry_status
                    emit_host_message(
                        {
                            "type": "busyStatus",
                            "isBusy": True,
                            "statusText": retry_status,
                        }
                    )
                    emit_host_message(
                        {
                            "type": "streamUpdate",
                            "eventId": assistant_event_id,
                            "content": f"Thinking... (Retrying in {delay_secs}s, attempt {attempt}/3)",
                        }
                    )

                    ticks = delay_secs * 5
                    for _ in range(ticks):
                        if self.agent.stop_requested:
                            break
                        await asyncio.sleep(0.2)

                    if self.agent.stop_requested:
                        raw_response_or_err = "Session interrupted by user during retry."
                        break

                    retry_gen_status = f"Retry {attempt}/3 — Thinking..."
                    self.status_text = retry_gen_status
                    emit_host_message(
                        {
                            "type": "busyStatus",
                            "isBusy": True,
                            "statusText": retry_gen_status,
                        }
                    )
                    emit_host_message(
                        {
                            "type": "streamUpdate",
                            "eventId": assistant_event_id,
                            "content": "Thinking...",
                        }
                    )
                    continue

                raw_response_or_err = result_text
                break

            if not chat_ok:
                active = self.session_store.get_active_session()
                if active is not None:
                    active.ui_events = [e for e in active.ui_events if e.id != assistant_event_id]
                    if attempt > 0:
                        msg = f"⚠️ LLM Error (failed after {attempt} retries — 2s/4s/8s): {raw_response_or_err}"
                    else:
                        msg = f"⚠️ LLM Error: {raw_response_or_err}"
                    active.ui_events.append(UiStreamEvent(event_type="notice", content=msg))
                break

            raw_response = raw_response_or_err

            if needs_session_title:
                extracted_title = MultiVendorClient.extract_session_title(raw_response)
                if not extracted_title:
                    first_line = user_prompt.splitlines()[0].strip() if user_prompt.strip() else ""
                    if first_line:
                        extracted_title = f"{first_line[:24]}..." if len(first_line) > 24 else first_line
                if extracted_title:
                    active = self.session_store.get_active_session()
                    if active is not None:
                        active.title = extracted_title
                        active.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    needs_session_title = False

            active = self.session_store.get_active_session()
            if active is not None:
                for ev in active.ui_events:
                    if ev.id == assistant_event_id:
                        ev.content = MultiVendorClient.format_display_text(raw_response)
                        break
                active.api_history.append(ChatHistoryItem(role="assistant", content=raw_response))

            parsed = MultiVendorClient.parse_agent_output(raw_response, whitelist_snapshot)
            had_actions = False
            any_edit_failed = False

            # 1. Process File Edits
            for edit in parsed.file_edits:
                had_actions = True
                ok_edit, msg_or_err, wf = self.whitelist.apply_edit_in_place(edit)
                active = self.session_store.get_active_session()
                if active is not None:
                    if ok_edit and wf is not None:
                        ev = UiStreamEvent(
                            event_type="file_edit",
                            content=msg_or_err,
                            file_name=wf.file_name,
                            file_path=wf.full_path,
                            edit_count=wf.edit_count,
                        )
                        active.ui_events.append(ev)
                        active.api_history.append(
                            ChatHistoryItem(
                                role="user",
                                content=f"[File Edit Feedback]:\n{msg_or_err}",
                            )
                        )
                    else:
                        any_edit_failed = True
                        ev_type = (
                            "security_block"
                            if "[Security Block" in msg_or_err
                            else "notice"
                        )
                        active.ui_events.append(UiStreamEvent(event_type=ev_type, content=msg_or_err))
                        active.api_history.append(
                            ChatHistoryItem(
                                role="user",
                                content=f"[Operation Feedback]:\n{msg_or_err}",
                            )
                        )

            # 2. Process Terminal Commands
            for cmd in parsed.terminal_commands:
                had_actions = True
                self.status_text = f"Running `{cmd}`..."
                emit_host_message(
                    {
                        "type": "busyStatus",
                        "isBusy": True,
                        "statusText": self.status_text,
                    }
                )
                ws_dir = self.whitelist.get_active_workspace_directory()
                exec_res = await ShellRunner.run_command(cmd, ws_dir, timeout_sec)

                active = self.session_store.get_active_session()
                if active is not None:
                    ev = UiStreamEvent(
                        event_type="verify_result",
                        content=exec_res.combined_output(),
                        command=cmd,
                        working_directory=ws_dir,
                        exit_code=exec_res.exit_code,
                        duration_seconds=exec_res.duration_seconds,
                        succeeded=exec_res.succeeded(),
                        terminal_output=exec_res.combined_output(),
                    )
                    active.ui_events.append(ev)
                    active.api_history.append(
                        ChatHistoryItem(
                            role="user",
                            content=(
                                f"[Terminal Execution Feedback for `{cmd}`]:\n"
                                f"Exit Code: {exec_res.exit_code}\n"
                                f"Output:\n{exec_res.combined_output()}"
                            ),
                        )
                    )

            self.broadcast_full_state()

            needs_followup_round = bool(
                parsed.terminal_commands or any_edit_failed
            )
            if not needs_followup_round:
                break

            current_round += 1

        self.agent.is_busy = False
        self.status_text = "Ready"
        emit_host_message(
            {
                "type": "busyStatus",
                "isBusy": False,
                "statusText": "Ready",
            }
        )
        self.broadcast_full_state()


async def run_self_test() -> int:
    print("=== Metis v1.3.0 (Tauri + Python Sidecar) Headless End-to-End Self-Test ===", flush=True)

    # 1. Config Persistence
    cfg = ConfigManager.load_or_create_default()
    ConfigManager.save(cfg)
    cfg_path = ConfigManager.get_config_path()
    if not cfg_path.exists():
        print("FAIL: Config file was not persisted.", file=sys.stderr, flush=True)
        return 1
    print(f"[PASS] Local Config Persisted (Python Sidecar): {cfg_path}", flush=True)

    # 2. Sessions Persistence
    sess = SessionManager.load_or_create_default()
    SessionManager.save(sess)
    sess_path = SessionManager.get_sessions_file_path()
    if not sess_path.exists():
        print("FAIL: Sessions file was not persisted.", file=sys.stderr, flush=True)
        return 2
    print(f"[PASS] Local Sessions Persisted (Python Sidecar): {sess_path}", flush=True)

    # 3. Whitelist strict security check: Creation prohibited
    wl = WhitelistManager()
    edit_fake = SurgicalFileEdit(
        action_type=SurgicalActionType.WriteFull,
        target_file="sub/auto_created_script.py",
        new_content="print('bad')",
    )
    ok_fake, err_fake, _ = wl.apply_edit_in_place(edit_fake)
    if ok_fake or "[Security Block - File Creation Prohibited]" not in err_fake:
        print(f"FAIL: Unwhitelisted file creation check failed: {err_fake}", file=sys.stderr, flush=True)
        return 3
    print(f"[PASS] Strict Block on New File Creation Verified: {err_fake}", flush=True)

    # 4. In-Place Search/Replace Block verification
    temp_dir = Path(tempfile.gettempdir()) / "metis_py_selftest"
    temp_dir.mkdir(parents=True, exist_ok=True)
    test_file = temp_dir / "calc.py"
    test_file.write_text("def calc(a, b):\n    return a + b\n", encoding="utf-8")

    wl.add_file(str(test_file))
    edit_replace = SurgicalFileEdit(
        action_type=SurgicalActionType.SearchReplace,
        target_file="calc.py",
        search_block="    return a + b",
        replace_block="    return a * b",
    )
    ok_rep, msg_rep, _ = wl.apply_edit_in_place(edit_replace)
    updated = test_file.read_text(encoding="utf-8")
    if not ok_rep or "return a * b" not in updated:
        print(f"FAIL: In-place edit failed: {msg_rep}", file=sys.stderr, flush=True)
        return 4
    print(f"[PASS] Search/Replace Block Engine & In-Place Edits Verified: {msg_rep}", flush=True)

    # 5. Empty File Population Verification
    empty_file = temp_dir / "new_empty.txt"
    empty_file.write_text("", encoding="utf-8")
    wl.add_file(str(empty_file))
    edit_empty = SurgicalFileEdit(
        action_type=SurgicalActionType.SearchReplace,
        target_file="new_empty.txt",
        search_block="",
        replace_block="Hello from Python Sidecar empty file!",
    )
    ok_emp, msg_emp, _ = wl.apply_edit_in_place(edit_empty)
    if not ok_emp or empty_file.read_text(encoding="utf-8") != "Hello from Python Sidecar empty file!":
        print(f"FAIL: Empty file population failed: {msg_emp}", file=sys.stderr, flush=True)
        return 5
    print(f"[PASS] Empty File Population Verified: {msg_emp}", flush=True)

    # 6. ShellRunner execution verification
    exec_res = await ShellRunner.run_command("echo Metis v1.3.0 Python Sidecar Self-Test OK", str(temp_dir), 10)
    if not exec_res.succeeded() or "Metis v1.3.0 Python Sidecar" not in exec_res.combined_output():
        print(f"FAIL: Shell execution failed: {exec_res.combined_output()}", file=sys.stderr, flush=True)
        return 6
    print(f"[PASS] Terminal Execution Verified (Exit 0): {exec_res.combined_output().strip()}", flush=True)

    # 7. Vendor API Web Search Grounding Tool Specification Verification (Gemini, Claude, Responses API / DeepSeek)
    gemini_tools = MultiVendorClient.build_gemini_tools()
    claude_tools = MultiVendorClient.build_claude_tools()
    responses_tools = MultiVendorClient.build_responses_tools()
    deepseek_tools = MultiVendorClient.build_deepseek_tools()
    url_oai = MultiVendorClient.resolve_responses_url("https://api.openai.com/v1")
    url_deepseek = MultiVendorClient.resolve_responses_url("https://api.deepseek.com")
    url_chat = MultiVendorClient.resolve_responses_url("https://api.deepseek.com/v1/chat/completions")
    if (
        not gemini_tools
        or "google_search" not in gemini_tools[0]
        or not claude_tools
        or claude_tools[0].get("type") != "web_search_20250101"
        or not responses_tools
        or responses_tools[0].get("type") != "web_search"
        or not deepseek_tools
        or deepseek_tools[0].get("type") != "web_search"
        or url_oai != "https://api.openai.com/v1/responses"
        or url_deepseek != "https://api.deepseek.com/v1/responses"
        or url_chat != "https://api.deepseek.com/v1/responses"
    ):
        print("FAIL: Vendor API search tool specification or Responses API URL resolution failed.", file=sys.stderr, flush=True)
        return 7
    print(f"[PASS] Vendor API Search Grounding & Responses API Protocol Verified (OpenAI, DeepSeek, Claude, Gemini)", flush=True)

    # 8. Session Title Extraction, Tag Stripping & Gemini Thinking Config verification (low, medium, high)
    sample_reply = "<think>Thinking deeply...</think>\n<metis_title>Python Sidecar Architecture</metis_title>\nReady to assist!"
    extracted_title = MultiVendorClient.extract_session_title(sample_reply)
    display_text = MultiVendorClient.format_display_text(sample_reply)
    gen_cfg_high, supports = MultiVendorClient.build_gemini_generation_config("gemini-2.5-flash", "high")
    gen_cfg_med, _ = MultiVendorClient.build_gemini_generation_config("gemini-2.5-flash", "medium")
    gen_cfg_low, _ = MultiVendorClient.build_gemini_generation_config("gemini-2.5-flash", "low")
    gen_cfg_off_fallback, _ = MultiVendorClient.build_gemini_generation_config("gemini-2.5-flash", "off")
    if (
        extracted_title != "Python Sidecar Architecture"
        or display_text != "Ready to assist!"
        or not supports
        or gen_cfg_high.get("thinkingConfig", {}).get("thinkingBudget") != 24576
        or gen_cfg_med.get("thinkingConfig", {}).get("thinkingBudget") != 8192
        or gen_cfg_low.get("thinkingConfig", {}).get("thinkingBudget") != 2048
        or gen_cfg_off_fallback.get("thinkingConfig", {}).get("thinkingBudget") != 8192
    ):
        print("FAIL: LLM title/thinking verification failed.", file=sys.stderr, flush=True)
        return 7
    print(f"[PASS] Session Title & 3-Tier Gemini Thinking Budget (low:2048, med:8192, high:24576) Verified: {extracted_title!r}", flush=True)

    # 9. Gemini & Claude Conversation Sanitization (Prevent trailing model turns)
    raw_hist = [
        ChatHistoryItem(role="user", content="Hello"),
        ChatHistoryItem(role="assistant", content="How can I help?"),
    ]
    gemini_turns = MultiVendorClient.prepare_gemini_contents(raw_hist)
    claude_msgs = MultiVendorClient.prepare_claude_messages(raw_hist)
    if (
        not gemini_turns
        or gemini_turns[-1]["role"] != "user"
        or not claude_msgs
        or claude_msgs[-1]["role"] != "user"
    ):
        print("FAIL: prepare_gemini_contents / prepare_claude_messages failed to sanitize trailing model turn.", file=sys.stderr, flush=True)
        return 8

    edge_hist = [
        ChatHistoryItem(role="assistant", content="leading orphan model turn"),
        ChatHistoryItem(role="user", content="part 1"),
        ChatHistoryItem(role="user", content="part 2"),
    ]
    edge_gemini = MultiVendorClient.prepare_gemini_contents(edge_hist)
    if (
        len(edge_gemini) != 1
        or edge_gemini[0]["role"] != "user"
        or "part 1\n\npart 2" not in edge_gemini[0]["parts"][0]["text"]
    ):
        print("FAIL: Consecutive turns merging or leading turn stripping failed in prepare_gemini_contents.", file=sys.stderr, flush=True)
        return 9
    print("[PASS] Gemini & Claude Chat History Sanitization Verified (Trailing Model Turn Prevented)", flush=True)

    print("=== All Metis v1.3.0 Python Sidecar Self-Test Suites Passed Successfully! ===", flush=True)
    return 0


async def run_ipc_loop() -> None:
    engine = SidecarEngine()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Optional[str]] = asyncio.Queue()

    def stdin_reader() -> None:
        try:
            while True:
                raw = sys.stdin.buffer.readline()
                if not raw:
                    loop.call_soon_threadsafe(queue.put_nowait, None)
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    loop.call_soon_threadsafe(queue.put_nowait, line)
        except Exception:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    reader_thread = threading.Thread(target=stdin_reader, daemon=True)
    reader_thread.start()

    while True:
        line = await queue.get()
        if line is None:
            # Parent Tauri process closed stdin pipe -> exit cleanly immediately
            os._exit(0)
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                await engine.handle_command(data)
        except Exception:
            continue


def main() -> None:
    if "--self-test" in sys.argv:
        code = asyncio.run(run_self_test())
        sys.exit(code)

    try:
        asyncio.run(run_ipc_loop())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
