from __future__ import annotations
from datetime import datetime
from typing import List, Optional
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
            "1. You may ONLY read and modify files that are explicitly in the whitelist or inside mounted workspace folders (including files in the initial snapshot and any files/folders added via [Workspace Event]). You are strictly forbidden from creating new files or deleting existing files.\n"
            "2. Mounted Folder Workflow (On-Demand File Reading): When a folder is added to the workspace, the system first prints the folder's detailed directory tree structure without pre-loading file contents. You must decide which files you need to read and request them using:\n"
            "<metis_read file=\"relative/path/to/file\" />\n"
            "(You may output multiple <metis_read file=\"...\" /> tags in a single turn to read several files at once).\n"
            "The Harness will then read the requested files and attach them to the conversation in the next round (if a requested file is an image or PDF, the Harness automatically converts it to Base64 and uploads it as multimodal input). Always read a file from a mounted folder before attempting to modify it.\n"
            "3. For modifying existing files, prefer precise in-place edits using <metis_replace file=\"filename\">\n"
            "<<<<<<< SEARCH\n"
            "[exact existing code]\n"
            "=======\n"
            "[replacement code]\n"
            ">>>>>>> REPLACE\n"
            "</metis_replace>\n"
            "For empty files or complete file rewrites, write the content using:\n"
            "<metis_write file=\"filename\">\n"
            "[new file content]\n"
            "</metis_write>\n"
            "(Note: On empty files, <metis_replace> with an empty SEARCH block is also accepted).\n"
            "4. You have access to native web search grounding provided by the API when technical documentation, framework specifications, or troubleshooting guides are needed. Use web search as appropriate, but do not force it when existing knowledge suffices.\n"
            "5. User visibility notice: The raw contents of action tags (<metis_read>, <metis_replace>, <metis_write>) are internal and NOT visible to the user. Only your direct conversational text is shown to the user. Therefore, always provide clear text explanations before or after actions so the user understands what files you are reading or modifying and what the outcome is.\n"
            "6. Keep changes focused and explain modifications clearly."
        )

    @staticmethod
    def build_file_snapshot(
        files: List[WhitelistedFile],
        directories: Optional[List[str]] = None,
    ) -> str:
        lines: List[str] = []
        dir_list = [d for d in (directories or []) if d and d.strip()]

        if dir_list:
            lines.append("### MOUNTED WORKSPACE DIRECTORIES (DETAILED TREE STRUCTURE)\n\n")
            lines.append(
                "(Note: File contents inside mounted folders are NOT pre-loaded. "
                "Inspect the directory tree below and decide which files you need to read by outputting "
                "<metis_read file=\"relative/path/to/file\" />. "
                "The Harness will attach the requested files to the conversation, converting images and PDFs to Base64 multimodal inputs.)\n\n"
            )
            for d in dir_list:
                tree_str = WhitelistManager.build_directory_tree(d)
                lines.append(f"```text\n{tree_str}\n```\n\n")

        lines.append("### WHITELISTED FILES CONTENT SNAPSHOT\n\n")
        if not files:
            if dir_list:
                lines.append(
                    "(No individual files are pre-loaded yet. Use <metis_read file=\"relative/path/to/file\" /> "
                    "to read any file from the mounted directory tree above.)\n"
                )
            else:
                lines.append("(No files are currently in the whitelist)\n")
            return "".join(lines)

        for f in files:
            if f.is_binary_media or f.media_kind in ("image", "pdf"):
                kind_label = "PDF Document" if f.media_kind == "pdf" else "Image"
                lines.append(
                    f"=== FILE: {f.relative_path} (Full Path: {f.full_path}) [{kind_label} - Uploaded as Base64 ({f.mime_type}, {f.current_size_bytes} bytes)] ===\n"
                )
                if f.last_saved_content and f.last_saved_content.strip():
                    lines.append(f"{f.last_saved_content}\n")
                lines.append(f"=== END OF FILE: {f.relative_path} ===\n\n")
            elif not f.last_saved_content.strip():
                lines.append(
                    f"=== FILE: {f.relative_path} (Full Path: {f.full_path}) [EMPTY FILE - 0 bytes] ===\n"
                    f"(This file is currently empty. Populate it using <metis_write file=\"{f.file_name}\">)\n"
                    f"=== END OF FILE: {f.relative_path} ===\n\n"
                )
            else:
                lines.append(f"=== FILE: {f.relative_path} (Full Path: {f.full_path}) ===\n")
                lines.append(f.last_saved_content)
                lines.append(f"\n=== END OF FILE: {f.relative_path} ===\n\n")

        return "".join(lines)
