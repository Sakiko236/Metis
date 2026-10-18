pub mod agent;
pub mod config;
pub mod llm;
pub mod models;
pub mod search;
pub mod session;
pub mod shell;
pub mod whitelist;

use agent::{AgentController, AgentLoop};
use config::ConfigManager;
use llm::MultiVendorClient;
use models::{
    AppConfig, ChatHistoryItem, ChatSession, SessionStoreData, UiStreamEvent,
};
use search::WebSearchRunner;
use session::SessionManager;
use shell::ShellRunner;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use tauri::{Emitter, State};
use tokio::sync::Mutex;
use whitelist::WhitelistManager;

pub struct AppState {
    pub config: Mutex<AppConfig>,
    pub session_store: Mutex<SessionStoreData>,
    pub whitelist: Mutex<WhitelistManager>,
    pub agent: AgentController,
    pub status_text: Mutex<String>,
}

impl AppState {
    pub fn new() -> Self {
        let config = ConfigManager::load_or_create_default();
        let session_store = SessionManager::load_or_create_default();
        let mut whitelist = WhitelistManager::new();

        if let Some(active) = session_store
            .get_active_session()
            .or_else(|| session_store.sessions.first())
        {
            whitelist.bind_session_list(active.whitelist.clone(), active.allowed_directories.clone());
        }

        Self {
            config: Mutex::new(config),
            session_store: Mutex::new(session_store),
            whitelist: Mutex::new(whitelist),
            agent: AgentController::new(),
            status_text: Mutex::new("Ready".to_string()),
        }
    }
}

pub async fn broadcast_full_state(
    app_handle: &tauri::AppHandle,
    state: &AppState,
    toast: Option<&str>,
    prefill_prompt: Option<&str>,
) {
    let config = state.config.lock().await.clone();
    let mut session_store = state.session_store.lock().await;
    let whitelist = state.whitelist.lock().await;

    // Sync whitelist files back to active session
    if let Some(active) = session_store.get_active_session_mut() {
        active.whitelist = whitelist.get_files().to_vec();
        active.allowed_directories = whitelist.get_directories().to_vec();
    }
    let _ = SessionManager::save(&session_store);

    let active_ws = whitelist.get_active_workspace_directory();
    let is_busy = state.agent.is_busy.load(Ordering::SeqCst);
    let status_text = state.status_text.lock().await.clone();
    let config_path = ConfigManager::get_config_path().to_string_lossy().to_string();

    let mut payload = serde_json::json!({
        "type": "fullState",
        "config": config,
        "sessions": session_store.sessions,
        "activeSessionId": session_store.active_session_id,
        "activeWorkspaceDir": active_ws,
        "isBusy": is_busy,
        "statusText": status_text,
        "configFilePath": config_path,
        "isMaximized": false
    });

    if let Some(t) = toast {
        payload["toast"] = serde_json::Value::String(t.to_string());
    }
    if let Some(p) = prefill_prompt {
        payload["prefillPrompt"] = serde_json::Value::String(p.to_string());
    }

    let _ = app_handle.emit("host_message", payload);
}

pub mod commands {
    use super::*;

