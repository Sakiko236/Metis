# Metis Automation & Build Scripts 🛠️

The `scripts/` directory provides developer utilities, build automations, and release management scripts for Metis.

---

## 📁 Tool & Script Overview

| Script | Purpose | Usage |
| :--- | :--- | :--- |
| [`bump_version.py`](bump_version.py) | **Version Management & Release Synchronizer**: Synchronizes semantic versions across `package.json`, `src-tauri/Cargo.toml`, and `src-tauri/tauri.conf.json`. Automatically updates `Cargo.lock` via `cargo check`, with optional support for automated git commits, semantic version tags, and remote pushes. | `python scripts/bump_version.py [patch\|minor\|major\|X.Y.Z] [--commit] [--tag] [--push]` |
| [`build_sidecar.py`](build_sidecar.py) | **Python Sidecar Packager**: Packages `src-python/main.py` into a single standalone binary using PyInstaller and places it in `src-tauri/bin/metis-engine-<target_triple>[.exe]` for Tauri `externalBin` distribution bundling. | `python scripts/build_sidecar.py [--target <target_triple>]` |
| [`generate_mascot_icons.py`](generate_mascot_icons.py) | **Mascot Icon Asset Generator**: Renders the SVG mascot to high-resolution PNGs using headless Chrome and converts them into platform-specific icon formats (`.ico`, `.icns`, and multi-size PNGs) across `src-tauri/icons/` and `assets/`. | `python scripts/generate_mascot_icons.py` |

---

## 🚀 Common Workflow Examples

### 1. Packaging the Python Sidecar
To compile the Python sidecar for local testing or release bundling:
```bash
# Detects host architecture and compiles to src-tauri/bin/metis-engine-<target>
python scripts/build_sidecar.py
```

### 2. Updating Version & Releasing
To update versions across all manifests in a single command:
```bash
# Bump patch version (e.g., 1.4.5 -> 1.4.6)
python scripts/bump_version.py patch

# Bump version, commit, tag, and push to trigger CI/CD release workflow
python scripts/bump_version.py patch --push
```
