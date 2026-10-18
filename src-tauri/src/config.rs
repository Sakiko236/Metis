use crate::models::{AppConfig, VendorProfile, VendorType};
use std::fs;
use std::path::PathBuf;

pub struct ConfigManager;

impl ConfigManager {
    pub fn get_app_data_dir() -> PathBuf {
        let base = dirs::config_dir().unwrap_or_else(|| PathBuf::from("."));
        let dir = base.join("Metis");
        let _ = fs::create_dir_all(&dir);
        dir
    }

    pub fn get_config_path() -> PathBuf {
        Self::get_app_data_dir().join("config.json")
    }

    pub fn create_default_config() -> AppConfig {
        let mut cfg = AppConfig::default();
        cfg.active_vendor_id = "deepseek".to_string();
        cfg.command_timeout_seconds = 60;
        cfg.theme = "dark".to_string();
        cfg.thinking_intensity = "medium".to_string();

        let mut gemini = VendorProfile::new(
            "gemini",
            "Google Gemini",
            VendorType::Gemini,
            "https://generativelanguage.googleapis.com/v1beta",
            "gemini-2.5-flash",
        );
        gemini.preset_models = vec![
            "gemini-2.5-flash".to_string(),
            "gemini-2.5-pro".to_string(),
            "gemini-2.0-flash".to_string(),
            "gemini-1.5-pro".to_string(),
        ];

        let mut openai = VendorProfile::new(
            "openai",
            "OpenAI",
            VendorType::OpenAI,
            "https://api.openai.com/v1",
            "gpt-4o",
        );
        openai.preset_models = vec![
            "gpt-4o".to_string(),
            "gpt-4o-mini".to_string(),
            "o3-mini".to_string(),
            "gpt-4.5-preview".to_string(),
        ];

        let mut claude = VendorProfile::new(
            "claude",
            "Anthropic Claude",
            VendorType::Claude,
            "https://api.anthropic.com/v1",
            "claude-3-7-sonnet-20250219",
        );
        claude.preset_models = vec![
            "claude-3-7-sonnet-20250219".to_string(),
            "claude-3-5-sonnet-20241022".to_string(),
            "claude-3-5-haiku-20241022".to_string(),
            "claude-3-opus-20240229".to_string(),
        ];

        let mut deepseek = VendorProfile::new(
            "deepseek",
            "DeepSeek",
            VendorType::DeepSeek,
            "https://api.deepseek.com/v1",
            "deepseek-chat",
        );
        deepseek.preset_models = vec![
            "deepseek-chat".to_string(),
            "deepseek-reasoner".to_string(),
        ];

        let mut ollama = VendorProfile::new(
            "ollama",
            "Ollama (Local Models)",
            VendorType::Ollama,
            "http://localhost:11434/v1",
            "qwen2.5-coder:latest",
        );
        ollama.preset_models = vec![
            "qwen2.5-coder:latest".to_string(),
            "deepseek-r1:latest".to_string(),
            "llama3.3:latest".to_string(),
        ];

        let mut custom = VendorProfile::new(
            "custom",
            "Custom (OpenAI-Compatible)",
            VendorType::CustomOpenAI,
            "https://api.openai.com/v1",
            "gpt-4o",
        );
        custom.preset_models = vec![
            "gpt-4o".to_string(),
            "deepseek-chat".to_string(),
        ];

        cfg.vendors = vec![deepseek, gemini, openai, claude, ollama, custom];

        cfg
    }

    pub fn load_or_create_default() -> AppConfig {
        let primary_path = Self::get_config_path();

        // Check primary or legacy fallback paths (Pinpoint, SurgicalAgent)
        let legacy_paths = vec![
            primary_path.clone(),
            dirs::config_dir().map(|d| d.join("Pinpoint").join("config.json")).unwrap_or_default(),
            PathBuf::from("metis_config.json"),
            PathBuf::from("pinpoint_config.json"),
        ];

        for path in legacy_paths {
            if path.exists() {
                if let Ok(content) = fs::read_to_string(&path) {
                    if let Ok(mut loaded) = serde_json::from_str::<AppConfig>(&content) {
                        if !loaded.vendors.is_empty() {
                            Self::ensure_all_presets_exist(&mut loaded);
                            Self::populate_env_keys(&mut loaded);
                            let _ = Self::save(&loaded);
                            return loaded;
                        }
                    }
                }
            }
        }

        let mut fresh = Self::create_default_config();
        Self::populate_env_keys(&mut fresh);
        let _ = Self::save(&fresh);
        fresh
    }

    pub fn save(config: &AppConfig) -> Result<(), std::io::Error> {
        let path = Self::get_config_path();
        let json = serde_json::to_string_pretty(config).map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
        fs::write(path, json)
    }

    pub fn ensure_vendor_model_remembered(vendor: &mut VendorProfile, model: &str) {
        let m = model.trim();
        if m.is_empty() {
            return;
        }
        vendor.preset_models.retain(|x| !x.eq_ignore_ascii_case(m));
        vendor.preset_models.insert(0, m.to_string());
        if vendor.preset_models.len() > 4 {
            vendor.preset_models.truncate(4);
        }
    }

    fn populate_env_keys(cfg: &mut AppConfig) {
        for v in &mut cfg.vendors {
            if !v.api_key.trim().is_empty() {
                continue;
            }
            match v.vendor_type {
                VendorType::Gemini => {
                    if let Ok(k) = std::env::var("GEMINI_API_KEY").or_else(|_| std::env::var("GOOGLE_API_KEY")) {
                        v.api_key = k;
                    }
                }
                VendorType::OpenAI => {
                    if let Ok(k) = std::env::var("OPENAI_API_KEY") {
                        v.api_key = k;
                    }
                }
                VendorType::Claude => {
                    if let Ok(k) = std::env::var("ANTHROPIC_API_KEY") {
                        v.api_key = k;
                    }
                }
                VendorType::DeepSeek => {
                    if let Ok(k) = std::env::var("DEEPSEEK_API_KEY") {
                        v.api_key = k;
                    }
                }
                _ => {}
            }
        }
    }

    fn ensure_all_presets_exist(cfg: &mut AppConfig) {
        let defaults = Self::create_default_config();
        for def_v in defaults.vendors {
            if let Some(existing) = cfg.vendors.iter_mut().find(|v| v.id.eq_ignore_ascii_case(&def_v.id)) {
                // Ensure active selected model is remembered
                if !existing.selected_model.trim().is_empty() {
                    Self::ensure_vendor_model_remembered(existing, &existing.selected_model.clone());
                }
            } else {
                cfg.vendors.push(def_v);
            }
        }
    }
}
