#!/usr/bin/env python3
"""
Builds the Metis Python Sidecar (`src-python/main.py`) into a standalone native binary
using PyInstaller and places it in `src-tauri/bin/metis-engine-<target_triple>[.exe]`
for Tauri `externalBin` bundling.
"""
from __future__ import annotations
import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path


def detect_rust_target_triple() -> str:
    # Check CLI arguments first
    args = [a for a in sys.argv[1:] if a.strip()]
    for i, a in enumerate(args):
        if a == "--target" and i + 1 < len(args):
            return args[i + 1].strip()
        if not a.startswith("-"):
            return a.strip()

    # Fallback: query `rustc -vV`
    try:
        out = subprocess.check_output(["rustc", "-vV"], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            if line.startswith("host:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass

    # Fallback based on OS/arch
    sys_name = platform.system().lower()
    machine = platform.machine().lower()
    if sys_name == "windows":
        return "x86_64-pc-windows-msvc"
    elif sys_name == "darwin":
        return "aarch64-apple-darwin" if "arm" in machine or "aarch64" in machine else "x86_64-apple-darwin"
    else:
        return "x86_64-unknown-linux-gnu"


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    src_python = repo_root / "src-python"
    entry_script = src_python / "main.py"
    bin_dir = repo_root / "src-tauri" / "bin"
    work_dir = repo_root / "src-tauri" / "target" / "pyinstaller-build"
    spec_dir = repo_root / "src-tauri" / "target" / "pyinstaller-spec"

    bin_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)

    target_triple = detect_rust_target_triple()
    is_windows = sys.platform == "win32" or "windows" in target_triple
    ext = ".exe" if is_windows else ""
    base_name = f"metis-engine-{target_triple}"

    print(f"[Metis Sidecar Builder] Target Triple: {target_triple}")
    print(f"[Metis Sidecar Builder] Entry Script : {entry_script}")

    pyinstaller_cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--noconsole",
        "--clean",
        "--paths",
        str(src_python),
        "--hidden-import=models",
        "--hidden-import=config",
        "--hidden-import=session",
        "--hidden-import=whitelist",
        "--hidden-import=agent",
        "--hidden-import=llm",
        "--hidden-import=shell",
        "--hidden-import=search",
        "--distpath",
        str(bin_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
        "--name",
        base_name,
        str(entry_script),
    ]

    res = subprocess.run(pyinstaller_cmd, cwd=str(repo_root))
    if res.returncode != 0:
        print("[Metis Sidecar Builder] ERROR: PyInstaller build failed.", file=sys.stderr)
        return res.returncode

    built_bin = bin_dir / f"{base_name}{ext}"
    if not built_bin.exists():
        print(f"[Metis Sidecar Builder] ERROR: Expected output not found at {built_bin}", file=sys.stderr)
        return 1

    if not is_windows:
        st = os.stat(built_bin)
        os.chmod(built_bin, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    else:
        # Ensure both msvc and gnu target triple names exist on Windows for local/CI compatibility
        for alias in (
            "metis-engine-x86_64-pc-windows-msvc.exe",
            "metis-engine-x86_64-pc-windows-gnu.exe",
            "metis-engine.exe",
        ):
            alias_path = bin_dir / alias
            if alias_path.resolve() != built_bin.resolve():
                shutil.copy2(built_bin, alias_path)

    print(f"[Metis Sidecar Builder] Successfully built sidecar binary: {built_bin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
