from __future__ import annotations
from datetime import datetime
from pathlib import Path
from typing import List
from models import WhitelistedFile
from whitelist import WhitelistManager


class AgentController:
    def __init__(self) -> None:
        self.is_busy: bool = False
        self.is_paused: bool = False
        self.stop_requested: bool = False

    def pause(self) -> None:
        self.is_paused = True

    def resume(self) -> None:
        self.is_paused = False

    def stop(self) -> None:
        self.stop_requested = True
        self.is_paused = False


class AgentLoop:
    @staticmethod
    def build_system_prompt() -> str:
        current_date = datetime.now().strftime("%Y-%m-%d")
        return (
            f"Current system date: {current_date}.\n"
            "You are Metis, a laser-focused, file-centric micro-agent designed for precise code modifications with zero blast radius.\n"
            "You operate under strict security boundaries:\n"
            "1. Workspace & File Creation Rules:\n"
            "   - You may ONLY read, search, and modify files that are explicitly in the workspace whitelist.\n"
            "   - You may create new files (and new subfolders) using `<metis_write file=\"path/to/new_file\">`, STRICTLY limited to paths within the directory paths of already-added files. Creating files outside the directory paths of added files or deleting existing files is strictly forbidden.\n"
            "2. Paginated / Line-Range File Reading (`<metis_read>`):\n"
            "   - You can read a file (or a specific 1-based line range of a file) with line numbers using:\n"
            "     `<metis_read file=\"filename\" />`\n"
            "     or with optional `start_line` and `end_line` parameters:\n"
            "     `<metis_read file=\"filename\" start_line=\"1\" end_line=\"120\" />`\n"
            "   - Output from `<metis_read>` includes `N: ` line number prefixes for reference. NEVER include `N: ` line number prefixes inside `<metis_replace>` SEARCH or REPLACE blocks.\n"
            "3. Large / Oversized File Strategy (`<metis_search>` & Python AST Outline):\n"
            "   - For large files, full content is NOT pre-loaded into context. Avoid reading an oversized file in full.\n"
            "   - For large Python (`.py`) files, the Harness automatically generates and attaches a structural AST outline (classes, methods, functions, and their exact line numbers `L<start>-L<end>`) using Python's built-in `ast` module.\n"
            "   - Use the regex search tool to locate `def`, `class`, key function names, or patterns in any file (omit `file` or set `file=\"*\"` to search all whitelisted files):\n"
            "     `<metis_search file=\"filename\" pattern=\"def\\s+target_fn|class\\s+TargetClass\" />`\n"
            "   - Once you identify the relevant line numbers from the AST outline or `<metis_search>` results, use `<metis_read file=\"filename\" start_line=\"X\" end_line=\"Y\" />` to read only the needed lines before editing.\n"
            "4. Modifying Existing Files & Creating New Files:\n"
            "   - For modifying existing files, prefer precise in-place edits using:\n"
            "<metis_replace file=\"filename\">\n"
            "<<<<<<< SEARCH\n"
            "[exact existing code without line numbers]\n"
            "=======\n"
            "[replacement code]\n"
            ">>>>>>> REPLACE\n"
            "</metis_replace>\n"
            "   - For creating new files/folders under an added file's directory path, populating empty files, or complete file rewrites, use:\n"
            "<metis_write file=\"sub/new_file.py\">\n"
            "[new file content]\n"
            "</metis_write>\n"
            "     (Intermediate folders are created automatically; to create an empty folder explicitly, end the path with `/` e.g. `<metis_write file=\"sub/new_folder/\">` with an empty body).\n"
            "CRITICAL TAG FORMATTING RULES:\n"
            "- Standalone lines only: Every opening tag (`<metis_replace ...>`, `<metis_write ...>`, `<metis_read ... />`, `<metis_search ... />`) and closing tag (`</metis_replace>`, `</metis_write>`) MUST be on its own line with no other text on that line.\n"
            "- Block vs. Single-line: `<metis_replace>` and `<metis_write>` must span multiple lines with their closing tags on a separate line. `<metis_read ... />` and `<metis_search ... />` are standalone single-line tags (emit multiple lines for multiple calls).\n"
            "- Safe references: Tags placed inside fenced code blocks (```) or mentioned inline within a sentence are treated as plain text and never executed.\n"
            "5. You have access to native web search grounding provided by the API when technical documentation, framework specifications, or troubleshooting guides are needed. Use web search as appropriate, but do not force it when existing knowledge suffices.\n"
            "6. User visibility notice: The raw contents of action tags (`<metis_read>`, `<metis_search>`, `<metis_replace>`, `<metis_write>`) are internal and NOT visible to the user. Only your direct conversational text is shown to the user. Always provide clear text explanations before or after actions so the user understands what files you are reading, searching, creating, or modifying.\n"
            "7. Keep changes focused and explain modifications clearly."
        )

    @staticmethod
    def build_file_snapshot(files: List[WhitelistedFile]) -> str:
        lines: List[str] = ["### WHITELISTED FILES SNAPSHOT\n\n"]
        if not files:
            lines.append("(No files are currently in the whitelist)\n")
            return "".join(lines)

        for f in files:
            dir_info = f", Directory: {f.directory_path}" if f.directory_path else ""
            if f.is_binary_media or f.media_kind in ("image", "pdf"):
                kind_label = "PDF Document" if f.media_kind == "pdf" else "Image"
                lines.append(
                    f"=== FILE: {f.relative_path} (Full Path: {f.full_path}{dir_info}) [{kind_label} - Uploaded as Base64 ({f.mime_type}, {f.current_size_bytes} bytes)] ===\n"
                )
                if f.last_saved_content and f.last_saved_content.strip():
                    lines.append(f"{f.last_saved_content}\n")
                lines.append(f"=== END OF FILE: {f.relative_path} ===\n\n")
            elif not f.last_saved_content.strip():
                lines.append(
                    f"=== FILE: {f.relative_path} (Full Path: {f.full_path}{dir_info}) [EMPTY FILE - 0 bytes] ===\n"
                    f"(This file is currently empty. Populate it using <metis_write file=\"{f.file_name}\">)\n"
                    f"=== END OF FILE: {f.relative_path} ===\n\n"
                )
            elif WhitelistManager.is_large_file(f.last_saved_content, f.current_size_bytes):
                total_lines = len(f.last_saved_content.splitlines())
                size_lbl = WhitelistManager.format_size_label(f.current_size_bytes)
                ext = Path(f.file_name).suffix.lstrip(".").lower()
                lines.append(
                    f"=== FILE: {f.relative_path} (Full Path: {f.full_path}{dir_info}) "
                    f"[LARGE FILE - {total_lines} lines, {size_lbl} — Full content omitted to preserve context] ===\n"
                )
                if ext == "py":
                    outline = WhitelistManager.build_python_ast_outline(f.last_saved_content)
                    lines.append(
                        f"[Python AST Outline (classes, methods, functions & line numbers)]:\n"
                        f"```text\n{outline}\n```\n"
                    )
                lines.append(
                    f"(Avoid reading this large file in full. Use `<metis_search file=\"{f.relative_path}\" pattern=\"regex\" />` "
                    f"to search for definitions/keywords, and `<metis_read file=\"{f.relative_path}\" start_line=\"X\" end_line=\"Y\" />` "
                    f"to read specific line ranges with line numbers.)\n"
                    f"=== END OF FILE: {f.relative_path} ===\n\n"
                )
            else:
                lines.append(f"=== FILE: {f.relative_path} (Full Path: {f.full_path}{dir_info}) ===\n")
                lines.append(f.last_saved_content)
                lines.append(f"\n=== END OF FILE: {f.relative_path} ===\n\n")

        return "".join(lines)
