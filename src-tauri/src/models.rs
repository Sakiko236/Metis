use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum VendorType {
    Gemini = 0,
    OpenAI = 1,
    Claude = 2,
    DeepSeek = 3,
    Ollama = 4,
    CustomOpenAI = 5,
    LocalSandbox = 6,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub struct VendorProfile {
    pub id: String,
    pub display_name: String,
    #[serde(rename = "Type")]
    pub vendor_type: VendorType,
    pub base_url: String,
    pub api_key: String,
    pub selected_model: String,
    pub preset_models: Vec<String>,
}

impl VendorProfile {
    pub fn new(id: &str, display_name: &str, vendor_type: VendorType, base_url: &str, model: &str) -> Self {
        Self {
            id: id.to_string(),
            display_name: display_name.to_string(),
            vendor_type,
            base_url: base_url.to_string(),
            api_key: String::new(),
            selected_model: model.to_string(),
            preset_models: vec![model.to_string()],
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub struct AppConfig {
    pub active_vendor_id: String,
    pub max_verify_loops: u32,
    pub command_timeout_seconds: u64,
    pub theme: String,
    pub thinking_intensity: String,
    pub vendors: Vec<VendorProfile>,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            active_vendor_id: "deepseek".to_string(),
            max_verify_loops: 8,
            command_timeout_seconds: 60,
            theme: "dark".to_string(),
            thinking_intensity: "medium".to_string(),
            vendors: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub struct WhitelistedFile {
    pub full_path: String,
    pub file_name: String,
    pub relative_path: String,
    pub directory_path: String,
    pub media_kind: String, // "code", "image", "pdf"
    pub mime_type: String,
    pub is_binary_media: bool,
    pub initial_content: String,
    pub last_saved_content: String,
    pub edit_count: u32,
    pub added_at: String,
    pub last_modified_by_agent_at: String,
    #[serde(default)]
    pub current_size_bytes: u64,
}

impl WhitelistedFile {
    pub fn new(full_path: &str, file_name: &str, directory_path: &str, content: &str) -> Self {
        let now = chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string();
        Self {
            full_path: full_path.to_string(),
            file_name: file_name.to_string(),
            relative_path: file_name.to_string(),
            directory_path: directory_path.to_string(),
            media_kind: "code".to_string(),
            mime_type: "text/plain".to_string(),
            is_binary_media: false,
            initial_content: content.to_string(),
            last_saved_content: content.to_string(),
            edit_count: 0,
            added_at: now.clone(),
            last_modified_by_agent_at: now,
            current_size_bytes: content.len() as u64,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub struct ChatHistoryItem {
    pub role: String, // "user" or "assistant"
    pub content: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub struct SearchResultItem {
    pub title: String,
    pub url: String,
    pub snippet: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub struct UiStreamEvent {
    pub id: String,
    #[serde(rename = "Type")]
    pub event_type: String, // "user", "assistant", "file_edit", "verify_result", "search_result", "loop_success", "notice", "security_block"
    pub timestamp: String,
    #[serde(default)]
    pub content: String,
    #[serde(default)]
    pub vendor_name: String,
    #[serde(default)]
    pub model_name: String,
    #[serde(default)]
    pub round: u32,
    #[serde(default)]
    pub max_rounds: u32,
    #[serde(default)]
    pub file_path: String,
    #[serde(default)]
    pub file_name: String,
    #[serde(default)]
    pub line_count: usize,
    #[serde(default)]
    pub edit_count: u32,
    #[serde(default)]
    pub command: String,
    #[serde(default)]
    pub working_directory: String,
    #[serde(default)]
    pub exit_code: i32,
    #[serde(default)]
    pub duration_seconds: f64,
    #[serde(default)]
    pub succeeded: bool,
    #[serde(default)]
    pub terminal_output: String,
    #[serde(default)]
    pub search_query: String,
    #[serde(default)]
    pub search_results: Vec<SearchResultItem>,
}

impl UiStreamEvent {
    pub fn new(event_type: &str, content: &str) -> Self {
        Self {
            id: uuid::Uuid::new_v4().simple().to_string(),
            event_type: event_type.to_string(),
            timestamp: chrono::Local::now().format("%H:%M:%S").to_string(),
            content: content.to_string(),
            vendor_name: String::new(),
            model_name: String::new(),
            round: 0,
            max_rounds: 0,
            file_path: String::new(),
            file_name: String::new(),
            line_count: 0,
            edit_count: 0,
            command: String::new(),
            working_directory: String::new(),
            exit_code: 0,
            duration_seconds: 0.0,
            succeeded: true,
            terminal_output: String::new(),
            search_query: String::new(),
            search_results: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub struct ChatSession {
    pub id: String,
    pub title: String,
    pub created_at: String,
    pub updated_at: String,
    #[serde(default)]
    pub allowed_directories: Vec<String>,
    #[serde(default)]
    pub whitelist: Vec<WhitelistedFile>,
    #[serde(default)]
    pub api_history: Vec<ChatHistoryItem>,
    #[serde(default)]
    pub ui_events: Vec<UiStreamEvent>,
}

impl Default for ChatSession {
    fn default() -> Self {
        let now = chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string();
        Self {
            id: uuid::Uuid::new_v4().simple().to_string(),
            title: "Default Session".to_string(),
            created_at: now.clone(),
            updated_at: now,
            allowed_directories: Vec::new(),
            whitelist: Vec::new(),
            api_history: Vec::new(),
            ui_events: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub struct SessionStoreData {
    pub active_session_id: String,
    pub sessions: Vec<ChatSession>,
}

impl ChatSession {
    pub fn is_empty(&self) -> bool {
        self.ui_events.is_empty() && self.api_history.is_empty()
    }
}

impl Default for SessionStoreData {
    fn default() -> Self {
        let first = ChatSession::default();
        Self {
            active_session_id: first.id.clone(),
            sessions: vec![first],
        }
    }
}

impl SessionStoreData {
    pub fn get_active_session(&self) -> Option<&ChatSession> {
        self.sessions.iter().find(|s| s.id == self.active_session_id)
    }

    pub fn get_active_session_mut(&mut self) -> Option<&mut ChatSession> {
        let aid = self.active_session_id.clone();
        self.sessions.iter_mut().find(|s| s.id == aid)
    }

    pub fn find_empty_session(&self) -> Option<&ChatSession> {
        self.sessions.iter().find(|s| s.is_empty())
    }

    pub fn prune_redundant_empty_sessions(&mut self) {
        let mut preserved_empty_id: Option<String> = None;
        let active_id = self.active_session_id.clone();

        if self.sessions.iter().any(|s| s.id == active_id && s.is_empty()) {
            preserved_empty_id = Some(active_id);
        }

        self.sessions.retain(|s| {
            if s.is_empty() {
                if let Some(ref pid) = preserved_empty_id {
                    s.id == *pid
                } else {
                    preserved_empty_id = Some(s.id.clone());
                    true
                }
            } else {
                true
            }
        });

        if !self.sessions.iter().any(|s| s.id == self.active_session_id) {
            if let Some(first) = self.sessions.first() {
                self.active_session_id = first.id.clone();
            } else {
                let fresh = ChatSession::default();
                self.active_session_id = fresh.id.clone();
                self.sessions.push(fresh);
            }
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum SurgicalActionType {
    WriteFull,
    SearchReplace,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SurgicalFileEdit {
    pub action_type: SurgicalActionType,
    pub target_file: String,
    pub new_content: String,
    pub search_block: String,
    pub replace_block: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ParsedAgentResponse {
    pub raw_text: String,
    pub explanation_text: String,
    pub file_edits: Vec<SurgicalFileEdit>,
    pub terminal_commands: Vec<String>,
    pub search_queries: Vec<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct VerifyExecutionResult {
    pub command: String,
    pub working_directory: String,
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
    pub duration_seconds: f64,
    pub timed_out: bool,
    pub cancelled: bool,
}

impl VerifyExecutionResult {
    pub fn succeeded(&self) -> bool {
        !self.timed_out && !self.cancelled && self.exit_code == 0
    }

    pub fn combined_output(&self) -> String {
        let mut out = String::new();
        if !self.stdout.trim().is_empty() {
            out.push_str(self.stdout.trim_end());
        }
        if !self.stderr.trim().is_empty() {
            if !out.is_empty() {
                out.push('\n');
            }
            out.push_str(self.stderr.trim_end());
        }
        if out.trim().is_empty() {
            "(No terminal standard output)".to_string()
        } else {
            out
        }
    }
}
