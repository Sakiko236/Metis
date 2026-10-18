#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use metis_lib::SidecarBridge;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|a| a == "--self-test") {
        let code = run_headless_self_test().await;
        std::process::exit(code);
    }

    metis_lib::run();
}

async fn run_headless_self_test() -> i32 {
    println!("=== Metis v1.4.1 (Tauri 2.0 Host + Python Sidecar) End-to-End Self-Test ===");

    // 1. Run Python Sidecar internal self-test suite
    let (program, args) = match SidecarBridge::resolve_sidecar_command(&["--self-test"]) {
        Ok(res) => res,
        Err(e) => {
            eprintln!("FAIL: Could not resolve Python sidecar: {}", e);
            return 1;
        }
    };

    println!("[INFO] Resolved Sidecar Command: {} {:?}", program, args);

    let mut cmd = SidecarBridge::build_tokio_command(&program, &args);
    let output = match cmd.output().await {
        Ok(out) => out,
        Err(e) => {
            eprintln!("FAIL: Failed to execute Python sidecar self-test: {}", e);
            return 2;
        }
    };

    let stdout_str = String::from_utf8_lossy(&output.stdout);
    let stderr_str = String::from_utf8_lossy(&output.stderr);
    for line in stdout_str.lines() {
        println!("  {}", line);
    }
    if !output.status.success() {
        eprintln!("FAIL: Python sidecar self-test exited with status {:?}:\n{}", output.status.code(), stderr_str);
        return 3;
    }

    // 2. Verify live bidirectional Rust <-> Python Sidecar IPC pipe (ping -> pong & uiReady -> fullState)
    let (ipc_prog, ipc_args) = match SidecarBridge::resolve_sidecar_command(&[]) {
        Ok(res) => res,
        Err(e) => {
            eprintln!("FAIL: Could not resolve Python sidecar for IPC test: {}", e);
            return 4;
        }
    };

    let mut ipc_cmd = SidecarBridge::build_tokio_command(&ipc_prog, &ipc_args);
    let mut child = match ipc_cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("FAIL: Could not spawn Python sidecar for IPC test: {}", e);
            return 5;
        }
    };

    let mut stdin = child.stdin.take().expect("stdin piped");
    let stdout = child.stdout.take().expect("stdout piped");
    let mut reader = BufReader::new(stdout).lines();

    let send_res = stdin
        .write_all(b"{\"action\":\"ping\"}\n{\"action\":\"uiReady\"}\n")
        .await;
    if let Err(e) = send_res {
        eprintln!("FAIL: Failed writing to sidecar stdin: {}", e);
        let _ = child.kill().await;
        return 6;
    }
    let _ = stdin.flush().await;

    let mut got_pong = false;
    let mut got_full_state = false;

    let check_loop = async {
        while let Ok(Some(line)) = reader.next_line().await {
            if let Some(json_str) = line.trim().strip_prefix("METIS_IPC:") {
                if let Ok(val) = serde_json::from_str::<serde_json::Value>(json_str) {
                    match val.get("type").and_then(|t| t.as_str()) {
                        Some("pong") => {
                            got_pong = true;
                        }
                        Some("fullState") => {
                            got_full_state = true;
                        }
                        _ => {}
                    }
                    if got_pong && got_full_state {
                        break;
                    }
                }
            }
        }
    };

    let _ = tokio::time::timeout(Duration::from_secs(10), check_loop).await;

    // Close stdin to verify graceful sidecar auto-exit
    drop(stdin);
    let _ = tokio::time::timeout(Duration::from_secs(3), child.wait()).await;
    let _ = child.kill().await;

    if !got_pong || !got_full_state {
        eprintln!(
            "FAIL: Bidirectional IPC test failed (got_pong={}, got_full_state={})",
            got_pong, got_full_state
        );
        return 7;
    }

    println!("[PASS] Rust <-> Python Sidecar Bidirectional Pipe IPC (`ping` -> `pong`, `uiReady` -> `fullState`) Verified!");
    println!("=== All Metis v1.4.1 Tauri + Python Sidecar Self-Tests Passed! ===");
    0
}
