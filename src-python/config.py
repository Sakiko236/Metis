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
            thinking_intensity="medium",
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
            thinking_intensity="high",
        )

        openai_compat = VendorProfile(
            id="openai_compatible",
            display_name="OpenAI Compatible",
            vendor_type=VendorType.CustomOpenAI,
            base_url="https://api.openai.com/v1",
            selected_model="gpt-4o",
            preset_models=[
                "gpt-4o",
                "gpt-4o-mini",
                "deepseek-chat",
                "deepseek-reasoner",
            ],
            thinking_intensity="high",
        )

        return AppConfig(
            active_vendor_id="openai_compatible",
            max_verify_loops=0,
            command_timeout_seconds=60,
            theme="dark",
            thinking_intensity="high",
            vendors=[gemini, claude, openai_compat],
        )

    @classmethod
    def migrate_to_three_providers(cls, cfg: AppConfig) -> None:
        """
        Consolidates legacy multi-vendor configurations into the 3 standardized providers:
        1. Google Gemini
        2. Anthropic Claude
        3. OpenAI Compatible
        """
        existing_vendor_ids = [v.id.lower() for v in cfg.vendors]
        if set(existing_vendor_ids) == {"gemini", "claude", "openai_compatible"}:
            return

        gemini_existing = next((v for v in cfg.vendors if v.id.lower() == "gemini"), None)
        claude_existing = next((v for v in cfg.vendors if v.id.lower() == "claude"), None)
        compat_existing = next((v for v in cfg.vendors if v.id.lower() in ("openai_compatible", "openai-compatible")), None)

        defaults = cls.create_default_config()
        final_gemini = gemini_existing or defaults.vendors[0]
        final_gemini.display_name = "Google Gemini"

        final_claude = claude_existing or defaults.vendors[1]
        final_claude.display_name = "Anthropic Claude"

        if compat_existing:
            final_compat = compat_existing
            final_compat.display_name = "OpenAI Compatible"
        else:
            active_id = (cfg.active_vendor_id or "").lower()
            active_donor = next((v for v in cfg.vendors if v.id.lower() == active_id and v.id.lower() not in ("gemini", "claude")), None)
            key_donor = next((v for v in cfg.vendors if v.id.lower() in ("deepseek", "openai", "custom", "ollama") and v.api_key and v.api_key.strip()), None)
            donor = active_donor or key_donor or next((v for v in cfg.vendors if v.id.lower() in ("deepseek", "openai", "custom", "ollama")), None)

            if donor:
                final_compat = VendorProfile(
                    id="openai_compatible",
                    display_name="OpenAI Compatible",
                    vendor_type=VendorType.CustomOpenAI,
                    base_url=donor.base_url or "https://api.openai.com/v1",
                    api_key=donor.api_key or "",
                    selected_model=donor.selected_model or "gpt-4o",
                    preset_models=list(donor.preset_models or ["gpt-4o", "gpt-4o-mini", "deepseek-chat", "deepseek-reasoner"]),
                )
            else:
                final_compat = defaults.vendors[2]

        # Aggregate preset models from old OpenAI-compatible configurations
        merged_presets = list(final_compat.preset_models or [])
        for v in cfg.vendors:
            if v.id.lower() in ("openai", "deepseek", "custom", "ollama"):
                if v.selected_model and v.selected_model not in merged_presets:
                    merged_presets.append(v.selected_model)
                for pm in (v.preset_models or []):
                    if pm and pm not in merged_presets:
                        merged_presets.append(pm)
        final_compat.preset_models = merged_presets[:6]

        if (cfg.active_vendor_id or "").lower() in ("openai", "deepseek", "custom", "ollama"):
            cfg.active_vendor_id = "openai_compatible"
        elif (cfg.active_vendor_id or "").lower() not in ("gemini", "claude", "openai_compatible"):
            cfg.active_vendor_id = "openai_compatible"

        cfg.vendors = [final_gemini, final_claude, final_compat]

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
                            cls.migrate_to_three_providers(loaded)
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
            if v.vendor_type == VendorType.Gemini or v.id == "gemini":
                k = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
                if k:
                    v.api_key = k
            elif v.vendor_type == VendorType.Claude or v.id == "claude":
                k = os.environ.get("ANTHROPIC_API_KEY")
                if k:
                    v.api_key = k
            elif v.id in ("openai_compatible", "openai", "custom"):
                k = os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
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