    #[tauri::command]
    pub async fn host_post(
    app: tauri::AppHandle,
    state: State<'_, Arc<AppState>>,
    window: tauri::Window,
    payload: String,
) -> Result<(), String> {
    let data: serde_json::Value =
        serde_json::from_str(&payload).map_err(|e| format!("Invalid JSON: {}", e))?;
    let action = data.get("action").and_then(|v| v.as_str()).unwrap_or("");

    match action {
        "uiReady" => {
            broadcast_full_state(&app, &state, None, None).await;
        }
        "createSession" => {
            let mut store = state.session_store.lock().await;
            let mut ns = ChatSession::default();
            ns.title = format!("Session {}", store.sessions.len() + 1);
            store.active_session_id = ns.id.clone();
            store.sessions.insert(0, ns);
            drop(store);
            broadcast_full_state(&app, &state, Some("New session created."), None).await;
        }
        "selectSession" => {
            if let Some(sid) = data.get("sessionId").and_then(|v| v.as_str()) {
                let mut store = state.session_store.lock().await;
                store.active_session_id = sid.to_string();
                if let Some(active) = store.sessions.iter().find(|s| s.id == sid) {
                    let mut wl = state.whitelist.lock().await;
                    wl.bind_session_list(active.whitelist.clone(), active.allowed_directories.clone());
                }
                drop(store);
                broadcast_full_state(&app, &state, None, None).await;
            }
        }
        "renameSession" => {
            let sid = data.get("sessionId").and_then(|v| v.as_str()).unwrap_or("");
            let title = data.get("title").and_then(|v| v.as_str()).unwrap_or("");
            if !sid.is_empty() && !title.is_empty() {
                let mut store = state.session_store.lock().await;
                if let Some(s) = store.sessions.iter_mut().find(|s| s.id == sid) {
                    s.title = title.trim().to_string();
                    s.updated_at = chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string();
                }
                drop(store);
                broadcast_full_state(&app, &state, Some("Session renamed."), None).await;
            }
        }
        "deleteSession" => {
            let sid = data.get("sessionId").and_then(|v| v.as_str()).unwrap_or("");
            let mut store = state.session_store.lock().await;
            store.sessions.retain(|s| s.id != sid);
            if store.sessions.is_empty() {
                store.sessions.push(ChatSession::default());
            }
            if store.active_session_id == sid {
                store.active_session_id = store.sessions[0].id.clone();
            }
            drop(store);
            broadcast_full_state(&app, &state, Some("Session deleted."), None).await;
        }
        "saveConfig" => {
            if let Some(cfg_val) = data.get("config") {
                if let Ok(updated) = serde_json::from_value::<AppConfig>(cfg_val.clone()) {
                    let mut cfg = state.config.lock().await;
                    *cfg = updated.clone();
                    let _ = ConfigManager::save(&cfg);
                    drop(cfg);
                    broadcast_full_state(&app, &state, Some("Settings saved."), None).await;
                }
            }
        }
        "setTheme" => {
            let theme = data.get("theme").and_then(|v| v.as_str()).unwrap_or("dark");
            let mut cfg = state.config.lock().await;
            cfg.theme = theme.to_string();
            let _ = ConfigManager::save(&cfg);
            drop(cfg);
            broadcast_full_state(&app, &state, None, None).await;
        }
        "setThinkingIntensity" => {
            let intensity = data.get("intensity").and_then(|v| v.as_str()).unwrap_or("medium");
            let mut cfg = state.config.lock().await;
            cfg.thinking_intensity = intensity.to_string();
            let _ = ConfigManager::save(&cfg);
            drop(cfg);
            broadcast_full_state(&app, &state, None, None).await;
        }
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
            let _ = window.close();
        }
        "windowDrag" => {
            let _ = window.start_dragging();
        }
        "probeOllama" => {
            let base_url = data.get("baseUrl").and_then(|v| v.as_str()).unwrap_or("http://localhost:11434/v1");
            let models = MultiVendorClient::fetch_ollama_models(base_url).await;
            let toast = if !models.is_empty() {
                format!("Detected {} local Ollama models.", models.len())
            } else {
                "No Ollama service detected. Ensure 'ollama serve' is running.".to_string()
            };
            let _ = app.emit("host_message", serde_json::json!({
                "type": "ollamaModels",
                "models": models,
                "toast": toast
            }));
        }
        "pickFiles" => {
            let app_clone = app.clone();
            let state_clone = state.inner().clone();
            tokio::spawn(async move {
                if let Some(files) = rfd::AsyncFileDialog::new()
                    .set_title("Select files to attach")
                    .pick_files()
                    .await
                {
                    let mut wl = state_clone.whitelist.lock().await;
                    let mut added = 0;
                    for f in files {
                        if wl.add_file(&f.path().to_string_lossy()) {
                            added += 1;
                        }
                    }
                    drop(wl);
                    broadcast_full_state(
                        &app_clone,
                        &state_clone,
                        Some(&format!("Added {} files to workspace.", added)),
                        None,
                    )
                    .await;
                }
            });
        }
        "pickFolder" => {
            let app_clone = app.clone();
            let state_clone = state.inner().clone();
            tokio::spawn(async move {
                if let Some(folder) = rfd::AsyncFileDialog::new()
                    .set_title("Select folder to attach")
                    .pick_folder()
                    .await
                {
                    let mut wl = state_clone.whitelist.lock().await;
                    let (loaded, _) = wl.add_directory(&folder.path().to_string_lossy());
                    drop(wl);
                    broadcast_full_state(
                        &app_clone,
                        &state_clone,
                        Some(&format!("Mounted folder with {} files.", loaded)),
                        None,
                    )
                    .await;
                }
            });
        }
        "removeFile" => {
            if let Some(path) = data.get("path").and_then(|v| v.as_str()) {
                let mut wl = state.whitelist.lock().await;
                wl.remove_file(path);
                drop(wl);
                broadcast_full_state(&app, &state, Some("Removed file from session."), None).await;
            }
        }
        "removeDirectory" => {
            if let Some(path) = data.get("path").and_then(|v| v.as_str()) {
                let mut wl = state.whitelist.lock().await;
                wl.remove_directory(path);
                drop(wl);
                broadcast_full_state(&app, &state, Some("Unmounted folder."), None).await;
            }
        }
        "clearWhitelist" => {
            let mut wl = state.whitelist.lock().await;
            wl.clear();
            drop(wl);
            broadcast_full_state(&app, &state, Some("Cleared all attached files."), None).await;
        }
        "inspectFile" => {
            if let Some(path) = data.get("path").and_then(|v| v.as_str()) {
                let mut wl = state.whitelist.lock().await;
                if let Some(wf) = wl.resolve_whitelisted_file(path) {
                    let content = if std::path::Path::new(&wf.full_path).exists() {
                        std::fs::read_to_string(&wf.full_path).unwrap_or_else(|_| wf.last_saved_content.clone())
                    } else {
                        wf.last_saved_content.clone()
                    };

                    let _ = app.emit("host_message", serde_json::json!({
                        "type": "fileContentPreview",
                        "fileName": wf.file_name,
                        "fullPath": wf.full_path,
                        "editCount": wf.edit_count,
                        "mediaKind": wf.media_kind,
                        "mimeType": wf.mime_type,
                        "isBinaryMedia": wf.is_binary_media,
                        "content": content
                    }));
                }
            }
        }
        "revealFile" => {
            if let Some(path) = data.get("path").and_then(|v| v.as_str()) {
                let _ = open::that(path);
            }
        }
        "revertFile" => {
            if let Some(path) = data.get("path").and_then(|v| v.as_str()) {
                let mut wl = state.whitelist.lock().await;
                if let Ok(msg) = wl.revert_file_to_initial(path) {
                    drop(wl);
                    broadcast_full_state(&app, &state, Some(&msg), None).await;
                }
            }
        }
        "pauseLoop" => {
            state.agent.pause();
            *state.status_text.lock().await = "⏸ Paused".to_string();
            let _ = app.emit("host_message", serde_json::json!({
                "type": "busyStatus",
                "isBusy": true,
                "statusText": "⏸ Paused"
            }));
        }
        "resumeLoop" => {
            state.agent.resume();
            *state.status_text.lock().await = "⚡ Working...".to_string();
            let _ = app.emit("host_message", serde_json::json!({
                "type": "busyStatus",
                "isBusy": true,
                "statusText": "⚡ Working..."
            }));
        }
        "stopLoop" => {
            state.agent.stop();
            *state.status_text.lock().await = "Ready".to_string();
            let _ = app.emit("host_message", serde_json::json!({
                "type": "busyStatus",
                "isBusy": false,
                "statusText": "Ready"
            }));
        }
        "sendPrompt" => {
            if let Some(prompt) = data.get("prompt").and_then(|v| v.as_str()) {
                let prompt_str = prompt.trim().to_string();
                if !prompt_str.is_empty() {
                    let app_clone = app.clone();
                    let state_clone = state.inner().clone();
                    tokio::spawn(async move {
                        run_autonomous_loop(app_clone, state_clone, prompt_str).await;
                    });
                }
            }
        }
        _ => {}
    }

