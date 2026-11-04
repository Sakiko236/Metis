from __future__ import annotations
import os
import re
import zlib
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from config import ConfigManager
from models import FileEdit, FileEditActionType, WhitelistedFile


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
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".pytest_cache",
    ".mypy_cache",
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
    "pyo",
    "pyd",
    "class",
    "jar",
    "war",
    "ear",
    "woff",
    "woff2",
    "ttf",
    "eot",
    "ico",
}

IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "gif", "bmp"}
PDF_EXTS = {"pdf"}


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

        file_count = 0
        stack = [Path(canonical_dir)]
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
                            file_count += 1
            except Exception:
                continue

        self.update_all_relative_paths()
        return file_count, None

    @staticmethod
    def format_size_label(size_bytes: int) -> str:
        if size_bytes <= 0:
            return "0 B [EMPTY]"
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        return f"{size_bytes / (1024 * 1024):.2f} MB"

    @classmethod
    def build_directory_tree(cls, folder_path: str, max_files: int = 2000) -> str:
        p = Path(folder_path)
        if not p.is_dir():
            return f"(Directory not found: {folder_path})"

        try:
            root_path = p.resolve()
        except Exception:
            root_path = p

        root_name = root_path.name or str(root_path)
        lines: List[str] = [
            f"Directory Root: `{root_name}/` (Full Path: `{root_path}`)",
            f"{root_name}/",
        ]

        total_files = 0
        total_dirs = 0
        truncated = False

        def _walk(curr_dir: Path, prefix: str) -> None:
            nonlocal total_files, total_dirs, truncated
            if truncated:
                return
            try:
                entries = list(os.scandir(curr_dir))
            except Exception:
                return

            dirs = []
            files = []
            for entry in entries:
                name = entry.name
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if name.lower() not in IGNORED_DIRS:
                            dirs.append(entry)
                    elif entry.is_file(follow_symlinks=False):
                        ext = Path(name).suffix.lstrip(".").lower()
                        if ext not in IGNORED_EXTS:
                            files.append(entry)
                except Exception:
                    continue

            dirs.sort(key=lambda e: e.name.lower())
            files.sort(key=lambda e: e.name.lower())

            combined = [("dir", d) for d in dirs] + [("file", f) for f in files]
            count = len(combined)

            for idx, (kind, entry) in enumerate(combined):
                if truncated:
                    break
                is_last = idx == count - 1
                connector = "└── " if is_last else "├── "
                child_prefix = prefix + ("    " if is_last else "│   ")

                if kind == "dir":
                    total_dirs += 1
                    lines.append(f"{prefix}{connector}{entry.name}/")
                    _walk(Path(entry.path), child_prefix)
                else:
                    if total_files >= max_files:
                        truncated = True
                        lines.append(f"{prefix}└── ... (truncated after {max_files} files)")
                        break
                    total_files += 1
                    entry_path = Path(entry.path)
                    try:
                        rel_str = str(entry_path.relative_to(root_path)).replace("\\", "/")
                    except Exception:
                        rel_str = entry.name
                    try:
                        sz = entry.stat(follow_symlinks=False).st_size
                    except Exception:
                        sz = 0

                    ext = entry_path.suffix.lstrip(".").lower()
                    size_lbl = cls.format_size_label(sz)
                    if ext in IMAGE_EXTS:
                        tag_lbl = " | Image (Base64 upload on read)"
                    elif ext in PDF_EXTS:
                        tag_lbl = " | PDF (Base64 upload on read)"
                    else:
                        tag_lbl = ""

                    lines.append(
                        f"{prefix}{connector}{entry.name}  [path: \"{rel_str}\" | {size_lbl}{tag_lbl}]"
                    )

        _walk(root_path, "")
        lines.append(
            f"\nSummary: {total_files} {'file' if total_files == 1 else 'files'}, "
            f"{total_dirs} {'subdirectory' if total_dirs == 1 else 'subdirectories'}."
        )
        return "\n".join(lines)

    @staticmethod
    def _is_path_within_dir(target_path: Path, base_dir: Path) -> bool:
        try:
            t_res = target_path.resolve()
            b_res = base_dir.resolve()
            if hasattr(t_res, "is_relative_to"):
                return t_res.is_relative_to(b_res)
            t_str = str(t_res).lower().rstrip("\\/")
            b_str = str(b_res).lower().rstrip("\\/")
            return t_str == b_str or t_str.startswith(b_str + os.sep)
        except Exception:
            return False

    def resolve_file_in_directories(self, requested_name_or_path: str) -> Optional[Path]:
        cleaned = requested_name_or_path.strip().strip("\"'`<>")
        if not cleaned:
            return None

        norm_clean = cleaned.replace("\\", "/").lstrip("./")

        # 1. Check direct relative paths or absolute paths within allowed directories
        for d in self.directories:
            dir_path = Path(d)
            if not dir_path.is_dir():
                continue
            try:
                dir_resolved = dir_path.resolve()
            except Exception:
                dir_resolved = dir_path

            candidates: List[Path] = []
            req_p = Path(cleaned)
            if req_p.is_absolute():
                candidates.append(req_p)
            candidates.append(dir_resolved / norm_clean)

            dir_name_prefix = f"{dir_resolved.name.lower()}/"
            if norm_clean.lower().startswith(dir_name_prefix):
                stripped_sub = norm_clean[len(dir_name_prefix):]
                if stripped_sub:
                    candidates.append(dir_resolved / stripped_sub)

            for cand in candidates:
                try:
                    if cand.exists() and cand.is_file():
                        if self._is_path_within_dir(cand, dir_resolved):
                            return cand.resolve()
                except Exception:
                    continue

        # 2. Fallback: Search mounted directories for matching relative suffix or filename
        suffix_matches: List[Path] = []
        fname_matches: List[Path] = []
        target_fname = Path(norm_clean).name.lower()
        norm_clean_lower = norm_clean.lower()

        for d in self.directories:
            dir_path = Path(d)
            if not dir_path.is_dir():
                continue
            try:
                dir_resolved = dir_path.resolve()
            except Exception:
                dir_resolved = dir_path

            stack = [dir_resolved]
            while stack:
                curr = stack.pop()
                try:
                    for entry in os.scandir(curr):
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name.lower() not in IGNORED_DIRS:
                                stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            ext = Path(entry.name).suffix.lstrip(".").lower()
                            if ext in IGNORED_EXTS:
                                continue
                            entry_p = Path(entry.path)
                            try:
                                rel = str(entry_p.relative_to(dir_resolved)).replace("\\", "/").lower()
                            except Exception:
                                rel = entry.name.lower()
                            if rel == norm_clean_lower or rel.endswith("/" + norm_clean_lower):
                                suffix_matches.append(entry_p.resolve())
                            elif entry.name.lower() == target_fname:
                                fname_matches.append(entry_p.resolve())
                except Exception:
                    continue

        if len(suffix_matches) >= 1:
            return suffix_matches[0]
        if len(fname_matches) == 1:
            return fname_matches[0]

        return None

    def read_and_attach_file(self, requested_name_or_path: str) -> Tuple[bool, str, Optional[WhitelistedFile]]:
        existing = self.resolve_whitelisted_file(requested_name_or_path)
        if existing is not None:
            p = Path(existing.full_path)
            if p.exists() and p.is_file() and not existing.is_binary_media and existing.media_kind not in ("image", "pdf"):
                try:
                    latest = p.read_text(encoding="utf-8", errors="replace")
                    existing.last_saved_content = latest
                    existing.current_size_bytes = len(latest.encode("utf-8", errors="replace"))
                except Exception:
                    pass
            display_name = existing.relative_path or existing.file_name
            return True, f"Attached file `{display_name}` to conversation.", existing

        resolved_path = self.resolve_file_in_directories(requested_name_or_path)
        if resolved_path is None:
            return (
                False,
                f"[File Read Error] Target file '{requested_name_or_path}' was not found in any mounted folder or the whitelist.",
                None,
            )

        if not self.add_file(str(resolved_path)):
            return (
                False,
                f"[File Read Error] Failed to read file '{requested_name_or_path}'.",
                None,
            )

        wf = self.resolve_whitelisted_file(str(resolved_path))
        if wf is None:
            return (
                False,
                f"[File Read Error] Failed to resolve attached file '{requested_name_or_path}'.",
                None,
            )

        display_name = wf.relative_path or wf.file_name
        return True, f"Attached file `{display_name}` to conversation.", wf

    def remove_file(self, full_path: str) -> bool:
        prev_len = len(self.files)
        self.files = [x for x in self.files if x.full_path.lower() != full_path.lower()]
        self.update_all_relative_paths()
        return len(self.files) < prev_len

    def remove_directory(self, dir_path: str) -> bool:
        prev_len = len(self.directories)
        norm_dir = dir_path.lower().rstrip("\\/")
        prefix_slash = norm_dir + "/"
        prefix_bslash = norm_dir + "\\"
        self.directories = [x for x in self.directories if x.lower().rstrip("\\/") != norm_dir]
        self.files = [
            x for x in self.files
            if not (
                x.full_path.lower().startswith(prefix_slash)
                or x.full_path.lower().startswith(prefix_bslash)
            )
        ]
        self.update_all_relative_paths()
        return len(self.directories) < prev_len

    def clear(self) -> None:
        self.files.clear()
        self.directories.clear()

    def resolve_whitelisted_file(self, requested_name_or_path: str) -> Optional[WhitelistedFile]:
        cleaned = requested_name_or_path.strip().strip("\"'`<>")
        if not cleaned:
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

    def apply_edit_in_place(self, edit: FileEdit) -> Tuple[bool, str, Optional[WhitelistedFile]]:
        target_file = self.resolve_whitelisted_file(edit.target_file)
        if target_file is None:
            dir_candidate = self.resolve_file_in_directories(edit.target_file)
            if dir_candidate is not None and self.add_file(str(dir_candidate)):
                target_file = self.resolve_whitelisted_file(str(dir_candidate))

        if target_file is None:
            return (
                False,
                f"[Security Block - File Creation Prohibited] Target file '{edit.target_file}' is not in the whitelist or mounted folders. Metis strictly forbids creating or deleting files; in-place edits on existing files only!",
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

        if edit.action_type == FileEditActionType.WriteFull:
            updated_content = edit.new_content
        else:
            if is_initially_empty and edit.replace_block:
                updated_content = edit.replace_block
            else:
                if not edit.search_block and not edit.replace_block:
                    return (
                        False,
                        f"Malformed edit block for `{target_file.file_name}`: no search block and no replacement "
                        f"content were parsed, so nothing was changed. Please resend the edit with the complete "
                        f"three-line delimiter structure (search start line, divider line, replace end line).",
                        None,
                    )
                res = self.try_apply_search_replace(
                    current_content,
                    edit.search_block,
                    edit.replace_block,
                )
                if res is None:
                    return (
                        False,
                        f"SEARCH block failed to match original code in `{target_file.file_name}`. If you have not read this file yet, please read it first using <metis_read file=\"{target_file.relative_path or target_file.file_name}\" /> and ensure the SEARCH block matches the file's current content verbatim.",
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
        elif edit.action_type == FileEditActionType.WriteFull:
            status_message = (
                f"Successfully wrote `{display_name}` in-place "
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

        if not search or not search.strip():
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
        dir_paths = [Path(d) for d in self.directories if d and d.strip()]
        for f in self.files:
            fp = Path(f.full_path)
            matched_rel: Optional[str] = None
            for dp in dir_paths:
                try:
                    rel = fp.relative_to(dp)
                    matched_rel = str(rel).replace("\\", "/")
                    break
                except Exception:
                    continue
            if matched_rel is not None:
                f.relative_path = matched_rel
                continue
            try:
                rel = fp.relative_to(ws_path)
                f.relative_path = str(rel).replace("\\", "/")
            except Exception:
                f.relative_path = f.file_name
