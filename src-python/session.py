from __future__ import annotations
import json
from pathlib import Path
from typing import List

from config import ConfigManager
from models import SessionStoreData


class SessionManager:
    @staticmethod
    def get_sessions_file_path() -> Path:
        return ConfigManager.get_app_data_dir() / "sessions.json"

    @classmethod
    def load_or_create_default(cls) -> SessionStoreData:
        primary_path = cls.get_sessions_file_path()
        legacy_paths: List[Path] = [
            primary_path,
            Path("sessions.json"),
        ]

        for p in legacy_paths:
            try:
                if p.exists():
                    content = p.read_text(encoding="utf-8")
                    data = json.loads(content)
                    if isinstance(data, dict):
                        loaded = SessionStoreData.from_dict(data)
                        if loaded.sessions:
                            if not loaded.active_session_id:
                                loaded.active_session_id = loaded.sessions[0].id
                            cls.save(loaded)
                            return loaded
            except Exception:
                continue

        fresh = SessionStoreData()
        cls.save(fresh)
        return fresh

    @classmethod
    def save(cls, store: SessionStoreData) -> None:
        try:
            path = cls.get_sessions_file_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(store.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass
