use crate::models::WhitelistedFile;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

pub struct AgentController {
    pub is_busy: Arc<AtomicBool>,
    pub is_paused: Arc<AtomicBool>,
    pub stop_requested: Arc<AtomicBool>,
}

impl Default for AgentController {
    fn default() -> Self {
        Self::new()
    }
}

impl AgentController {
    pub fn new() -> Self {
        Self {
            is_busy: Arc::new(AtomicBool::new(false)),
            is_paused: Arc::new(AtomicBool::new(false)),
            stop_requested: Arc::new(AtomicBool::new(false)),
        }
    }

    pub fn pause(&self) {
        self.is_paused.store(true, Ordering::SeqCst);
    }

    pub fn resume(&self) {
        self.is_paused.store(false, Ordering::SeqCst);
    }

    pub fn stop(&self) {
        self.stop_requested.store(true, Ordering::SeqCst);
        self.is_paused.store(false, Ordering::SeqCst);
    }
}

pub struct AgentLoop;

impl AgentLoop {
    pub fn build_system_prompt() -> &'static str {
        r#"You are Metis, a laser-focused, file-centric micro-agent designed for surgical code modifications with zero blast radius.
You operate under strict security boundaries:
1. You may ONLY modify files that are explicitly in the whitelist. You are strictly forbidden from creating new files or deleting existing files.
2. Prefer surgical in-place edits using <metis_replace file="filename">
<<<<<<< SEARCH
[exact existing code]
=======
[replacement code]
>>>>>>> REPLACE
</metis_replace>
3. To run terminal commands for testing or building, emit:
<metis_cmd>
your command here
</metis_cmd>
4. To search online documentation or technical references, emit:
<metis_search>
search keywords
</metis_search>
5. Keep changes focused and verified. When verification passes, summarize concisely."#
    }

    pub fn build_file_snapshot(files: &[WhitelistedFile]) -> String {
        let mut sb = String::new();
        sb.push_str("### WHITELISTED FILES CONTENT SNAPSHOT\n\n");
        if files.is_empty() {
            sb.push_str("(No files are currently in the whitelist)\n");
            return sb;
        }

        for f in files {
            sb.push_str(&format!("=== FILE: {} (Full Path: {}) ===\n", f.relative_path, f.full_path));
            sb.push_str(&f.last_saved_content);
            sb.push_str(&format!("\n=== END OF FILE: {} ===\n\n", f.relative_path));
        }
        sb
    }
}
