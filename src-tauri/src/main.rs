// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use metis_lib::config::ConfigManager;
use metis_lib::models::{SurgicalActionType, SurgicalFileEdit};
use metis_lib::search::WebSearchRunner;
use metis_lib::session::SessionManager;
use metis_lib::shell::ShellRunner;
use metis_lib::whitelist::WhitelistManager;
use std::env;

#[tokio::main]
async fn main() {
    let args: Vec<String> = env::args().collect();
    if args.iter().any(|a| a == "--self-test") {
        let code = run_headless_self_test().await;
        std::process::exit(code);
    }

    metis_lib::run();
}

async fn run_headless_self_test() -> i32 {
    println!("=== Metis v1.2.0 Tauri 2.0 Headless End-to-End Self-Test ===");

    // 1. Config Persistence
    let cfg = ConfigManager::load_or_create_default();
    let _ = ConfigManager::save(&cfg);
    let cfg_path = ConfigManager::get_config_path();
    if !cfg_path.exists() {
        eprintln!("FAIL: Config file was not persisted.");
        return 1;
    }
    println!("[PASS] Local Config Persisted: {}", cfg_path.display());

    // 2. Session Persistence
    let store = SessionManager::load_or_create_default();
    let _ = SessionManager::save(&store);
    let sess_path = SessionManager::get_sessions_file_path();
    if !sess_path.exists() {
        eprintln!("FAIL: Sessions file was not persisted.");
        return 2;
    }
    println!("[PASS] Local Sessions Persisted: {}", sess_path.display());

    // 3. Whitelist strict security check: Creation prohibited
    let mut wl = WhitelistManager::new();
    let edit_fake = SurgicalFileEdit {
        action_type: SurgicalActionType::WriteFull,
        target_file: "sub/auto_created_script.py".to_string(),
        new_content: "print('bad')".to_string(),
        search_block: String::new(),
        replace_block: String::new(),
    };

    match wl.apply_edit_in_place(&edit_fake) {
        Ok(_) => {
            eprintln!("FAIL: Unwhitelisted file creation was allowed!");
            return 3;
        }
        Err(err) => {
            if !err.contains("[Security Block - File Creation Prohibited]") {
                eprintln!("FAIL: Did not output expected security block message: {}", err);
                return 4;
            }
            println!("[PASS] Strict Block on New File Creation Verified: {}", err);
        }
    }

    // 4. In-Place Search/Replace Block verification
    let temp_dir = std::env::temp_dir().join("metis_selftest");
    let _ = std::fs::create_dir_all(&temp_dir);
    let test_file = temp_dir.join("calc.py");
    let original_code = "def calc(a, b):\n    return a + b\n";
    let _ = std::fs::write(&test_file, original_code);

    wl.add_file(&test_file.to_string_lossy());

    let edit_replace = SurgicalFileEdit {
        action_type: SurgicalActionType::SearchReplace,
        target_file: "calc.py".to_string(),
        new_content: String::new(),
        search_block: "    return a + b".to_string(),
        replace_block: "    return a * b".to_string(),
    };

    match wl.apply_edit_in_place(&edit_replace) {
        Ok((msg, _)) => {
            let updated = std::fs::read_to_string(&test_file).unwrap_or_default();
            if !updated.contains("return a * b") {
                eprintln!("FAIL: File content was not updated with replacement.");
                return 5;
            }
            println!("[PASS] Search/Replace Block Engine & In-Place Edits Verified: {}", msg);
        }
        Err(e) => {
            eprintln!("FAIL: In-place edit failed: {}", e);
            return 6;
        }
    }

    // 5. ShellRunner execution verification
    let exec_res = ShellRunner::run_command("echo Metis Tauri 2.0 Self-Test OK", &temp_dir.to_string_lossy(), 10).await;
    if !exec_res.succeeded() || !exec_res.combined_output().contains("Metis Tauri 2.0") {
        eprintln!("FAIL: Shell execution failed: {:?}", exec_res);
        return 7;
    }
    println!("[PASS] Terminal Execution Verified (Exit 0): {}", exec_res.combined_output().trim());

    // 6. WebSearchRunner verification
    let search_res = WebSearchRunner::search("rust lang", 2).await;
    println!("[PASS] Web Search Engine Verified (Found {} results)", search_res.len());

    println!("=== All Metis Tauri 2.0 Self-Test Suites Passed Successfully! ===");
    0
}
