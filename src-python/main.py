from __future__ import annotations
import asyncio
import base64
import copy
import difflib
import json
import os
import sys
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def compute_diff_lines(search_block: str, replace_block: str) -> List[Dict[str, str]]:
    diff_lines: List[Dict[str, str]] = []
    if not search_block and replace_block:
        for line in replace_block.splitlines():
            diff_lines.append({"type": "add", "line": line})
        return diff_lines

    s_lines = search_block.splitlines(keepends=True)
    r_lines = replace_block.splitlines(keepends=True)
    for raw in difflib.ndiff(s_lines, r_lines):
        if not raw:
            continue
        prefix = raw[:2]
        line = raw[2:].rstrip("\r\n")
        if prefix == "- ":
            diff_lines.append({"type": "del", "line": line})
        elif prefix == "+ ":
            diff_lines.append({"type": "add", "line": line})
        elif prefix == "  ":
            diff_lines.append({"type": "ctx", "line": line})
    return diff_lines


def get_language_for_ext(ext: str) -> str:
    mapping = {
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
        "tsx": "tsx",
        "jsx": "jsx",
        "rs": "rust",
        "cpp": "cpp",
        "cc": "cpp",
        "c": "c",
        "h": "c",
        "hpp": "cpp",
        "cs": "csharp",
        "go": "go",
        "java": "java",
        "html": "html",
        "htm": "html",
        "css": "css",
        "scss": "scss",
        "json": "json",
        "yaml": "yaml",
        "yml": "yaml",
        "toml": "toml",
        "xml": "xml",
        "sql": "sql",
        "sh": "bash",
        "bash": "bash",
        "zsh": "bash",
        "md": "markdown",
        "markdown": "markdown",
    }
    return mapping.get(ext.lower(), ext.lower() if ext.isalnum() else "")


def format_file_added_event(file_path: str, content: str) -> str:
    ext = Path(file_path).suffix.lstrip(".")
    lang = get_language_for_ext(ext)
    return (
        f"[Workspace Event] File added to workspace: `{file_path}`\n"
        f"Content:\n"
        f"```{lang}\n"
        f"{content}\n"
        f"```"
    )


