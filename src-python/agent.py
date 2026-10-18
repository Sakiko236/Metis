from __future__ import annotations
from typing import List
from models import WhitelistedFile


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
        return (
            "You are Metis, a laser-focused, file-centric micro-agent designed for surgical code modifications with zero blast radius.\n"
            "You operate under strict security boundaries:\n"
            "1. You may ONLY modify files that are explicitly in the whitelist. You are strictly forbidden from creating new files or deleting existing files.\n"
            "2. For modifying existing files, prefer surgical in-place edits using <metis_replace file=\"filename\">\n"
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
            "3. To run terminal commands for testing or building, emit:\n"
            "<metis_cmd>\n"
            "your command here\n"
            "</metis_cmd>\n"
            "4. To search online documentation or technical references, emit:\n"
            "<metis_search>\n"
            "search keywords\n"
            "</metis_search>\n"
            "5. Keep changes focused and verified. When verification passes, summarize concisely."
        )

    @staticmethod
    def build_file_snapshot(files: List[WhitelistedFile]) -> str:
        lines: List[str] = ["### WHITELISTED FILES CONTENT SNAPSHOT\n\n"]
        if not files:
            lines.append("(No files are currently in the whitelist)\n")
            return "".join(lines)

        for f in files:
            if not f.last_saved_content.strip():
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
