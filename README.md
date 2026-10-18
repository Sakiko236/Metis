# Metis 🎯

<div align="center">
  <img src="assets/metis-icon.png" width="108" height="108" alt="Metis Logo" />
  <br/>
  <h3> Metis Agent </h3>
  <p><b>File-Centric Mini Coding Agent with Zero Blast Radius</b></p>

  [![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-0078D6)](https://github.com/Sakiko236/Metis)
  [![Framework](https://img.shields.io/badge/Tauri-2.0%20%2B%20Rust-orange?logo=tauri&logoColor=white)](https://tauri.app)
  [![License](https://img.shields.io/badge/License-MIT-emerald)](LICENSE)
</div>

---

## 🖼️ Interface Preview / Screenshots

<div align="center">
  <p><b>Clean, minimalist workspace and file management</b></p>
  <img src="assets/firstpage.png" alt="Metis Minimalist Main Interface" width="850" />
</div>

---

## 💡 Philosophy & Design

> **"Why scan and re-index an entire repository when you only need to fix a single bug?"**

In an era where AI coding assistants promise "autonomous full-stack repository generation", real-world developers often encounter:
* **High Blast Radius**: Unchecked full-repo agents frequently rearrange directories, overwrite dependencies, or introduce breaking structural changes without warning;
* **Context Dilution**: Cramming entire monorepos into LLM context windows causes severe token waste, slow responses, and hallucinated modifications.

**Metis is built on the philosophy of "Laser Focus, Zero Blast Radius":**
* **Laser Focus**: Only files or directories you explicitly drag in or select are admitted into the prompt context. No uninvited background file crawling.
* **Zero Blast Radius**: **Creating new files is strictly forbidden. Deleting existing files is strictly forbidden.** Metis operates exclusively by surgically modifying existing whitelisted files in-place using Search/Replace blocks.
* **Autonomous Live Web Search**: Metis can autonomously query public technical documentation, API specifications, and troubleshooting guides in real time.

---

## ✨ Key Features

### 1. 📂 Explicit File Management & Directory Trees
* Support for individual file drag-and-drop or selection, as well as **recursive directory uploading**.
* Built-in file snapshots with one-click historical version rollback.

### 2. 🌐 Live Web Search
* When modern API documentation, framework specifications, or troubleshooting guides are required, Metis emits `<metis_search>` tags to fetch and summarize web content automatically.
* Search results are seamlessly fed back into the reasoning context and rendered as rich cards in the UI.

### 3. 🛡️ Strict Prohibition on File Creation & Deletion
* Triply enforced across the prompt instruction protocol, the file management layer, and the local terminal verification engine.
* Any command attempting to delete files (`rm`, `del`, `Remove-Item`) or create unauthorized scripts is intercepted and blocked immediately.

### 4. 🖼️ Native Multimodal Support (PDF & Images)
* **PDF Document Multimodal Pipeline**: Direct Base64 streaming of PDF documents to supported models for cross-page architecture and requirement analysis.
* **Visual Understanding**: Direct inspection of architecture diagrams, UI mockups, and terminal error screenshots in PNG, JPG, or WEBP formats (compatible with OpenAI, Claude, Gemini, DeepSeek, etc.).

### 5. 🎨 Minimalist Aesthetic & Dark/Light Themes
* Distraction-free modern interface with mist-dissolving text animations, fluid streaming, and full KaTeX mathematical typesetting.
* Seamless switching between **Dark Mode** and **Light Mode**.

### 6. 🔌 Multi-Vendor Direct API Connectivity
* Native zero-proxy support for **DeepSeek**, **OpenAI**, **Anthropic Claude**, **Google Gemini**, **Kimi / Moonshot**, **Zhipu GLM**, and local **Ollama** models.

---

## 🖥️ Quick Start & Downloads

Download the appropriate release package for your operating system from the [Releases](https://github.com/Sakiko236/Metis/releases) page:

* **🪟 Windows**:
  * **Installer**: `Metis_<version>_x64-setup.exe` (NSIS installer with desktop & Start Menu shortcuts)
* **🍎 macOS**:
  * **Apple Silicon (M1/M2/M3/M4)**: `Metis_<version>_aarch64.dmg`
* **🐧 Linux**:
  * **Debian / Ubuntu**: `metis_<version>_amd64.deb`
  * **Universal Linux**: `metis_<version>_amd64.AppImage`

---

## 🛠️ Configuration & Workflow

1. **Configure Your API Key**:
   * Click **⚙️ Settings** in the bottom-left corner;
   * Select your preferred LLM provider (e.g., DeepSeek, OpenAI, Claude, or Gemini), enter your `Base URL`, `API Key`, and `Model Name`;
   * Settings automatically save immediately as you type or change selections (persisted locally on your system, never uploaded to third parties).

2. **Select Target Files**:
   * In the workspace panel, click "Add File" or "Upload Folder" to scope down the exact files requiring modification.

3. **Request Surgical Edits**:
   * Enter your prompt in the chat composer, for example:
     > *"Add token expiration retry handling to auth.py, update the test suite in test_auth.py, and run python -m unittest to verify."*
   * Metis will reason, propose surgical Search/Replace edits, and invoke the terminal to run tests until validation succeeds.

---

## 🔨 Building from Source

Metis is built with **Tauri 2.0** (Rust backend + Web frontend):

```bash
# Clone the repository
git clone https://github.com/Sakiko236/Metis.git
cd Metis

# Install frontend & CLI dependencies
npm install

# Run in development mode
npm run tauri dev

# Build production bundle for your platform
npm run tauri build
```

> **Prerequisites**:
> * [Rust](https://rustup.rs/) (1.78+)
> * [Node.js](https://nodejs.org/) (LTS)
> * Platform webview:
>   * Windows: Microsoft Edge WebView2 (built-in on Windows 10/11)
>   * macOS: WebKit (built-in)
>   * Linux: `libwebkit2gtk-4.1-dev` (install via `apt-get install -y libwebkit2gtk-4.1-dev build-essential`)

---

## 📂 Project Structure

```text
├── assets/
│   ├── metis-icon.png          # High-resolution application logo
│   └── firstpage.png           # Main workspace interface preview
├── src/
│   └── frontend/
│       └── index.html          # Modern responsive UI (Sky Blue theme, KaTeX, syntax highlighting)
├── src-tauri/
│   ├── Cargo.toml              # Rust crate manifest & dependencies
│   ├── tauri.conf.json         # Tauri 2.0 application & multiplatform bundle configuration
│   ├── build.rs                # Universal cross-platform build script
│   ├── icons/                  # Multi-resolution application icons (ICO, ICNS, PNG)
│   ├── capabilities/           # Tauri 2.0 application security capabilities
│   └── src/
│       ├── main.rs             # CLI headless verification runner & desktop launcher
│       ├── lib.rs              # Tauri coordinator, AppState, host IPC dispatcher
│       ├── models.rs           # Core domain models & serialization schemas
│       ├── config.rs           # Cross-platform persistent configuration & model memory
│       ├── session.rs          # Cross-platform chat session store
│       ├── whitelist.rs        # Zero-blast-radius file guardrails & Search/Replace engine
│       ├── llm.rs              # Multi-vendor streaming LLM client
│       ├── search.rs           # DuckDuckGo autonomous live web search scraper
│       ├── shell.rs            # Cross-platform safe terminal execution runner
│       └── agent.rs            # Autonomous cycle controller & snapshot builder
├── demo_workspace/             # Sample workspace files for quick testing
├── .github/workflows/
│   └── release.yml             # Automated multi-platform CI/CD release workflow
├── package.json                # Project root manifest & Tauri CLI scripts
├── .gitignore                  # Git ignore rules
└── README.md                   # Project documentation and architectural guide
```

<div align="center">
  <sub>Metis · Surgical precision for the code that matters most.</sub>
</div>
