from __future__ import annotations
import uuid
from datetime import datetime
from enum import IntEnum
from typing import Any, Dict, List, Optional


class VendorType(IntEnum):
    Gemini = 0
    OpenAI = 1
    Claude = 2
    DeepSeek = 3
    Ollama = 4
    CustomOpenAI = 5
    LocalSandbox = 6

    @classmethod
    def from_any(cls, val: Any) -> "VendorType":
        if isinstance(val, cls):
            return val
        if isinstance(val, int):
            try:
                return cls(val)
            except ValueError:
                return cls.DeepSeek
        if isinstance(val, str):
            norm = val.strip().lower()
            mapping = {
                "gemini": cls.Gemini,
                "0": cls.Gemini,
                "openai": cls.OpenAI,
                "1": cls.OpenAI,
                "claude": cls.Claude,
                "2": cls.Claude,
                "deepseek": cls.DeepSeek,
                "3": cls.DeepSeek,
                "ollama": cls.Ollama,
                "4": cls.Ollama,
                "customopenai": cls.CustomOpenAI,
                "custom": cls.CustomOpenAI,
                "5": cls.CustomOpenAI,
                "localsandbox": cls.LocalSandbox,
                "6": cls.LocalSandbox,
            }
            return mapping.get(norm, cls.DeepSeek)
        return cls.DeepSeek


class VendorProfile:
    def __init__(
        self,
        id: str = "",
        display_name: str = "",
        vendor_type: VendorType = VendorType.DeepSeek,
        base_url: str = "",
        api_key: str = "",
        selected_model: str = "",
        preset_models: Optional[List[str]] = None,
    ):
        self.id = id
        self.display_name = display_name
        self.vendor_type = VendorType.from_any(vendor_type)
        self.base_url = base_url
        self.api_key = api_key
        self.selected_model = selected_model
        self.preset_models = preset_models if preset_models is not None else ([selected_model] if selected_model else [])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "Id": self.id,
            "DisplayName": self.display_name,
            "Type": int(self.vendor_type),
            "BaseUrl": self.base_url,
            "ApiKey": self.api_key,
            "SelectedModel": self.selected_model,
            "PresetModels": list(self.preset_models),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VendorProfile":
        return cls(
            id=str(data.get("Id", data.get("id", ""))),
            display_name=str(data.get("DisplayName", data.get("display_name", ""))),
            vendor_type=VendorType.from_any(data.get("Type", data.get("vendor_type", 3))),
            base_url=str(data.get("BaseUrl", data.get("base_url", ""))),
            api_key=str(data.get("ApiKey", data.get("api_key", ""))),
            selected_model=str(data.get("SelectedModel", data.get("selected_model", ""))),
            preset_models=list(data.get("PresetModels", data.get("preset_models", []))),
        )


class AppConfig:
    def __init__(
        self,
        active_vendor_id: str = "deepseek",
        max_verify_loops: int = 8,
        command_timeout_seconds: int = 60,
        theme: str = "dark",
        thinking_intensity: str = "medium",
        vendors: Optional[List[VendorProfile]] = None,
    ):
        self.active_vendor_id = active_vendor_id
        self.max_verify_loops = max_verify_loops
        self.command_timeout_seconds = command_timeout_seconds
        self.theme = theme
        self.thinking_intensity = (
            thinking_intensity.lower()
            if thinking_intensity and thinking_intensity.lower() in ("low", "medium", "high")
            else "medium"
        )
        self.vendors = vendors if vendors is not None else []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ActiveVendorId": self.active_vendor_id,
            "MaxVerifyLoops": self.max_verify_loops,
            "CommandTimeoutSeconds": self.command_timeout_seconds,
            "Theme": self.theme,
            "ThinkingIntensity": self.thinking_intensity,
            "Vendors": [v.to_dict() for v in self.vendors],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        raw_vendors = data.get("Vendors", data.get("vendors", []))
        vendors = [VendorProfile.from_dict(v) for v in raw_vendors if isinstance(v, dict)]
        raw_thinking = str(data.get("ThinkingIntensity", data.get("thinking_intensity", "medium"))).lower()
        thinking_intensity = raw_thinking if raw_thinking in ("low", "medium", "high") else "medium"
        return cls(
            active_vendor_id=str(data.get("ActiveVendorId", data.get("active_vendor_id", "deepseek"))),
            max_verify_loops=int(data.get("MaxVerifyLoops", data.get("max_verify_loops", 8))),
            command_timeout_seconds=int(data.get("CommandTimeoutSeconds", data.get("command_timeout_seconds", 60))),
            theme=str(data.get("Theme", data.get("theme", "dark"))),
            thinking_intensity=thinking_intensity,
            vendors=vendors,
        )


