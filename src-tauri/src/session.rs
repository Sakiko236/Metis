use crate::config::ConfigManager;
use crate::models::SessionStoreData;
use std::fs;
use std::path::PathBuf;

pub struct SessionManager;

impl SessionManager {
    pub fn get_sessions_file_path() -> PathBuf {
        ConfigManager::get_app_data_dir().join("sessions.json")
    }

    pub fn load_or_create_default() -> SessionStoreData {
        let primary_path = Self::get_sessions_file_path();

        let legacy_paths = vec![
            primary_path.clone(),
            dirs::config_dir().map(|d| d.join("Pinpoint").join("sessions.json")).unwrap_or_default(),
            PathBuf::from("sessions.json"),
        ];

        for path in legacy_paths {
            if path.exists() {
                if let Ok(content) = fs::read_to_string(&path) {
                    if let Ok(mut loaded) = serde_json::from_str::<SessionStoreData>(&content) {
                        if !loaded.sessions.is_empty() {
                            if loaded.active_session_id.is_empty() {
                                loaded.active_session_id = loaded.sessions[0].id.clone();
                            }
                            let _ = Self::save(&loaded);
                            return loaded;
                        }
                    }
                }
            }
        }

        let fresh = SessionStoreData::default();
        let _ = Self::save(&fresh);
        fresh
    }

    pub fn save(store: &SessionStoreData) -> Result<(), std::io::Error> {
        let path = Self::get_sessions_file_path();
        let json = serde_json::to_string_pretty(store).map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
        fs::write(path, json)
    }
}
