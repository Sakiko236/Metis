#!/usr/bin/env python3
"""
Metis Version Bumper & Synchronizer
Single-command utility to manage Metis versioning across package.json and Cargo.toml.
All other components (Tauri config, Python Sidecar, Rust binary, and Webview Frontend)
dynamically inherit the version at runtime/compile-time.

Usage:
    python scripts/bump_version.py patch
    python scripts/bump_version.py minor
    python scripts/bump_version.py major
    python scripts/bump_version.py 1.5.0
    python scripts/bump_version.py 1.5.0 --commit --push
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def parse_semver(ver: str) -> tuple[int, int, int]:
    clean = ver.strip().lstrip("v")
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", clean)
    if not m:
        raise ValueError(f"Invalid semver string: {ver!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def bump_semver(current: str, rule: str) -> str:
    major, minor, patch = parse_semver(current)
    rule = rule.lower().strip()
    if rule == "patch":
        patch += 1
    elif rule == "minor":
        minor += 1
        patch = 0
    elif rule == "major":
        major += 1
        minor = 0
        patch = 0
    else:
        # User passed explicit version string like "1.5.0"
        major, minor, patch = parse_semver(rule)
    return f"{major}.{minor}.{patch}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Bump and synchronize Metis version across components.")
    parser.add_argument("target", nargs="?", default="patch", help="patch | minor | major | X.Y.Z (default: patch)")
    parser.add_argument("-m", "--message", default=None, help="Custom release message for commit and tag")
    parser.add_argument("--commit", action="store_true", help="Automatically git commit the version bump")
    parser.add_argument("--tag", action="store_true", help="Create git tag (e.g. vX.Y.Z)")
    parser.add_argument("--push", action="store_true", help="Push commit and tags to origin")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    pkg_json_path = repo_root / "package.json"
    cargo_toml_path = repo_root / "src-tauri" / "Cargo.toml"
    tauri_conf_path = repo_root / "src-tauri" / "tauri.conf.json"

    if not pkg_json_path.is_file():
        print(f"ERROR: {pkg_json_path} not found.", file=sys.stderr)
        return 1

    # 1. Read current version from package.json
    with open(pkg_json_path, "r", encoding="utf-8") as f:
        pkg_data = json.load(f)
    current_ver = str(pkg_data.get("version", "1.0.0"))

    try:
        new_ver = bump_semver(current_ver, args.target)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"[Metis Version] {current_ver} -> {new_ver}")

    # 2. Update package.json
    pkg_data["version"] = new_ver
    with open(pkg_json_path, "w", encoding="utf-8") as f:
        json.dump(pkg_data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"  [OK] Updated {pkg_json_path.relative_to(repo_root)}")

    # 3. Update src-tauri/Cargo.toml
    if cargo_toml_path.is_file():
        cargo_content = cargo_toml_path.read_text(encoding="utf-8")
        updated_cargo = re.sub(
            r'(?m)^(\s*version\s*=\s*)"[^"]+"',
            rf'\g<1>"{new_ver}"',
            cargo_content,
            count=1,
        )
        cargo_toml_path.write_text(updated_cargo, encoding="utf-8")
        print(f"  [OK] Updated {cargo_toml_path.relative_to(repo_root)}")

    # 4. Ensure tauri.conf.json delegates version to package.json
    if tauri_conf_path.is_file():
        with open(tauri_conf_path, "r", encoding="utf-8") as f:
            tconf_data = json.load(f)
        if tconf_data.get("version") != "../package.json":
            tconf_data["version"] = "../package.json"
            with open(tauri_conf_path, "w", encoding="utf-8") as f:
                json.dump(tconf_data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            print(f"  [OK] Linked {tauri_conf_path.relative_to(repo_root)} -> ../package.json")
        else:
            print(f"  [OK] Verified {tauri_conf_path.relative_to(repo_root)} delegates to ../package.json")

    # 5. Run Cargo Check to sync Cargo.lock
    print("\n[Synchronizing Cargo.lock via cargo check...]")
    subprocess.run(["cargo", "check", "--manifest-path", str(cargo_toml_path)], cwd=str(repo_root))

    # 6. Optional Git operations
    if args.commit or args.push:
        print("\n[Git Commit & Tag]")
        subprocess.check_call(["git", "add", "-A"], cwd=str(repo_root))
        commit_msg = f"release: v{new_ver} - {args.message}" if args.message else f"release: v{new_ver}"
        subprocess.check_call(["git", "commit", "-m", commit_msg], cwd=str(repo_root))
        print(f"  [OK] Committed '{commit_msg}'")

    if args.tag or args.push:
        tag_name = f"v{new_ver}"
        tag_msg = args.message if args.message else tag_name
        subprocess.check_call(["git", "tag", "-a", tag_name, "-m", tag_msg], cwd=str(repo_root))
        print(f"  [OK] Created git tag '{tag_name}' with message: {tag_msg}")

    if args.push:
        print("\n[Pushing to Remote]")
        subprocess.check_call(["git", "push", "origin", "main", "--tags"], cwd=str(repo_root))
        print("  [OK] Pushed commits and tags to origin/main successfully!")

    print(f"\n[SUCCESS] Version successfully bumped to v{new_ver} across all components!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
