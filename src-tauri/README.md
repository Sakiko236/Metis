# Metis Tauri 2.0 Host Shell 🦀

The `src-tauri/` directory contains the native desktop shell built with Tauri 2.0 and Rust. It acts as a lightweight supervisor process that hosts the webview frontend, manages the Python sidecar process lifecycle, provides native operating system file dialogs, and coordinates IPC messaging.

---

## 📁 File & Module Overview

| File / Folder | Description |
| :--- | :--- |
| [`src/main.rs`](src/main.rs) | **Desktop Application Entrypoint**: Initializes the application process and launches the native runtime via `metis_lib::run()`. |
| [`src/lib.rs`](src/lib.rs) | **Supervisor Library & IPC Bridge**: Implements the `SidecarBridge` actor, child process management, stdio piping, native OS file dialogs (`pickFiles`, `pickFolder`, `pickMedia`), drag-and-drop window event routing, and Tauri invoke handlers (`host_post`). |
| [`Cargo.toml`](Cargo.toml) | **Crate Manifest**: Configures Rust crate metadata, optimization flags, and dependencies including Tauri 2.0, Tokio async runtime, Serde, and `rfd` (Rusty File Dialogs). |
| [`tauri.conf.json`](tauri.conf.json) | **Tauri Configuration**: Declares window properties (dimensions, title, frameless/decorated), CSP settings, permissions, bundle identifiers, and the `externalBin` packaging mapping (`bin/metis-engine`). |
| [`build.rs`](build.rs) | **Build Script**: Executes Tauri build-time code generation and asset compilation. |
| [`capabilities/`](capabilities/) | **Security Capabilities**: Grants granular security permissions to the frontend webview under Tauri 2.0's capability model. |
| [`icons/`](icons/) | **Application Icons**: Platform-specific icon assets (.ico for Windows, .icns for macOS, multi-resolution PNGs for Linux desktop environments). |
| `bin/` | **Sidecar Binary Directory**: Destination directory for the PyInstaller-compiled Python sidecar executable bundled into final distribution packages. |

---

## 🔄 Host & Sidecar Lifecycle Management

* **Development Mode**: If no precompiled binary is found in `bin/` or if `METIS_DEV_PYTHON=1` is set, `SidecarBridge` automatically launches `python -u src-python/main.py`.
* **Production Packaging**: Tauri packages the native binary placed in `src-tauri/bin/metis-engine-<target>[.exe]` as an `externalBin` alongside the app installer.
* **Graceful Teardown**: When the Tauri application window closes or destroys, `SidecarBridge.shutdown()` closes child stdin and issues termination signals to prevent orphaned Python background processes.
* **Native Drag-and-Drop**: Operating system file drag-and-drop events are intercepted at the window level and forwarded directly to the Python sidecar via `addFiles`.