    Ok(())
}
}

async fn run_autonomous_loop(app: tauri::AppHandle, state: Arc<AppState>, user_prompt: String) {
    state.agent.is_busy.store(true, Ordering::SeqCst);
    state.agent.stop_requested.store(false, Ordering::SeqCst);
    state.agent.is_paused.store(false, Ordering::SeqCst);
    *state.status_text.lock().await = "Thinking...".to_string();

    let _ = app.emit("host_message", serde_json::json!({
        "type": "busyStatus",
        "isBusy": true,
        "statusText": "Thinking..."
    }));

    // Record User event
    {
        let mut store = state.session_store.lock().await;
        if let Some(active) = store.get_active_session_mut() {
            active.api_history.push(ChatHistoryItem {
                role: "user".to_string(),
                content: user_prompt.clone(),
            });
            active.ui_events.push(UiStreamEvent::new("user", &user_prompt));
        }
    }
    broadcast_full_state(&app, &state, None, None).await;

    let config = state.config.lock().await.clone();
    let max_rounds = config.max_verify_loops.max(1);
    let timeout_sec = config.command_timeout_seconds;
    let vendor = config
        .vendors
        .iter()
        .find(|v| v.id == config.active_vendor_id)
        .cloned()
        .unwrap_or_else(|| config.vendors[0].clone());

    let mut current_round = 1;

    while current_round <= max_rounds {
        if state.agent.stop_requested.load(Ordering::SeqCst) {
            break;
        }

        while state.agent.is_paused.load(Ordering::SeqCst) {
            tokio::time::sleep(tokio::time::Duration::from_millis(200)).await;
            if state.agent.stop_requested.load(Ordering::SeqCst) {
                break;
            }
        }

        *state.status_text.lock().await = format!("Round {}/{} — Generating...", current_round, max_rounds);
        let _ = app.emit("host_message", serde_json::json!({
            "type": "busyStatus",
            "isBusy": true,
            "statusText": format!("Round {}/{} — Generating...", current_round, max_rounds)
        }));

        // Assistant UI event placeholder
        let assistant_event_id = uuid::Uuid::new_v4().simple().to_string();
        {
            let mut store = state.session_store.lock().await;
            if let Some(active) = store.get_active_session_mut() {
                let mut ev = UiStreamEvent::new("assistant", "");
                ev.id = assistant_event_id.clone();
                ev.vendor_name = vendor.display_name.clone();
                ev.model_name = vendor.selected_model.clone();
                ev.round = current_round;
                ev.max_rounds = max_rounds;
                active.ui_events.push(ev);
            }
        }

        let history_snapshot = {
            let store = state.session_store.lock().await;
            store
                .get_active_session()
                .map(|s| s.api_history.clone())
                .unwrap_or_default()
        };

        let whitelist_snapshot = state.whitelist.lock().await.get_files().to_vec();
        let files_block = AgentLoop::build_file_snapshot(&whitelist_snapshot);
        let system_prompt = AgentLoop::build_system_prompt();

        let app_stream = app.clone();
        let ev_id_stream = assistant_event_id.clone();
        let mut streamed_text = String::new();

        let chat_result = MultiVendorClient::send_chat(
            &vendor,
            system_prompt,
            &files_block,
            &history_snapshot,
            &whitelist_snapshot,
            &config.thinking_intensity,
            |chunk| {
                streamed_text.push_str(chunk);
                let display = MultiVendorClient::format_display_text(&streamed_text);
                let _ = app_stream.emit("host_message", serde_json::json!({
                    "type": "streamUpdate",
                    "eventId": ev_id_stream,
                    "content": display
                }));
            },
        )
        .await;

        let raw_response = match chat_result {
            Ok(txt) => txt,
            Err(err) => {
                let mut store = state.session_store.lock().await;
                if let Some(active) = store.get_active_session_mut() {
                    active.ui_events.push(UiStreamEvent::new("notice", &format!("⚠️ LLM Error: {}", err)));
                }
                break;
            }
        };

        // Update assistant event final content and api history
        {
            let mut store = state.session_store.lock().await;
            if let Some(active) = store.get_active_session_mut() {
                if let Some(ev) = active.ui_events.iter_mut().find(|e| e.id == assistant_event_id) {
                    ev.content = MultiVendorClient::format_display_text(&raw_response);
                }
                active.api_history.push(ChatHistoryItem {
                    role: "assistant".to_string(),
                    content: raw_response.clone(),
                });
            }
        }

        let parsed = MultiVendorClient::parse_agent_output(&raw_response, &whitelist_snapshot);
        let mut had_actions = false;

        // 1. Process Web Searches
        for query in &parsed.search_queries {
            had_actions = true;
            *state.status_text.lock().await = format!("Searching web for '{}'...", query);
            let _ = app.emit("host_message", serde_json::json!({
                "type": "busyStatus",
                "isBusy": true,
                "statusText": format!("Searching web for '{}'...", query)
            }));

            let results = WebSearchRunner::search(query, 5).await;
            let formatted_md = WebSearchRunner::format_markdown(query, &results);

            let mut store = state.session_store.lock().await;
            if let Some(active) = store.get_active_session_mut() {
                let mut ev = UiStreamEvent::new("search_result", &formatted_md);
                ev.search_query = query.clone();
                ev.search_results = results;
                active.ui_events.push(ev);
                active.api_history.push(ChatHistoryItem {
                    role: "user".to_string(),
                    content: format!("[Web Search Results for '{}']:\n{}", query, formatted_md),
                });
            }
        }

        // 2. Process File Edits
        for edit in &parsed.file_edits {
            had_actions = true;
            let mut wl = state.whitelist.lock().await;
            match wl.apply_edit_in_place(edit) {
                Ok((msg, wf)) => {
                    let mut store = state.session_store.lock().await;
                    if let Some(active) = store.get_active_session_mut() {
                        let mut ev = UiStreamEvent::new("file_edit", &msg);
                        ev.file_name = wf.file_name.clone();
                        ev.file_path = wf.full_path.clone();
                        ev.edit_count = wf.edit_count;
                        active.ui_events.push(ev);
                    }
                }
                Err(err_msg) => {
                    let mut store = state.session_store.lock().await;
                    if let Some(active) = store.get_active_session_mut() {
                        let ev_type = if err_msg.contains("[Security Block") {
                            "security_block"
                        } else {
                            "notice"
                        };
                        active.ui_events.push(UiStreamEvent::new(ev_type, &err_msg));
                        active.api_history.push(ChatHistoryItem {
                            role: "user".to_string(),
                            content: format!("[Operation Feedback]:\n{}", err_msg),
                        });
                    }
                }
            }
        }

        // 3. Process Terminal Commands
        for cmd in &parsed.terminal_commands {
            had_actions = true;
            *state.status_text.lock().await = format!("Running `{}`...", cmd);
            let _ = app.emit("host_message", serde_json::json!({
                "type": "busyStatus",
                "isBusy": true,
                "statusText": format!("Running `{}`...", cmd)
            }));

            let ws_dir = state.whitelist.lock().await.get_active_workspace_directory();
            let exec_res = ShellRunner::run_command(cmd, &ws_dir, timeout_sec).await;

            let mut store = state.session_store.lock().await;
            if let Some(active) = store.get_active_session_mut() {
                let mut ev = UiStreamEvent::new("verify_result", &exec_res.combined_output());
                ev.command = cmd.clone();
                ev.working_directory = ws_dir;
                ev.exit_code = exec_res.exit_code;
                ev.duration_seconds = exec_res.duration_seconds;
                ev.succeeded = exec_res.succeeded();
                ev.terminal_output = exec_res.combined_output();
                active.ui_events.push(ev);

                active.api_history.push(ChatHistoryItem {
                    role: "user".to_string(),
                    content: format!(
                        "[Terminal Execution Feedback for `{}`]:\nExit Code: {}\nOutput:\n{}",
                        cmd, exec_res.exit_code, exec_res.combined_output()
                    ),
                });
            }
        }

        broadcast_full_state(&app, &state, None, None).await;

        if !had_actions {
            // No tools or edits were invoked — conversational turn is naturally complete!
            let mut store = state.session_store.lock().await;
            if let Some(active) = store.get_active_session_mut() {
                active.ui_events.push(UiStreamEvent::new("loop_success", "Completed all requested tasks."));
            }
            break;
        }

        current_round += 1;
    }

    state.agent.is_busy.store(false, Ordering::SeqCst);
    *state.status_text.lock().await = "Ready".to_string();

    let _ = app.emit("host_message", serde_json::json!({
        "type": "busyStatus",
        "isBusy": false,
        "statusText": "Ready"
    }));

    broadcast_full_state(&app, &state, None, None).await;
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app_state = Arc::new(AppState::new());

    tauri::Builder::default()
        .plugin(tauri_plugin_log::Builder::default().build())
        .manage(app_state)
        .invoke_handler(tauri::generate_handler![commands::host_post])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