class WhitelistedFile:
    def __init__(
        self,
        full_path: str = "",
        file_name: str = "",
        directory_path: str = "",
        content: str = "",
        relative_path: Optional[str] = None,
        media_kind: str = "code",
        mime_type: str = "text/plain",
        is_binary_media: bool = False,
        initial_content: Optional[str] = None,
        last_saved_content: Optional[str] = None,
        edit_count: int = 0,
        added_at: Optional[str] = None,
        last_modified_by_agent_at: Optional[str] = None,
        current_size_bytes: Optional[int] = None,
    ):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.full_path = full_path
        self.file_name = file_name
        self.relative_path = relative_path if relative_path is not None else file_name
        self.directory_path = directory_path
        self.media_kind = media_kind
        self.mime_type = mime_type
        self.is_binary_media = is_binary_media
        self.initial_content = initial_content if initial_content is not None else content
        self.last_saved_content = last_saved_content if last_saved_content is not None else content
        self.edit_count = edit_count
        self.added_at = added_at if added_at is not None else now
        self.last_modified_by_agent_at = last_modified_by_agent_at if last_modified_by_agent_at is not None else now
        self.current_size_bytes = (
            current_size_bytes
            if current_size_bytes is not None
            else len(self.last_saved_content.encode("utf-8", errors="replace"))
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "FullPath": self.full_path,
            "FileName": self.file_name,
            "RelativePath": self.relative_path,
            "DirectoryPath": self.directory_path,
            "MediaKind": self.media_kind,
            "MimeType": self.mime_type,
            "IsBinaryMedia": self.is_binary_media,
            "InitialContent": self.initial_content,
            "LastSavedContent": self.last_saved_content,
            "EditCount": self.edit_count,
            "AddedAt": self.added_at,
            "LastModifiedByAgentAt": self.last_modified_by_agent_at,
            "CurrentSizeBytes": self.current_size_bytes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WhitelistedFile":
        return cls(
            full_path=str(data.get("FullPath", "")),
            file_name=str(data.get("FileName", "")),
            relative_path=str(data.get("RelativePath", data.get("FileName", ""))),
            directory_path=str(data.get("DirectoryPath", "")),
            media_kind=str(data.get("MediaKind", "code")),
            mime_type=str(data.get("MimeType", "text/plain")),
            is_binary_media=bool(data.get("IsBinaryMedia", False)),
            initial_content=str(data.get("InitialContent", "")),
            last_saved_content=str(data.get("LastSavedContent", "")),
            edit_count=int(data.get("EditCount", 0)),
            added_at=str(data.get("AddedAt", "")),
            last_modified_by_agent_at=str(data.get("LastModifiedByAgentAt", "")),
            current_size_bytes=int(data.get("CurrentSizeBytes", 0)),
        )


class ChatHistoryItem:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content

    def to_dict(self) -> Dict[str, Any]:
        return {
            "Role": self.role,
            "Content": self.content,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatHistoryItem":
        return cls(
            role=str(data.get("Role", data.get("role", "user"))),
            content=str(data.get("Content", data.get("content", ""))),
        )


class SearchResultItem:
    def __init__(self, title: str = "", url: str = "", snippet: str = ""):
        self.title = title
        self.url = url
        self.snippet = snippet

    def to_dict(self) -> Dict[str, Any]:
        return {
            "Title": self.title,
            "Url": self.url,
            "Snippet": self.snippet,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SearchResultItem":
        return cls(
            title=str(data.get("Title", "")),
            url=str(data.get("Url", "")),
            snippet=str(data.get("Snippet", "")),
        )


class UiStreamEvent:
    def __init__(
        self,
        event_type: str,
        content: str = "",
        id: Optional[str] = None,
        timestamp: Optional[str] = None,
        vendor_name: str = "",
        model_name: str = "",
        round: int = 0,
        max_rounds: int = 0,
        file_path: str = "",
        file_name: str = "",
        line_count: int = 0,
        edit_count: int = 0,
        command: str = "",
        working_directory: str = "",
        exit_code: int = 0,
        duration_seconds: float = 0.0,
        succeeded: bool = True,
        terminal_output: str = "",
        search_query: str = "",
        search_results: Optional[List[SearchResultItem]] = None,
    ):
        self.id = id if id else uuid.uuid4().hex
        self.event_type = event_type
        self.timestamp = timestamp if timestamp else datetime.now().strftime("%H:%M:%S")
        self.content = content
        self.vendor_name = vendor_name
        self.model_name = model_name
        self.round = round
        self.max_rounds = max_rounds
        self.file_path = file_path
        self.file_name = file_name
        self.line_count = line_count
        self.edit_count = edit_count
        self.command = command
        self.working_directory = working_directory
        self.exit_code = exit_code
        self.duration_seconds = duration_seconds
        self.succeeded = succeeded
        self.terminal_output = terminal_output
        self.search_query = search_query
        self.search_results = search_results if search_results is not None else []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "Id": self.id,
            "Type": self.event_type,
            "Timestamp": self.timestamp,
            "Content": self.content,
            "VendorName": self.vendor_name,
            "ModelName": self.model_name,
            "Round": self.round,
            "MaxRounds": self.max_rounds,
            "FilePath": self.file_path,
            "FileName": self.file_name,
            "LineCount": self.line_count,
            "EditCount": self.edit_count,
            "Command": self.command,
            "WorkingDirectory": self.working_directory,
            "ExitCode": self.exit_code,
            "DurationSeconds": self.duration_seconds,
            "Succeeded": self.succeeded,
            "TerminalOutput": self.terminal_output,
            "SearchQuery": self.search_query,
            "SearchResults": [r.to_dict() for r in self.search_results],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UiStreamEvent":
        raw_sr = data.get("SearchResults", [])
        sr = [SearchResultItem.from_dict(x) for x in raw_sr if isinstance(x, dict)]
        return cls(
            id=str(data.get("Id", uuid.uuid4().hex)),
            event_type=str(data.get("Type", "notice")),
            timestamp=str(data.get("Timestamp", "")),
            content=str(data.get("Content", "")),
            vendor_name=str(data.get("VendorName", "")),
            model_name=str(data.get("ModelName", "")),
            round=int(data.get("Round", 0)),
            max_rounds=int(data.get("MaxRounds", 0)),
            file_path=str(data.get("FilePath", "")),
            file_name=str(data.get("FileName", "")),
            line_count=int(data.get("LineCount", 0)),
            edit_count=int(data.get("EditCount", 0)),
            command=str(data.get("Command", "")),
            working_directory=str(data.get("WorkingDirectory", "")),
            exit_code=int(data.get("ExitCode", 0)),
            duration_seconds=float(data.get("DurationSeconds", 0.0)),
            succeeded=bool(data.get("Succeeded", True)),
            terminal_output=str(data.get("TerminalOutput", "")),
            search_query=str(data.get("SearchQuery", "")),
            search_results=sr,
        )


class ChatSession:
    def __init__(
        self,
        id: Optional[str] = None,
        title: str = "Default Session",
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        allowed_directories: Optional[List[str]] = None,
        whitelist: Optional[List[WhitelistedFile]] = None,
        api_history: Optional[List[ChatHistoryItem]] = None,
        ui_events: Optional[List[UiStreamEvent]] = None,
    ):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.id = id if id else uuid.uuid4().hex
        self.title = title
        self.created_at = created_at if created_at else now
        self.updated_at = updated_at if updated_at else now
        self.allowed_directories = allowed_directories if allowed_directories is not None else []
        self.whitelist = whitelist if whitelist is not None else []
        self.api_history = api_history if api_history is not None else []
        self.ui_events = ui_events if ui_events is not None else []

    def is_empty(self) -> bool:
        return len(self.ui_events) == 0 and len(self.api_history) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "Id": self.id,
            "Title": self.title,
            "CreatedAt": self.created_at,
            "UpdatedAt": self.updated_at,
            "AllowedDirectories": list(self.allowed_directories),
            "Whitelist": [f.to_dict() for f in self.whitelist],
            "ApiHistory": [h.to_dict() for h in self.api_history],
            "UiEvents": [e.to_dict() for e in self.ui_events],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatSession":
        return cls(
            id=str(data.get("Id", uuid.uuid4().hex)),
            title=str(data.get("Title", "Default Session")),
            created_at=str(data.get("CreatedAt", "")),
            updated_at=str(data.get("UpdatedAt", "")),
            allowed_directories=list(data.get("AllowedDirectories", [])),
            whitelist=[WhitelistedFile.from_dict(x) for x in data.get("Whitelist", []) if isinstance(x, dict)],
            api_history=[ChatHistoryItem.from_dict(x) for x in data.get("ApiHistory", []) if isinstance(x, dict)],
            ui_events=[UiStreamEvent.from_dict(x) for x in data.get("UiEvents", []) if isinstance(x, dict)],
        )


class SessionStoreData:
    def __init__(
        self,
        active_session_id: Optional[str] = None,
        sessions: Optional[List[ChatSession]] = None,
    ):
        if not sessions:
            first = ChatSession()
            self.sessions = [first]
            self.active_session_id = active_session_id or first.id
        else:
            self.sessions = sessions
            self.active_session_id = active_session_id or self.sessions[0].id

    def get_active_session(self) -> Optional[ChatSession]:
        for s in self.sessions:
            if s.id == self.active_session_id:
                return s
        return self.sessions[0] if self.sessions else None

    def find_empty_session(self) -> Optional[ChatSession]:
        for s in self.sessions:
            if s.is_empty():
                return s
        return None

    def prune_redundant_empty_sessions(self) -> None:
        preserved_empty_id: Optional[str] = None
        for s in self.sessions:
            if s.id == self.active_session_id and s.is_empty():
                preserved_empty_id = self.active_session_id
                break

        retained: List[ChatSession] = []
        for s in self.sessions:
            if s.is_empty():
                if preserved_empty_id is not None:
                    if s.id == preserved_empty_id:
                        retained.append(s)
                else:
                    preserved_empty_id = s.id
                    retained.append(s)
            else:
                retained.append(s)

        if not retained:
            fresh = ChatSession()
            retained.append(fresh)
            self.active_session_id = fresh.id
        elif not any(s.id == self.active_session_id for s in retained):
            self.active_session_id = retained[0].id

        self.sessions = retained

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ActiveSessionId": self.active_session_id,
            "Sessions": [s.to_dict() for s in self.sessions],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionStoreData":
        raw_sessions = data.get("Sessions", [])
        sessions = [ChatSession.from_dict(x) for x in raw_sessions if isinstance(x, dict)]
        active_id = str(data.get("ActiveSessionId", ""))
        return cls(active_session_id=active_id, sessions=sessions)


class SurgicalActionType:
    WriteFull = "WriteFull"
    SearchReplace = "SearchReplace"


class SurgicalFileEdit:
    def __init__(
        self,
        action_type: str,
        target_file: str,
        new_content: str = "",
        search_block: str = "",
        replace_block: str = "",
    ):
        self.action_type = action_type
        self.target_file = target_file
        self.new_content = new_content
        self.search_block = search_block
        self.replace_block = replace_block


class ParsedAgentResponse:
    def __init__(
        self,
        raw_text: str = "",
        explanation_text: str = "",
        file_edits: Optional[List[SurgicalFileEdit]] = None,
        terminal_commands: Optional[List[str]] = None,
        search_queries: Optional[List[str]] = None,
    ):
        self.raw_text = raw_text
        self.explanation_text = explanation_text
        self.file_edits = file_edits if file_edits is not None else []
        self.terminal_commands = terminal_commands if terminal_commands is not None else []
        self.search_queries = search_queries if search_queries is not None else []


class VerifyExecutionResult:
    def __init__(
        self,
        command: str = "",
        working_directory: str = "",
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
        duration_seconds: float = 0.0,
        timed_out: bool = False,
        cancelled: bool = False,
    ):
        self.command = command
        self.working_directory = working_directory
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_seconds = duration_seconds
        self.timed_out = timed_out
        self.cancelled = cancelled

    def succeeded(self) -> bool:
        return not self.timed_out and not self.cancelled and self.exit_code == 0

    def combined_output(self) -> str:
        parts: List[str] = []
        if self.stdout and self.stdout.strip():
            parts.append(self.stdout.rstrip())
        if self.stderr and self.stderr.strip():
            parts.append(self.stderr.rstrip())
        out = "\n".join(parts)
        return out if out.strip() else "(No terminal standard output)"
