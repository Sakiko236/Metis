from __future__ import annotations
import os
import re
import zlib
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from config import ConfigManager
from models import SurgicalActionType, SurgicalFileEdit, WhitelistedFile


IGNORED_DIRS = {
    ".git",
    ".svn",
    ".hg",
    "node_modules",
    "bin",
    "obj",
    "__pycache__",
    "target",
    ".vscode",
    ".idea",
}

IGNORED_EXTS = {
    "exe",
    "dll",
    "pdb",
    "obj",
    "o",
    "so",
    "dylib",
    "zip",
    "tar",
    "gz",
    "7z",
    "pyc",
}


class WhitelistManager:
    def __init__(self) -> None:
        self.files: List[WhitelistedFile] = []
        self.directories: List[str] = []

    def bind_session_list(
        self,
        session_files: List[WhitelistedFile],
        session_dirs: List[str],
    ) -> None:
        self.files = list(session_files)
        self.directories = list(session_dirs)
        self.update_all_relative_paths()

    def get_files(self) -> List[WhitelistedFile]:
        return self.files

    def get_directories(self) -> List[str]:
        return self.directories

    def get_active_workspace_directory(self) -> str:
        for d in self.directories:
            if d and d.strip() and Path(d).is_dir():
                return d
        for f in self.files:
            if f.directory_path and f.directory_path.strip() and Path(f.directory_path).is_dir():
                return f.directory_path
        fallback = ConfigManager.get_app_data_dir() / "workspace"
        fallback.mkdir(parents=True, exist_ok=True)
        return str(fallback)

    @staticmethod
    def extract_pdf_text(data_bytes: bytes) -> str:
        """Extract readable text from PDF bytes using standard library zlib & regex."""
        try:
            texts = []
            stream_re = re.compile(rb"stream[\r\n]+(.*?)[\r\n]+endstream", re.DOTALL)
            for match in stream_re.finditer(data_bytes):
                stream_data = match.group(1)
                try:
                    decompressed = zlib.decompress(stream_data)
                except Exception:
                    decompressed = stream_data

                for tj in re.finditer(rb"\((.*?)\)\s*Tj", decompressed):
                    try:
                        texts.append(tj.group(1).decode("utf-8", errors="ignore"))
                    except Exception:
                        pass
                for tja in re.finditer(rb"\[(.*?)\]\s*TJ", decompressed):
                    inner = tja.group(1)
                    for part in re.finditer(rb"\((.*?)\)", inner):
                        try:
                            texts.append(part.group(1).decode("utf-8", errors="ignore"))
                        except Exception:
                            pass
            cleaned = " ".join(texts).strip()
            return cleaned if len(cleaned) > 10 else ""
        except Exception:
            return ""

    def add_file(self, full_path: str) -> bool:
        try:
            p = Path(full_path)
            if not p.exists() or p.is_dir():
                return False
            try:
                canonical_str = str(p.resolve())
            except Exception:
                canonical_str = str(p)

            if any(x.full_path.lower() == canonical_str.lower() for x in self.files):
                return True

            file_name = p.name
            dir_path = str(p.parent)
            ext = p.suffix.lstrip(".").lower()

            is_pdf = ext == "pdf"
            is_img = ext in {"png", "jpg", "jpeg", "webp", "gif", "bmp"}

            raw_file_bytes = b""
            if is_pdf or is_img:
                try:
                    raw_file_bytes = p.read_bytes()
                except Exception:
                    pass

            if is_pdf:
                extracted = self.extract_pdf_text(raw_file_bytes) if raw_file_bytes else ""
                if extracted:
                    content = f"[Attached PDF Document: {file_name} ({len(extracted)} characters extracted)]\n\n{extracted}"
                else:
                    content = f"[Attached PDF Document: {file_name} (Multimodal visual document)]"
            elif is_img:
                content = f"[Attached Image: {file_name} (Multimodal visual input)]"
            else:
                try:
                    content = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    content = ""

            file_size = (
                len(raw_file_bytes)
                if (is_pdf or is_img) and raw_file_bytes
                else len(content.encode("utf-8", errors="replace"))
            )

            wf = WhitelistedFile(
                full_path=canonical_str,
                file_name=file_name,
                directory_path=dir_path,
                content=content,
                current_size_bytes=file_size,
            )
            if is_pdf:
                wf.media_kind = "pdf"
                wf.mime_type = "application/pdf"
                wf.is_binary_media = True
            elif is_img:
                wf.media_kind = "image"
                wf.mime_type = f"image/{'jpeg' if ext == 'jpg' else ext}"
                wf.is_binary_media = True

            self.files.append(wf)
            self.update_all_relative_paths()
            return True
        except Exception:
            return False

    def add_directory(self, folder_path: str) -> Tuple[int, Optional[str]]:
        p = Path(folder_path)
        if not p.is_dir():
            return 0, "Not a valid directory"

        try:
            canonical_dir = str(p.resolve())
        except Exception:
            canonical_dir = str(p)

        if not any(d.lower() == canonical_dir.lower() for d in self.directories):
            self.directories.append(canonical_dir)

        loaded = 0
        stack = [p]
        while stack:
            current_dir = stack.pop()
            try:
                for entry in os.scandir(current_dir):
                    name = entry.name
                    if entry.is_dir(follow_symlinks=False):
                        if name.lower() not in IGNORED_DIRS:
                            stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        ext = Path(name).suffix.lstrip(".").lower()
                        if ext not in IGNORED_EXTS:
                            if self.add_file(entry.path):
                                loaded += 1
            except Exception:
                continue

        self.update_all_relative_paths()
        return loaded, None

    def remove_file(self, full_path: str) -> bool:
        prev_len = len(self.files)
        self.files = [x for x in self.files if x.full_path.lower() != full_path.lower()]
        self.update_all_relative_paths()
        return len(self.files) < prev_len

    def remove_directory(self, dir_path: str) -> bool:
        prev_len = len(self.directories)
        norm_dir = dir_path.lower()
        self.directories = [x for x in self.directories if x.lower() != norm_dir]
        self.files = [x for x in self.files if not x.full_path.lower().startswith(norm_dir)]
        self.update_all_relative_paths()
        return len(self.directories) < prev_len

    def clear(self) -> None:
        self.files.clear()
        self.directories.clear()

    def resolve_whitelisted_file(self, requested_name_or_path: str) -> Optional[WhitelistedFile]:
        cleaned = requested_name_or_path.strip().strip("\"'`<>")
        if not cleaned:
            if len(self.files) == 1:
                return self.files[0]
            return None

        try:
            p = Path(cleaned)
            if p.exists():
                canon_str = str(p.resolve()).lower()
                for f in self.files:
                    if f.full_path.lower() == canon_str:
                        return f
        except Exception:
            pass

        norm_req = cleaned.replace("/", "\\").lstrip(".\\").lower()
        for f in self.files:
            if f.relative_path.replace("/", "\\").lower() == norm_req:
                return f

        target_fname = Path(cleaned).name.lower()
        cleaned_lower = cleaned.lower()
        matches = [
            f
            for f in self.files
            if f.file_name.lower() == cleaned_lower or f.file_name.lower() == target_fname
        ]
        if len(matches) == 1:
            return matches[0]

        return None

    def apply_edit_in_place(self, edit: SurgicalFileEdit) -> Tuple[bool, str, Optional[WhitelistedFile]]:
        target_file = self.resolve_whitelisted_file(edit.target_file)
        if target_file is None:
            return (
                False,
                f"[Security Block - File Creation Prohibited] Target file '{edit.target_file}' is not in the whitelist. Metis strictly forbids creating or deleting files; in-place edits on existing files only!",
                None,
            )

        p = Path(target_file.full_path)
        if p.exists():
            try:
                current_content = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                current_content = target_file.last_saved_content
        else:
            current_content = target_file.last_saved_content

        is_initially_empty = len(current_content.strip()) == 0

        if edit.action_type == SurgicalActionType.WriteFull:
            updated_content = edit.new_content
        else:
            if is_initially_empty:
                updated_content = edit.replace_block
            else:
                res = self.try_apply_search_replace(
                    current_content,
                    edit.search_block,
                    edit.replace_block,
                )
                if res is None:
                    return (
                        False,
                        f"SEARCH block failed to match original code in `{target_file.file_name}`. Please ensure the SEARCH block matches the file's current content verbatim.",
                        None,
                    )
                updated_content = res

        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="") as fp:
                fp.write(updated_content)
        except Exception as e:
            return False, f"Failed to write file [{target_file.file_name}]: {e}", None

        byte_len = len(updated_content.encode("utf-8", errors="replace"))
        target_file.last_saved_content = updated_content
        target_file.edit_count += 1
        target_file.current_size_bytes = byte_len
        target_file.last_modified_by_agent_at = datetime.now().strftime("%H:%M:%S")

        line_count = len(updated_content.splitlines()) if updated_content else 0
        display_name = target_file.relative_path if target_file.relative_path else target_file.file_name

        if is_initially_empty:
            status_message = (
                f"Successfully populated empty file `{display_name}` in-place "
                f"({line_count} lines / {byte_len} bytes)."
            )
        else:
            status_message = (
                f"Successfully replaced 1 occurrence in `{display_name}` "
                f"({line_count} lines / {byte_len} bytes)."
            )

        return True, status_message, target_file

    def revert_file_to_initial(self, full_path: str) -> Tuple[bool, str]:
        target = self.resolve_whitelisted_file(full_path)
        if target is None:
            return False, f"Target file '{full_path}' not in whitelist"

        init = target.initial_content
        try:
            with open(target.full_path, "w", encoding="utf-8", newline="") as fp:
                fp.write(init)
        except Exception as e:
            return False, f"Failed to revert file: {e}"

        target.last_saved_content = init
        target.edit_count = 0
        target.current_size_bytes = len(init.encode("utf-8", errors="replace"))
        target.last_modified_by_agent_at = datetime.now().strftime("%H:%M:%S")
        return True, f"Reverted {target.file_name} to initial state."

    @staticmethod
    def try_apply_search_replace(current: str, search: str, replace: str) -> Optional[str]:
        if not current.strip():
            return replace

        if not search:
            return None

        # 1. Exact match
        idx = current.find(search)
        if idx != -1:
            return current[:idx] + replace + current[idx + len(search) :]

        # 2. Normalized CRLF/LF match
        norm_cur = current.replace("\r\n", "\n")
        norm_search = search.replace("\r\n", "\n")
        norm_replace = replace.replace("\r\n", "\n")
        uses_crlf = "\r\n" in current

        idx = norm_cur.find(norm_search)
        if idx != -1:
            out = norm_cur[:idx] + norm_replace + norm_cur[idx + len(norm_search) :]
            return out.replace("\n", "\r\n") if uses_crlf else out

        # 3. Line-by-line whitespace-trimmed match
        cur_lines = norm_cur.splitlines()
        search_lines = norm_search.strip("\n").splitlines()

        if search_lines and len(cur_lines) >= len(search_lines):
            s_len = len(search_lines)
            for i in range(len(cur_lines) - s_len + 1):
                match_found = True
                for j in range(s_len):
                    if cur_lines[i + j].strip() != search_lines[j].strip():
                        match_found = False
                        break
                if match_found:
                    replace_lines = norm_replace.strip("\n").splitlines()
                    new_lines = cur_lines[:i] + replace_lines + cur_lines[i + s_len :]
                    out = "\n".join(new_lines)
                    return out.replace("\n", "\r\n") if uses_crlf else out

        return None

    def update_all_relative_paths(self) -> None:
        ws_dir = self.get_active_workspace_directory()
        ws_path = Path(ws_dir)
        for f in self.files:
            try:
                rel = Path(f.full_path).relative_to(ws_path)
                f.relative_path = str(rel).replace("\\", "/")
            except Exception:
                f.relative_path = f.file_name
