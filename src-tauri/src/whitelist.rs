use crate::models::{SurgicalActionType, SurgicalFileEdit, WhitelistedFile};
use std::fs;
use std::path::{Path, PathBuf};

pub struct WhitelistManager {
    files: Vec<WhitelistedFile>,
    directories: Vec<String>,
}

impl Default for WhitelistManager {
    fn default() -> Self {
        Self::new()
    }
}

impl WhitelistManager {
    pub fn new() -> Self {
        Self {
            files: Vec::new(),
            directories: Vec::new(),
        }
    }

    pub fn bind_session_list(&mut self, session_files: Vec<WhitelistedFile>, session_dirs: Vec<String>) {
        self.files = session_files;
        self.directories = session_dirs;
        self.update_all_relative_paths();
    }

    pub fn get_files(&self) -> &[WhitelistedFile] {
        &self.files
    }

    pub fn get_files_mut(&mut self) -> &mut Vec<WhitelistedFile> {
        &mut self.files
    }

    pub fn get_directories(&self) -> &[String] {
        &self.directories
    }

    pub fn get_active_workspace_directory(&self) -> String {
        for d in &self.directories {
            if !d.trim().is_empty() && Path::new(d).is_dir() {
                return d.clone();
            }
        }
        for f in &self.files {
            if !f.directory_path.trim().is_empty() && Path::new(&f.directory_path).is_dir() {
                return f.directory_path.clone();
            }
        }
        let fallback = dirs::config_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("Metis")
            .join("workspace");
        let _ = fs::create_dir_all(&fallback);
        fallback.to_string_lossy().to_string()
    }

    pub fn add_file(&mut self, full_path: &str) -> bool {
        let p = Path::new(full_path);
        if !p.exists() || p.is_dir() {
            return false;
        }

        let canonical_str = p.canonicalize().unwrap_or_else(|_| p.to_path_buf()).to_string_lossy().to_string();

        if self.files.iter().any(|x| x.full_path.eq_ignore_ascii_case(&canonical_str)) {
            return true;
        }

        let file_name = p.file_name().unwrap_or_default().to_string_lossy().to_string();
        let dir_path = p.parent().unwrap_or_else(|| Path::new("")).to_string_lossy().to_string();

        let ext = p.extension().unwrap_or_default().to_string_lossy().to_lowercase();
        let is_pdf = ext == "pdf";
        let is_img = matches!(ext.as_str(), "png" | "jpg" | "jpeg" | "webp" | "gif" | "bmp");

        let content = if is_pdf || is_img {
            format!("[Binary Media: {}]", file_name)
        } else {
            fs::read_to_string(p).unwrap_or_default()
        };

        let mut wf = WhitelistedFile::new(&canonical_str, &file_name, &dir_path, &content);
        if is_pdf {
            wf.media_kind = "pdf".to_string();
            wf.mime_type = "application/pdf".to_string();
            wf.is_binary_media = true;
        } else if is_img {
            wf.media_kind = "image".to_string();
            wf.mime_type = format!("image/{}", if ext == "jpg" { "jpeg" } else { &ext });
            wf.is_binary_media = true;
        }

        self.files.push(wf);
        self.update_all_relative_paths();
        true
    }

