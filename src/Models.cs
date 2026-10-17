using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace Metis
{
    public enum VendorType
    {
        Gemini,
        OpenAI,
        Claude,
        DeepSeek,
        Ollama,
        CustomOpenAI,
        LocalSandbox
    }

    public class VendorProfile
    {
        public string Id { get; set; }
        public string DisplayName { get; set; }
        public VendorType Type { get; set; }
        public string BaseUrl { get; set; }
        public string ApiKey { get; set; }
        public string SelectedModel { get; set; }
        public List<string> PresetModels { get; set; }

        public VendorProfile()
        {
            PresetModels = new List<string>();
            ApiKey = "";
        }
    }

    public class AppConfig
    {
        public string ActiveVendorId { get; set; }
        public int MaxVerifyLoops { get; set; }
        public int CommandTimeoutSeconds { get; set; }
        public string Theme { get; set; }
        public string ThinkingIntensity { get; set; }
        public List<VendorProfile> Vendors { get; set; }

        public AppConfig()
        {
            ActiveVendorId = "gemini";
            MaxVerifyLoops = 8;
            CommandTimeoutSeconds = 60;
            Theme = "dark";
            ThinkingIntensity = "medium";
            Vendors = new List<VendorProfile>();
        }
    }

    public class WhitelistedFile
    {
        public string FullPath { get; set; }
        public string FileName { get; set; }
        public string RelativePath { get; set; }
        public string DirectoryPath { get; set; }
        public string MediaKind { get; set; } // "code", "image", "pdf"
        public string MimeType { get; set; }
        public bool IsBinaryMedia { get; set; }
        public string InitialContent { get; set; }
        public string LastSavedContent { get; set; }
        public int EditCount { get; set; }
        public string AddedAt { get; set; }
        public string LastModifiedByAgentAt { get; set; }

        public WhitelistedFile()
        {
            AddedAt = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
            EditCount = 0;
            RelativePath = "";
            MediaKind = "code";
            MimeType = "text/plain";
            IsBinaryMedia = false;
        }

        public long CurrentSizeBytes
        {
            get
            {
                try
                {
                    if (!string.IsNullOrEmpty(FullPath) && File.Exists(FullPath))
                    {
                        return new FileInfo(FullPath).Length;
                    }
                }
                catch { }
                return 0;
            }
        }

        public int EstimatedTokens
        {
            get
            {
                try
                {
                    if (MediaKind == "image" || MediaAttachmentHelper.IsImageFile(FullPath))
                    {
                        return 260;
                    }
                    string content = ReadCurrentContent();
                    if (string.IsNullOrEmpty(content)) return (MediaKind == "pdf" ? 300 : 0);
                    return Math.Max(1, (int)(content.Length / 3.5)) + (MediaKind == "pdf" ? 200 : 0);
                }
                catch
                {
                    return 0;
                }
            }
        }

        public string ReadCurrentContent()
        {
            try
            {
                if (IsBinaryMedia || MediaAttachmentHelper.IsMediaAttachment(FullPath))
                {
                    if (!string.IsNullOrEmpty(LastSavedContent))
                    {
                        return LastSavedContent;
                    }
                    if (MediaAttachmentHelper.IsPdfFile(FullPath))
                    {
                        LastSavedContent = MediaAttachmentHelper.BuildPdfMetadataSummary(FullPath);
                        return LastSavedContent;
                    }
                    if (MediaAttachmentHelper.IsImageFile(FullPath))
                    {
                        LastSavedContent = MediaAttachmentHelper.BuildImageMetadataSummary(FullPath);
                        return LastSavedContent;
                    }
                    return "";
                }

                if (!string.IsNullOrEmpty(FullPath) && File.Exists(FullPath))
                {
                    return File.ReadAllText(FullPath, Encoding.UTF8);
                }
            }
            catch { }
            return LastSavedContent ?? "";
        }
    }

    public class ChatHistoryItem
    {
        public string Role { get; set; } // "user" or "assistant"
        public string Content { get; set; }

        public ChatHistoryItem() { }
        public ChatHistoryItem(string role, string content)
        {
            Role = role;
            Content = content;
        }
    }

    public class SearchResultItem
    {
        public string Title { get; set; }
        public string Url { get; set; }
        public string Snippet { get; set; }
    }

    public class UiStreamEvent
    {
        public string Id { get; set; }
        public string Type { get; set; } // "user", "assistant", "file_edit", "verify_result", "search_result", "loop_success", "notice", "security_block"
        public string Timestamp { get; set; }
        public string Content { get; set; }
        public string VendorName { get; set; }
        public string ModelName { get; set; }
        public int Round { get; set; }
        public int MaxRounds { get; set; }
        public string FilePath { get; set; }
        public string FileName { get; set; }
        public int LineCount { get; set; }
        public int EditCount { get; set; }
        public string Command { get; set; }
        public string WorkingDirectory { get; set; }
        public int ExitCode { get; set; }
        public double DurationSeconds { get; set; }
        public bool Succeeded { get; set; }
        public string TerminalOutput { get; set; }
        public string SearchQuery { get; set; }
        public List<SearchResultItem> SearchResults { get; set; }

        public UiStreamEvent()
        {
            Id = Guid.NewGuid().ToString("N");
            Timestamp = DateTime.Now.ToString("HH:mm:ss");
            SearchResults = new List<SearchResultItem>();
        }
    }

    public class ChatSession
    {
        public string Id { get; set; }
        public string Title { get; set; }
        public string CreatedAt { get; set; }
        public string UpdatedAt { get; set; }
        public List<string> AllowedDirectories { get; set; }
        public List<WhitelistedFile> Whitelist { get; set; }
        public List<ChatHistoryItem> ApiHistory { get; set; }
        public List<UiStreamEvent> UiEvents { get; set; }

        public ChatSession()
        {
            Id = Guid.NewGuid().ToString("N");
            Title = "New Session";
            CreatedAt = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
            UpdatedAt = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
            AllowedDirectories = new List<string>();
            Whitelist = new List<WhitelistedFile>();
            ApiHistory = new List<ChatHistoryItem>();
            UiEvents = new List<UiStreamEvent>();
        }
    }

    public class SessionStoreData
    {
        public string ActiveSessionId { get; set; }
        public List<ChatSession> Sessions { get; set; }

        public SessionStoreData()
        {
            Sessions = new List<ChatSession>();
        }
    }

    public enum SurgicalActionType
    {
        WriteFull,
        SearchReplace
    }

    public class SurgicalFileEdit
    {
        public SurgicalActionType ActionType { get; set; }
        public string TargetFile { get; set; }
        public string NewContent { get; set; }
        public string SearchBlock { get; set; }
        public string ReplaceBlock { get; set; }
    }

    public class ParsedAgentResponse
    {
        public string RawText { get; set; }
        public string ExplanationText { get; set; }
        public List<SurgicalFileEdit> FileEdits { get; set; }
        public List<string> TerminalCommands { get; set; }
        public List<string> SearchQueries { get; set; }

        public string VerifyCommand
        {
            get
            {
                if (TerminalCommands == null || TerminalCommands.Count == 0) return "";
                return string.Join(" && ", TerminalCommands.ToArray());
            }
            set
            {
                if (TerminalCommands == null) TerminalCommands = new List<string>();
                TerminalCommands.Clear();
                if (!string.IsNullOrWhiteSpace(value))
                {
                    TerminalCommands.Add(value.Trim());
                }
            }
        }

        public ParsedAgentResponse()
        {
            FileEdits = new List<SurgicalFileEdit>();
            TerminalCommands = new List<string>();
            SearchQueries = new List<string>();
            RawText = "";
            ExplanationText = "";
        }
    }

    public class VerifyExecutionResult
    {
        public string Command { get; set; }
        public string WorkingDirectory { get; set; }
        public int ExitCode { get; set; }
        public string StdOut { get; set; }
        public string StdErr { get; set; }
        public double DurationSeconds { get; set; }
        public bool TimedOut { get; set; }
        public bool Cancelled { get; set; }

        public bool Succeeded
        {
            get { return !TimedOut && !Cancelled && ExitCode == 0; }
        }

        public string CombinedOutput
        {
            get
            {
                string s = "";
                if (!string.IsNullOrWhiteSpace(StdOut))
                {
                    s += StdOut.TrimEnd();
                }
                if (!string.IsNullOrWhiteSpace(StdErr))
                {
                    if (s.Length > 0) s += Environment.NewLine;
                    s += StdErr.TrimEnd();
                }
                if (string.IsNullOrWhiteSpace(s))
                {
                    s = "(No terminal standard output)";
                }
                return s;
            }
        }
    }
}
