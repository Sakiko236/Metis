from __future__ import annotations
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

from models import VerifyExecutionResult


class ShellRunner:
    @staticmethod
    def _run_sync(command_line: str, working_dir: str, timeout_seconds: int) -> VerifyExecutionResult:
        start = time.perf_counter()
        cmd_trimmed = command_line.strip()
        cwd = working_dir if (working_dir and working_dir.strip() and Path(working_dir).is_dir()) else None
        timeout_val = timeout_seconds if timeout_seconds > 0 else 60

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        # PyInstaller safety: prevent child Python interpreter from inheriting PyInstaller's bundle path
        for py_var in ("PYTHONHOME", "_MEIPASS2", "_MEIPASS"):
            env.pop(py_var, None)
        if "PYTHONPATH" in env and hasattr(sys, "_MEIPASS") and str(getattr(sys, "_MEIPASS")) in env["PYTHONPATH"]:
            env.pop("PYTHONPATH", None)

        kwargs = {
            "cwd": cwd,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "timeout": timeout_val,
            "shell": True,
            "env": env,
        }

        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        try:
            proc = subprocess.run(cmd_trimmed, **kwargs)
            stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
            stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
            return VerifyExecutionResult(
                command=cmd_trimmed,
                working_directory=working_dir,
                exit_code=proc.returncode,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=round(time.perf_counter() - start, 3),
                timed_out=False,
                cancelled=False,
            )
        except subprocess.TimeoutExpired as te:
            stdout = te.stdout.decode("utf-8", errors="replace") if te.stdout else ""
            stderr = te.stderr.decode("utf-8", errors="replace") if te.stderr else ""
            if stderr:
                stderr += f"\nProcess timed out after {timeout_val} seconds."
            else:
                stderr = f"Process timed out after {timeout_val} seconds."
            return VerifyExecutionResult(
                command=cmd_trimmed,
                working_directory=working_dir,
                exit_code=-1,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=round(time.perf_counter() - start, 3),
                timed_out=True,
                cancelled=False,
            )
        except Exception as e:
            return VerifyExecutionResult(
                command=cmd_trimmed,
                working_directory=working_dir,
                exit_code=-1,
                stdout="",
                stderr=f"Failed to execute shell process: {e}",
                duration_seconds=round(time.perf_counter() - start, 3),
                timed_out=False,
                cancelled=False,
            )

    @classmethod
    async def run_command(cls, command_line: str, working_dir: str, timeout_seconds: int = 60) -> VerifyExecutionResult:
        return await asyncio.to_thread(cls._run_sync, command_line, working_dir, timeout_seconds)
