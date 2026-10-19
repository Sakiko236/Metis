use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use tauri::{Emitter, Manager, State};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, Command};
use tokio::sync::Mutex;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

pub struct SidecarBridge {
    stdin: Mutex<Option<ChildStdin>>,
    child: Mutex<Option<Child>>,
}

impl SidecarBridge {
    pub fn new() -> Self {
        Self {
            stdin: Mutex::new(None),
            child: Mutex::new(None),
        }
    }

    /// Resolves either the packaged PyInstaller sidecar executable (`metis-engine`)
    /// or falls back to running `python -u src-python/main.py` in development.
    pub fn resolve_sidecar_command(extra_args: &[&str]) -> Result<(String, Vec<String>), String> {
        let force_dev_py = std::env::var("METIS_DEV_PYTHON")
            .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
            .unwrap_or(false);

        let exe_path = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("."));
        let exe_dir = exe_path.parent().unwrap_or_else(|| Path::new("."));
        let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));

        if !force_dev_py {
            let mut bin_candidates: Vec<PathBuf> = Vec::new();

            #[cfg(target_os = "windows")]
            {
                bin_candidates.push(exe_dir.join("metis-engine.exe"));
                bin_candidates.push(exe_dir.join("metis-engine-x86_64-pc-windows-msvc.exe"));
                bin_candidates.push(exe_dir.join("metis-engine-x86_64-pc-windows-gnu.exe"));
                bin_candidates.push(cwd.join("src-tauri/bin/metis-engine.exe"));
                bin_candidates.push(cwd.join("src-tauri/bin/metis-engine-x86_64-pc-windows-msvc.exe"));
                bin_candidates.push(cwd.join("src-tauri/bin/metis-engine-x86_64-pc-windows-gnu.exe"));
                bin_candidates.push(cwd.join("bin/metis-engine.exe"));
            }

            #[cfg(not(target_os = "windows"))]
            {
                bin_candidates.push(exe_dir.join("metis-engine"));
                bin_candidates.push(exe_dir.join("metis-engine-aarch64-apple-darwin"));
                bin_candidates.push(exe_dir.join("metis-engine-x86_64-apple-darwin"));
                bin_candidates.push(exe_dir.join("metis-engine-x86_64-unknown-linux-gnu"));
                bin_candidates.push(cwd.join("src-tauri/bin/metis-engine"));
                bin_candidates.push(cwd.join("src-tauri/bin/metis-engine-aarch64-apple-darwin"));
                bin_candidates.push(cwd.join("src-tauri/bin/metis-engine-x86_64-unknown-linux-gnu"));
                bin_candidates.push(cwd.join("bin/metis-engine"));
            }

            for cand in bin_candidates {
                let is_real_binary = cand.exists()
                    && cand.is_file()
                    && cand.metadata().map(|m| m.len() > 1024).unwrap_or(false);
                if is_real_binary {
                    let args: Vec<String> = extra_args.iter().map(|s| s.to_string()).collect();
                    return Ok((cand.to_string_lossy().to_string(), args));
                }
            }
        }

        // Fallback: Locate `src-python/main.py` for development / source mode
        let mut script_candidates: Vec<PathBuf> = vec![
            cwd.join("src-python/main.py"),
            cwd.join("../src-python/main.py"),
            exe_dir.join("src-python/main.py"),
            exe_dir.join("../src-python/main.py"),
            exe_dir.join("../../src-python/main.py"),
            exe_dir.join("../../../src-python/main.py"),
        ];
        if let Some(p) = exe_dir.parent().and_then(|p| p.parent()).and_then(|p| p.parent()) {
            script_candidates.push(p.join("src-python/main.py"));
        }

        for script in script_candidates {
            if script.exists() && script.is_file() {
                let py_bin = Self::find_python_interpreter();
                let mut args = vec!["-u".to_string(), script.to_string_lossy().to_string()];
                for a in extra_args {
                    args.push(a.to_string());
                }
                return Ok((py_bin, args));
            }
        }

        Err("Could not locate Metis Python Sidecar binary (`metis-engine`) or `src-python/main.py`.".to_string())
    }

    fn find_python_interpreter() -> String {
        if let Ok(custom_py) = std::env::var("METIS_PYTHON_BIN") {
            if !custom_py.trim().is_empty() {
                return custom_py;
            }
        }
        #[cfg(target_os = "windows")]
        {
            "python".to_string()
        }
        #[cfg(not(target_os = "windows"))]
        {
            "python3".to_string()
        }
    }

    pub fn build_tokio_command(program: &str, args: &[String]) -> Command {
        let mut cmd = Command::new(program);
        cmd.args(args)
            .env("PYTHONIOENCODING", "utf-8")
            .env("PYTHONUTF8", "1")
            .env("METIS_APP_VERSION", env!("CARGO_PKG_VERSION"))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        #[cfg(target_os = "windows")]
        {
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        cmd
    }

    pub async fn start(&self, app_handle: tauri::AppHandle) -> Result<(), String> {
        let mut child_guard = self.child.lock().await;
        if child_guard.is_some() {
            return Ok(());
        }

        let (program, args) = Self::resolve_sidecar_command(&[])?;
        let mut cmd = Self::build_tokio_command(&program, &args);

        let mut child = cmd
            .spawn()
            .map_err(|e| format!("Failed to spawn Python sidecar ({}): {}", program, e))?;

        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| "Failed to open stdin pipe to Python sidecar".to_string())?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "Failed to open stdout pipe from Python sidecar".to_string())?;
        let stderr = child.stderr.take();

        *self.stdin.lock().await = Some(stdin);
        *child_guard = Some(child);
        drop(child_guard);

        // Background task: read METIS_IPC messages from Python sidecar stdout and emit to WebView
        let app_clone = app_handle.clone();
        tokio::spawn(async move {
            let mut reader = BufReader::new(stdout).lines();
            while let Ok(Some(line)) = reader.next_line().await {
                let trimmed = line.trim();
                if let Some(json_str) = trimmed.strip_prefix("METIS_IPC:") {
                    if let Ok(val) = serde_json::from_str::<serde_json::Value>(json_str) {
                        let _ = app_clone.emit("host_message", val);
                    }
                } else if !trimmed.is_empty() {
                    log::info!("[Python Sidecar] {}", trimmed);
                }
            }
        });

        if let Some(err_stream) = stderr {
            tokio::spawn(async move {
                let mut reader = BufReader::new(err_stream).lines();
                while let Ok(Some(line)) = reader.next_line().await {
                    let trimmed = line.trim();
                    if !trimmed.is_empty() {
                        log::warn!("[Python Sidecar stderr] {}", trimmed);
                    }
                }
            });
        }

        Ok(())
    }

    pub async fn send_json(&self, payload: &serde_json::Value) -> Result<(), String> {
        let mut line = serde_json::to_string(payload).map_err(|e| e.to_string())?;
        line.push('\n');

        let mut stdin_guard = self.stdin.lock().await;
        if let Some(ref mut stdin) = *stdin_guard {
            stdin
                .write_all(line.as_bytes())
                .await
                .map_err(|e| format!("Failed to write to Python sidecar stdin: {}", e))?;
            stdin
                .flush()
                .await
                .map_err(|e| format!("Failed to flush Python sidecar stdin: {}", e))?;
            Ok(())
        } else {
            Err("Python sidecar stdin pipe is not connected.".to_string())
        }
    }

    pub async fn shutdown(&self) {
        // Close stdin first so Python sidecar sees EOF and exits immediately
        let _ = self.stdin.lock().await.take();
        if let Some(mut child) = self.child.lock().await.take() {
            let _ = child.kill().await;
        }
    }
}

