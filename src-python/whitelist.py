from __future__ import annotations
import ast
import os
import re
import zlib
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from config import ConfigManager
from models import FileEdit, FileEditActionType, WhitelistedFile


IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "gif", "bmp"}
PDF_EXTS = {"pdf"}


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


class WhitelistManager:
    LARGE_FILE_LINE_THRESHOLD = 250
    LARGE_FILE_BYTE_THRESHOLD = 15_000
    MAX_READ_RANGE_LINES = 300

    def __init__(self) -> None:
        self.files: List[WhitelistedFile] = []

    def bind_session_list(self, session_files: List[WhitelistedFile]) -> None:
        self.files = list(session_files)
        self.update_all_relative_paths()

    def get_files(self) -> List[WhitelistedFile]:
        return self.files

    def get_allowed_base_directories(self) -> List[Path]:
        dirs: List[Path] = []
        for f in self.files:
            raw_dir = f.directory_path.strip() if f.directory_path else ""
            cand_dir = Path(raw_dir) if raw_dir else Path(f.full_path).parent
            try:
                resolved = cand_dir.resolve()
                if resolved.is_dir() and all(str(resolved).lower() != str(d).lower() for d in dirs):
                    dirs.append(resolved)
            except Exception:
                continue
        return dirs

    def get_active_workspace_directory(self) -> str:
        allowed_dirs = self.get_allowed_base_directories()
        if allowed_dirs:
            if len(allowed_dirs) > 1:
                try:
                    common = Path(os.path.commonpath([str(d) for d in allowed_dirs]))
                    if common.is_dir() and any(str(common).lower() == str(d).lower() for d in allowed_dirs):
                        return str(common)
                except Exception:
                    pass
            return str(allowed_dirs[0])
        fallback = ConfigManager.get_app_data_dir() / "workspace"
        fallback.mkdir(parents=True, exist_ok=True)
        return str(fallback)

    @classmethod
    def is_large_file(cls, content: str, size_bytes: int = 0) -> bool:
        if size_bytes > cls.LARGE_FILE_BYTE_THRESHOLD or len(content) > cls.LARGE_FILE_BYTE_THRESHOLD:
            return True
        return len(content.splitlines()) > cls.LARGE_FILE_LINE_THRESHOLD

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
    def build_python_ast_outline(cls, content: str) -> str:
        """Build a concise structural outline (classes, methods, functions & line numbers) using Python's built-in ast module."""
        if not content or not content.strip():
            return "(Empty Python file)"

        def _unparse_safe(node: Optional[ast.AST]) -> str:
            if node is None:
                return ""
            try:
                if hasattr(ast, "unparse"):
                    return ast.unparse(node)
            except Exception:
                pass
            return ""

        def _format_func_sig(node: ast.FunctionDef | ast.AsyncFunctionDef, indent: str = "") -> str:
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            args_str = _unparse_safe(node.args)
            ret_str = _unparse_safe(node.returns)
            ret_part = f" -> {ret_str}" if ret_str else ""
            start_l = getattr(node, "lineno", 0)
            end_l = getattr(node, "end_lineno", start_l)
            line_tag = f"L{start_l}-L{end_l}" if end_l and end_l != start_l else f"L{start_l}"
            return f"{indent}{line_tag}: {prefix} {node.name}({args_str}){ret_part}"

        try:
            tree = ast.parse(content)
            outline_lines: List[str] = []
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    bases = [_unparse_safe(b) for b in node.bases]
                    bases = [b for b in bases if b]
                    base_str = f"({', '.join(bases)})" if bases else ""
                    start_l = getattr(node, "lineno", 0)
                    end_l = getattr(node, "end_lineno", start_l)
                    line_tag = f"L{start_l}-L{end_l}" if end_l and end_l != start_l else f"L{start_l}"
                    outline_lines.append(f"{line_tag}: class {node.name}{base_str}:")
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            outline_lines.append(_format_func_sig(sub, indent="  "))
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    outline_lines.append(_format_func_sig(node, indent=""))
                elif isinstance(node, ast.Assign):
                    targets = [_unparse_safe(t) for t in node.targets]
                    targets = [t for t in targets if t]
                    if targets:
                        start_l = getattr(node, "lineno", 0)
                        outline_lines.append(f"L{start_l}: {', '.join(targets)} = ...")
                elif isinstance(node, ast.AnnAssign):
                    target_str = _unparse_safe(node.target)
                    if target_str:
                        start_l = getattr(node, "lineno", 0)
                        outline_lines.append(f"L{start_l}: {target_str} = ...")

            if outline_lines:
                return "\n".join(outline_lines)
            return "(No top-level classes, functions, or global assignments found)"
        except SyntaxError as se:
            # Fallback to regex scan if the Python file currently has a syntax error
            fallback_lines: List[str] = [f"(Note: AST parse encountered SyntaxError at line {se.lineno}; showing regex symbol scan)"]
            sig_re = re.compile(r"^(\s*)(?:async\s+def|def|class)\s+[a-zA-Z_]\w*.*$")
            for idx, raw_line in enumerate(content.splitlines(), start=1):
                if sig_re.match(raw_line):
                    fallback_lines.append(f"L{idx}: {raw_line.rstrip()}")
            return "\n".join(fallback_lines)
        except Exception as e:
            return f"(Failed to generate AST outline: {e})"

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
            is_img = ext in IMAGE_EXTS

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

    def read_and_attach_file(
        self,
        requested_name_or_path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> Tuple[bool, str, Optional[WhitelistedFile], str]:
        existing = self.resolve_whitelisted_file(requested_name_or_path)
        if existing is None:
            return (
                False,
                f"[File Read Error] Target file '{requested_name_or_path}' is not in the workspace whitelist.",
                None,
                "",
            )

        p = Path(existing.full_path)
        if p.exists() and p.is_file() and not existing.is_binary_media and existing.media_kind not in ("image", "pdf"):
            try:
                latest = p.read_text(encoding="utf-8", errors="replace")
                existing.last_saved_content = latest
                existing.current_size_bytes = len(latest.encode("utf-8", errors="replace"))
            except Exception:
                pass

        display_name = existing.relative_path or existing.file_name
        if existing.is_binary_media or existing.media_kind in ("image", "pdf"):
            return True, display_name, existing, ""

        content = existing.last_saved_content or ""
        all_lines = content.splitlines()
        total_lines = len(all_lines)
        ext = Path(display_name).suffix.lstrip(".")
        lang = get_language_for_ext(ext)

        if total_lines == 0:
            attachment = (
                f"[Harness File Attachment] Requested file `{display_name}` is empty (0 lines / 0 bytes):\n"
                f"```{lang}\n[EMPTY FILE - 0 bytes]\n```"
            )
            return True, f"{display_name} (empty)", existing, attachment

        has_explicit_range = start_line is not None or end_line is not None
        is_large = self.is_large_file(content, existing.current_size_bytes)

        # Oversized file protection when no line range is specified
        if not has_explicit_range and is_large:
            size_lbl = self.format_size_label(existing.current_size_bytes)
            if ext.lower() == "py":
                outline = self.build_python_ast_outline(content)
                attachment = (
                    f"[Harness Large File Outline] `{display_name}` is a large Python file "
                    f"({total_lines} lines, {size_lbl}). Direct full-file read is avoided.\n"
                    f"Below is its structural outline generated via Python's `ast` module:\n"
                    f"```text\n{outline}\n```\n"
                    f"Use `<metis_read file=\"{display_name}\" start_line=\"X\" end_line=\"Y\" />` to read specific line ranges, "
                    f"or `<metis_search file=\"{display_name}\" pattern=\"regex\" />` to search within the file."
                )
                return True, f"{display_name} (AST Outline, {total_lines} lines)", existing, attachment
            else:
                preview_end = min(total_lines, 80)
                numbered_preview = "\n".join(
                    f"{ln}: {all_lines[ln - 1]}" for ln in range(1, preview_end + 1)
                )
                attachment = (
                    f"[Harness Large File Preview] `{display_name}` is a large file ({total_lines} lines, {size_lbl}). "
                    f"Direct full-file read is avoided; showing preview of lines 1-{preview_end} of {total_lines}:\n"
                    f"(Note: Line numbers `N: ` are for reference only; do NOT include them in <metis_replace> blocks.)\n"
                    f"```{lang}\n{numbered_preview}\n```\n"
                    f"Use `<metis_search file=\"{display_name}\" pattern=\"regex\" />` to search for symbols/keywords, "
                    f"or `<metis_read file=\"{display_name}\" start_line=\"X\" end_line=\"Y\" />` to read a specific line range."
                )
                return True, f"{display_name} (L1-L{preview_end} of {total_lines})", existing, attachment

        s_line = max(1, start_line if start_line is not None else 1)
        e_line = min(total_lines, end_line if end_line is not None else total_lines)

        if s_line > total_lines:
            return (
                False,
                f"[File Read Error] Requested start_line={s_line} exceeds total lines ({total_lines}) in `{display_name}`.",
                existing,
                "",
            )
        if e_line < s_line:
            e_line = s_line

        trunc_notice = ""
        if e_line - s_line + 1 > self.MAX_READ_RANGE_LINES:
            e_line = min(total_lines, s_line + self.MAX_READ_RANGE_LINES - 1)
            trunc_notice = (
                f"\n(Note: Read range capped at {self.MAX_READ_RANGE_LINES} lines -> showing lines {s_line}-{e_line} of {total_lines}. "
                f"To continue reading, request start_line=\"{e_line + 1}\".)"
            )

        numbered_body = "\n".join(
            f"{ln}: {all_lines[ln - 1]}" for ln in range(s_line, e_line + 1)
        )
        attachment = (
            f"[Harness File Attachment] Requested file `{display_name}` (Showing lines {s_line}-{e_line} of {total_lines}):\n"
            f"(Note: Line numbers `N: ` are for reference only; do NOT include line number prefixes inside <metis_replace> SEARCH/REPLACE blocks.)\n"
            f"```{lang}\n{numbered_body}\n```{trunc_notice}"
        )
        range_label = f"{display_name} (L{s_line}-L{e_line}/{total_lines})"
        return True, range_label, existing, attachment

    def search_in_files(
        self,
        pattern: str,
        requested_file: str = "",
        context_lines: int = 1,
        max_matches: int = 80,
    ) -> Tuple[bool, str, str, Optional[WhitelistedFile]]:
        cleaned_pat = pattern.strip()
        if not cleaned_pat:
            return False, "[File Search Error] Search pattern cannot be empty.", "", None

        req_file_clean = requested_file.strip().strip("\"'`<>")
        if req_file_clean and req_file_clean != "*":
            wf = self.resolve_whitelisted_file(req_file_clean)
            if wf is None:
                return (
                    False,
                    f"[File Search Error] Target file '{requested_file}' is not in the workspace whitelist.",
                    "",
                    None,
                )
            if wf.is_binary_media or wf.media_kind in ("image", "pdf"):
                return (
                    False,
                    f"[File Search Error] Target file '{requested_file}' is a binary media file and cannot be searched with regex.",
                    "",
                    wf,
                )
            target_files = [wf]
        else:
            target_files = [
                f for f in self.files
                if not f.is_binary_media and f.media_kind not in ("image", "pdf")
            ]
            if not target_files:
                return (
                    False,
                    "[File Search Error] No searchable text files in the workspace whitelist.",
                    "",
                    None,
                )

        # Refresh file contents from disk
        for f in target_files:
            p = Path(f.full_path)
            if p.exists() and p.is_file():
                try:
                    latest = p.read_text(encoding="utf-8", errors="replace")
                    f.last_saved_content = latest
                    f.current_size_bytes = len(latest.encode("utf-8", errors="replace"))
                except Exception:
                    pass

        regex_note = ""
        try:
            compiled = re.compile(cleaned_pat)
            compiled_icase = re.compile(cleaned_pat, re.IGNORECASE)
        except re.error:
            escaped = re.escape(cleaned_pat)
            compiled = re.compile(escaped)
            compiled_icase = re.compile(escaped, re.IGNORECASE)
            regex_note = " (invalid regex syntax; fell back to literal substring match)"

        def _collect_matches(rx: re.Pattern) -> Tuple[int, List[str]]:
            total_hits = 0
            sections: List[str] = []
            for f in target_files:
                lines = (f.last_saved_content or "").splitlines()
                n_lines = len(lines)
                disp = f.relative_path or f.file_name
                file_hits: List[str] = []
                for idx, line_text in enumerate(lines, start=1):
                    if rx.search(line_text):
                        total_hits += 1
                        if total_hits <= max_matches:
                            if context_lines > 0:
                                c_start = max(1, idx - context_lines)
                                c_end = min(n_lines, idx + context_lines)
                                snippet = []
                                for c_ln in range(c_start, c_end + 1):
                                    marker = ">" if c_ln == idx else " "
                                    snippet.append(f"  {marker} {c_ln}: {lines[c_ln - 1]}")
                                file_hits.append("\n".join(snippet))
                            else:
                                file_hits.append(f"  > {idx}: {line_text}")
                if file_hits:
                    header = f"=== File: `{disp}` ({n_lines} lines total) ==="
                    sections.append(header + "\n" + "\n  ---\n".join(file_hits))
            return total_hits, sections

        hit_count, match_sections = _collect_matches(compiled)
        if hit_count == 0:
            hit_count, match_sections = _collect_matches(compiled_icase)
            if hit_count > 0:
                regex_note += " (case-insensitive)"

        scope_label = (target_files[0].relative_path or target_files[0].file_name) if len(target_files) == 1 else f"{len(target_files)} files"
        first_wf = target_files[0] if target_files else None

        if hit_count == 0:
            report = (
                f"[Harness File Search Result] Pattern `{cleaned_pat}`{regex_note} in `{scope_label}`: "
                f"No matches found."
            )
            ui_summary = f"Searched {scope_label} for /{cleaned_pat}/ (0 matches)"
            return True, ui_summary, report, first_wf

        trunc_msg = (
            f"\n(Showing first {max_matches} of {hit_count} matches. Refine your regex pattern if needed.)"
            if hit_count > max_matches
            else ""
        )
        body_text = "\n\n".join(match_sections)
        report = (
            f"[Harness File Search Result] Pattern `{cleaned_pat}`{regex_note} in `{scope_label}` "
            f"({hit_count} {'match' if hit_count == 1 else 'matches'} found):\n"
            f"```text\n{body_text}\n```{trunc_msg}\n"
            f"(Use `<metis_read file=\"...\" start_line=\"X\" end_line=\"Y\" />` to inspect surrounding code before editing.)"
        )
        ui_summary = f"Searched {scope_label} for /{cleaned_pat}/ ({hit_count} {'match' if hit_count == 1 else 'matches'})"
        return True, ui_summary, report, first_wf

    def remove_file(self, full_path: str) -> bool:
        prev_len = len(self.files)
        self.files = [x for x in self.files if x.full_path.lower() != full_path.lower()]
        self.update_all_relative_paths()
        return len(self.files) < prev_len

    def clear(self) -> None:
        self.files.clear()

    def resolve_whitelisted_file(self, requested_name_or_path: str) -> Optional[WhitelistedFile]:
        cleaned = requested_name_or_path.strip().strip("\"'`<>")
        if not cleaned:
            return None

        try:
            p = Path(cleaned)
            if p.exists() and p.is_file():
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

    def resolve_new_path_in_allowed_dirs(self, requested_path: str) -> Tuple[Optional[Path], Optional[str]]:
        """
        Resolve a new file or folder path for <metis_write>, strictly limited to
        paths inside the directories of already-added whitelisted files.
        """
        cleaned = requested_path.strip().strip("\"'`<>")
        if not cleaned:
            return None, "[Security Block] Target file path cannot be empty."

        allowed_dirs = self.get_allowed_base_directories()
        if not allowed_dirs:
            return (
                None,
                f"[Security Block - File Creation Prohibited] Cannot create '{cleaned}' because no files have been added to the workspace yet. "
                f"<metis_write> can only create new files or folders under the directory paths of already-added files.",
            )

        req_p = Path(cleaned)
        if req_p.is_absolute():
            try:
                cand = req_p.resolve()
            except Exception:
                return None, f"[Security Block] Invalid target path '{cleaned}'."
            if any(self._is_path_within_dir(cand, base_d) for base_d in allowed_dirs):
                return cand, None
            allowed_str = ", ".join(str(d) for d in allowed_dirs)
            return (
                None,
                f"[Security Block - Path Outside Allowed Directories] Cannot create '{cleaned}'. "
                f"New files and folders can ONLY be created within the directory paths of already-added files ({allowed_str}).",
            )

        # Relative path resolution against allowed base directories
        norm_rel = cleaned.replace("\\", "/").lstrip("/")
        while norm_rel.startswith("./"):
            norm_rel = norm_rel[2:]

        if not norm_rel:
            return None, f"[Security Block] Invalid relative path '{cleaned}'."

        chosen_base = allowed_dirs[0]
        rel_sub = norm_rel

        # If relative path starts with the directory name of one of the allowed base dirs, match it
        first_seg = norm_rel.split("/", 1)[0].lower()
        for base_d in allowed_dirs:
            if base_d.name.lower() == first_seg and "/" in norm_rel:
                rest = norm_rel.split("/", 1)[1]
                if rest and not (base_d / first_seg).exists():
                    chosen_base = base_d
                    rel_sub = rest
                    break

        try:
            cand = (chosen_base / rel_sub).resolve()
        except Exception:
            return None, f"[Security Block] Failed to resolve target path '{cleaned}'."

        if not any(self._is_path_within_dir(cand, base_d) for base_d in allowed_dirs):
            allowed_str = ", ".join(str(d) for d in allowed_dirs)
            return (
                None,
                f"[Security Block - Path Traversal Blocked] Target path '{cleaned}' resolves outside the allowed directory paths of added files ({allowed_str}).",
            )

        return cand, None

    def apply_edit_in_place(self, edit: FileEdit) -> Tuple[bool, str, Optional[WhitelistedFile]]:
        raw_target = edit.target_file.strip().strip("\"'`<>")
        is_explicit_dir = raw_target.endswith(("/", "\\"))

        target_file = None if is_explicit_dir else self.resolve_whitelisted_file(edit.target_file)

        if target_file is None:
            if edit.action_type != FileEditActionType.WriteFull:
                return (
                    False,
                    f"[Security Block] Target file '{edit.target_file}' is not in the workspace whitelist. "
                    f"<metis_replace> only modifies existing whitelisted files; to create a new file under an added file's directory path, use <metis_write file=\"{edit.target_file}\">.",
                    None,
                )

            new_path, err_msg = self.resolve_new_path_in_allowed_dirs(edit.target_file)
            if new_path is None:
                return False, err_msg or "Failed to resolve new path.", None

            # Support explicit folder creation via <metis_write file="path/to/folder/">
            if is_explicit_dir or (new_path.exists() and new_path.is_dir()):
                try:
                    new_path.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    return False, f"Failed to create folder [{new_path}]: {e}", None
                ws_dir = Path(self.get_active_workspace_directory())
                try:
                    rel_dir = str(new_path.relative_to(ws_dir)).replace("\\", "/") + "/"
                except Exception:
                    rel_dir = new_path.name + "/"
                placeholder_wf = WhitelistedFile(
                    full_path=str(new_path),
                    file_name=new_path.name + "/",
                    directory_path=str(new_path.parent),
                    content="",
                    relative_path=rel_dir,
                )
                return True, f"Successfully created folder `{rel_dir}`.", placeholder_wf

            # Check if resolved new_path already matches an existing whitelisted file
            existing_by_canon = self.resolve_whitelisted_file(str(new_path))
            if existing_by_canon is not None:
                target_file = existing_by_canon
            else:
                updated_content = edit.new_content
                try:
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(new_path, "w", encoding="utf-8", newline="") as fp:
                        fp.write(updated_content)
                except Exception as e:
                    return False, f"Failed to create new file [{new_path.name}]: {e}", None

                byte_len = len(updated_content.encode("utf-8", errors="replace"))
                new_wf = WhitelistedFile(
                    full_path=str(new_path),
                    file_name=new_path.name,
                    directory_path=str(new_path.parent),
                    content=updated_content,
                    initial_content="",
                    last_saved_content=updated_content,
                    edit_count=1,
                    current_size_bytes=byte_len,
                )
                self.files.append(new_wf)
                self.update_all_relative_paths()

                line_count = len(updated_content.splitlines()) if updated_content else 0
                display_name = new_wf.relative_path or new_wf.file_name
                status_message = (
                    f"Successfully created new file `{display_name}` "
                    f"({line_count} lines / {byte_len} bytes)."
                )
                return True, status_message, new_wf

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
                        f"SEARCH block failed to match original code in `{target_file.file_name}`. Please read the relevant lines first using <metis_read file=\"{target_file.relative_path or target_file.file_name}\" start_line=\"...\" end_line=\"...\" /> and ensure the SEARCH block matches the file's current content verbatim (without line numbers).",
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
        for f in self.files:
            fp = Path(f.full_path)
            try:
                rel = fp.relative_to(ws_path)
                f.relative_path = str(rel).replace("\\", "/")
            except Exception:
                f.relative_path = f.file_name
