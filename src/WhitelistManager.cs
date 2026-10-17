using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace Metis
{
    public class WhitelistManager
    {
        private List<WhitelistedFile> _files = new List<WhitelistedFile>();
        private List<string> _directories = new List<string>();

        private static readonly HashSet<string> IgnoredDirNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            ".git", ".svn", ".hg", "node_modules", "bin", "obj", "__pycache__",
            ".pytest_cache", ".vs", ".idea", ".vscode", "dist", "build", "target",
            "venv", ".venv", "env", "packages"
        };

        private static readonly HashSet<string> BinaryOrSkippedExtensions = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            ".exe", ".dll", ".pdb", ".obj", ".o", ".so", ".dylib", ".lib", ".a",
            ".zip", ".tar", ".gz", ".7z", ".rar", ".bz2", ".xz",
            ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".bmp", ".svg",
            ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac", ".mkv",
            ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
            ".woff", ".woff2", ".ttf", ".eot", ".otf",
            ".pyc", ".pyo", ".class", ".jar", ".war",
            ".gguf", ".bin", ".onnx", ".pt", ".pth", ".safetensors", ".db", ".sqlite", ".sqlite3", ".lock"
        };

        public IList<WhitelistedFile> Files
        {
            get { return _files; }
        }

        public IList<string> Directories
        {
            get { return _directories; }
        }

        public void BindSessionList(List<WhitelistedFile> sessionList, List<string> sessionDirectories = null)
        {
            _files = sessionList ?? new List<WhitelistedFile>();
            _directories = sessionDirectories ?? new List<string>();
            UpdateAllRelativePaths();
        }

        public string GetActiveWorkspaceDirectory()
        {
            if (_directories != null)
            {
                foreach (string dir in _directories)
                {
                    if (!string.IsNullOrWhiteSpace(dir) && Directory.Exists(dir))
                    {
                        return dir;
                    }
                }
            }

            if (_files != null)
            {
                foreach (WhitelistedFile wf in _files)
                {
                    if (!string.IsNullOrWhiteSpace(wf.DirectoryPath) && Directory.Exists(wf.DirectoryPath))
                    {
                        return wf.DirectoryPath;
                    }
                }
            }

            string defaultWorkspace = Path.Combine(ConfigManager.GetAppDataDir(), "Metis_Workspace");
            string oldPinpointAppWorkspace = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Pinpoint", "Pinpoint_Workspace");
            string altWorkspace = Path.Combine(ConfigManager.GetAppDataDir(), "workspace");
            if (Directory.Exists(altWorkspace) && !Directory.Exists(defaultWorkspace))
            {
                defaultWorkspace = altWorkspace;
            }

            try
            {
                if (Directory.Exists(oldPinpointAppWorkspace) && !Directory.Exists(defaultWorkspace))
                {
                    try
                    {
                        Directory.CreateDirectory(defaultWorkspace);
                        foreach (string file in Directory.GetFiles(oldPinpointAppWorkspace))
                        {
                            string dest = Path.Combine(defaultWorkspace, Path.GetFileName(file));
                            if (!File.Exists(dest)) File.Copy(file, dest, true);
                        }
                    }
                    catch { }
                }

                // Smooth migration: if old SurgicalAgent_Workspace or Pinpoint_Workspace exists in MyDocuments, migrate files into AppData workspace
                string oldDocWorkspace = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
                    "SurgicalAgent_Workspace");
                if (Directory.Exists(oldDocWorkspace))
                {
                    try
                    {
                        if (!Directory.Exists(defaultWorkspace))
                        {
                            Directory.CreateDirectory(defaultWorkspace);
                        }
                        foreach (string file in Directory.GetFiles(oldDocWorkspace))
                        {
                            string dest = Path.Combine(defaultWorkspace, Path.GetFileName(file));
                            if (!File.Exists(dest))
                            {
                                File.Copy(file, dest, true);
                            }
                        }
                        Directory.Delete(oldDocWorkspace, true);
                    }
                    catch { }
                }

                string oldPinpointDoc = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
                    "Pinpoint_Workspace");
                if (Directory.Exists(oldPinpointDoc))
                {
                    try
                    {
                        if (!Directory.Exists(defaultWorkspace))
                        {
                            Directory.CreateDirectory(defaultWorkspace);
                        }
                        foreach (string file in Directory.GetFiles(oldPinpointDoc))
                        {
                            string dest = Path.Combine(defaultWorkspace, Path.GetFileName(file));
                            if (!File.Exists(dest))
                            {
                                File.Copy(file, dest, true);
                            }
                        }
                        Directory.Delete(oldPinpointDoc, true);
                    }
                    catch { }
                }

                if (!Directory.Exists(defaultWorkspace))
                {
                    Directory.CreateDirectory(defaultWorkspace);
                }
                return defaultWorkspace;
            }
            catch
            {
                return Environment.CurrentDirectory;
            }
        }

        public bool AddDirectory(string folderPath, out int filesLoaded, out string error)
        {
            filesLoaded = 0;
            error = null;
            if (string.IsNullOrWhiteSpace(folderPath))
            {
                error = "Folder path is empty.";
                return false;
            }

            try
            {
                string fullDir = Path.GetFullPath(folderPath.Trim().TrimEnd('\\', '/'));
                if (!Directory.Exists(fullDir))
                {
                    error = "Folder does not exist: " + fullDir;
                    return false;
                }

                bool alreadyInDirs = false;
                foreach (string d in _directories)
                {
                    if (string.Equals(d, fullDir, StringComparison.OrdinalIgnoreCase))
                    {
                        alreadyInDirs = true;
                        break;
                    }
                }
                if (!alreadyInDirs)
                {
                    _directories.Add(fullDir);
                }

                filesLoaded = ScanAndImportDirectoryFiles(fullDir, 80);
                UpdateAllRelativePaths();
                return true;
            }
            catch (Exception ex)
            {
                error = "Failed to read folder: " + ex.Message;
                return false;
            }
        }

        public int RefreshWorkspaceDirectories()
        {
            int newlyAdded = 0;
            if (_directories == null) return 0;
            foreach (string dir in _directories)
            {
                if (!string.IsNullOrWhiteSpace(dir) && Directory.Exists(dir))
                {
                    newlyAdded += ScanAndImportDirectoryFiles(dir, 80);
                }
            }
            UpdateAllRelativePaths();
            return newlyAdded;
        }

        private int ScanAndImportDirectoryFiles(string rootDir, int maxFilesToImport)
        {
            int importedCount = 0;
            Queue<string> queue = new Queue<string>();
            queue.Enqueue(rootDir);

            while (queue.Count > 0 && importedCount < maxFilesToImport)
            {
                string currentDir = queue.Dequeue();

                string[] files = null;
                try { files = Directory.GetFiles(currentDir); } catch { }
                if (files != null)
                {
                    Array.Sort(files, StringComparer.OrdinalIgnoreCase);
                    foreach (string f in files)
                    {
                        if (importedCount >= maxFilesToImport) break;
                        if (ShouldSkipFileInFolderScan(f)) continue;

                        bool isNew;
                        string err;
                        WhitelistedFile wf = AddOrUpdateFile(f, out isNew, out err);
                        if (wf != null && isNew)
                        {
                            importedCount++;
                        }
                    }
                }

                string[] subDirs = null;
                try { subDirs = Directory.GetDirectories(currentDir); } catch { }
                if (subDirs != null)
                {
                    Array.Sort(subDirs, StringComparer.OrdinalIgnoreCase);
                    foreach (string sd in subDirs)
                    {
                        string dirName = Path.GetFileName(sd);
                        if (string.IsNullOrEmpty(dirName) || dirName.StartsWith(".") || IgnoredDirNames.Contains(dirName))
                        {
                            continue;
                        }
                        queue.Enqueue(sd);
                    }
                }
            }

            return importedCount;
        }

        private static bool ShouldSkipFileInFolderScan(string filePath)
        {
            try
            {
                string fileName = Path.GetFileName(filePath);
                if (string.IsNullOrEmpty(fileName) || fileName.StartsWith(".")) return true;

                string ext = Path.GetExtension(filePath);
                if (!string.IsNullOrEmpty(ext) && BinaryOrSkippedExtensions.Contains(ext))
                {
                    return true;
                }

                FileInfo fi = new FileInfo(filePath);
                if (fi.Length > 512 * 1024) // Skip files > 512KB during folder scan
                {
                    return true;
                }

                // Quick binary check on first 512 bytes (skip if null byte found)
                if (fi.Length > 0)
                {
                    byte[] sample = new byte[(int)Math.Min(512, fi.Length)];
                    using (FileStream fs = new FileStream(filePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                    {
                        int read = fs.Read(sample, 0, sample.Length);
                        for (int i = 0; i < read; i++)
                        {
                            if (sample[i] == 0) return true;
                        }
                    }
                }

                return false;
            }
            catch
            {
                return true;
            }
        }

        private void UpdateAllRelativePaths()
        {
            foreach (WhitelistedFile wf in _files)
            {
                wf.RelativePath = ComputeRelativePath(wf.FullPath);
            }
        }

        private string ComputeRelativePath(string fullPath)
        {
            if (string.IsNullOrEmpty(fullPath)) return "";
            if (_directories != null)
            {
                foreach (string dir in _directories)
                {
                    if (string.IsNullOrEmpty(dir)) continue;
                    string prefix = dir.TrimEnd('\\', '/') + "\\";
                    if (fullPath.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                    {
                        return fullPath.Substring(prefix.Length);
                    }
                }
            }
            try { return Path.GetFileName(fullPath); } catch { return fullPath; }
        }

        public WhitelistedFile AddOrUpdateFile(string filePath, out bool isNew, out string error)
        {
            isNew = false;
            error = null;
            if (string.IsNullOrWhiteSpace(filePath))
            {
                error = "File path is empty.";
                return null;
            }

            try
            {
                string fullPath = Path.GetFullPath(filePath);
                if (Directory.Exists(fullPath))
                {
                    int count;
                    if (AddDirectory(fullPath, out count, out error))
                    {
                        return _files.Count > 0 ? _files[_files.Count - 1] : null;
                    }
                    return null;
                }
                if (!File.Exists(fullPath))
                {
                    error = "File does not exist: " + fullPath;
                    return null;
                }

                bool isImage = MediaAttachmentHelper.IsImageFile(fullPath);
                bool isPdf = MediaAttachmentHelper.IsPdfFile(fullPath);
                bool isMedia = isImage || isPdf;

                FileInfo fi = new FileInfo(fullPath);
                long maxAllowedBytes = isMedia ? (25L * 1024 * 1024) : (2L * 1024 * 1024);
                if (fi.Length > maxAllowedBytes)
                {
                    error = string.Format("File exceeds {0}MB limit ({1}).", isMedia ? 25 : 2, fi.Name);
                    return null;
                }

                string content;
                if (isPdf)
                {
                    content = MediaAttachmentHelper.BuildPdfMetadataSummary(fullPath);
                }
                else if (isImage)
                {
                    content = MediaAttachmentHelper.BuildImageMetadataSummary(fullPath);
                }
                else
                {
                    content = File.ReadAllText(fullPath, Encoding.UTF8);
                }

                foreach (WhitelistedFile existing in _files)
                {
                    if (string.Equals(existing.FullPath, fullPath, StringComparison.OrdinalIgnoreCase))
                    {
                        existing.MediaKind = isPdf ? "pdf" : (isImage ? "image" : "code");
                        existing.MimeType = isMedia ? MediaAttachmentHelper.GetMimeType(fullPath) : "text/plain";
                        existing.IsBinaryMedia = isMedia;
                        existing.LastSavedContent = content;
                        existing.RelativePath = ComputeRelativePath(fullPath);
                        return existing;
                    }
                }

                WhitelistedFile wf = new WhitelistedFile();
                wf.FullPath = fullPath;
                wf.FileName = Path.GetFileName(fullPath);
                wf.RelativePath = ComputeRelativePath(fullPath);
                wf.DirectoryPath = Path.GetDirectoryName(fullPath);
                wf.MediaKind = isPdf ? "pdf" : (isImage ? "image" : "code");
                wf.MimeType = isMedia ? MediaAttachmentHelper.GetMimeType(fullPath) : "text/plain";
                wf.IsBinaryMedia = isMedia;
                wf.InitialContent = content;
                wf.LastSavedContent = content;
                wf.AddedAt = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
                _files.Add(wf);
                isNew = true;
                return wf;
            }
            catch (Exception ex)
            {
                error = "Failed to read file: " + ex.Message;
                return null;
            }
        }

        public bool RemoveDirectory(string folderPath)
        {
            if (string.IsNullOrWhiteSpace(folderPath)) return false;
            string normDir = folderPath.Trim().TrimEnd('\\', '/');
            try { normDir = Path.GetFullPath(normDir).TrimEnd('\\', '/'); } catch { }

            bool removed = false;
            for (int i = _directories.Count - 1; i >= 0; i--)
            {
                if (string.Equals(_directories[i].TrimEnd('\\', '/'), normDir, StringComparison.OrdinalIgnoreCase))
                {
                    _directories.RemoveAt(i);
                    removed = true;
                }
            }

            string prefix = normDir + "\\";
            for (int i = _files.Count - 1; i >= 0; i--)
            {
                if (_files[i].FullPath.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                {
                    _files.RemoveAt(i);
                }
            }

            UpdateAllRelativePaths();
            return removed;
        }

        public bool RemoveFile(string fullPath)
        {
            for (int i = 0; i < _files.Count; i++)
            {
                if (string.Equals(_files[i].FullPath, fullPath, StringComparison.OrdinalIgnoreCase))
                {
                    _files.RemoveAt(i);
                    return true;
                }
            }
            return false;
        }

        public void Clear()
        {
            _files.Clear();
            _directories.Clear();
        }

        public List<WhitelistedFile> GetEditableCodeFiles()
        {
            List<WhitelistedFile> list = new List<WhitelistedFile>();
            foreach (WhitelistedFile wf in _files)
            {
                if (!wf.IsBinaryMedia && !MediaAttachmentHelper.IsMediaAttachment(wf.FullPath))
                {
                    list.Add(wf);
                }
            }
            return list;
        }

        public WhitelistedFile ResolveWhitelistedFile(string requestedPathOrName, bool allowSingleFileFallback = false)
        {
            if (_files.Count == 0) return null;

            List<WhitelistedFile> editableFiles = GetEditableCodeFiles();

            if (string.IsNullOrWhiteSpace(requestedPathOrName))
            {
                if (editableFiles.Count == 1) return editableFiles[0];
                return null;
            }

            string cleaned = requestedPathOrName.Trim().Trim('"', '\'', '`', '<', '>');
            if (cleaned == "目标文件名" || cleaned == "文件名" || cleaned == "新文件名或相对路径" || cleaned == "文件名或相对路径" ||
                cleaned == "filename" || cleaned == "file_path" || cleaned == "target_file")
            {
                if (editableFiles.Count == 1) return editableFiles[0];
                return null;
            }

            try
            {
                if (Path.IsPathRooted(cleaned))
                {
                    string normalized = Path.GetFullPath(cleaned);
                    foreach (WhitelistedFile wf in _files)
                    {
                        if (string.Equals(wf.FullPath, normalized, StringComparison.OrdinalIgnoreCase))
                        {
                            return wf;
                        }
                    }
                }
            }
            catch { }

            string normReqRel = cleaned.Replace('/', '\\').TrimStart('.', '\\');
            foreach (WhitelistedFile wf in _files)
            {
                if (!string.IsNullOrEmpty(wf.RelativePath) &&
                    string.Equals(wf.RelativePath.Replace('/', '\\'), normReqRel, StringComparison.OrdinalIgnoreCase))
                {
                    return wf;
                }
            }

            List<WhitelistedFile> nameMatches = new List<WhitelistedFile>();
            string targetFileName = "";
            try { targetFileName = Path.GetFileName(cleaned); } catch { targetFileName = cleaned; }

            foreach (WhitelistedFile wf in _files)
            {
                if (string.Equals(wf.FileName, cleaned, StringComparison.OrdinalIgnoreCase) ||
                    (!cleaned.Contains("/") && !cleaned.Contains("\\") && string.Equals(wf.FileName, targetFileName, StringComparison.OrdinalIgnoreCase)))
                {
                    nameMatches.Add(wf);
                }
            }

            if (nameMatches.Count == 1)
            {
                return nameMatches[0];
            }

            if (nameMatches.Count > 1)
            {
                foreach (WhitelistedFile wf in nameMatches)
                {
                    if (wf.FullPath.EndsWith(normReqRel, StringComparison.OrdinalIgnoreCase))
                    {
                        return wf;
                    }
                }
                return nameMatches[0];
            }

            if (allowSingleFileFallback && _files.Count == 1)
            {
                return _files[0];
            }

            return null;
        }

        public static bool TryApplySearchReplace(string currentContent, string search, string replace, out string result)
        {
            result = currentContent;
            if (string.IsNullOrEmpty(search))
            {
                if (string.IsNullOrWhiteSpace(currentContent) && !string.IsNullOrEmpty(replace))
                {
                    result = CleanEchoedPlaceholders(replace);
                    return true;
                }
                return false;
            }

            // 1. Direct exact match
            int idx = currentContent.IndexOf(search, StringComparison.Ordinal);
            if (idx >= 0)
            {
                result = currentContent.Substring(0, idx) + replace + currentContent.Substring(idx + search.Length);
                return true;
            }

            // 2. Normalized line endings (\r\n -> \n)
            string normCur = currentContent.Replace("\r\n", "\n");
            string normSearch = search.Replace("\r\n", "\n");
            string normReplace = replace.Replace("\r\n", "\n");
            bool usesCrLf = currentContent.Contains("\r\n");

            int normIdx = normCur.IndexOf(normSearch, StringComparison.Ordinal);
            if (normIdx >= 0)
            {
                string merged = normCur.Substring(0, normIdx) + normReplace + normCur.Substring(normIdx + normSearch.Length);
                result = usesCrLf ? merged.Replace("\n", "\r\n") : merged;
                return true;
            }

            // 3. Trimmed search block (trim empty leading/trailing newlines)
            string trimSearch = normSearch.Trim('\r', '\n');
            string trimReplace = normReplace.Trim('\r', '\n');
            if (!string.IsNullOrEmpty(trimSearch))
            {
                int trimIdx = normCur.IndexOf(trimSearch, StringComparison.Ordinal);
                if (trimIdx >= 0)
                {
                    string merged = normCur.Substring(0, trimIdx) + trimReplace + normCur.Substring(trimIdx + trimSearch.Length);
                    result = usesCrLf ? merged.Replace("\n", "\r\n") : merged;
                    return true;
                }
            }

            // 4. Line-by-line whitespace-trimmed match
            string[] curLines = normCur.Split('\n');
            string[] searchLines = normSearch.Trim('\r', '\n').Split('\n');
            if (searchLines.Length > 0 && curLines.Length >= searchLines.Length)
            {
                for (int i = 0; i <= curLines.Length - searchLines.Length; i++)
                {
                    bool match = true;
                    for (int j = 0; j < searchLines.Length; j++)
                    {
                        if (curLines[i + j].Trim() != searchLines[j].Trim())
                        {
                            match = false;
                            break;
                        }
                    }
                    if (match)
                    {
                        string[] replaceLines = normReplace.Trim('\r', '\n').Split('\n');
                        List<string> newLines = new List<string>();
                        for (int k = 0; k < i; k++) newLines.Add(curLines[k]);
                        newLines.AddRange(replaceLines);
                        for (int k = i + searchLines.Length; k < curLines.Length; k++) newLines.Add(curLines[k]);

                        string merged = string.Join("\n", newLines.ToArray());
                        result = usesCrLf ? merged.Replace("\n", "\r\n") : merged;
                        return true;
                    }
                }
            }

            return false;
        }

        public bool ApplyEditInPlace(SurgicalFileEdit edit, out WhitelistedFile targetFile, out string statusMessage)
        {
            targetFile = ResolveWhitelistedFile(edit.TargetFile, false);

            if (targetFile == null)
            {
                statusMessage = string.Format("[Security Block - File Creation Prohibited] Target file '{0}' is not in the whitelist. Metis strictly forbids creating or deleting files; in-place edits on existing files only!", edit.TargetFile);
                return false;
            }

            try
            {
                string currentContent = File.Exists(targetFile.FullPath)
                    ? File.ReadAllText(targetFile.FullPath, Encoding.UTF8)
                    : (targetFile.LastSavedContent ?? "");

                string updatedContent = currentContent;

                if (edit.ActionType == SurgicalActionType.WriteFull)
                {
                    updatedContent = CleanEchoedPlaceholders(edit.NewContent ?? "");
                }
                else if (edit.ActionType == SurgicalActionType.SearchReplace)
                {
                    string search = edit.SearchBlock ?? "";
                    string replace = edit.ReplaceBlock ?? "";

                    string res;
                    if (TryApplySearchReplace(currentContent, search, replace, out res))
                    {
                        updatedContent = res;
                    }
                    else
                    {
                        statusMessage = string.Format("SEARCH block failed to match original code in `{0}`. Please ensure the SEARCH block matches the file's current content verbatim.", targetFile.FileName);
                        return false;
                    }
                }

                string dir = Path.GetDirectoryName(targetFile.FullPath);
                if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                {
                    Directory.CreateDirectory(dir);
                }

                byte[] bytes = new UTF8Encoding(false).GetBytes(updatedContent);
                using (FileStream fs = new FileStream(targetFile.FullPath, FileMode.Create, FileAccess.Write, FileShare.ReadWrite))
                {
                    fs.Write(bytes, 0, bytes.Length);
                    fs.Flush(true);
                }

                targetFile.LastSavedContent = updatedContent;
                targetFile.EditCount++;
                targetFile.LastModifiedByAgentAt = DateTime.Now.ToString("HH:mm:ss");
                targetFile.RelativePath = ComputeRelativePath(targetFile.FullPath);

                int lineCount = string.IsNullOrEmpty(updatedContent) ? 0 : updatedContent.Split('\n').Length;
                string displayName = !string.IsNullOrEmpty(targetFile.RelativePath) ? targetFile.RelativePath : targetFile.FileName;

                statusMessage = string.Format("⚡ Modified file {0} in-place ({1} lines / {2} bytes, update #{3})",
                    displayName, lineCount, bytes.Length, targetFile.EditCount);
                return true;
            }
            catch (Exception ex)
            {
                statusMessage = string.Format("Failed to write file [{0}]: {1}", targetFile.FileName, ex.Message);
                return false;
            }
        }

        private static string CleanEchoedPlaceholders(string code)
        {
            if (string.IsNullOrEmpty(code)) return "";
            string cleaned = Regex.Replace(code, @"^\s*#\s*(?:完整的新文件内容|full new file content)\s*\r?\n", "", RegexOptions.IgnoreCase);
            cleaned = Regex.Replace(cleaned, @"^\s*//\s*(?:完整的新文件内容|full new file content)\s*\r?\n", "", RegexOptions.IgnoreCase);
            cleaned = Regex.Replace(cleaned, @"^\s*这里(?:直接)?写完整的?(?:新文件)?(?:代码|源码)(?:内容)?\s*\r?\n", "", RegexOptions.IgnoreCase);
            return cleaned;
        }

        public bool RevertFileToInitial(WhitelistedFile wf, out string message)
        {
            if (wf == null)
            {
                message = "Target file not found.";
                return false;
            }
            try
            {
                byte[] bytes = new UTF8Encoding(false).GetBytes(wf.InitialContent ?? "");
                using (FileStream fs = new FileStream(wf.FullPath, FileMode.Create, FileAccess.Write, FileShare.ReadWrite))
                {
                    fs.Write(bytes, 0, bytes.Length);
                    fs.Flush(true);
                }
                wf.LastSavedContent = wf.InitialContent ?? "";
                wf.EditCount = 0;
                wf.LastModifiedByAgentAt = null;
                message = string.Format("Reverted {0} to its initial version.", wf.FileName);
                return true;
            }
            catch (Exception ex)
            {
                message = "Rollback failed: " + ex.Message;
                return false;
            }
        }

        public int EstimateTotalContextTokens(IList<ChatHistoryItem> history)
        {
            int total = 220;
            foreach (WhitelistedFile wf in _files)
            {
                total += wf.EstimatedTokens;
            }
            if (history != null)
            {
                foreach (ChatHistoryItem item in history)
                {
                    if (item.Content != null)
                    {
                        total += Math.Max(1, (int)(item.Content.Length / 3.5));
                    }
                }
            }
            return total;
        }

        public string BuildSystemPrompt()
        {
            bool hasControlledFiles = (_files != null && _files.Count > 0);
            string workDir = GetActiveWorkspaceDirectory();

            StringBuilder sb = new StringBuilder();
            sb.AppendLine("You are Metis — a file-centric desktop coding agent designed for surgical micro-editing, live web searching, and closed-loop terminal verification. You are laser-focused on the specific files and tasks explicitly scoped by the user, rather than indexing or generating large full-stack projects.");
            sb.AppendLine(string.Format("Active Workspace Root: `{0}`", workDir));
            sb.AppendLine();
            sb.AppendLine("=== ABSOLUTE SECURITY PRINCIPLE: ZERO BLAST RADIUS ===");
            sb.AppendLine("1. **STRICTLY FORBIDDEN TO CREATE NEW FILES**: You have zero permission to create files. If a file is not in the active workspace whitelist, you must NEVER attempt to create it. You may only modify existing whitelisted files in-place!");
            sb.AppendLine("2. **STRICTLY FORBIDDEN TO DELETE FILES**: You have zero permission to delete files or directories. You must NEVER run destructive commands in the terminal (such as rm, del, rmdir, Remove-Item, unlink, shred, etc.).");
            sb.AppendLine("3. Your only permitted file modification capability is in-place surgical edits on currently whitelisted files.");
            sb.AppendLine();

            if (!hasControlledFiles)
            {
                sb.AppendLine("[WORKSPACE STATUS]: No files currently attached. The file whitelist is empty.");
                sb.AppendLine("You may answer questions, provide coding advice, search the web, and execute read-only terminal commands. Modifying or creating local files is strictly forbidden. If code is shown in your response, format it using standard Markdown code fences for user reference. Do NOT attempt to use XML file edit tags. If the user wants automated in-place code edits, instruct them to add the target files to the workspace first.");
                sb.AppendLine();
                sb.AppendLine("=== AVAILABLE CAPABILITIES & MANDATORY XML PROTOCOL ===");
                sb.AppendLine("1. **Live Web Search (`<metis_search>` or `<search>`)**:");
                sb.AppendLine("   - When you need to look up current documentation, library usage, API parameters, or troubleshooting guides, output a `<metis_search>` tag:");
                sb.AppendLine("<metis_search>");
                sb.AppendLine("python unittest mock patch");
                sb.AppendLine("</metis_search>");
                sb.AppendLine("   - The system will autonomously query the web and feed back extracted summaries and links to drive your next reasoning step.");
                sb.AppendLine();
                sb.AppendLine("2. **Execute Terminal Commands & Continue Interaction (`<metis_cmd>` or `<surgical_cmd>`)**:");
                sb.AppendLine("   - When you need to run terminal commands (e.g., checking environment, running tests, invoking specific tools), output a `<metis_cmd>` XML tag:");
                sb.AppendLine("<metis_cmd>");
                sb.AppendLine("git status");
                sb.AppendLine("</metis_cmd>");
                sb.AppendLine("   - The system executes the command in the active workspace directory, captures real `stdout`, `stderr`, and `ExitCode`, and **automatically feeds the execution feedback back to you to continue the conversation**.");
                sb.AppendLine("   - When your task is complete, summarize your findings directly for the user (**do NOT output another `<metis_cmd>` tag when finished**).");
                sb.AppendLine();
                sb.AppendLine("3. **PDF & Image Multimodal Attachments**: User-uploaded PDFs and images are sent natively via multimodal API. You can read, inspect, and analyze them directly. Strictly forbidden to run python/OCR/pypdf scripts in the terminal to parse or edit these attachments.");
                sb.AppendLine();
                sb.AppendLine("4. Keep explanations concise and structured in Markdown. Wrap web searches and terminal commands in their respective XML tags.");
            }
            else
            {
                string exampleFileName = _files[0].FileName;
                sb.AppendLine("=== CORE CAPABILITIES & MANDATORY XML OUTPUT PROTOCOL ===");
                sb.AppendLine("1. **Surgical In-Place Edits via SEARCH/REPLACE Blocks (`<metis_replace file=\"filename\">`)**:");
                sb.AppendLine("   - When updating whitelisted files, **NEVER rewrite the entire file**. You must use concise and precise SEARCH/REPLACE blocks:");
                sb.AppendLine(string.Format("<metis_replace file=\"{0}\">", exampleFileName));
                sb.AppendLine("<<<<<<< SEARCH");
                sb.AppendLine("Exact original code snippet to be replaced (must match existing file content verbatim, including indentation and newlines)");
                sb.AppendLine("=======");
                sb.AppendLine("New replacement code snippet");
                sb.AppendLine(">>>>>>> REPLACE");
                sb.AppendLine("</metis_replace>");
                sb.AppendLine("   - Rules & Best Practices:");
                sb.AppendLine("     * The SEARCH block must contain enough surrounding lines (1-2 lines of unchanged code) to guarantee unique positioning, and must not include fabricated code;");
                sb.AppendLine("     * Code inside SEARCH must match the file's current content character-for-character;");
                sb.AppendLine("     * To delete code, leave the REPLACE block completely empty;");
                sb.AppendLine("     * You may include multiple `<<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE` blocks inside a single `<metis_replace>` tag;");
                sb.AppendLine("     * Strictly forbidden to reference non-existent files. Attempting to create new files will be blocked immediately.");
                sb.AppendLine();
                sb.AppendLine("2. **Live Web Search (`<metis_search>` or `<search>`)**:");
                sb.AppendLine("   - When you need modern API specifications, third-party library details, error diagnosis, or technical guidance, output a `<metis_search>` tag:");
                sb.AppendLine("<metis_search>");
                sb.AppendLine("python unittest mock patch");
                sb.AppendLine("</metis_search>");
                sb.AppendLine("   - The system retrieves public web summaries and links and returns them as feedback to continue reasoning.");
                sb.AppendLine();
                sb.AppendLine("3. **Execute Terminal Commands & Closed-Loop Verification (`<metis_cmd>` or `<surgical_cmd>`)**:");
                sb.AppendLine("   - When you need to execute commands (e.g., running scripts, running test suites, verifying fixes), output a `<metis_cmd>` tag:");
                sb.AppendLine("<metis_cmd>");
                sb.AppendLine("python " + exampleFileName);
                sb.AppendLine("</metis_cmd>");
                sb.AppendLine("   - The system runs the command in the workspace directory, captures real `stdout`, `stderr`, and `ExitCode`, and **automatically feeds the result back to you**.");
                sb.AppendLine("   - Upon receiving execution output: if errors occur or follow-up is needed, emit another `<metis_replace>` or `<metis_cmd>`; if everything succeeded, provide a concise summary response (**do NOT output another `<metis_cmd>` tag when finished**).");
                sb.AppendLine();
                sb.AppendLine("4. **PDF & Image Multimodal Attachments**: User-uploaded PDFs and images are sent natively via multimodal API. **Directly read and interpret these media files. NEVER use `<metis_cmd>` to invoke local tools (python, pypdf, OCR, etc.) to extract, parse, or modify them.**");
                sb.AppendLine();
                sb.AppendLine("5. Keep responses concise and formatted in Markdown. Always wrap file replacements, searches, and commands in their respective XML tags.");
            }
            return sb.ToString();
        }

        public string BuildCurrentFilesSnapshotBlock()
        {
            StringBuilder sb = new StringBuilder();
            sb.AppendLine(string.Format("=== ACTIVE WORKSPACE DIRECTORY: `{0}` ===", GetActiveWorkspaceDirectory()));

            if (_directories != null && _directories.Count > 0)
            {
                sb.AppendLine("=== MOUNTED WORKSPACE FOLDERS ===");
                foreach (string d in _directories)
                {
                    sb.AppendLine("  - " + d);
                }
            }

            if (_files.Count == 0)
            {
                sb.AppendLine("=== ATTACHED WORKSPACE FILES ===");
                sb.AppendLine("(No files attached. File whitelist is empty; in-place edits are disabled)");
                return sb.ToString();
            }

            sb.AppendLine(string.Format("=== ATTACHED WORKSPACE FILES ({0} files, modify in-place only, creating/deleting prohibited) ===", _files.Count));
            int totalChars = 0;
            const int maxTotalSnapshotChars = 120000;

            foreach (WhitelistedFile wf in _files)
            {
                string displayRel = !string.IsNullOrEmpty(wf.RelativePath) ? wf.RelativePath : wf.FileName;
                string content = wf.ReadCurrentContent();

                if (wf.IsBinaryMedia || MediaAttachmentHelper.IsMediaAttachment(wf.FullPath))
                {
                    string kind = string.Equals(wf.MediaKind, "pdf", StringComparison.OrdinalIgnoreCase) ? "PDF Document" : "Image";
                    sb.AppendLine(string.Format("=== ATTACHMENT ({0}): {1} ===", kind, displayRel));
                    sb.AppendLine("[This multimodal attachment was sent natively to the model with your request. Read and analyze it directly. Do not run terminal scripts to parse it.]");
                    sb.AppendLine(string.Format("=== END OF ATTACHMENT: {0} ===", displayRel));
                    sb.AppendLine();
                    continue;
                }

                sb.AppendLine(string.Format("=== FILE: {0} (Full Path: {1}) ===", displayRel, wf.FullPath));
                if (string.IsNullOrEmpty(content))
                {
                    sb.AppendLine("(Empty file)");
                }
                else if (totalChars + content.Length > maxTotalSnapshotChars)
                {
                    string truncated = content.Substring(0, Math.Min(content.Length, 4000)) + "\r\n... (Content truncated, showing first 4000 characters)";
                    sb.AppendLine(truncated);
                    totalChars += truncated.Length;
                }
                else
                {
                    sb.AppendLine(content);
                    totalChars += content.Length;
                }
                sb.AppendLine(string.Format("=== END OF FILE: {0} ===", displayRel));
                sb.AppendLine();
            }
            return sb.ToString();
        }

        /// <summary>
        /// Strips chain-of-thought blocks (<think>...</think>, ◁think▷...◁/think▷) so thinking is hidden from the app UI.
        /// </summary>
        public static string StripThinkingBlocks(string text, out bool isThinkingInProgress)
        {
            isThinkingInProgress = false;
            if (string.IsNullOrEmpty(text)) return "";

            string cleaned = Regex.Replace(text, @"<think\b[^>]*>[\s\S]*?</think>\s*", "", RegexOptions.IgnoreCase);
            cleaned = Regex.Replace(cleaned, @"◁think▷[\s\S]*?◁/think▷\s*", "", RegexOptions.IgnoreCase);

            int openThink = cleaned.IndexOf("<think", StringComparison.OrdinalIgnoreCase);
            if (openThink >= 0)
            {
                isThinkingInProgress = true;
                cleaned = cleaned.Substring(0, openThink).Trim();
            }
            int openTriThink = cleaned.IndexOf("◁think▷", StringComparison.OrdinalIgnoreCase);
            if (openTriThink >= 0)
            {
                isThinkingInProgress = true;
                cleaned = cleaned.Substring(0, openTriThink).Trim();
            }

            return cleaned.Trim();
        }

        /// <summary>
        /// Formats streaming or completed model text for UI display (hides <think>, converts <surgical_write> into readable markdown code blocks).
        /// </summary>
        public static string FormatModelTextForDisplay(string rawOutput)
        {
            bool thinking;
            string cleaned = StripThinkingBlocks(rawOutput, out thinking);
            if (string.IsNullOrWhiteSpace(cleaned))
            {
                return thinking ? "🧠 Model is thinking (reasoning chain hidden)..." : "";
            }

            // Convert <metis_write> / <pinpoint_write> / <surgical_write> into markdown code blocks so the user sees the code in the app
            cleaned = Regex.Replace(
                cleaned,
                @"<(?:metis|surgical|pinpoint)_(?:write|create)(?:\s+(?:file|path|name)?\s*=?\s*[""']?([^""'>\r\n]+)[""']?)?\s*>\r?\n?([\s\S]*?)(?:\r?\n?</(?:metis|surgical|pinpoint)_(?:write|create)>|$)",
                m =>
                {
                    string fName = m.Groups[1].Success ? m.Groups[1].Value.Trim() : "";
                    string inner = StripWrappingCodeFence(m.Groups[2].Value);
                    inner = CleanEchoedPlaceholders(inner);
                    string lang = GuessFenceLanguage(fName);
                    string header = !string.IsNullOrEmpty(fName) ? ("📄 **`" + fName + "`**\r\n") : "";
                    return header + "```" + lang + "\r\n" + inner.TrimEnd() + "\r\n```";
                },
                RegexOptions.IgnoreCase);

            // Convert <metis_replace> / <pinpoint_replace> / <surgical_replace> into readable markdown block
            cleaned = Regex.Replace(
                cleaned,
                @"<(?:metis|surgical|pinpoint)_replace(?:\s+(?:file|path|name)?\s*=?\s*[""']?([^""'>\r\n]+)[""']?)?\s*>\r?\n?([\s\S]*?)(?:\r?\n?</(?:metis|surgical|pinpoint)_replace>|$)",
                m =>
                {
                    string fName = m.Groups[1].Success ? m.Groups[1].Value.Trim() : "";
                    string inner = m.Groups[2].Value.Trim();
                    string lang = GuessFenceLanguage(fName);
                    string header = !string.IsNullOrEmpty(fName) ? ("📄 **`" + fName + "` (Surgical Update)**\r\n") : "";
                    return header + "```" + lang + "\r\n" + inner + "\r\n```";
                },
                RegexOptions.IgnoreCase);

            // Format <metis_search> / <pinpoint_search> / <search> as a clean badge
            cleaned = Regex.Replace(
                cleaned,
                @"<(?:metis|surgical|pinpoint)?_?search[^>]*>\r?\n?([\s\S]*?)(?:\r?\n?</(?:metis|surgical|pinpoint)?_?search>|$)",
                m =>
                {
                    string q = StripWrappingCodeFence(m.Groups[1].Value).Trim();
                    if (string.IsNullOrEmpty(q)) return "";
                    return "\r\n🌐 **Web Search Requested**: `" + q + "`";
                },
                RegexOptions.IgnoreCase);

            // Format <metis_cmd> / <pinpoint_cmd> / <surgical_cmd> as a clean command line badge
            cleaned = Regex.Replace(
                cleaned,
                @"<(?:metis|surgical|pinpoint)_(?:cmd|terminal|verify)[^>]*>\r?\n?([\s\S]*?)(?:\r?\n?</(?:metis|surgical|pinpoint)_(?:cmd|terminal|verify)>|$)",
                m =>
                {
                    string cmd = StripWrappingCodeFence(m.Groups[1].Value).Trim();
                    if (string.IsNullOrEmpty(cmd)) return "";
                    return "\r\n🖥️ **Terminal Command Requested**: `" + cmd + "`";
                },
                RegexOptions.IgnoreCase).Trim();

            return cleaned;
        }

        private static string GuessFenceLanguage(string fileName)
        {
            if (string.IsNullOrEmpty(fileName)) return "";
            string ext = "";
            try { ext = Path.GetExtension(fileName).ToLowerInvariant(); } catch { }
            switch (ext)
            {
                case ".py": return "python";
                case ".js": return "javascript";
                case ".ts": return "typescript";
                case ".tsx": return "tsx";
                case ".jsx": return "jsx";
                case ".cs": return "csharp";
                case ".cpp":
                case ".cc":
                case ".cxx":
                case ".h":
                case ".hpp": return "cpp";
                case ".c": return "c";
                case ".rs": return "rust";
                case ".go": return "go";
                case ".java": return "java";
                case ".json": return "json";
                case ".html": return "html";
                case ".css": return "css";
                case ".sh": return "bash";
                case ".ps1": return "powershell";
                case ".bat":
                case ".cmd": return "batch";
                case ".md": return "markdown";
                case ".yml":
                case ".yaml": return "yaml";
                case ".sql": return "sql";
                default: return "";
            }
        }

        public static ParsedAgentResponse ParseAgentOutput(string rawOutput, IList<WhitelistedFile> whitelist, bool allowMarkdownFallback = true)
        {
            ParsedAgentResponse parsed = new ParsedAgentResponse();
            if (string.IsNullOrEmpty(rawOutput))
            {
                return parsed;
            }

            parsed.RawText = rawOutput;

            bool isThinking;
            string cleanedOutput = StripThinkingBlocks(rawOutput, out isThinking);

            // 1. Primary: Parse <metis_replace ...> / <pinpoint_replace ...> / <surgical_replace ...>
            Regex replaceRegex = new Regex(@"<(?:metis|surgical|pinpoint)_replace(?:\s+(?:file|path|name)?\s*=?\s*[""']?([^""'>\r\n]+)[""']?)?\s*>\r?\n?([\s\S]*?)(?:\r?\n?</(?:metis|surgical|pinpoint)_replace>|$)", RegexOptions.IgnoreCase);
            MatchCollection replaceMatches = replaceRegex.Matches(cleanedOutput);
            foreach (Match m in replaceMatches)
            {
                string fileAttr = m.Groups[1].Success ? m.Groups[1].Value.Trim() : "";
                string body = m.Groups[2].Value;

                Regex srBlockRegex = new Regex(@"<{5,}\s*SEARCH\r?\n([\s\S]*?)\r?\n={5,}\r?\n([\s\S]*?)\r?\n>{5,}\s*REPLACE", RegexOptions.IgnoreCase);
                MatchCollection srMatches = srBlockRegex.Matches(body);
                foreach (Match sr in srMatches)
                {
                    SurgicalFileEdit edit = new SurgicalFileEdit();
                    edit.ActionType = SurgicalActionType.SearchReplace;
                    edit.TargetFile = fileAttr;
                    edit.SearchBlock = sr.Groups[1].Value;
                    edit.ReplaceBlock = sr.Groups[2].Value;
                    parsed.FileEdits.Add(edit);
                }
            }

            // 2. Parse Raw <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE blocks outside XML tags
            Regex rawSrRegex = new Regex(@"<{5,}\s*SEARCH\r?\n([\s\S]*?)\r?\n={5,}\r?\n([\s\S]*?)\r?\n>{5,}\s*REPLACE", RegexOptions.IgnoreCase);
            MatchCollection rawSrMatches = rawSrRegex.Matches(cleanedOutput);
            foreach (Match sr in rawSrMatches)
            {
                bool alreadyCaptured = false;
                foreach (SurgicalFileEdit existing in parsed.FileEdits)
                {
                    if (existing.ActionType == SurgicalActionType.SearchReplace &&
                        existing.SearchBlock == sr.Groups[1].Value &&
                        existing.ReplaceBlock == sr.Groups[2].Value)
                    {
                        alreadyCaptured = true;
                        break;
                    }
                }
                if (alreadyCaptured) continue;

                string targetFile = "";
                if (whitelist != null && whitelist.Count > 0)
                {
                    int matchIdx = sr.Index;
                    int lookbackStart = Math.Max(0, matchIdx - 350);
                    string preceding = cleanedOutput.Substring(lookbackStart, matchIdx - lookbackStart);

                    foreach (WhitelistedFile wf in whitelist)
                    {
                        if (preceding.IndexOf(wf.FileName, StringComparison.OrdinalIgnoreCase) >= 0 ||
                            (!string.IsNullOrEmpty(wf.RelativePath) && preceding.IndexOf(wf.RelativePath, StringComparison.OrdinalIgnoreCase) >= 0))
                        {
                            targetFile = wf.FileName;
                            break;
                        }
                    }

                    if (string.IsNullOrEmpty(targetFile))
                    {
                        targetFile = whitelist[0].FileName;
                    }
                }

                SurgicalFileEdit edit = new SurgicalFileEdit();
                edit.ActionType = SurgicalActionType.SearchReplace;
                edit.TargetFile = targetFile;
                edit.SearchBlock = sr.Groups[1].Value;
                edit.ReplaceBlock = sr.Groups[2].Value;
                parsed.FileEdits.Add(edit);
            }

            // 3. Fallback: Parse <metis_write ...>, <pinpoint_write ...>, and <surgical_write ...>
            if (parsed.FileEdits.Count == 0)
            {
                Regex writeRegex = new Regex(@"<(?:metis|surgical|pinpoint)_write(?:\s+(?:file|path|name)?\s*=?\s*[""']?([^""'>\r\n]+)[""']?)?\s*>\r?\n?([\s\S]*?)(?:\r?\n?</(?:metis|surgical|pinpoint)_write>|$)", RegexOptions.IgnoreCase);
                MatchCollection writeMatches = writeRegex.Matches(cleanedOutput);
                foreach (Match m in writeMatches)
                {
                    SurgicalFileEdit edit = new SurgicalFileEdit();
                    edit.ActionType = SurgicalActionType.WriteFull;
                    edit.TargetFile = m.Groups[1].Success ? m.Groups[1].Value.Trim() : "";
                    edit.NewContent = CleanEchoedPlaceholders(StripWrappingCodeFence(m.Groups[2].Value));
                    if (!string.IsNullOrWhiteSpace(edit.NewContent))
                    {
                        parsed.FileEdits.Add(edit);
                    }
                }
            }

            // 4. Fallback: Standard Markdown Code Blocks ```lang ... ``` when the model omitted tags!
            if (allowMarkdownFallback && parsed.FileEdits.Count == 0)
            {
                ExtractEditsFromMarkdownCodeBlocks(cleanedOutput, whitelist, parsed);
            }

            // 5. Parse <metis_cmd>, <pinpoint_cmd>, and <surgical_cmd>
            Regex cmdTagRegex = new Regex(@"<(?:metis|surgical|pinpoint)_(?:cmd|terminal|verify)[^>]*>\r?\n?([\s\S]*?)\r?\n?</(?:metis|surgical|pinpoint)_(?:cmd|terminal|verify)>", RegexOptions.IgnoreCase);
            MatchCollection cmdMatches = cmdTagRegex.Matches(cleanedOutput);
            foreach (Match cm in cmdMatches)
            {
                string rawCmd = StripWrappingCodeFence(cm.Groups[1].Value).Trim();
                if (!string.IsNullOrWhiteSpace(rawCmd))
                {
                    // Support multi-line commands inside a single <metis_cmd> block by joining non-comment lines with &&
                    string[] lines = rawCmd.Split(new char[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
                    List<string> validLines = new List<string>();
                    foreach (string line in lines)
                    {
                        string t = line.Trim();
                        if (string.IsNullOrEmpty(t) || t.StartsWith("#") || t.StartsWith("//") || t.StartsWith("REM ", StringComparison.OrdinalIgnoreCase))
                        {
                            continue;
                        }
                        if (t.StartsWith("$ ")) t = t.Substring(2).Trim();
                        if (t.StartsWith("> ")) t = t.Substring(2).Trim();
                        if (!string.IsNullOrEmpty(t)) validLines.Add(t);
                    }

                    if (validLines.Count > 0)
                    {
                        string combinedCmd = string.Join(" && ", validLines.ToArray());
                        if (!parsed.TerminalCommands.Contains(combinedCmd))
                        {
                            parsed.TerminalCommands.Add(combinedCmd);
                        }
                    }
                }
            }

            // 6. Parse <metis_search>, <pinpoint_search>, and <search>
            Regex searchTagRegex = new Regex(@"<(?:metis|surgical|pinpoint)?_?search[^>]*>\r?\n?([\s\S]*?)\r?\n?</(?:metis|surgical|pinpoint)?_?search>", RegexOptions.IgnoreCase);
            MatchCollection searchMatches = searchTagRegex.Matches(cleanedOutput);
            foreach (Match sm in searchMatches)
            {
                string rawQuery = StripWrappingCodeFence(sm.Groups[1].Value).Trim();
                if (!string.IsNullOrWhiteSpace(rawQuery) && !parsed.SearchQueries.Contains(rawQuery))
                {
                    parsed.SearchQueries.Add(rawQuery);
                }
            }

            string displayText = FormatModelTextForDisplay(rawOutput);
            parsed.ExplanationText = string.IsNullOrWhiteSpace(displayText)
                ? "Completed analysis and required actions."
                : displayText;

            return parsed;
        }

        private class CandidateFenceBlock
        {
            public string Info { get; set; }
            public string Lang { get; set; }
            public string Code { get; set; }
            public int StartIndex { get; set; }
            public bool HasExplicitLang { get; set; }
            public string InferredFileName { get; set; }
        }

        private static readonly HashSet<string> TerminalOrOutputLanguages = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "output", "out", "console", "terminal", "term", "shell-session", "console-output",
            "stdout", "stderr", "log", "logs", "result", "results", "none", "null"
        };

        private static bool IsPrecededByOutputIndicator(string fullText, int fenceStartIndex)
        {
            if (fenceStartIndex <= 0 || string.IsNullOrEmpty(fullText)) return false;
            int lookbackStart = Math.Max(0, fenceStartIndex - 250);
            string preceding = fullText.Substring(lookbackStart, fenceStartIndex - lookbackStart).ToLowerInvariant();

            string[] outputKeywords = new string[]
            {
                "输出结果", "运行结果", "执行结果", "终端输出", "终端运行", "测试结果", "验证结果",
                "结果如下", "输出如下", "运行输出", "输出内容", "运行验证完毕", "测试通过",
                "terminal output", "console output", "stdout", "stderr",
                "command output", "run result", "test result", "execution result",
                "output:", "results:"
            };

            foreach (string kw in outputKeywords)
            {
                if (preceding.Contains(kw))
                {
                    return true;
                }
            }
            return false;
        }

        private static bool IsPastTenseOrCompletedReference(string text)
        {
            if (string.IsNullOrEmpty(text)) return false;
            string lower = text.ToLowerInvariant();
            string[] pastIndicators = new string[]
            {
                "已写入", "已成功写入", "已修改", "已成功修改", "已保存", "已更新", "已创建",
                "运行验证完毕", "测试通过", "执行完毕", "执行成功", "已经修改", "已经写入",
                "already written", "successfully written", "already saved", "already updated",
                "has been written", "have been written", "has been saved", "has been updated",
                "verified successfully", "passed tests"
            };

            foreach (string pi in pastIndicators)
            {
                if (lower.Contains(pi)) return true;
            }
            return false;
        }

        private static void ExtractEditsFromMarkdownCodeBlocks(
            string cleanedOutput,
            IList<WhitelistedFile> whitelist,
            ParsedAgentResponse parsed)
        {
            Regex fenceRegex = new Regex(@"```([^\r\n`]*)\r?\n([\s\S]*?)```");
            MatchCollection matches = fenceRegex.Matches(cleanedOutput);
            if (matches.Count == 0) return;

            List<CandidateFenceBlock> candidates = new List<CandidateFenceBlock>();
            foreach (Match m in matches)
            {
                string info = (m.Groups[1].Value ?? "").Trim();
                string code = CleanEchoedPlaceholders(m.Groups[2].Value ?? "");
                if (string.IsNullOrWhiteSpace(code)) continue;

                string[] infoParts = info.Split(new char[] { ' ', ':', '\t' }, StringSplitOptions.RemoveEmptyEntries);
                string firstToken = infoParts.Length > 0 ? infoParts[0].ToLowerInvariant() : "";

                // 1. Skip non-code and execution output blocks (e.g. output, console, terminal, log, stdout, stderr, etc.)
                if (TerminalOrOutputLanguages.Contains(firstToken))
                {
                    continue;
                }

                // 2. Skip blocks immediately preceded by output/result keywords (e.g. "终端运行输出结果如下：")
                if (IsPrecededByOutputIndicator(cleanedOutput, m.Index))
                {
                    continue;
                }

                // 3. If block is tagged as a shell/terminal command, only consider as edit if whitelist explicitly has a shell script (.sh, .bat, .cmd, .ps1). Never for .txt!
                if (firstToken == "bash" || firstToken == "sh" || firstToken == "shell" ||
                    firstToken == "cmd" || firstToken == "powershell" || firstToken == "ps1")
                {
                    bool hasShellTarget = false;
                    if (whitelist != null)
                    {
                        foreach (WhitelistedFile wf in whitelist)
                        {
                            string ext = Path.GetExtension(wf.FileName).ToLowerInvariant();
                            if (ext == ".sh" || ext == ".bat" || ext == ".cmd" || ext == ".ps1")
                            {
                                hasShellTarget = true;
                                break;
                            }
                        }
                    }
                    if (!hasShellTarget) continue;
                }

                // 4. If block is plain text, only consider if whitelist explicitly has a .txt file, AND not preceded by output indicators
                if (firstToken == "text" || firstToken == "plaintext" || firstToken == "txt")
                {
                    bool hasTextTarget = false;
                    if (whitelist != null)
                    {
                        foreach (WhitelistedFile wf in whitelist)
                        {
                            string ext = Path.GetExtension(wf.FileName).ToLowerInvariant();
                            if (ext == ".txt")
                            {
                                hasTextTarget = true;
                                break;
                            }
                        }
                    }
                    if (!hasTextTarget) continue;
                }

                string inferredFile = ExtractFileNameMention(info, m.Index, cleanedOutput, code);

                CandidateFenceBlock block = new CandidateFenceBlock();
                block.Info = info;
                block.Lang = firstToken;
                block.Code = code;
                block.StartIndex = m.Index;
                block.HasExplicitLang = !string.IsNullOrEmpty(firstToken) && firstToken != "text" && firstToken != "plaintext" && firstToken != "txt";
                block.InferredFileName = inferredFile;
                candidates.Add(block);
            }

            if (candidates.Count == 0) return;

            List<CandidateFenceBlock> tagged = new List<CandidateFenceBlock>();
            foreach (CandidateFenceBlock c in candidates)
            {
                if (c.HasExplicitLang) tagged.Add(c);
            }
            List<CandidateFenceBlock> filtered = tagged.Count > 0 ? tagged : candidates;

            // If any block explicitly mentions a filename (existing or new), honor it
            bool anyExplicitFileMatched = false;
            Dictionary<string, SurgicalFileEdit> editsByTarget = new Dictionary<string, SurgicalFileEdit>(StringComparer.OrdinalIgnoreCase);

            foreach (CandidateFenceBlock c in filtered)
            {
                if (!string.IsNullOrEmpty(c.InferredFileName))
                {
                    SurgicalFileEdit edit = new SurgicalFileEdit();
                    edit.ActionType = SurgicalActionType.WriteFull;
                    edit.TargetFile = c.InferredFileName;
                    edit.NewContent = c.Code;
                    editsByTarget[c.InferredFileName] = edit;
                    anyExplicitFileMatched = true;
                }
            }

            if (anyExplicitFileMatched)
            {
                foreach (SurgicalFileEdit e in editsByTarget.Values)
                {
                    parsed.FileEdits.Add(e);
                }
                return;
            }

            List<WhitelistedFile> editableWhitelist = new List<WhitelistedFile>();
            if (whitelist != null)
            {
                foreach (WhitelistedFile wf in whitelist)
                {
                    if (!wf.IsBinaryMedia && !MediaAttachmentHelper.IsMediaAttachment(wf.FullPath))
                    {
                        editableWhitelist.Add(wf);
                    }
                }
            }

            if (editableWhitelist.Count == 0)
            {
                return;
            }

            if (editableWhitelist.Count == 1)
            {
                WhitelistedFile target = editableWhitelist[0];
                string expectedLang = GuessFenceLanguage(target.FileName);

                CandidateFenceBlock chosen = null;
                foreach (CandidateFenceBlock c in filtered)
                {
                    if (!string.IsNullOrEmpty(expectedLang) &&
                        (string.Equals(c.Lang, expectedLang, StringComparison.OrdinalIgnoreCase) ||
                         (expectedLang == "python" && c.Lang == "py") ||
                         (expectedLang == "javascript" && c.Lang == "js") ||
                         (expectedLang == "typescript" && c.Lang == "ts") ||
                         (expectedLang == "csharp" && c.Lang == "cs") ||
                         (expectedLang == "rust" && c.Lang == "rs")))
                    {
                        chosen = c;
                        break;
                    }
                }
                if (chosen == null)
                {
                    if (filtered.Count > 0)
                    {
                        CandidateFenceBlock c0 = filtered[0];
                        bool isTextLang = string.IsNullOrEmpty(c0.Lang) || c0.Lang == "text" || c0.Lang == "plaintext" || c0.Lang == "txt";
                        bool targetIsText = string.Equals(Path.GetExtension(target.FileName), ".txt", StringComparison.OrdinalIgnoreCase);

                        if (isTextLang && !targetIsText)
                        {
                            // Target is code (e.g. .py), but candidate is plain text/output -> do not overwrite
                            return;
                        }

                        if (isTextLang && targetIsText)
                        {
                            // Target is .txt and block is plain text: only write if explicit filename was mentioned with forward writing intent
                            if (string.IsNullOrEmpty(c0.InferredFileName) || !string.Equals(Path.GetFileName(c0.InferredFileName), target.FileName, StringComparison.OrdinalIgnoreCase))
                            {
                                return;
                            }
                        }

                        chosen = c0;
                    }
                }

                if (chosen != null)
                {
                    SurgicalFileEdit edit = new SurgicalFileEdit();
                    edit.ActionType = SurgicalActionType.WriteFull;
                    edit.TargetFile = target.FullPath;
                    edit.NewContent = chosen.Code;
                    parsed.FileEdits.Add(edit);
                }
                return;
            }

            Dictionary<string, SurgicalFileEdit> editsByPath = new Dictionary<string, SurgicalFileEdit>(StringComparer.OrdinalIgnoreCase);
            foreach (CandidateFenceBlock c in filtered)
            {
                WhitelistedFile matched = null;

                if (!string.IsNullOrEmpty(c.Lang))
                {
                    foreach (WhitelistedFile wf in editableWhitelist)
                    {
                        string expected = GuessFenceLanguage(wf.FileName);
                        if (string.Equals(expected, c.Lang, StringComparison.OrdinalIgnoreCase) ||
                            (expected == "python" && c.Lang == "py") ||
                            (expected == "javascript" && c.Lang == "js") ||
                            (expected == "typescript" && c.Lang == "ts"))
                        {
                            matched = wf;
                            break;
                        }
                    }
                }

                if (matched == null)
                {
                    bool isTextLang = string.IsNullOrEmpty(c.Lang) || c.Lang == "text" || c.Lang == "plaintext" || c.Lang == "txt";
                    if (isTextLang)
                    {
                        continue;
                    }
                    matched = editableWhitelist[0];
                }

                if (!editsByPath.ContainsKey(matched.FullPath))
                {
                    SurgicalFileEdit edit = new SurgicalFileEdit();
                    edit.ActionType = SurgicalActionType.WriteFull;
                    edit.TargetFile = matched.FullPath;
                    edit.NewContent = c.Code;
                    editsByPath[matched.FullPath] = edit;
                }
            }

            foreach (SurgicalFileEdit e in editsByPath.Values)
            {
                parsed.FileEdits.Add(e);
            }
        }

        private static string ExtractFileNameMention(string fenceInfo, int fenceStartIndex, string fullText, string codeContent)
        {
            Regex filePattern = new Regex(@"\b([a-zA-Z0-9_./\\-]+\.(?:py|js|ts|tsx|jsx|cs|cpp|c|h|hpp|rs|go|java|html|css|json|yaml|yml|toml|sh|bat|ps1|md|sql|txt))\b", RegexOptions.IgnoreCase);

            if (!string.IsNullOrEmpty(fenceInfo))
            {
                Match mInfo = filePattern.Match(fenceInfo);
                if (mInfo.Success) return mInfo.Groups[1].Value;
            }

            if (!string.IsNullOrEmpty(codeContent))
            {
                int firstNl = codeContent.IndexOf('\n');
                string firstLine = firstNl >= 0 ? codeContent.Substring(0, firstNl).Trim() : codeContent.Trim();
                if (firstLine.StartsWith("#") || firstLine.StartsWith("//") || firstLine.StartsWith("/*") || firstLine.StartsWith("<!--") || firstLine.StartsWith("REM ", StringComparison.OrdinalIgnoreCase))
                {
                    Match mFirst = filePattern.Match(firstLine);
                    if (mFirst.Success) return mFirst.Groups[1].Value;
                }
            }

            if (fenceStartIndex > 0 && !string.IsNullOrEmpty(fullText))
            {
                int lookbackStart = Math.Max(0, fenceStartIndex - 200);
                string preceding = fullText.Substring(lookbackStart, fenceStartIndex - lookbackStart);

                // If preceding text indicates past-tense or completed action for the file, do NOT treat it as the target of this block!
                if (IsPastTenseOrCompletedReference(preceding))
                {
                    return null;
                }

                MatchCollection matches = filePattern.Matches(preceding);
                if (matches.Count > 0)
                {
                    return matches[matches.Count - 1].Groups[1].Value;
                }
            }

            return null;
        }

        private static string StripWrappingCodeFence(string text)
        {
            if (string.IsNullOrEmpty(text)) return "";
            string trimmed = text.Trim();
            if (trimmed.StartsWith("```"))
            {
                int firstNewline = trimmed.IndexOf('\n');
                if (firstNewline > 0 && trimmed.EndsWith("```"))
                {
                    string inner = trimmed.Substring(firstNewline + 1);
                    if (inner.EndsWith("```"))
                    {
                        inner = inner.Substring(0, inner.Length - 3).TrimEnd('\r', '\n');
                    }
                    return inner;
                }
            }
            return text;
        }
    }
}