    pub fn add_directory(&mut self, folder_path: &str) -> (usize, Result<(), String>) {
        let p = Path::new(folder_path);
        if !p.is_dir() {
            return (0, Err("Not a valid directory".to_string()));
        }

        let canonical_dir = p.canonicalize().unwrap_or_else(|_| p.to_path_buf()).to_string_lossy().to_string();
        if !self.directories.iter().any(|d| d.eq_ignore_ascii_case(&canonical_dir)) {
            self.directories.push(canonical_dir.clone());
        }

        let mut loaded = 0;
        let mut stack = vec![PathBuf::from(folder_path)];

        while let Some(current_dir) = stack.pop() {
            if let Ok(entries) = fs::read_dir(&current_dir) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    let name = entry.file_name().to_string_lossy().to_string();

                    if path.is_dir() {
                        if !matches!(
                            name.to_lowercase().as_str(),
                            ".git" | ".svn" | ".hg" | "node_modules" | "bin" | "obj" | "__pycache__" | "target" | ".vscode" | ".idea"
                        ) {
                            stack.push(path);
                        }
                    } else if path.is_file() {
                        let ext = path.extension().unwrap_or_default().to_string_lossy().to_lowercase();
                        if !matches!(
                            ext.as_str(),
                            "exe" | "dll" | "pdb" | "obj" | "o" | "so" | "dylib" | "zip" | "tar" | "gz" | "7z" | "pyc"
                        ) {
                            let f_str = path.to_string_lossy().to_string();
                            if self.add_file(&f_str) {
                                loaded += 1;
                            }
                        }
                    }
                }
            }
        }

        self.update_all_relative_paths();
        (loaded, Ok(()))
    }

    pub fn remove_file(&mut self, full_path: &str) -> bool {
        let prev_len = self.files.len();
        self.files.retain(|x| !x.full_path.eq_ignore_ascii_case(full_path));
        self.update_all_relative_paths();
        self.files.len() < prev_len
    }

    pub fn remove_directory(&mut self, dir_path: &str) -> bool {
        let prev_len = self.directories.len();
        self.directories.retain(|x| !x.eq_ignore_ascii_case(dir_path));
        // Remove files belonging to this dir
        self.files.retain(|x| !x.full_path.starts_with(dir_path));
        self.update_all_relative_paths();
        self.directories.len() < prev_len
    }

    pub fn clear(&mut self) {
        self.files.clear();
        self.directories.clear();
    }

    pub fn resolve_whitelisted_file(&mut self, requested_name_or_path: &str) -> Option<&mut WhitelistedFile> {
        let cleaned = requested_name_or_path.trim().trim_matches(|c| c == '"' || c == '\'' || c == '`' || c == '<' || c == '>');
        if cleaned.is_empty() {
            if self.files.len() == 1 {
                return self.files.first_mut();
            }
            return None;
        }

        let p = Path::new(cleaned);
        if let Ok(canon) = p.canonicalize() {
            let canon_str = canon.to_string_lossy().to_string();
            if let Some(idx) = self.files.iter().position(|f| f.full_path.eq_ignore_ascii_case(&canon_str)) {
                return Some(&mut self.files[idx]);
            }
        }

        // Relative path match
        let norm_req = cleaned.replace('/', "\\").trim_start_matches(|c| c == '.' || c == '\\').to_string();
        if let Some(idx) = self.files.iter().position(|f| f.relative_path.replace('/', "\\").eq_ignore_ascii_case(&norm_req)) {
            return Some(&mut self.files[idx]);
        }

        // Filename match
        let target_fname = p.file_name().unwrap_or_default().to_string_lossy().to_string();
        let matches: Vec<usize> = self.files.iter().enumerate()
            .filter(|(_, f)| f.file_name.eq_ignore_ascii_case(cleaned) || f.file_name.eq_ignore_ascii_case(&target_fname))
            .map(|(i, _)| i)
            .collect();

        if matches.len() == 1 {
            return Some(&mut self.files[matches[0]]);
        }

        None
    }

    pub fn apply_edit_in_place(&mut self, edit: &SurgicalFileEdit) -> Result<(String, WhitelistedFile), String> {
        let target_opt = self.resolve_whitelisted_file(&edit.target_file);

        let target_file = match target_opt {
            Some(f) => f,
            None => {
                return Err(format!(
                    "[Security Block - File Creation Prohibited] Target file '{}' is not in the whitelist. Metis strictly forbids creating or deleting files; in-place edits on existing files only!",
                    edit.target_file
                ));
            }
        };

        let current_content = if Path::new(&target_file.full_path).exists() {
            fs::read_to_string(&target_file.full_path).unwrap_or_else(|_| target_file.last_saved_content.clone())
        } else {
            target_file.last_saved_content.clone()
        };

        let updated_content = match edit.action_type {
            SurgicalActionType::WriteFull => edit.new_content.clone(),
            SurgicalActionType::SearchReplace => {
                match Self::try_apply_search_replace(&current_content, &edit.search_block, &edit.replace_block) {
                    Some(res) => res,
                    None => {
                        return Err(format!(
                            "SEARCH block failed to match original code in `{}`. Please ensure the SEARCH block matches the file's current content verbatim.",
                            target_file.file_name
                        ));
                    }
                }
            }
        };

        if let Some(parent) = Path::new(&target_file.full_path).parent() {
            let _ = fs::create_dir_all(parent);
        }

        fs::write(&target_file.full_path, &updated_content)
            .map_err(|e| format!("Failed to write file [{}]: {}", target_file.file_name, e))?;

        target_file.last_saved_content = updated_content.clone();
        target_file.edit_count += 1;
        target_file.current_size_bytes = updated_content.len() as u64;
        target_file.last_modified_by_agent_at = chrono::Local::now().format("%H:%M:%S").to_string();

        let line_count = updated_content.lines().count();
        let display_name = if !target_file.relative_path.is_empty() {
            target_file.relative_path.clone()
        } else {
            target_file.file_name.clone()
        };

        let status_message = format!(
            "⚡ Modified file {} in-place ({} lines / {} bytes, update #{})",
            display_name, line_count, updated_content.len(), target_file.edit_count
        );

        Ok((status_message, target_file.clone()))
    }

    pub fn revert_file_to_initial(&mut self, full_path: &str) -> Result<String, String> {
        let target = self.resolve_whitelisted_file(full_path)
            .ok_or_else(|| format!("Target file '{}' not in whitelist", full_path))?;

        let init = target.initial_content.clone();
        fs::write(&target.full_path, &init)
            .map_err(|e| format!("Failed to revert file: {}", e))?;

        target.last_saved_content = init;
        target.edit_count = 0;
        target.last_modified_by_agent_at = chrono::Local::now().format("%H:%M:%S").to_string();

        Ok(format!("Reverted {} to initial state.", target.file_name))
    }

    pub fn try_apply_search_replace(current: &str, search: &str, replace: &str) -> Option<String> {
        if search.is_empty() {
            return None;
        }

        // 1. Exact match
        if let Some(idx) = current.find(search) {
            let mut out = String::with_capacity(current.len() + replace.len());
            out.push_str(&current[..idx]);
            out.push_str(replace);
            out.push_str(&current[idx + search.len()..]);
            return Some(out);
        }

        // 2. Normalized CRLF/LF match
        let norm_cur = current.replace("\r\n", "\n");
        let norm_search = search.replace("\r\n", "\n");
        let norm_replace = replace.replace("\r\n", "\n");
        let uses_crlf = current.contains("\r\n");

        if let Some(idx) = norm_cur.find(&norm_search) {
            let mut out = String::new();
            out.push_str(&norm_cur[..idx]);
            out.push_str(&norm_replace);
            out.push_str(&norm_cur[idx + norm_search.len()..]);
            return Some(if uses_crlf { out.replace('\n', "\r\n") } else { out });
        }

        // 3. Line-by-line whitespace-trimmed match
        let cur_lines: Vec<&str> = norm_cur.lines().collect();
        let search_lines: Vec<&str> = norm_search.trim_matches('\n').lines().collect();

        if !search_lines.is_empty() && cur_lines.len() >= search_lines.len() {
            for i in 0..=(cur_lines.len() - search_lines.len()) {
                let mut match_found = true;
                for j in 0..search_lines.len() {
                    if cur_lines[i + j].trim() != search_lines[j].trim() {
                        match_found = false;
                        break;
                    }
                }
                if match_found {
                    let mut new_lines = Vec::new();
                    for &line in cur_lines.iter().take(i) {
                        new_lines.push(line.to_string());
                    }
                    for line in norm_replace.trim_matches('\n').lines() {
                        new_lines.push(line.to_string());
                    }
                    for &line in cur_lines.iter().skip(i + search_lines.len()) {
                        new_lines.push(line.to_string());
                    }
                    let out = new_lines.join("\n");
                    return Some(if uses_crlf { out.replace('\n', "\r\n") } else { out });
                }
            }
        }

        None
    }

    fn update_all_relative_paths(&mut self) {
        let ws_dir = self.get_active_workspace_directory();
        let ws_path = Path::new(&ws_dir);
        for f in &mut self.files {
            if let Ok(rel) = Path::new(&f.full_path).strip_prefix(ws_path) {
                f.relative_path = rel.to_string_lossy().replace('\\', "/");
            } else {
                f.relative_path = f.file_name.clone();
            }
        }
    }
}
