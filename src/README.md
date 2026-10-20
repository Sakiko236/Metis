# Metis Frontend Application 💻

The `src/` directory contains the responsive single-page webview interface for Metis.

---

## 📁 File Overview

| File / Folder | Description |
| :--- | :--- |
| [`frontend/index.html`](frontend/index.html) | **Single-Page Application (SPA)**: Self-contained frontend combining HTML structure, responsive modern styling, and vanilla JavaScript logic without external framework dependencies. |

---

## 🎨 UI Features & Components

* **Responsive Minimalist Workspace**:
  * Uncapped, edge-to-edge conversational interface with auto-scrolling message streams.
  * Collapsible sidebar for managing active chat sessions and whitelisted files.
* **Themes**:
  * Seamless toggling between **Dark Mode** and **Light Mode** with persistent local state.
* **Math & Markdown Rendering**:
  * KaTeX integration for rendering inline `\(...\)` and display `\[...\]` mathematical formulas.
  * Real-time Markdown syntax highlighting and code block copy utilities.
* **Thinking & Tool Execution Visuals**:
  * Dynamic breathing thinking bubbles that parse `<think>` tokens as they stream from the LLM.
  * Clean, compact tool execution chips (e.g. `Executing: python -m unittest`) replacing noisy raw command dumps.
* **File & Media Ingestion**:
  * Native picker buttons for individual files, recursive folders, and multimodal images/PDFs (`triggerMediaPicker()`).
  * Direct clipboard paste detection (`handleComposerPaste`) supporting screenshots and PDF blobs.
  * Interactive modal inspector for reviewing file snapshots, diffs, and previewing binary media.
* **Tauri IPC Integration**:
  * Communicates with the Rust shell and Python sidecar using `window.__TAURI__.core.invoke('host_post', ...)` and listens for incoming updates via `window.__TAURI__.event.listen('metis-event', ...)`.