pub mod commands {
    use super::*;

    #[tauri::command]
    pub async fn host_post(
        app: tauri::AppHandle,
        state: State<'_, Arc<SidecarBridge>>,
        window: tauri::Window,
        payload: String,
    ) -> Result<(), String> {
        // Ensure Python sidecar is started
        state.start(app.clone()).await?;

        let data: serde_json::Value =
            serde_json::from_str(&payload).map_err(|e| format!("Invalid JSON: {}", e))?;
        let action = data.get("action").and_then(|v| v.as_str()).unwrap_or("");

        match action {
            // Native window management handled directly by Tauri shell
            "windowMin" => {
                let _ = window.minimize();
            }
            "windowMax" | "windowToggleMaximize" => {
                if let Ok(is_max) = window.is_maximized() {
                    if is_max {
                        let _ = window.unmaximize();
                    } else {
                        let _ = window.maximize();
                    }
                }
            }
            "windowClose" => {
                state.shutdown().await;
                let _ = window.close();
            }
            "windowDrag" => {
                let _ = window.start_dragging();
            }
            // Native OS file/folder pickers bridge selected paths to Python sidecar
            "pickFiles" => {
                let bridge = state.inner().clone();
                tokio::spawn(async move {
                    if let Some(files) = rfd::AsyncFileDialog::new()
                        .set_title("Select files to attach")
                        .pick_files()
                        .await
                    {
                        let paths: Vec<String> = files
                            .into_iter()
                            .map(|f| f.path().to_string_lossy().to_string())
                            .collect();
                        let _ = bridge
                            .send_json(&serde_json::json!({
                                "action": "addFiles",
                                "paths": paths
                            }))
                            .await;
                    }
                });
            }
            "pickFolder" => {
                let bridge = state.inner().clone();
                tokio::spawn(async move {
                    if let Some(folder) = rfd::AsyncFileDialog::new()
                        .set_title("Select folder to attach")
                        .pick_folder()
                        .await
                    {
                        let folder_path = folder.path().to_string_lossy().to_string();
                        let _ = bridge
                            .send_json(&serde_json::json!({
                                "action": "addDirectory",
                                "path": folder_path
                            }))
                            .await;
                    }
                });
            }
            // Native OS file reveal & browser URL open
            "revealFile" => {
                if let Some(path) = data.get("path").and_then(|v| v.as_str()) {
                    let _ = open::that(path);
                }
            }
            "openUrl" => {
                if let Some(url) = data.get("url").and_then(|v| v.as_str()) {
                    let trimmed = url.trim();
                    if trimmed.starts_with("http://")
                        || trimmed.starts_with("https://")
                        || trimmed.starts_with("mailto:")
                    {
                        let _ = open::that(trimmed);
                    }
                }
            }
            // Forward all core business & agent actions directly to the Python Sidecar Engine
            _ => {
                state.send_json(&data).await?;
            }
        }

        Ok(())
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let bridge = Arc::new(SidecarBridge::new());
    let bridge_setup = bridge.clone();
    let bridge_window = bridge.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_log::Builder::default().build())
        .manage(bridge)
        .setup(move |app| {
            if let Some(window) = app.get_webview_window("main") {
                let icon_bytes = include_bytes!("../icons/icon.png");
                if let Ok(icon) = tauri::image::Image::from_bytes(icon_bytes) {
                    let _ = window.set_icon(icon);
                }
                let ver = env!("CARGO_PKG_VERSION");
                let _ = window.eval(&format!("window.__METIS_VERSION__ = '{}';", ver));
            }

            let handle = app.handle().clone();
            let b = bridge_setup.clone();
            tauri::async_runtime::spawn(async move {
                let _ = b.start(handle).await;
            });

            Ok(())
        })
        .on_window_event(move |_window, event| {
            if matches!(
                event,
                tauri::WindowEvent::CloseRequested { .. } | tauri::WindowEvent::Destroyed
            ) {
                let b = bridge_window.clone();
                tauri::async_runtime::spawn(async move {
                    b.shutdown().await;
                });
            }
        })
        .invoke_handler(tauri::generate_handler![commands::host_post])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
