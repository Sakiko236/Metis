import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src-python"))

import unittest
from models import AppConfig, VendorProfile, VendorType
from llm import MultiVendorClient
from config import ConfigManager


class TestThinkingTiers(unittest.TestCase):
    def test_vendor_profile_thinking_intensity(self):
        gemini = VendorProfile(
            id="gemini",
            display_name="Google Gemini",
            vendor_type=VendorType.Gemini,
            thinking_intensity="medium",
        )
        self.assertEqual(gemini.thinking_intensity, "medium")

        deepseek = VendorProfile(
            id="openai_compatible",
            display_name="OpenAI Compatible",
            vendor_type=VendorType.CustomOpenAI,
            thinking_intensity="max",
        )
        self.assertEqual(deepseek.thinking_intensity, "max")

        # Serialization round-trip
        d = deepseek.to_dict()
        self.assertEqual(d["ThinkingIntensity"], "max")
        restored = VendorProfile.from_dict(d)
        self.assertEqual(restored.thinking_intensity, "max")

    def test_app_config_thinking_intensity_validity(self):
        cfg = AppConfig(thinking_intensity="max")
        self.assertEqual(cfg.thinking_intensity, "max")

        cfg2 = AppConfig(thinking_intensity="high")
        self.assertEqual(cfg2.thinking_intensity, "high")

        cfg3 = AppConfig(thinking_intensity="low")
        self.assertEqual(cfg3.thinking_intensity, "low")

        cfg4 = AppConfig(thinking_intensity="medium")
        self.assertEqual(cfg4.thinking_intensity, "medium")

        # Invalid fallback
        cfg_invalid = AppConfig(thinking_intensity="super_extreme")
        self.assertEqual(cfg_invalid.thinking_intensity, "medium")

        # Deserialization from dict
        loaded = AppConfig.from_dict({"ThinkingIntensity": "max"})
        self.assertEqual(loaded.thinking_intensity, "max")

    def test_sanitize_thinking_intensity(self):
        gemini_vendor = VendorProfile(id="gemini", vendor_type=VendorType.Gemini)
        claude_vendor = VendorProfile(id="claude", vendor_type=VendorType.Claude)
        deepseek_vendor = VendorProfile(id="openai_compatible", vendor_type=VendorType.CustomOpenAI)

        # Gemini Tiers: low, medium, high
        self.assertEqual(AppConfig.sanitize_thinking_intensity(gemini_vendor, "low"), "low")
        self.assertEqual(AppConfig.sanitize_thinking_intensity(gemini_vendor, "medium"), "medium")
        self.assertEqual(AppConfig.sanitize_thinking_intensity(gemini_vendor, "high"), "high")
        # 'max' switches to 'high' for Gemini
        self.assertEqual(AppConfig.sanitize_thinking_intensity(gemini_vendor, "max"), "high")
        self.assertEqual(AppConfig.sanitize_thinking_intensity(gemini_vendor, "unknown"), "medium")

        # DeepSeek & Claude Tiers: low, high, max
        for vendor in (claude_vendor, deepseek_vendor):
            self.assertEqual(AppConfig.sanitize_thinking_intensity(vendor, "low"), "low")
            self.assertEqual(AppConfig.sanitize_thinking_intensity(vendor, "high"), "high")
            self.assertEqual(AppConfig.sanitize_thinking_intensity(vendor, "max"), "max")
            # 'medium' switches to 'high' for DeepSeek / Claude
            self.assertEqual(AppConfig.sanitize_thinking_intensity(vendor, "medium"), "high")
            self.assertEqual(AppConfig.sanitize_thinking_intensity(vendor, "unknown"), "high")

    def test_gemini_generation_config(self):
        cfg_low, ok_low = MultiVendorClient.build_gemini_generation_config("gemini-2.5-flash", "low")
        self.assertTrue(ok_low)
        self.assertEqual(cfg_low["thinkingConfig"]["thinkingBudget"], 2048)

        cfg_med, ok_med = MultiVendorClient.build_gemini_generation_config("gemini-2.5-flash", "medium")
        self.assertTrue(ok_med)
        self.assertEqual(cfg_med["thinkingConfig"]["thinkingBudget"], 8192)

        cfg_high, ok_high = MultiVendorClient.build_gemini_generation_config("gemini-2.5-flash", "high")
        self.assertTrue(ok_high)
        self.assertEqual(cfg_high["thinkingConfig"]["thinkingBudget"], 24576)

        # Max maps to high on Gemini
        cfg_max, ok_max = MultiVendorClient.build_gemini_generation_config("gemini-2.5-flash", "max")
        self.assertTrue(ok_max)
        self.assertEqual(cfg_max["thinkingConfig"]["thinkingBudget"], 24576)

    def test_default_config_provider_tiers(self):
        cfg = ConfigManager.create_default_config()
        gemini = next(v for v in cfg.vendors if v.id == "gemini")
        claude = next(v for v in cfg.vendors if v.id == "claude")
        compat = next(v for v in cfg.vendors if v.id == "openai_compatible")

        self.assertEqual(gemini.thinking_intensity, "medium")
        self.assertEqual(claude.thinking_intensity, "high")
        self.assertEqual(compat.thinking_intensity, "high")
        self.assertEqual(cfg.thinking_intensity, "high")


if __name__ == "__main__":
    unittest.main()
