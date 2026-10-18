using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;

namespace Metis
{
    public static class ConfigManager
    {
        public static string GetAppDataDir()
        {
            string appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
            string dir = Path.Combine(appData, "Metis");
            if (!Directory.Exists(dir))
            {
                Directory.CreateDirectory(dir);
            }
            return dir;
        }

        public static string GetConfigPath()
        {
            return Path.Combine(GetAppDataDir(), "config.json");
        }

        public static AppConfig LoadOrCreateDefault()
        {
            string primaryPath = GetConfigPath();
            string appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
            string oldPinpointPrimary = Path.Combine(appData, "Pinpoint", "config.json");
            string oldLocalPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "metis_config.json");
            string oldPinpointLocal = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "pinpoint_config.json");
            string oldSurgicalPrimary = Path.Combine(appData, "SurgicalAgent", "config.json");
            string oldSurgicalMirror = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "surgical_config.json");

            string pathToRead = File.Exists(primaryPath)
                ? primaryPath
                : (File.Exists(oldPinpointPrimary)
                    ? oldPinpointPrimary
                    : (File.Exists(oldLocalPath)
                        ? oldLocalPath
                        : (File.Exists(oldPinpointLocal)
                            ? oldPinpointLocal
                            : (File.Exists(oldSurgicalPrimary) ? oldSurgicalPrimary : (File.Exists(oldSurgicalMirror) ? oldSurgicalMirror : null)))));

            if (pathToRead != null)
            {
                try
                {
                    string json = File.ReadAllText(pathToRead, Encoding.UTF8);
                    JavaScriptSerializer ser = new JavaScriptSerializer();
                    ser.MaxJsonLength = int.MaxValue;
                    AppConfig loaded = ser.Deserialize<AppConfig>(json);
                    if (loaded != null && loaded.Vendors != null && loaded.Vendors.Count > 0)
                    {
                        if (string.IsNullOrWhiteSpace(loaded.Theme)) loaded.Theme = "dark";
                        loaded.Vendors.RemoveAll(v => v.Type == VendorType.LocalSandbox || string.Equals(v.Id, "sandbox", StringComparison.OrdinalIgnoreCase));
                        if (string.Equals(loaded.ActiveVendorId, "sandbox", StringComparison.OrdinalIgnoreCase))
                        {
                            loaded.ActiveVendorId = loaded.Vendors.Count > 0 ? loaded.Vendors[0].Id : "deepseek";
                        }
                        EnsureAllPresetsExist(loaded);
                        PopulateEnvKeysIfEmpty(loaded);

                        if (!string.Equals(pathToRead, primaryPath, StringComparison.OrdinalIgnoreCase))
                        {
                            Save(loaded);
                        }
                        return loaded;
                    }
                }
                catch { }
            }

            AppConfig cfg = CreateDefaultConfig();
            PopulateEnvKeysIfEmpty(cfg);
            Save(cfg);
            return cfg;
        }

        public static void Save(AppConfig config)
        {
            JavaScriptSerializer ser = new JavaScriptSerializer();
            ser.MaxJsonLength = int.MaxValue;
            string json = ser.Serialize(config);
            UTF8Encoding utf8 = new UTF8Encoding(false);

            try
            {
                File.WriteAllText(GetConfigPath(), json, utf8);
            }
            catch { }
        }

        private static void PopulateEnvKeysIfEmpty(AppConfig cfg)
        {
            foreach (VendorProfile v in cfg.Vendors)
            {
                if (!string.IsNullOrWhiteSpace(v.ApiKey)) continue;
                if (v.Type == VendorType.Gemini)
                {
                    string k = Environment.GetEnvironmentVariable("GEMINI_API_KEY");
                    if (string.IsNullOrWhiteSpace(k)) k = Environment.GetEnvironmentVariable("GOOGLE_API_KEY");
                    if (!string.IsNullOrWhiteSpace(k)) v.ApiKey = k;
                }
                else if (v.Type == VendorType.OpenAI)
                {
                    string k = Environment.GetEnvironmentVariable("OPENAI_API_KEY");
                    if (!string.IsNullOrWhiteSpace(k)) v.ApiKey = k;
                }
                else if (v.Type == VendorType.Claude)
                {
                    string k = Environment.GetEnvironmentVariable("ANTHROPIC_API_KEY");
                    if (!string.IsNullOrWhiteSpace(k)) v.ApiKey = k;
                }
                else if (v.Type == VendorType.DeepSeek)
                {
                    string k = Environment.GetEnvironmentVariable("DEEPSEEK_API_KEY");
                    if (!string.IsNullOrWhiteSpace(k)) v.ApiKey = k;
                }
            }
        }

        private static void EnsureAllPresetsExist(AppConfig cfg)
        {
            AppConfig defaults = CreateDefaultConfig();
            foreach (VendorProfile defV in defaults.Vendors)
            {
                bool found = false;
                foreach (VendorProfile curV in cfg.Vendors)
                {
                    if (string.Equals(curV.Id, defV.Id, StringComparison.OrdinalIgnoreCase))
                    {
                        found = true;
                        // Translate Chinese display names from older config if present
                        if (curV.DisplayName == "Ollama (本地模型)") curV.DisplayName = "Ollama (Local Models)";
                        if (curV.DisplayName == "自定义 (OpenAI 兼容)") curV.DisplayName = "Custom (OpenAI-Compatible)";

                        if (curV.PresetModels == null)
                        {
                            curV.PresetModels = new List<string>();
                        }
                        // Ensure active selected model is remembered
                        if (!string.IsNullOrWhiteSpace(curV.SelectedModel) && !curV.PresetModels.Contains(curV.SelectedModel))
                        {
                            curV.PresetModels.Insert(0, curV.SelectedModel);
                        }
                        // Limit remembered models to maximum 4
                        if (curV.PresetModels.Count > 4)
                        {
                            curV.PresetModels = curV.PresetModels.GetRange(0, 4);
                        }

                        // Upgrade outdated selected models
                        if (string.IsNullOrWhiteSpace(curV.SelectedModel))
                        {
                            curV.SelectedModel = defV.SelectedModel;
                        }
                        else if (curV.Type == VendorType.Gemini && (curV.SelectedModel.StartsWith("gemini-2.") || curV.SelectedModel.StartsWith("gemini-1.")))
                        {
                            curV.SelectedModel = "gemini-3.8-flash";
                        }
                        else if (curV.Type == VendorType.OpenAI && (curV.SelectedModel == "gpt-4" || curV.SelectedModel == "gpt-3.5-turbo"))
                        {
                            curV.SelectedModel = "gpt-6.1-sol";
                        }
                        else if (curV.Type == VendorType.Claude && curV.SelectedModel.StartsWith("claude-3-5"))
                        {
                            curV.SelectedModel = "claude-sonnet-5-5";
                        }
                        break;
                    }
                }
                if (!found)
                {
                    cfg.Vendors.Add(defV);
                }
            }
            if (string.IsNullOrWhiteSpace(cfg.ThinkingIntensity))
            {
                cfg.ThinkingIntensity = "medium";
            }
        }

        public static AppConfig CreateDefaultConfig()
        {
            AppConfig cfg = new AppConfig();
            cfg.ActiveVendorId = "gemini";
            cfg.MaxVerifyLoops = 8;
            cfg.CommandTimeoutSeconds = 60;
            cfg.Theme = "light";
            cfg.ThinkingIntensity = "medium";

            VendorProfile gemini = new VendorProfile();
            gemini.Id = "gemini";
            gemini.DisplayName = "Google Gemini";
            gemini.Type = VendorType.Gemini;
            gemini.BaseUrl = "https://generativelanguage.googleapis.com/v1beta";
            gemini.SelectedModel = "gemini-3.8-flash";
            gemini.PresetModels = new List<string> { gemini.SelectedModel };
            cfg.Vendors.Add(gemini);

            VendorProfile openai = new VendorProfile();
            openai.Id = "openai";
            openai.DisplayName = "OpenAI";
            openai.Type = VendorType.OpenAI;
            openai.BaseUrl = "https://api.openai.com/v1";
            openai.SelectedModel = "gpt-6.1-sol";
            openai.PresetModels = new List<string> { openai.SelectedModel };
            cfg.Vendors.Add(openai);

            VendorProfile claude = new VendorProfile();
            claude.Id = "claude";
            claude.DisplayName = "Anthropic Claude";
            claude.Type = VendorType.Claude;
            claude.BaseUrl = "https://api.anthropic.com/v1";
            claude.SelectedModel = "claude-sonnet-5-5";
            claude.PresetModels = new List<string> { claude.SelectedModel };
            cfg.Vendors.Add(claude);

            VendorProfile deepseek = new VendorProfile();
            deepseek.Id = "deepseek";
            deepseek.DisplayName = "DeepSeek";
            deepseek.Type = VendorType.DeepSeek;
            deepseek.BaseUrl = "https://api.deepseek.com/v1";
            deepseek.SelectedModel = "deepseek-flash";
            deepseek.PresetModels = new List<string> { deepseek.SelectedModel };
            cfg.Vendors.Add(deepseek);

            VendorProfile ollama = new VendorProfile();
            ollama.Id = "ollama";
            ollama.DisplayName = "Ollama (Local Models)";
            ollama.Type = VendorType.Ollama;
            ollama.BaseUrl = "http://localhost:11434/v1";
            ollama.SelectedModel = "qwen3-coder:30b";
            ollama.PresetModels = new List<string> { ollama.SelectedModel };
            cfg.Vendors.Add(ollama);

            VendorProfile custom = new VendorProfile();
            custom.Id = "custom";
            custom.DisplayName = "Custom (OpenAI-Compatible)";
            custom.Type = VendorType.CustomOpenAI;
            custom.BaseUrl = "https://api.openai.com/v1";
            custom.SelectedModel = "deepseek-flash";
            custom.PresetModels = new List<string> { custom.SelectedModel };
            cfg.Vendors.Add(custom);

            return cfg;
        }
    }

    public static class SessionManager
    {
        public static string GetSessionsFilePath()
        {
            return Path.Combine(ConfigManager.GetAppDataDir(), "sessions.json");
        }

        public static SessionStoreData LoadOrCreate()
        {
            string primary = GetSessionsFilePath();
            string appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
            string oldPinpointPrimary = Path.Combine(appData, "Pinpoint", "sessions.json");
            string oldLocalPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "metis_sessions.json");
            string oldPinpointLocal = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "pinpoint_sessions.json");
            string oldSurgicalPrimary = Path.Combine(appData, "SurgicalAgent", "sessions.json");
            string oldSurgicalMirror = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "surgical_sessions.json");

            string path = File.Exists(primary)
                ? primary
                : (File.Exists(oldPinpointPrimary)
                    ? oldPinpointPrimary
                    : (File.Exists(oldLocalPath)
                        ? oldLocalPath
                        : (File.Exists(oldPinpointLocal)
                            ? oldPinpointLocal
                            : (File.Exists(oldSurgicalPrimary) ? oldSurgicalPrimary : (File.Exists(oldSurgicalMirror) ? oldSurgicalMirror : null)))));

            if (path != null)
            {
                try
                {
                    string json = File.ReadAllText(path, Encoding.UTF8);
                    JavaScriptSerializer ser = new JavaScriptSerializer();
                    ser.MaxJsonLength = int.MaxValue;
                    SessionStoreData data = ser.Deserialize<SessionStoreData>(json);
                    if (data != null && data.Sessions != null && data.Sessions.Count > 0)
                    {
                        foreach (ChatSession s in data.Sessions)
                        {
                            if (s.AllowedDirectories == null) s.AllowedDirectories = new List<string>();
                            if (s.Whitelist == null) s.Whitelist = new List<WhitelistedFile>();
                            if (s.ApiHistory == null) s.ApiHistory = new List<ChatHistoryItem>();
                            if (s.UiEvents == null) s.UiEvents = new List<UiStreamEvent>();
                            if (s.Title == "默认会话") s.Title = "Default Session";
                        }
                        if (string.IsNullOrEmpty(data.ActiveSessionId))
                        {
                            data.ActiveSessionId = data.Sessions[0].Id;
                        }

                        if (!string.Equals(path, primary, StringComparison.OrdinalIgnoreCase))
                        {
                            Save(data);
                        }
                        return data;
                    }
                }
                catch { }
            }

            SessionStoreData fresh = new SessionStoreData();
            ChatSession first = new ChatSession();
            first.Title = "Default Session";
            fresh.Sessions.Add(first);
            fresh.ActiveSessionId = first.Id;
            Save(fresh);
            return fresh;
        }

        public static void Save(SessionStoreData store)
        {
            if (store == null) return;
            JavaScriptSerializer ser = new JavaScriptSerializer();
            ser.MaxJsonLength = int.MaxValue;
            string json = ser.Serialize(store);
            UTF8Encoding utf8 = new UTF8Encoding(false);

            try
            {
                File.WriteAllText(GetSessionsFilePath(), json, utf8);
            }
            catch { }
        }
    }
}
