use crate::models::VerifyExecutionResult;
use std::process::Stdio;
use std::time::{Duration, Instant};
use tokio::process::Command;

pub struct ShellRunner;

impl ShellRunner {
    pub async fn run_command(command_line: &str, working_dir: &str, timeout_seconds: u64) -> VerifyExecutionResult {
        let start = Instant::now();
        let cmd_trimmed = command_line.trim();

        #[cfg(target_os = "windows")]
        let mut cmd = {
            let mut c = Command::new("cmd.exe");
            c.args(["/c", cmd_trimmed]);
            c
        };

        #[cfg(not(target_os = "windows"))]
        let mut cmd = {
            let mut c = Command::new("sh");
            c.args(["-c", cmd_trimmed]);
            c
        };

        if !working_dir.trim().is_empty() && std::path::Path::new(working_dir).is_dir() {
            cmd.current_dir(working_dir);
        }

        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());

        let timeout_duration = Duration::from_secs(if timeout_seconds > 0 { timeout_seconds } else { 60 });

        let child = match cmd.spawn() {
            Ok(c) => c,
            Err(e) => {
                return VerifyExecutionResult {
                    command: cmd_trimmed.to_string(),
                    working_directory: working_dir.to_string(),
                    exit_code: -1,
                    stdout: String::new(),
                    stderr: format!("Failed to spawn shell process: {}", e),
                    duration_seconds: start.elapsed().as_secs_f64(),
                    timed_out: false,
                    cancelled: false,
                };
            }
        };

        match tokio::time::timeout(timeout_duration, child.wait_with_output()).await {
            Ok(Ok(output)) => {
                let stdout = String::from_utf8_lossy(&output.stdout).to_string();
                let stderr = String::from_utf8_lossy(&output.stderr).to_string();
                let exit_code = output.status.code().unwrap_or(-1);

                VerifyExecutionResult {
                    command: cmd_trimmed.to_string(),
                    working_directory: working_dir.to_string(),
                    exit_code,
                    stdout,
                    stderr,
                    duration_seconds: start.elapsed().as_secs_f64(),
                    timed_out: false,
                    cancelled: false,
                }
            }
            Ok(Err(e)) => VerifyExecutionResult {
                command: cmd_trimmed.to_string(),
                working_directory: working_dir.to_string(),
                exit_code: -1,
                stdout: String::new(),
                stderr: format!("Process execution error: {}", e),
                duration_seconds: start.elapsed().as_secs_f64(),
                timed_out: false,
                cancelled: false,
            },
            Err(_) => VerifyExecutionResult {
                command: cmd_trimmed.to_string(),
                working_directory: working_dir.to_string(),
                exit_code: -1,
                stdout: String::new(),
                stderr: format!("Process timed out after {} seconds.", timeout_seconds),
                duration_seconds: start.elapsed().as_secs_f64(),
                timed_out: true,
                cancelled: false,
            },
        }
    }
}