def format_file_removed_event(file_path: str) -> str:
    return f"[Workspace Event] File `{file_path}` has been removed from workspace."

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
    FileEdit,
    FileEditActionType,
    SessionStoreData,
    UiStreamEvent,
    VendorProfile,
    VendorType,
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

        self.session_controllers: Dict[str, AgentController] = {}
        self.session_status_texts: Dict[str, str] = {}
        self.session_tasks: Dict[str, asyncio.Task] = {}
        self.session_whitelists: Dict[str, WhitelistManager] = {}

    def get_session_controller(self, session_id: str) -> AgentController:
        if session_id not in self.session_controllers:
            self.session_controllers[session_id] = AgentController()
        return self.session_controllers[session_id]

    def is_session_busy(self, session_id: str) -> bool:
        ctrl = self.session_controllers.get(session_id)
        return ctrl.is_busy if ctrl is not None else False

    def get_session_status(self, session_id: str) -> str:
        return self.session_status_texts.get(session_id, "Ready")

    def set_session_status(self, session_id: str, status: str) -> None:
        self.session_status_texts[session_id] = status

    def get_busy_session_ids(self) -> List[str]:
        return [sid for sid, ctrl in self.session_controllers.items() if ctrl.is_busy]

    @property
    def agent(self) -> AgentController:
        active_id = self.session_store.active_session_id
        return self.get_session_controller(active_id)

    @property
    def status_text(self) -> str:
        active_id = self.session_store.active_session_id
        return self.get_session_status(active_id)

    @status_text.setter
    def status_text(self, val: str) -> None:
        active_id = self.session_store.active_session_id
        self.set_session_status(active_id, val)

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

        active_id = self.session_store.active_session_id
        is_busy = self.is_session_busy(active_id)
        status_txt = self.get_session_status(active_id)
        busy_ids = self.get_busy_session_ids()

        payload: Dict[str, Any] = {
            "type": "fullState",
            "appVersion": ConfigManager.get_app_version(),
            "config": self.config.to_dict(),
            "sessions": [s.to_dict() for s in self.session_store.sessions],
            "activeSessionId": active_id,
            "activeWorkspaceDir": self.whitelist.get_active_workspace_directory(),
            "isBusy": is_busy,
            "statusText": status_txt,
            "busySessionIds": busy_ids,
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
                    "version": ConfigManager.get_app_version(),
                }
            )
            return

        if action == "uiReady":
            self.broadcast_full_state()

        elif action == "createSession":
            curr = self.session_store.get_active_session()
            if curr is not None:
                curr.whitelist = list(self.whitelist.get_files())
                curr.allowed_directories = list(self.whitelist.get_directories())

            busy_ids = set(self.get_busy_session_ids())
            self.session_store.prune_redundant_empty_sessions(busy_ids=busy_ids)
            existing_empty = self.session_store.find_empty_session(busy_ids=busy_ids)
            if existing_empty is not None:
                is_already_active = self.session_store.active_session_id == existing_empty.id
                self.session_store.active_session_id = existing_empty.id
                self.whitelist.bind_session_list(
                    existing_empty.whitelist,
                    existing_empty.allowed_directories,
                )
                msg = "Already in an empty chat." if is_already_active else "Switched to empty chat."
                self.broadcast_full_state(toast=msg)
            else:
                ns = ChatSession()
                content_count = sum(1 for s in self.session_store.sessions if not s.is_empty())
                ns.title = f"Chat {content_count + 1}"
                self.session_store.active_session_id = ns.id
                self.session_store.sessions.insert(0, ns)
                self.whitelist.clear()
                self.broadcast_full_state(toast="New chat created.")

        elif action == "selectSession":
            sid = str(data.get("sessionId", ""))
            if sid:
                curr = self.session_store.get_active_session()
                if curr is not None:
                    curr.whitelist = list(self.whitelist.get_files())
                    curr.allowed_directories = list(self.whitelist.get_directories())
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
                self.broadcast_full_state(toast="Chat renamed.")

        elif action == "deleteSession":
            sid = str(data.get("sessionId", ""))
            if sid:
                if sid in self.session_tasks:
                    ctrl = self.get_session_controller(sid)
                    ctrl.stop()
                    t = self.session_tasks.pop(sid, None)
                    if t and not t.done():
                        t.cancel()
                self.session_controllers.pop(sid, None)
                self.session_status_texts.pop(sid, None)
                self.session_whitelists.pop(sid, None)

                self.session_store.sessions = [s for s in self.session_store.sessions if s.id != sid]
                if not self.session_store.sessions:
                    self.session_store.sessions.append(ChatSession())
                if self.session_store.active_session_id == sid:
                    self.session_store.active_session_id = self.session_store.sessions[0].id
                self.session_store.prune_redundant_empty_sessions(busy_ids=set(self.get_busy_session_ids()))
                active = self.session_store.get_active_session()
                if active is not None:
                    self.whitelist.bind_session_list(active.whitelist, active.allowed_directories)
                self.broadcast_full_state(toast="Chat deleted.")

        elif action == "saveConfig":
            cfg_val = data.get("config")
            if isinstance(cfg_val, dict):
                self.config = AppConfig.from_dict(cfg_val)
                active_v = next((v for v in self.config.vendors if v.id == self.config.active_vendor_id), None)
                if active_v:
                    if active_v.thinking_intensity:
                        self.config.thinking_intensity = active_v.thinking_intensity
                    else:
                        self.config.thinking_intensity = AppConfig.sanitize_thinking_intensity(active_v, self.config.thinking_intensity)
                        active_v.thinking_intensity = self.config.thinking_intensity
                ConfigManager.save(self.config)
                self.broadcast_full_state(toast="Settings saved.")

        elif action == "setTheme":
            theme = str(data.get("theme", "dark"))
            self.config.theme = theme
            ConfigManager.save(self.config)
            self.broadcast_full_state()

        elif action == "setThinkingIntensity":
            raw_intensity = str(data.get("intensity", "high")).lower()
            vendor_id = data.get("vendorId")
            target_vendor_id = vendor_id or self.config.active_vendor_id
            target_v = next((v for v in self.config.vendors if v.id == target_vendor_id), None)
            intensity = AppConfig.sanitize_thinking_intensity(target_v, raw_intensity)
            self.config.thinking_intensity = intensity
            if target_v:
                target_v.thinking_intensity = intensity
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
                active_session = self.session_store.get_active_session()
                for p in paths:
                    if isinstance(p, str) and self.whitelist.add_file(p):
                        added += 1
                        wf = self.whitelist.resolve_whitelisted_file(p)
                        if wf is not None and active_session is not None and active_session.api_history:
                            display_path = wf.relative_path or wf.file_name
                            event_text = format_file_added_event(display_path, wf.last_saved_content)
                            active_session.api_history.append(ChatHistoryItem(role="user", content=event_text))
                self.broadcast_full_state(toast=f"Added {added} files to workspace.")

        elif action == "addDirectory":
            folder_path = str(data.get("path", ""))
            if folder_path:
                active_session = self.session_store.get_active_session()
                prev_paths = {f.full_path.lower() for f in self.whitelist.get_files()}
                loaded, _ = self.whitelist.add_directory(folder_path)
                if active_session is not None and active_session.api_history:
                    for wf in self.whitelist.get_files():
                        if wf.full_path.lower() not in prev_paths:
                            display_path = wf.relative_path or wf.file_name
                            event_text = format_file_added_event(display_path, wf.last_saved_content)
                            active_session.api_history.append(ChatHistoryItem(role="user", content=event_text))
                self.broadcast_full_state(toast=f"Mounted folder with {loaded} files.")

        elif action == "removeFile":
            path = str(data.get("path", ""))
            if path:
                active_session = self.session_store.get_active_session()
                wf = self.whitelist.resolve_whitelisted_file(path)
                display_path = (wf.relative_path or wf.file_name) if wf else Path(path).name
                self.whitelist.remove_file(path)
                if active_session is not None and active_session.api_history:
                    event_text = format_file_removed_event(display_path)
                    active_session.api_history.append(ChatHistoryItem(role="user", content=event_text))
                self.broadcast_full_state(toast="Removed file from chat.")

        elif action == "removeDirectory":
            path = str(data.get("path", ""))
            if path:
                active_session = self.session_store.get_active_session()
                norm_dir = path.lower()
                removed_files = [f for f in self.whitelist.get_files() if f.full_path.lower().startswith(norm_dir)]
                self.whitelist.remove_directory(path)
                if active_session is not None and active_session.api_history:
                    for wf in removed_files:
                        display_path = wf.relative_path or wf.file_name
                        event_text = format_file_removed_event(display_path)
                        active_session.api_history.append(ChatHistoryItem(role="user", content=event_text))
                self.broadcast_full_state(toast="Unmounted folder.")

        elif action == "clearWhitelist":
            active_session = self.session_store.get_active_session()
            all_files = list(self.whitelist.get_files())
            self.whitelist.clear()
            if active_session is not None and active_session.api_history:
                for wf in all_files:
                    display_path = wf.relative_path or wf.file_name
                    event_text = format_file_removed_event(display_path)
                    active_session.api_history.append(ChatHistoryItem(role="user", content=event_text))
            self.broadcast_full_state(toast="Cleared all attached files.")

        elif action in ("pasteImage", "addMediaBlob"):
            b64_raw = str(data.get("base64", ""))
            mime_type = str(data.get("mimeType", "image/png"))
            file_name = str(data.get("fileName", ""))
            if b64_raw:
                if "," in b64_raw:
                    b64_raw = b64_raw.split(",", 1)[1]
                try:
                    raw_bytes = base64.b64decode(b64_raw)
                    ext = "png"
                    if "jpeg" in mime_type or "jpg" in mime_type:
                        ext = "jpg"
                    elif "pdf" in mime_type:
                        ext = "pdf"
                    elif "webp" in mime_type:
                        ext = "webp"
                    elif "gif" in mime_type:
                        ext = "gif"

                    if not file_name:
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        file_name = f"attached_{timestamp}.{ext}"

                    temp_dir = Path(tempfile.gettempdir()) / "metis_media"
                    temp_dir.mkdir(parents=True, exist_ok=True)
                    target_path = temp_dir / file_name
                    target_path.write_bytes(raw_bytes)
                    if self.whitelist.add_file(str(target_path)):
                        active_session = self.session_store.get_active_session()
                        if active_session is not None and active_session.api_history:
                            wf = self.whitelist.resolve_whitelisted_file(str(target_path))
                            display_path = (wf.relative_path or wf.file_name) if wf else file_name
                            content_str = wf.content if wf else f"[Attached Media: {file_name}]"
                            event_text = format_file_added_event(display_path, content_str)
                            active_session.api_history.append(ChatHistoryItem(role="user", content=event_text))
                    self.broadcast_full_state(toast=f"Attached media file: {file_name}")
                except Exception:
                    pass

        elif action == "inspectFile":
            path = str(data.get("path", ""))
            if path:
                wf = self.whitelist.resolve_whitelisted_file(path)
                if wf is not None:
                    p = Path(wf.full_path)
                    data_url = ""
                    content = wf.last_saved_content
                    if p.exists():
                        if wf.is_binary_media or wf.media_kind in ("image", "pdf"):
                            try:
                                b_data = p.read_bytes()
                                b64 = base64.b64encode(b_data).decode("ascii")
                                data_url = f"data:{wf.mime_type};base64,{b64}"
                            except Exception:
                                pass
                        else:
                            try:
                                content = p.read_text(encoding="utf-8", errors="replace")
                            except Exception:
                                content = wf.last_saved_content
                    else:
                        content = wf.last_saved_content

                    diff_lines = (
                        compute_diff_lines(wf.initial_content, content)
                        if (
                            not wf.is_binary_media
                            and wf.media_kind not in ("image", "pdf")
                            and wf.edit_count > 0
                            and wf.initial_content != content
                        )
                        else []
                    )
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
                            "diffLines": diff_lines,
                            "dataUrl": data_url,
                        }
                    )

        elif action == "revertFile":
            path = str(data.get("path", ""))
            if path:
                wf = self.whitelist.resolve_whitelisted_file(path)
                display_path = (wf.relative_path or wf.file_name) if wf else Path(path).name
                ok, msg = self.whitelist.revert_file_to_initial(path)
                if ok:
                    active_session = self.session_store.get_active_session()
                    if active_session is not None and active_session.api_history:
                        event_text = f"[Workspace Event] File `{display_path}` has been reverted to its initial content."
                        active_session.api_history.append(ChatHistoryItem(role="user", content=event_text))
                    self.broadcast_full_state(toast=msg)

        elif action == "pauseLoop":
            sid = str(data.get("sessionId", "")) or self.session_store.active_session_id
            ctrl = self.get_session_controller(sid)
            ctrl.pause()
            self.set_session_status(sid, "⏸ Paused")
            emit_host_message(
                {
                    "type": "busyStatus",
                    "sessionId": sid,
                    "isBusy": True,
                    "statusText": "⏸ Paused",
                }
            )

        elif action == "resumeLoop":
            sid = str(data.get("sessionId", "")) or self.session_store.active_session_id
            ctrl = self.get_session_controller(sid)
            ctrl.resume()
            self.set_session_status(sid, "⚡ Working...")
            emit_host_message(
                {
                    "type": "busyStatus",
                    "sessionId": sid,
                    "isBusy": True,
                    "statusText": "⚡ Working...",
                }
            )

        elif action == "stopLoop":
            sid = str(data.get("sessionId", "")) or self.session_store.active_session_id
            ctrl = self.get_session_controller(sid)
            ctrl.stop()
            self.set_session_status(sid, "Ready")
            emit_host_message(
                {
                    "type": "busyStatus",
                    "sessionId": sid,
                    "isBusy": False,
                    "statusText": "Ready",
                }
            )

        elif action == "sendPrompt":
            prompt = str(data.get("prompt", "")).strip()
            sid = str(data.get("sessionId", "")) or self.session_store.active_session_id
            if prompt and not self.is_session_busy(sid):
                task = asyncio.create_task(self.run_autonomous_loop(sid, prompt))
                self.session_tasks[sid] = task

    async def run_autonomous_loop(self, session_id: str, user_prompt: str) -> None:
        ctrl = self.get_session_controller(session_id)
        ctrl.is_busy = True
        ctrl.stop_requested = False
        ctrl.is_paused = False
        self.set_session_status(session_id, "Thinking...")

        emit_host_message(
            {
                "type": "busyStatus",
                "sessionId": session_id,
                "isBusy": True,
                "statusText": "Thinking...",
            }
        )

        target_session = self.session_store.get_session(session_id)
        if target_session is None:
            ctrl.is_busy = False
            self.set_session_status(session_id, "Ready")
            emit_host_message(
                {
                    "type": "busyStatus",
                    "sessionId": session_id,
                    "isBusy": False,
                    "statusText": "Ready",
                }
            )
            return

        # Dedicated WhitelistManager for this session's autonomous loop
        if session_id == self.session_store.active_session_id:
            target_session.whitelist = list(self.whitelist.get_files())
            target_session.allowed_directories = list(self.whitelist.get_directories())

        loop_whitelist = WhitelistManager()
        loop_whitelist.bind_session_list(target_session.whitelist, target_session.allowed_directories)
        self.session_whitelists[session_id] = loop_whitelist

        needs_session_title = False
        is_default_title = (
            target_session.title in ("Default Session", "Untitled Session", "Default Chat", "Untitled Chat")
            or target_session.title.startswith("Session ")
            or target_session.title.startswith("Chat ")
        )
        if not target_session.api_history or is_default_title:
            needs_session_title = True

        # Append-Only Architecture:
        # If this is the initial turn of the session, load the initial workspace snapshot
        # to the very head of the event log. This snapshot is immutable and never updated in place.
        if not target_session.api_history:
            whitelist_snapshot = list(loop_whitelist.get_files())
            initial_files_block = AgentLoop.build_file_snapshot(whitelist_snapshot)
            target_session.api_history.append(
                ChatHistoryItem(
                    role="user",
                    content=f"[Workspace Initial Files Snapshot]\n{initial_files_block}",
                )
            )

        if needs_session_title:
            user_msg_content = (
                f"{user_prompt}\n\n"
                "[System Instruction: Since this is the first turn of the chat, please summarize a concise chat title (2-6 words, in the same language as the user's request) at the very beginning of your reply wrapped in <metis_title>...</metis_title> tags, then provide your normal response.]"
            )
        else:
            user_msg_content = user_prompt

        target_session.api_history.append(ChatHistoryItem(role="user", content=user_msg_content))
        target_session.ui_events.append(UiStreamEvent(event_type="user", content=user_prompt))
        SessionManager.save(self.session_store)

        max_rounds = self.config.max_verify_loops
        timeout_sec = self.config.command_timeout_seconds
        vendor = next(
            (v for v in self.config.vendors if v.id == self.config.active_vendor_id),
            self.config.vendors[0] if self.config.vendors else None,
        )

        if vendor is None:
            ctrl.is_busy = False
            self.set_session_status(session_id, "Ready")
            self.broadcast_full_state()
            return

        current_round = 1
        retry_delays_secs = [2, 4, 8]

        try:
            while True:
                if ctrl.stop_requested:
                    break

                while ctrl.is_paused:
                    await asyncio.sleep(0.2)
                    if ctrl.stop_requested:
                        break

                if ctrl.stop_requested:
                    break

                if max_rounds > 0 and current_round > max_rounds:
                    break

                if current_round == 1:
                    round_status = "Thinking..."
                elif max_rounds > 0:
                    round_status = f"Round {current_round}/{max_rounds} — Thinking..."
                else:
                    round_status = f"Round {current_round} — Thinking..."
                self.set_session_status(session_id, round_status)
                emit_host_message(
                    {
                        "type": "busyStatus",
                        "sessionId": session_id,
                        "isBusy": True,
                        "statusText": round_status,
                    }
                )

                assistant_event_id = uuid.uuid4().hex
                target_session = self.session_store.get_session(session_id)
                if target_session is not None:
                    ev = UiStreamEvent(
                        id=assistant_event_id,
                        event_type="assistant",
                        content="Thinking...",
                        vendor_name=vendor.display_name,
                        model_name=vendor.selected_model,
                        round=current_round,
                        max_rounds=max_rounds,
                    )
                    target_session.ui_events.append(ev)

                self.broadcast_full_state()

                history_snapshot = (
                    copy.deepcopy(target_session.api_history) if target_session is not None else []
                )

                whitelist_snapshot = list(loop_whitelist.get_files())
                system_prompt = AgentLoop.build_system_prompt()

                attempt = 0
                chat_ok = False
                raw_response_or_err = ""

                while True:
                    streamed_chunks: list[str] = []
                    last_emitted_display: str = ""

                    def on_chunk(chunk: str) -> None:
                        nonlocal last_emitted_display
                        streamed_chunks.append(chunk)
                        full_so_far = "".join(streamed_chunks)
                        display = MultiVendorClient.format_display_text(full_so_far)
                        display_or_thinking = display if display.strip() else "Thinking..."
                        if display_or_thinking == last_emitted_display:
                            return
                        last_emitted_display = display_or_thinking
                        emit_host_message(
                            {
                                "type": "streamUpdate",
                                "sessionId": session_id,
                                "eventId": assistant_event_id,
                                "content": display_or_thinking,
                            }
                        )

                    ok, result_text = await MultiVendorClient.send_chat(
                        vendor=vendor,
                        system_prompt=system_prompt,
                        files_snapshot=None,
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
                        and not ctrl.stop_requested
                    ):
                        delay_secs = retry_delays_secs[attempt]
                        attempt += 1

                        retry_status = f"API returned 503/busy, retrying in {delay_secs}s ({attempt}/3)..."
                        self.set_session_status(session_id, retry_status)
                        emit_host_message(
                            {
                                "type": "busyStatus",
                                "sessionId": session_id,
                                "isBusy": True,
                                "statusText": retry_status,
                            }
                        )
                        emit_host_message(
                            {
                                "type": "streamUpdate",
                                "sessionId": session_id,
                                "eventId": assistant_event_id,
                                "content": f"Thinking... (Retrying in {delay_secs}s, attempt {attempt}/3)",
                            }
                        )

                        ticks = delay_secs * 5
                        for _ in range(ticks):
                            if ctrl.stop_requested:
                                break
                            await asyncio.sleep(0.2)

                        if ctrl.stop_requested:
                            raw_response_or_err = "Chat interrupted by user during retry."
                            break

                        retry_gen_status = f"Retry {attempt}/3 — Thinking..."
                        self.set_session_status(session_id, retry_gen_status)
                        emit_host_message(
                            {
                                "type": "busyStatus",
                                "sessionId": session_id,
                                "isBusy": True,
                                "statusText": retry_gen_status,
                            }
                        )
                        emit_host_message(
                            {
                                "type": "streamUpdate",
                                "sessionId": session_id,
                                "eventId": assistant_event_id,
                                "content": "Thinking...",
                            }
                        )
                        continue

                    raw_response_or_err = result_text
                    break

                if not chat_ok:
                    target_session = self.session_store.get_session(session_id)
                    if target_session is not None:
                        target_session.ui_events = [e for e in target_session.ui_events if e.id != assistant_event_id]
                        if attempt > 0:
                            msg = f"⚠️ LLM Error (failed after {attempt} retries — 2s/4s/8s): {raw_response_or_err}"
                        else:
                            msg = f"⚠️ LLM Error: {raw_response_or_err}"
                        target_session.ui_events.append(UiStreamEvent(event_type="notice", content=msg))
                    break

                raw_response = raw_response_or_err

                if needs_session_title:
                    extracted_title = MultiVendorClient.extract_session_title(raw_response)
                    if not extracted_title:
                        first_line = user_prompt.splitlines()[0].strip() if user_prompt.strip() else ""
                        if first_line:
                            extracted_title = f"{first_line[:24]}..." if len(first_line) > 24 else first_line
                    if extracted_title:
                        target_session = self.session_store.get_session(session_id)
                        if target_session is not None:
                            target_session.title = extracted_title
                            target_session.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        needs_session_title = False

                target_session = self.session_store.get_session(session_id)
                formatted_explanation = MultiVendorClient.format_display_text(raw_response)
                if target_session is not None:
                    if not formatted_explanation:
                        target_session.ui_events = [
                            e for e in target_session.ui_events if e.id != assistant_event_id
                        ]
                    else:
                        for ev in target_session.ui_events:
                            if ev.id == assistant_event_id:
                                ev.content = formatted_explanation
                                break
                    target_session.api_history.append(ChatHistoryItem(role="assistant", content=raw_response))

                parsed = MultiVendorClient.parse_agent_output(raw_response, whitelist_snapshot)
                had_actions = False
                any_edit_failed = False

                # 1. Process File Edits
                successful_edits: List[Dict[str, Any]] = []

                for edit in parsed.file_edits:
                    had_actions = True
                    pre_wf = loop_whitelist.resolve_whitelisted_file(edit.target_file)
                    prev_content = ""
                    if pre_wf is not None:
                        pre_p = Path(pre_wf.full_path)
                        if pre_p.exists():
                            try:
                                prev_content = pre_p.read_text(encoding="utf-8", errors="replace")
                            except Exception:
                                prev_content = pre_wf.last_saved_content
                        else:
                            prev_content = pre_wf.last_saved_content

                    ok_edit, msg_or_err, wf = loop_whitelist.apply_edit_in_place(edit)
                    target_session = self.session_store.get_session(session_id)
                    if target_session is not None:
                        if ok_edit and wf is not None:
                            target_search = (
                                edit.search_block
                                if edit.action_type != FileEditActionType.WriteFull
                                else prev_content
                            )
                            target_replace = (
                                edit.replace_block
                                if edit.action_type != FileEditActionType.WriteFull
                                else edit.new_content
                            )
                            diff_lines = compute_diff_lines(target_search, target_replace)
                            successful_edits.append(
                                {
                                    "filePath": wf.full_path,
                                    "fileName": wf.file_name,
                                    "relativePath": wf.relative_path or wf.file_name,
                                    "actionType": edit.action_type,
                                    "searchBlock": target_search,
                                    "replaceBlock": target_replace,
                                    "diffLines": diff_lines,
                                    "editCount": wf.edit_count,
                                    "statusMessage": msg_or_err,
                                }
                            )
                            target_session.api_history.append(
                                ChatHistoryItem(
                                    role="user",
                                    content=f"[File Edit Feedback]: {msg_or_err}",
                                )
                            )
                        else:
                            any_edit_failed = True
                            ev_type = (
                                "security_block"
                                if "[Security Block" in msg_or_err
                                else "notice"
                            )
                            target_session.ui_events.append(UiStreamEvent(event_type=ev_type, content=msg_or_err))
                            target_session.api_history.append(
                                ChatHistoryItem(
                                    role="user",
                                    content=f"[Operation Feedback]:\n{msg_or_err}",
                                )
                            )

                # Sync whitelist to target session
                target_session = self.session_store.get_session(session_id)
                if target_session is not None:
                    target_session.whitelist = list(loop_whitelist.get_files())
                    target_session.allowed_directories = list(loop_whitelist.get_directories())
                    if session_id == self.session_store.active_session_id:
                        self.whitelist.bind_session_list(target_session.whitelist, target_session.allowed_directories)

                # Consolidate multiple file modifications into a single merged event
                if successful_edits and target_session is not None:
                    unique_files = list(dict.fromkeys(e["filePath"] for e in successful_edits))
                    file_count = len(unique_files)
                    summary_label = f"Modified {file_count} {'file' if file_count == 1 else 'files'}"
                    first_edit = successful_edits[0]
                    ev = UiStreamEvent(
                        event_type="file_edit",
                        content=summary_label,
                        file_name=first_edit["fileName"],
                        file_path=first_edit["filePath"],
                        line_count=file_count,
                        edit_count=len(successful_edits),
                        file_edits=successful_edits,
                    )
                    target_session.ui_events.append(ev)

                self.broadcast_full_state()

                needs_followup_round = bool(any_edit_failed)
                if not needs_followup_round:
                    break

                current_round += 1
        finally:
            ctrl.is_busy = False
            self.set_session_status(session_id, "Ready")
            self.session_tasks.pop(session_id, None)
            self.session_whitelists.pop(session_id, None)
            emit_host_message(
                {
                    "type": "busyStatus",
                    "sessionId": session_id,
                    "isBusy": False,
                    "statusText": "Ready",
                }
            )
            self.broadcast_full_state()


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
    try:
        asyncio.run(run_ipc_loop())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
