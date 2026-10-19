# Metis 🎯

<div align="center">
  <img src="assets/metis-icon.png" width="108" height="108" alt="Metis Logo" />
  <br/>
  <h3> Metis Agent </h3>
  <p><b>File-Centric Micro Coding Agent</b></p>

  [![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-0078D6)](https://github.com/Sakiko236/Metis)
  [![Architecture](https://img.shields.io/badge/Architecture-Tauri%202.0%20%2B%20Python%20Sidecar-blue?logo=tauri&logoColor=white)](https://tauri.app)
  [![Python](https://img.shields.io/badge/Engine-Python%203.10%2B-yellow?logo=python&logoColor=white)](https://python.org)
  [![License](https://img.shields.io/badge/License-MIT-emerald)](LICENSE)
</div>

---

## 🖼️ Interface Preview

<div align="center">
  <p><b>Clean, minimalist workspace and file management with zero clutter</b></p>
  <img src="assets/firstpage.png" alt="Metis Minimalist Main Interface" width="850" />
</div>

---

## 💡 Philosophy & Design

> **"Why scan and re-index an entire repository when you only need to fix a single bug?"**

In an era where AI coding assistants promise "autonomous full-stack repository generation", real-world developers often encounter:
* **High Blast Radius**: Unchecked full-repo agents frequently rearrange directories, overwrite dependencies, or introduce breaking structural changes without warning.
* **Context Dilution**: Cramming entire monorepos into LLM context windows causes severe token waste, slow responses, and hallucinated modifications.

**Metis is built on the philosophy of "Laser Focus, Zero Blast Radius":**
* **Laser Focus**: Only files or directories you explicitly drag in or select are admitted into the prompt context. No uninvited background file crawling.
* **Zero Blast Radius**: **Creating new files is strictly forbidden. Deleting existing files is strictly forbidden.** Metis operates exclusively by surgically modifying existing whitelisted files in-place using exact Search/Replace blocks or initial population.
* **Native Vendor API Search Grounding**: Metis integrates with official provider search specifications (e.g. Google Search Grounding for Gemini, Web Search for Claude) dynamically without forcing search when existing knowledge suffices.

---

## ✨ Key Features

### 1. 📂 Explicit Whitelist & In-Place Surgical Editing
* Support for individual file drag-and-drop or selection, as well as **recursive directory mounting**.
* **Zero Blast Radius**: Modifies existing whitelisted files in-place using precise 3-tier Search/Replace blocks (exact, normalized CRLF/LF, line-by-line whitespace-trimmed), with automatic support for populating empty 0-byte files.
* Built-in file snapshots with one-click historical version rollback.

### 2. 🧠 Thinking Budget & Extended Reasoning
* Comprehensive support for Thinking configurations (`thinkingBudget`: `low` = 2048, `medium` = 8192, `high` = 24576).
* Real-time streaming parser extracts `thought` parts dynamically, rendering breathing animation placeholders without polluting generated code.
* Automatic fallback protection for legacy models without thinking support.

### 3. 🛡️ Strict Prohibition on File Creation & Deletion
* Enforced across the prompt protocol, the Python whitelist guardrails, and the terminal runner.
* Any command attempting to delete files (`rm`, `del`, `Remove-Item`) or create unauthorized scripts is intercepted and blocked immediately.

### 4. 🎨 Responsive Minimalist UI & Dark/Light Themes
* Distraction-free modern interface with mist-dissolving text animations, uncapped full-width responsive dialog bubbles and composer, and KaTeX mathematical typesetting.
* Seamless switching between **Dark Mode** and **Light Mode**.
* System default browser integration for all external links.

---

## 🖥️ Quick Start & Downloads

Download the appropriate release package for your operating system from the [Releases](https://github.com/Sakiko236/Metis/releases) page:

* **🪟 Windows**:
  * **Installer**: `Metis_<version>_x64-setup.exe` (NSIS installer with bundled standalone Python engine & clean uninstaller)
* **🍎 macOS**:
  * **Apple Silicon (M1/M2/M3/M4)**: `Metis_<version>_aarch64.dmg`
* **🐧 Linux**:
  * **Debian / Ubuntu**: `metis_<version>_amd64.deb`
  * **Universal Linux**: `Metis_<version>_amd64.AppImage`

---

## 🛠️ Configuration & Workflow

1. **Configure Your API Key**:
   * Click **⚙️ Settings** in the bottom-left corner;
   * Select your preferred LLM provider (e.g., Gemini, DeepSeek, OpenAI, or Claude), enter your `Base URL`, `API Key`, and `Model Name`;
   * Settings automatically save locally as you type (stored locally on your system, never uploaded to third parties).

2. **Select Target Files**:
   * In the workspace panel, click "Add File" or "Upload Folder" to scope down the exact files requiring modification.

3. **Request Surgical Edits**:
   * Enter your prompt in the chat composer, for example:
      > *"Add token expiration retry handling to auth.py, update the test suite in test_auth.py, and run python -m unittest to verify."*
    * Metis will reason, propose surgical Search/Replace edits, and invoke the terminal to run tests until validation succeeds.

---

## 🔄 One-Click Self-Test & Version Bump

Metis follows a **Single Source of Truth (SSOT)** dynamic version distribution architecture. All components (Tauri configuration, Rust host binary, Python sidecar engine, and frontend UI) dynamically inherit the version at compile time or runtime, eliminating manual synchronization across multiple files.

### 1. Headless End-to-End Self-Test

Metis features a built-in cross-process suite of 10 core smoke and stability self-tests, verifying configuration persistence, chat session storage, whitelist security guardrails, surgical search/replace operations, silent terminal execution, and multi-vendor LLM streaming protocols:

```bash
# Run standalone Python sidecar engine self-test suite
python src-python/main.py --self-test

# Run full cross-process bidirectional IPC pipeline test (Rust host + Python sidecar)
cargo run --manifest-path src-tauri/Cargo.toml -- --self-test
```

### 2. Version Bump & Release Automation CLI

The project provides an automated version management utility [`scripts/bump_version.py`](scripts/bump_version.py). It enforces **complete end-to-end self-test verification** before permitting version bumps or git pushes:

```bash
# Bump patch version (e.g. 1.4.2 -> 1.4.3) with automated self-tests
python scripts/bump_version.py patch

# Bump minor version (e.g. 1.4.2 -> 1.5.0)
python scripts/bump_version.py minor

# Set an explicit target version
python scripts/bump_version.py 2.0.0

# 🌟 Automated release mode: self-test -> version sync -> git commit -> git tag -> push to remote & trigger GitHub Actions CI
python scripts/bump_version.py patch --push
```

---

## 📂 Project Structure

```text
├── assets/
│   ├── metis-icon.png          # High-resolution application logo
│   └── firstpage.png           # Main workspace interface preview
├── demo_workspace/             # Sample workspace files for quick testing
├── scripts/
│   ├── build_sidecar.py        # PyInstaller build script for Tauri externalBin
│   └── bump_version.py         # One-command self-test, version bump & release synchronizer
├── src/
│   └── frontend/
│       └── index.html          # Modern responsive UI (Sky Blue theme, KaTeX, uncapped width)
├── src-python/                 # Core Python Sidecar Business Engine
│   ├── main.py                 # Async IPC loop, CLI self-test runner, and agent orchestrator
│   ├── agent.py                # AgentController state & AgentLoop prompt builder
│   ├── config.py               # Cross-platform config persistence & model memory
│   ├── llm.py                  # Multi-vendor streaming client (Gemini, Claude, OpenAI, DeepSeek)
│   ├── models.py               # Data domain models & PascalCase serialization schemas
│   ├── requirements.txt        # Python build dependencies (PyInstaller)
│   ├── session.py              # Cross-platform chat session store
│   ├── shell.py                # Silent cross-platform terminal command runner
│   └── whitelist.py            # Zero-blast-radius file guardrails & surgical editor
├── src-tauri/                  # Tauri 2.0 Native Shell Host
│   ├── Cargo.toml              # Rust crate manifest (minimal lightweight shell)
│   ├── tauri.conf.json         # Tauri 2.0 app, window, and externalBin configuration
│   ├── build.rs                # Universal cross-platform build script
│   ├── icons/                  # Multi-resolution application icons (ICO, ICNS, PNG)
│   ├── capabilities/           # Tauri 2.0 security capabilities
│   └── src/
│       ├── main.rs             # Desktop launcher & end-to-end headless self-test runner
│       └── lib.rs              # SidecarBridge supervisor, window controls, and IPC bridge
├── .github/workflows/
│   └── release.yml             # Automated multi-platform CI/CD release workflow
├── package.json                # Project root manifest & Tauri CLI scripts
├── metis_config.example.json   # Sample configuration file
├── .gitignore                  # Git ignore rules
└── README.md                   # Project documentation and architectural guide
```

---

<div align="center">
  <sub>Metis · Surgical precision for the code that matters most.</sub>
</div>
