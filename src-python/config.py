from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import List

from models import AppConfig, VendorProfile, VendorType


class ConfigManager:
    @staticmethod
    def get_base_config_dir() -> Path:
        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA")
            if appdata:
                return Path(appdata)
            return Path.home() / "AppData" / "Roaming"
        elif sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support"
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME")
            if xdg:
                return Path(xdg)
            return Path.home() / ".config"

    @classmethod
    def get_app_data_dir(cls) -> Path:
        d = cls.get_base_config_dir() / "Metis"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @classmethod
    def get_config_path(cls) -> Path:
        return cls.get_app_data_dir() / "config.json"

    @staticmethod
    def get_app_version() -> str:
        # 1. Injected via environment variable from Tauri host
        env_ver = os.environ.get("METIS_APP_VERSION")
        if env_ver and env_ver.strip():
            return env_ver.strip()

        # 2. In local dev / standalone mode, resolve dynamically from package.json or Cargo.toml
        cur_file = Path(__file__).resolve()
        candidates = [
            cur_file.parent.parent / "package.json",
            cur_file.parent.parent / "src-tauri" / "Cargo.toml",
            Path.cwd() / "package.json",
            Path.cwd() / "src-tauri" / "Cargo.toml",
        ]
        for c in candidates:
            if c.is_file():
                try:
                    if c.suffix == ".json":
                        with open(c, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            v = data.get("version")
                            if v and isinstance(v, str) and not v.endswith(".json"):
                                return v.strip()
                    elif c.name == "Cargo.toml":
                        with open(c, "r", encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if line.startswith("version") and "=" in line:
                                    val = line.split("=", 1)[1].strip().strip("\"'")
                                    if val:
                                        return val
                except Exception:
                    continue

        return "1.0.0"

    @staticmethod
    def create_default_config() -> AppConfig:
        gemini = VendorProfile(
            id="gemini",
            display_name="Google Gemini",
            vendor_type=VendorType.Gemini,
            base_url="https://generativelanguage.googleapis.com/v1beta",
            selected_model="gemini-2.5-flash",
            preset_models=[
                "gemini-2.5-flash",
                "gemini-2.5-pro",
                "gemini-2.0-flash",
                "gemini-1.5-pro",
            ],
        )

        openai = VendorProfile(
            id="openai",
            display_name="OpenAI",
            vendor_type=VendorType.OpenAI,
            base_url="https://api.openai.com/v1",
            selected_model="gpt-4o",
            preset_models=[
                "gpt-4o",
                "gpt-4o-mini",
                "o3-mini",
                "gpt-4.5-preview",
            ],
        )

        claude = VendorProfile(
            id="claude",
            display_name="Anthropic Claude",
            vendor_type=VendorType.Claude,
            base_url="https://api.anthropic.com/v1",
            selected_model="claude-3-7-sonnet-20250219",
            preset_models=[
                "claude-3-7-sonnet-20250219",
                "claude-3-5-sonnet-20241022",
                "claude-3-5-haiku-20241022",
                "claude-3-opus-20240229",
            ],
        )

        deepseek = VendorProfile(
            id="deepseek",
            display_name="DeepSeek",
            vendor_type=VendorType.DeepSeek,
            base_url="https://api.deepseek.com/v1",
            selected_model="deepseek-chat",
            preset_models=[
                "deepseek-chat",
                "deepseek-reasoner",
            ],
        )

        ollama = VendorProfile(
            id="ollama",
            display_name="Ollama (Local Models)",
            vendor_type=VendorType.Ollama,
            base_url="http://localhost:11434/v1",
            selected_model="qwen2.5-coder:latest",
            preset_models=[
                "qwen2.5-coder:latest",
                "deepseek-r1:latest",
                "llama3.3:latest",
            ],
        )

        custom = VendorProfile(
            id="custom",
            display_name="Custom (OpenAI-Compatible)",
            vendor_type=VendorType.CustomOpenAI,
            base_url="https://api.openai.com/v1",
            selected_model="gpt-4o",
            preset_models=[
                "gpt-4o",
                "deepseek-chat",
            ],
        )

        return AppConfig(
            active_vendor_id="deepseek",
            max_verify_loops=8,
            command_timeout_seconds=60,
            theme="dark",
            thinking_intensity="medium",
            vendors=[deepseek, gemini, openai, claude, ollama, custom],
        )

    @classmethod
    def load_or_create_default(cls) -> AppConfig:
        primary_path = cls.get_config_path()
        legacy_paths: List[Path] = [
            primary_path,
            cls.get_base_config_dir() / "Pinpoint" / "config.json",
            Path("metis_config.json"),
            Path("pinpoint_config.json"),
        ]

        for p in legacy_paths:
            try:
                if p.exists():
                    content = p.read_text(encoding="utf-8")
                    data = json.loads(content)
                    if isinstance(data, dict):
                        loaded = AppConfig.from_dict(data)
                        if loaded.vendors:
                            cls.ensure_all_presets_exist(loaded)
                            cls.populate_env_keys(loaded)
                            cls.save(loaded)
                            return loaded
            except Exception:
                continue

        fresh = cls.create_default_config()
        cls.populate_env_keys(fresh)
        cls.save(fresh)
        return fresh

    @classmethod
    def save(cls, config: AppConfig) -> None:
        try:
            path = cls.get_config_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(config.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    @staticmethod
    def ensure_vendor_model_remembered(vendor: VendorProfile, model: str) -> None:
        m = model.strip()
        if not m:
            return
        vendor.preset_models = [x for x in vendor.preset_models if x.lower() != m.lower()]
        vendor.preset_models.insert(0, m)
        if len(vendor.preset_models) > 4:
            vendor.preset_models = vendor.preset_models[:4]

    @staticmethod
    def populate_env_keys(cfg: AppConfig) -> None:
        for v in cfg.vendors:
            if v.api_key and v.api_key.strip():
                continue
            if v.vendor_type == VendorType.Gemini:
                k = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
                if k:
                    v.api_key = k
            elif v.vendor_type == VendorType.OpenAI:
                k = os.environ.get("OPENAI_API_KEY")
                if k:
                    v.api_key = k
            elif v.vendor_type == VendorType.Claude:
                k = os.environ.get("ANTHROPIC_API_KEY")
                if k:
                    v.api_key = k
            elif v.vendor_type == VendorType.DeepSeek:
                k = os.environ.get("DEEPSEEK_API_KEY")
                if k:
                    v.api_key = k

    @classmethod
    def ensure_all_presets_exist(cls, cfg: AppConfig) -> None:
        defaults = cls.create_default_config()
        for def_v in defaults.vendors:
            existing = next((v for v in cfg.vendors if v.id.lower() == def_v.id.lower()), None)
            if existing is not None:
                if existing.selected_model and existing.selected_model.strip():
                    cls.ensure_vendor_model_remembered(existing, existing.selected_model)
            else:
                cfg.vendors.append(def_v)
