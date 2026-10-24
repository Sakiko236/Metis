# Metis Python Sidecar Engine 🐍

The `src-python/` directory houses the core business logic, ReAct agent loop, security guardrails, and multi-vendor LLM streaming engine for Metis. It runs as an independent sidecar process spawned by the Tauri native shell and communicates over standard input/output (stdio) using line-delimited JSON IPC (`METIS_IPC:`).

---

## 📁 File & Module Overview

| File | Description |
| :--- | :--- |
| [`main.py`](main.py) | **Sidecar Entrypoint & IPC Loop**: Runs the asynchronous stdio reader/writer loops, routes host commands (`addFiles`, `removeFile`, `sendPrompt`, `stopAgent`, `saveConfig`, `inspectFile`, `pasteImage`, `addMediaBlob`), broadcasts state updates to the Tauri host, and manages lifecycle auto-termination when the host closes the pipe. |
| [`agent.py`](agent.py) | **Agent Controller & ReAct Loop**: Coordinates conversational turns, ReAct multi-step tool calls, and prompt synthesis. Formats the strict system prompt with file snapshot contents, in-place editing protocols, and tool schemas. |
| [`whitelist.py`](whitelist.py) | **Zero-Blast-Radius Guardrails & In-Place Editor**: Manages whitelisted target files and mounted directories. Implements the 3-tier in-place `SEARCH/REPLACE` block matching engine, empty-file population, snapshot history for rollback, and PDF text decompression via `zlib`. |
| [`llm.py`](llm.py) | **Multi-Vendor Streaming Client**: Unified streaming interface for Google Gemini, Anthropic Claude, OpenAI, and DeepSeek. Supports native search grounding tool definitions, thinking budget configurations, real-time thought extraction, and multimodal image/PDF base64 serialization. |
| [`models.py`](models.py) | **Domain Schemas & Data Models**: Defines typed domain structures (`WhitelistedFile`, `FileEdit`, `VendorProfile`, `ChatHistoryItem`, `SessionRecord`) with serialization schemas compatible with frontend IPC messages. |
| [`config.py`](config.py) | **Configuration Persistence**: Reads and writes application preferences (`config.json`) in platform-standard app data directories (`%APPDATA%/Metis` on Windows, `~/.config/Metis` on Linux, `~/Library/Application Support/Metis` on macOS). |
| [`session.py`](session.py) | **Session Store**: Manages conversation history persistence (`sessions.json`), title generation, active session switching, and message history truncation. |
| [`shell.py`](shell.py) | **Silent Terminal Runner**: Executes shell commands (`powershell.exe` on Windows, `sh` on Unix) asynchronously with configurable timeouts, capturing stdout/stderr while intercepting destructive commands. |
| [`requirements.txt`](requirements.txt) | **Build Dependencies**: Lists dependencies required for building the standalone sidecar binary with PyInstaller. |

---

## 🛠️ Key Architectural Principles

1. **Zero Blast Radius**:
   * File creation and deletion are strictly prevented by `WhitelistManager`. Edits are strictly applied in-place to files explicitly chosen by the user.
2. **Dynamic Multimodal Ingestion**:
   * When binary media files (PNG, JPG, WEBP, GIF, PDF) are added to the whitelist or pasted, `WhitelistManager` and `llm.py` serialize them as base64 payloads tailored to each provider's multimodal schema:
     * **Gemini**: `inlineData` parts with matching MIME types.
     * **Claude**: `image` and `document` (PDF) content blocks with Anthropic beta headers.
     * **OpenAI / DeepSeek**: `image_url` data URIs.
3. **Headless IPC Bridge**:
   * The Python process runs headless with unbuffered output (`python -u`). Messages sent to the host are formatted as `METIS_IPC:<json>\n`, allowing clean separation from any incidental third-party logging.
