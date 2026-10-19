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
            "4. You have access to native web search grounding provided by the API when technical documentation, framework specifications, or troubleshooting guides are needed. Use web search as appropriate, but do not force it when existing knowledge suffices.\n"
            "5. User visibility notice: The raw contents of tool invocations (<metis_cmd>, <metis_replace>, <metis_write>) and their execution outputs are internal and NOT visible to the user. Only your direct conversational text is shown to the user. Therefore, always provide clear text explanations before or after actions so the user understands what you are doing and what the outcome is.\n"
            "6. Keep changes focused and verified. When verification passes, summarize concisely."
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
