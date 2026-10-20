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
  <p><b>Clean, minimalist workspace and file management</b></p>
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

## 📚 Component Documentation

Detailed documentation for tools, engine scripts, and codebase files is maintained directly within each component directory:

* [`src-python/`](src-python/README.md) – Core Python sidecar engine, agent loop, LLM integrations, and file guardrails.
* [`src-tauri/`](src-tauri/README.md) – Tauri 2.0 native host shell, application lifecycle, and IPC bridge.
* [`src/`](src/README.md) – Frontend user interface and responsive single-page webview application.
* [`scripts/`](scripts/README.md) – Developer automation tools, build scripts, and version synchronizer.
* [`demo_workspace/`](demo_workspace/README.md) – Sample workspace files for testing surgical edits and multimodal ingestion.
* [`assets/`](assets/README.md) – Application graphics, icons, and mascot art assets.

---

<div align="center">
  <sub>Metis · Surgical precision for the code that matters most.</sub>
</div>
