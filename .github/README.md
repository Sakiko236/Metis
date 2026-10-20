# GitHub Workflows & CI/CD 🚀

The `.github/` directory configures continuous integration, automated builds, and multi-platform distribution workflows for Metis.

---

## 📁 Workflow Overview

| Workflow | File | Triggers | Description |
| :--- | :--- | :--- | :--- |
| **Release** | [`.github/workflows/release.yml`](workflows/release.yml) | Tag push (`v*`), Manual dispatch | Matrix build job across Windows (`x86_64-pc-windows-msvc`), macOS (`aarch64-apple-darwin`), and Linux (`x86_64-unknown-linux-gnu`). Sets up Python, builds the standalone sidecar binary via `scripts/build_sidecar.py`, compiles the Tauri application bundle, and publishes native install packages (Windows NSIS `.exe`, macOS `.dmg`, Linux `.AppImage` / `.deb`) to the GitHub Releases page. |
