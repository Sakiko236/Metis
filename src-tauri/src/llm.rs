use crate::models::{
    ChatHistoryItem, ParsedAgentResponse, SurgicalActionType, SurgicalFileEdit, VendorProfile, VendorType, WhitelistedFile,
};
use futures_util::StreamExt;
use regex::Regex;
use serde_json::{json, Value};
use std::time::Duration;

pub struct MultiVendorClient;

impl MultiVendorClient {
    pub async fn send_chat<F>(
        vendor: &VendorProfile,
        system_prompt: &str,
        files_snapshot: &str,
        history: &[ChatHistoryItem],
        whitelist: &[WhitelistedFile],
        thinking_intensity: &str,
        on_chunk: F,
    ) -> Result<String, String>
    where
        F: FnMut(&str),
    {
        if vendor.vendor_type == VendorType::LocalSandbox {
            return Self::run_local_sandbox(files_snapshot, history, whitelist, on_chunk).await;
        }

        let full_system = format!("{}\n\n{}", system_prompt, files_snapshot);
        let base_url = vendor.base_url.trim_end_matches('/');

        match vendor.vendor_type {
            VendorType::Gemini => {
                Self::call_gemini(vendor, base_url, &full_system, history, thinking_intensity, on_chunk).await
            }
            VendorType::Claude => {
                Self::call_claude(vendor, base_url, &full_system, history, thinking_intensity, on_chunk).await
            }
            _ => {
                Self::call_openai_compatible(vendor, base_url, &full_system, history, on_chunk).await
            }
        }
    }

    async fn call_openai_compatible<F>(
        vendor: &VendorProfile,
        base_url: &str,
        system_prompt: &str,
        history: &[ChatHistoryItem],
        mut on_chunk: F,
    ) -> Result<String, String>
    where
        F: FnMut(&str),
    {
        let url = if base_url.ends_with("/chat/completions") {
            base_url.to_string()
        } else {
            format!("{}/chat/completions", base_url)
        };

        let mut messages = vec![json!({
            "role": "system",
            "content": system_prompt
        })];

        for item in history {
            messages.push(json!({
                "role": if item.role == "assistant" { "assistant" } else { "user" },
                "content": item.content
            }));
        }

        let payload = json!({
            "model": vendor.selected_model,
            "messages": messages,
            "stream": true,
            "temperature": 0.2
        });

        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(180))
            .build()
            .map_err(|e| e.to_string())?;

        let mut req = client.post(&url).json(&payload);
        if !vendor.api_key.trim().is_empty() {
            req = req.header("Authorization", format!("Bearer {}", vendor.api_key.trim()));
        }

        let resp = req.send().await.map_err(|e| format!("HTTP request error: {}", e))?;
        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            return Err(format!("LLM Provider Error (HTTP {}): {}", status, body));
        }

        let mut stream = resp.bytes_stream();
        let mut full_output = String::new();
        let mut buffer = String::new();
        let mut is_in_reasoning = false;

        while let Some(chunk_result) = stream.next().await {
            let chunk = chunk_result.map_err(|e| format!("Stream read error: {}", e))?;
            let text = String::from_utf8_lossy(&chunk);
            buffer.push_str(&text);

            while let Some(newline_pos) = buffer.find('\n') {
                let line = buffer[..newline_pos].trim().to_string();
                buffer.drain(..=newline_pos);

                if line.starts_with("data:") {
                    let json_str = line.trim_start_matches("data:").trim();
                    if json_str == "[DONE]" {
                        break;
                    }
                    if let Ok(v) = serde_json::from_str::<Value>(json_str) {
                        if let Some(choices) = v.get("choices").and_then(|c| c.as_array()) {
                            if let Some(delta) = choices.first().and_then(|c| c.get("delta")) {
                                if let Some(content) = delta.get("content").and_then(|s| s.as_str()) {
                                    if is_in_reasoning {
                                        is_in_reasoning = false;
                                        full_output.push_str("</think>\n");
                                        on_chunk("</think>\n");
                                    }
                                    full_output.push_str(content);
                                    on_chunk(content);
                                } else if let Some(reasoning) = delta.get("reasoning_content").and_then(|s| s.as_str()) {
                                    // Handle DeepSeek reasoner
                                    if !is_in_reasoning {
                                        is_in_reasoning = true;
                                        full_output.push_str("<think>");
                                        on_chunk("<think>");
                                    }
                                    full_output.push_str(reasoning);
                                    on_chunk(reasoning);
                                }
                            }
                        }
                    }
                }
            }
        }

        if is_in_reasoning {
            full_output.push_str("</think>\n");
            on_chunk("</think>\n");
        }

        Ok(full_output)
    }

    async fn call_claude<F>(
        vendor: &VendorProfile,
        base_url: &str,
        system_prompt: &str,
        history: &[ChatHistoryItem],
        thinking_intensity: &str,
        mut on_chunk: F,
    ) -> Result<String, String>
    where
        F: FnMut(&str),
    {
        let url = if base_url.ends_with("/messages") {
            base_url.to_string()
        } else {
            format!("{}/messages", base_url)
        };

        let mut messages = Vec::new();
        for item in history {
            messages.push(json!({
                "role": if item.role == "assistant" { "assistant" } else { "user" },
                "content": item.content
            }));
        }

        let budget_tokens = match thinking_intensity.to_lowercase().as_str() {
            "high" => 16000,
            "low" => 2048,
            "off" => 0,
            _ => 6000,
        };

        let mut payload = json!({
            "model": vendor.selected_model,
            "max_tokens": 8192,
            "system": system_prompt,
            "messages": messages,
            "stream": true
        });

        if budget_tokens > 0 {
            payload["thinking"] = json!({
                "type": "enabled",
                "budget_tokens": budget_tokens
            });
        }

        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(180))
            .build()
            .map_err(|e| e.to_string())?;

        let resp = client
            .post(&url)
            .header("x-api-key", vendor.api_key.trim())
            .header("anthropic-version", "2023-06-01")
            .json(&payload)
            .send()
            .await
            .map_err(|e| format!("HTTP request error: {}", e))?;

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            return Err(format!("Claude API Error (HTTP {}): {}", status, body));
        }

        let mut stream = resp.bytes_stream();
        let mut full_output = String::new();
        let mut buffer = String::new();

        while let Some(chunk_result) = stream.next().await {
            let chunk = chunk_result.map_err(|e| format!("Stream read error: {}", e))?;
            let text = String::from_utf8_lossy(&chunk);
            buffer.push_str(&text);

            while let Some(newline_pos) = buffer.find('\n') {
                let line = buffer[..newline_pos].trim().to_string();
                buffer.drain(..=newline_pos);

                if line.starts_with("data:") {
                    let json_str = line.trim_start_matches("data:").trim();
                    if let Ok(v) = serde_json::from_str::<Value>(json_str) {
                        if v.get("type").and_then(|t| t.as_str()) == Some("content_block_delta") {
                            if let Some(delta) = v.get("delta") {
                                if let Some(txt) = delta.get("text").and_then(|t| t.as_str()) {
                                    full_output.push_str(txt);
                                    on_chunk(txt);
                                }
                            }
                        }
                    }
                }
            }
        }

        Ok(full_output)
    }

    async fn call_gemini<F>(
        vendor: &VendorProfile,
        base_url: &str,
        system_prompt: &str,
        history: &[ChatHistoryItem],
        thinking_intensity: &str,
        mut on_chunk: F,
    ) -> Result<String, String>
    where
        F: FnMut(&str),
    {
        let raw_model = vendor.selected_model.trim();
        let clean_model = if raw_model.is_empty() {
            "gemini-2.5-flash"
        } else {
            raw_model.trim_start_matches("models/")
        };

        let url = if vendor.api_key.trim().is_empty() {
            format!("{}/models/{}:streamGenerateContent?alt=sse", base_url, clean_model)
        } else {
            format!(
                "{}/models/{}:streamGenerateContent?alt=sse&key={}",
                base_url,
                clean_model,
                vendor.api_key.trim()
            )
        };

        let mut contents = Vec::new();
        for item in history {
            contents.push(json!({
                "role": if item.role == "assistant" { "model" } else { "user" },
                "parts": [{ "text": item.content }]
            }));
        }

        let budget_tokens = match thinking_intensity.to_lowercase().as_str() {
            "off" => 0,
            "low" => 2048,
            "high" => 24576,
            _ => 8192,
        };

        let mut gen_config = json!({
            "temperature": 0.2
        });

        // Gemini 2.5 Flash / Pro and 2.0 Thinking models support thinkingConfig
        let supports_thinking = !clean_model.contains("1.5");
        if supports_thinking {
            gen_config["thinkingConfig"] = json!({
                "thinkingBudget": budget_tokens
            });
        }

        let mut payload = json!({
            "contents": contents,
            "systemInstruction": {
                "parts": [{ "text": system_prompt }]
            },
            "generationConfig": gen_config
        });

        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(180))
            .build()
            .map_err(|e| e.to_string())?;

        let mut req = client.post(&url).json(&payload);
        if !vendor.api_key.trim().is_empty() {
            req = req.header("x-goog-api-key", vendor.api_key.trim());
        }

        let mut resp = req.send().await.map_err(|e| format!("HTTP request error: {}", e))?;

        // If the endpoint failed because thinkingConfig was rejected, fallback gracefully without thinkingConfig
        if !resp.status().is_success() && supports_thinking {
            let body = resp.text().await.unwrap_or_default();
            if body.contains("thinkingConfig") || body.contains("thinkingBudget") || body.contains("not supported") {
                payload["generationConfig"] = json!({ "temperature": 0.2 });
                let mut retry_req = client.post(&url).json(&payload);
                if !vendor.api_key.trim().is_empty() {
                    retry_req = retry_req.header("x-goog-api-key", vendor.api_key.trim());
                }
                resp = retry_req.send().await.map_err(|e| format!("HTTP request error on retry: {}", e))?;
            } else {
                return Err(format!("Gemini API Error (HTTP {}): {}", 400, body));
            }
        }

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            return Err(format!("Gemini API Error (HTTP {}): {}", status, body));
        }

        let mut stream = resp.bytes_stream();
        let mut full_output = String::new();
        let mut buffer = String::new();
        let mut is_in_thought = false;

        while let Some(chunk_result) = stream.next().await {
            let chunk = chunk_result.map_err(|e| format!("Stream read error: {}", e))?;
            let text = String::from_utf8_lossy(&chunk);
            buffer.push_str(&text);

            while let Some(newline_pos) = buffer.find('\n') {
                let line = buffer[..newline_pos].trim().to_string();
                buffer.drain(..=newline_pos);

                if line.starts_with("data:") {
                    let json_str = line.trim_start_matches("data:").trim();
                    if let Ok(v) = serde_json::from_str::<Value>(json_str) {
                        if let Some(candidates) = v.get("candidates").and_then(|c| c.as_array()) {
                            if let Some(first) = candidates.first() {
                                if let Some(parts) = first.get("content").and_then(|c| c.get("parts")).and_then(|p| p.as_array()) {
                                    for part in parts {
                                        let is_thought = part.get("thought").and_then(|t| t.as_bool()).unwrap_or(false);
                                        if let Some(txt) = part.get("text").and_then(|t| t.as_str()) {
                                            if is_thought {
                                                if !is_in_thought {
                                                    is_in_thought = true;
                                                    full_output.push_str("<think>");
                                                    on_chunk("<think>");
                                                }
                                            } else if is_in_thought {
                                                is_in_thought = false;
                                                full_output.push_str("</think>\n");
                                                on_chunk("</think>\n");
                                            }
                                            full_output.push_str(txt);
                                            on_chunk(txt);
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        if is_in_thought {
            full_output.push_str("</think>\n");
            on_chunk("</think>\n");
        }

        Ok(full_output)
    }

    pub fn extract_session_title(raw_output: &str) -> Option<String> {
        let title_re = Regex::new(r#"(?is)<(?:metis|session)_title\b[^>]*>(.*?)</(?:metis|session)_title>"#).unwrap();
        if let Some(cap) = title_re.captures(raw_output) {
            if let Some(m) = cap.get(1) {
                let cleaned = m
                    .as_str()
                    .lines()
                    .next()
                    .unwrap_or("")
                    .trim()
                    .trim_matches(|c: char| c == '"' || c == '\'' || c == '`' || c == '#' || c == '*')
                    .trim();
                if !cleaned.is_empty() {
                    let chars: Vec<char> = cleaned.chars().collect();
                    let truncated: String = if chars.len() > 40 {
                        format!("{}...", chars[..40].iter().collect::<String>())
                    } else {
                        cleaned.to_string()
                    };
                    return Some(truncated);
                }
            }
        }
        None
    }

    pub fn parse_agent_output(raw_output: &str, whitelist: &[WhitelistedFile]) -> ParsedAgentResponse {
        let mut parsed = ParsedAgentResponse {
            raw_text: raw_output.to_string(),
            ..Default::default()
        };

        let think_re = Regex::new(r#"(?s)<think\b[^>]*>.*?</think>\s*"#).unwrap();
        let title_re = Regex::new(r#"(?is)<(?:metis|session)_title\b[^>]*>.*?</(?:metis|session)_title>\s*"#).unwrap();
        let cleaned_think = think_re.replace_all(raw_output, "");
        let cleaned = title_re.replace_all(&cleaned_think, "");

        // 1. Parse <metis_replace file="...">
        let replace_re = Regex::new(r#"(?is)<(?:metis|surgical|pinpoint)_replace(?:\s+(?:file|path|name)?\s*=?\s*["']?([^"'>\r\n]+)["']?)?\s*>\r?\n?(.*?)(?:</(?:metis|surgical|pinpoint)_replace>|$)"#).unwrap();
        let sr_block_re = Regex::new(r#"(?is)<{5,}\s*SEARCH\r?\n(.*?)\r?\n={5,}\r?\n(.*?)\r?\n>{5,}\s*REPLACE"#).unwrap();

        for cap in replace_re.captures_iter(&cleaned) {
            let file_attr = cap.get(1).map_or("", |m| m.as_str()).trim();
            let body = cap.get(2).map_or("", |m| m.as_str());

            for sr in sr_block_re.captures_iter(body) {
                let search = sr.get(1).map_or("", |m| m.as_str());
                let replace = sr.get(2).map_or("", |m| m.as_str());
                parsed.file_edits.push(SurgicalFileEdit {
                    action_type: SurgicalActionType::SearchReplace,
                    target_file: file_attr.to_string(),
                    new_content: String::new(),
                    search_block: search.to_string(),
                    replace_block: replace.to_string(),
                });
            }
        }

        // 2. Parse <metis_write file="..."> (for empty files or complete rewrites)
        let write_re = Regex::new(r#"(?is)<(?:metis|surgical|pinpoint)_write(?:\s+(?:file|path|name)?\s*=?\s*["']?([^"'>\r\n]+)["']?)?\s*>\r?\n?(.*?)(?:</(?:metis|surgical|pinpoint)_write>|$)"#).unwrap();
        for cap in write_re.captures_iter(&cleaned) {
            let file_attr = cap.get(1).map_or("", |m| m.as_str()).trim();
            let body = cap.get(2).map_or("", |m| m.as_str());
            if !parsed.file_edits.iter().any(|e| e.target_file.eq_ignore_ascii_case(file_attr)) {
                parsed.file_edits.push(SurgicalFileEdit {
                    action_type: SurgicalActionType::WriteFull,
                    target_file: file_attr.to_string(),
                    new_content: body.to_string(),
                    search_block: String::new(),
                    replace_block: String::new(),
                });
            }
        }

        // 3. Parse raw SEARCH/REPLACE blocks outside tags
        for sr in sr_block_re.captures_iter(&cleaned) {
            let search = sr.get(1).map_or("", |m| m.as_str());
            let replace = sr.get(2).map_or("", |m| m.as_str());

            let already = parsed.file_edits.iter().any(|e| e.search_block == search && e.replace_block == replace);
            if !already {
                let target_file = whitelist.first().map(|f| f.file_name.clone()).unwrap_or_default();
                parsed.file_edits.push(SurgicalFileEdit {
                    action_type: SurgicalActionType::SearchReplace,
                    target_file,
                    new_content: String::new(),
                    search_block: search.to_string(),
                    replace_block: replace.to_string(),
                });
            }
        }

        // 4. Parse <metis_cmd>
        let cmd_re = Regex::new(r#"(?is)<(?:metis|surgical|pinpoint)_(?:cmd|terminal|verify)[^>]*>\r?\n?(.*?)\r?\n?</(?:metis|surgical|pinpoint)_(?:cmd|terminal|verify)>"#).unwrap();
        for cap in cmd_re.captures_iter(&cleaned) {
            let raw_cmd = cap.get(1).map_or("", |m| m.as_str()).trim();
            if !raw_cmd.is_empty() {
                let valid_lines: Vec<&str> = raw_cmd
                    .lines()
                    .map(|l| l.trim())
                    .filter(|l| !l.is_empty() && !l.starts_with('#') && !l.starts_with("//") && !l.to_lowercase().starts_with("rem "))
                    .collect();
                if !valid_lines.is_empty() {
                    let combined = valid_lines.join(" && ");
                    if !parsed.terminal_commands.contains(&combined) {
                        parsed.terminal_commands.push(combined);
                    }
                }
            }
        }

        // 5. Parse <metis_search>
        let search_re = Regex::new(r#"(?is)<(?:metis|surgical|pinpoint)?_?search[^>]*>\r?\n?(.*?)\r?\n?</(?:metis|surgical|pinpoint)?_?search>"#).unwrap();
        for cap in search_re.captures_iter(&cleaned) {
            let q = cap.get(1).map_or("", |m| m.as_str()).trim();
            if !q.is_empty() && !parsed.search_queries.contains(&q.to_string()) {
                parsed.search_queries.push(q.to_string());
            }
        }

        parsed.explanation_text = Self::format_display_text(raw_output);
        parsed
    }

    pub fn format_display_text(text: &str) -> String {
        let think_re = Regex::new(r#"(?is)<think\b[^>]*>.*?</think>\s*"#).unwrap();
        let unclosed_think_re = Regex::new(r#"(?is)<think\b[^>]*>.*$"#).unwrap();
        let title_re = Regex::new(r#"(?is)<(?:metis|session)_title\b[^>]*>.*?</(?:metis|session)_title>\s*"#).unwrap();
        let unclosed_title_re = Regex::new(r#"(?is)<(?:metis|session)_title\b[^>]*>.*$"#).unwrap();

        let step1 = think_re.replace_all(text, "");
        let step2 = unclosed_think_re.replace_all(&step1, "");
        let step3 = title_re.replace_all(&step2, "");
        let cleaned = unclosed_title_re.replace_all(&step3, "").trim().to_string();

        let write_re = Regex::new(r#"(?is)<(?:metis|surgical|pinpoint)_write(?:\s+(?:file|path|name)?\s*=?\s*["']?([^"'>\r\n]+)["']?)?\s*>\r?\n?(.*?)(?:</(?:metis|surgical|pinpoint)_write>|$)"#).unwrap();
        let formatted = write_re.replace_all(&cleaned, |caps: &regex::Captures| {
            let f_name = caps.get(1).map_or("", |m| m.as_str());
            let body = caps.get(2).map_or("", |m| m.as_str());
            format!("📄 **`{}`**\n```\n{}\n```", f_name, body.trim_end())
        });

        let replace_re = Regex::new(r#"(?is)<(?:metis|surgical|pinpoint)_replace(?:\s+(?:file|path|name)?\s*=?\s*["']?([^"'>\r\n]+)["']?)?\s*>\r?\n?(.*?)(?:</(?:metis|surgical|pinpoint)_replace>|$)"#).unwrap();
        let formatted2 = replace_re.replace_all(&formatted, |caps: &regex::Captures| {
            let f_name = caps.get(1).map_or("", |m| m.as_str());
            let body = caps.get(2).map_or("", |m| m.as_str());
            format!("📄 **`{}` (Surgical Update)**\n```\n{}\n```", f_name, body.trim())
        });

        formatted2.to_string()
    }

    pub async fn fetch_ollama_models(base_url: &str) -> Vec<String> {
        let clean = base_url.trim_end_matches('/');
        let url = format!("{}/tags", clean.trim_end_matches("/v1"));

        let client = match reqwest::Client::builder().timeout(Duration::from_secs(5)).build() {
            Ok(c) => c,
            Err(_) => return Vec::new(),
        };

        if let Ok(resp) = client.get(&url).send().await {
            if let Ok(v) = resp.json::<Value>().await {
                if let Some(models) = v.get("models").and_then(|m| m.as_array()) {
                    let mut list = Vec::new();
                    for m in models {
                        if let Some(name) = m.get("name").and_then(|n| n.as_str()) {
                            list.push(name.to_string());
                        }
                    }
                    return list;
                }
            }
        }

        Vec::new()
    }

    async fn run_local_sandbox<F>(
        _files_snapshot: &str,
        history: &[ChatHistoryItem],
        whitelist: &[WhitelistedFile],
        mut on_chunk: F,
    ) -> Result<String, String>
    where
        F: FnMut(&str),
    {
        tokio::time::sleep(Duration::from_millis(150)).await;

        let last_user_msg = history
            .iter()
            .rev()
            .find(|m| m.role == "user")
            .map(|m| m.content.as_str())
            .unwrap_or_default();

        if last_user_msg.starts_with("[Terminal Execution Feedback") || last_user_msg.contains("Exit Code: 0") {
            let summary = "✅ Terminal execution completed (Exit Code 0). Output matches expectations, all tests verified successfully!";
            on_chunk(summary);
            return Ok(summary.to_string());
        }

        if whitelist.is_empty() {
            let msg = "<metis_title>Workspace File Setup</metis_title>\nNo files are currently attached to this workspace. Please drop files to edit.";
            on_chunk(msg);
            return Ok(msg.to_string());
        }

        let target = &whitelist[0];
        let code = if std::path::Path::new(&target.full_path).exists() {
            std::fs::read_to_string(&target.full_path).unwrap_or_else(|_| target.last_saved_content.clone())
        } else {
            target.last_saved_content.clone()
        };

        let mut resp = String::new();
        resp.push_str("<metis_title>Fix Token Expiration Bug</metis_title>\n");

        if code.contains("is_token_expired") {
            resp.push_str(&format!("Inspected whitelisted file `{}`:\n", target.file_name));
            resp.push_str("- **Issue Identified**: Inverted operator in `is_token_expired`.\n");
            resp.push_str(&format!("<metis_replace file=\"{}\">\n", target.file_name));
            resp.push_str("<<<<<<< SEARCH\n");
            resp.push_str("    return current_time < expires_at\n");
            resp.push_str("=======\n");
            resp.push_str("    return current_time >= expires_at\n");
            resp.push_str(">>>>>>> REPLACE\n");
            resp.push_str("</metis_replace>\n");
            resp.push_str("<metis_cmd>\npython -m unittest test_auth.py\n</metis_cmd>\n");
        } else {
            resp.push_str(&format!("[Mock Sandbox Engine] Inspected `{}`.\n", target.file_name));
        }

        on_chunk(&resp);
        Ok(resp)
    }
}
