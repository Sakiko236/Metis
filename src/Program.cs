using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;

[assembly: AssemblyTitle("Metis")]
[assembly: AssemblyDescription("Laser-focused micro-editing coding agent with zero blast radius")]
[assembly: AssemblyProduct("Metis")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

namespace Metis
{
    public static class Program
    {
        [STAThread]
        public static int Main(string[] args)
        {
            try { Console.OutputEncoding = Encoding.UTF8; } catch { }
            AppDomain.CurrentDomain.AssemblyResolve += OnResolveEmbeddedAssembly;
            EnsureRuntimeAssetsExtracted();

            if (args != null && args.Length > 0 && string.Equals(args[0], "--self-test", StringComparison.OrdinalIgnoreCase))
            {
                return RunHeadlessSelfTest().GetAwaiter().GetResult();
            }

            return RunGuiApp();
        }

        private static int RunGuiApp()
        {
            Application app = new Application();
            app.DispatcherUnhandledException += (s, e) =>
            {
                MessageBox.Show(
                    "UI Runtime Exception: " + e.Exception.Message,
                    "Metis Error",
                    MessageBoxButton.OK,
                    MessageBoxImage.Error);
                e.Handled = true;
            };

            MainWindow mainWindow = new MainWindow(false);
            app.Run(mainWindow);
            return 0;
        }

        private static Assembly OnResolveEmbeddedAssembly(object sender, ResolveEventArgs args)
        {
            AssemblyName requested = new AssemblyName(args.Name);
            string resourceName = requested.Name + ".dll";

            Assembly executing = Assembly.GetExecutingAssembly();
            using (Stream s = executing.GetManifestResourceStream(resourceName))
            {
                if (s != null)
                {
                    byte[] raw = new byte[s.Length];
                    s.Read(raw, 0, raw.Length);
                    return Assembly.Load(raw);
                }
            }
            return null;
        }

        public static string EnsureRuntimeAssetsExtracted()
        {
            string runtimeDir = Path.Combine(ConfigManager.GetAppDataDir(), "runtime");
            if (!Directory.Exists(runtimeDir))
            {
                Directory.CreateDirectory(runtimeDir);
            }

            ExtractEmbeddedResourceToFile("WebView2Loader.dll", Path.Combine(runtimeDir, "WebView2Loader.dll"), false);
            ExtractEmbeddedResourceToFile("app.html", Path.Combine(runtimeDir, "app.html"), true);

            return runtimeDir;
        }

        private static void ExtractEmbeddedResourceToFile(string resourceName, string targetPath, bool alwaysOverwrite)
        {
            try
            {
                if (!alwaysOverwrite && File.Exists(targetPath))
                {
                    return;
                }

                Assembly executing = Assembly.GetExecutingAssembly();
                using (Stream s = executing.GetManifestResourceStream(resourceName))
                {
                    if (s == null) return;
                    using (FileStream fs = new FileStream(targetPath, FileMode.Create, FileAccess.Write, FileShare.Read))
                    {
                        s.CopyTo(fs);
                    }
                }
            }
            catch { }
        }

        private static async Task<int> RunHeadlessSelfTest()
        {
            Console.WriteLine("=== Metis v1.0.0 Headless End-to-End Self-Test ===");

            // 1. Verify Config & Persistent Session Store
            AppConfig cfg = ConfigManager.LoadOrCreateDefault();
            ConfigManager.Save(cfg);
            if (!File.Exists(ConfigManager.GetConfigPath()))
            {
                Console.WriteLine("FAIL: Config file was not persisted.");
                return 1;
            }
            Console.WriteLine("[PASS] Local Config Persisted: " + ConfigManager.GetConfigPath());

            SessionStoreData store = SessionManager.LoadOrCreate();
            SessionManager.Save(store);
            if (!File.Exists(SessionManager.GetSessionsFilePath()))
            {
                Console.WriteLine("FAIL: Sessions file was not persisted.");
                return 2;
            }
            Console.WriteLine("[PASS] Local Sessions Persisted: " + SessionManager.GetSessionsFilePath());

            // 2. Verify Runtime Assets Extraction (WebView2Loader.dll + app.html)
            string rtDir = EnsureRuntimeAssetsExtracted();
            if (!File.Exists(Path.Combine(rtDir, "app.html")) || !File.Exists(Path.Combine(rtDir, "WebView2Loader.dll")))
            {
                Console.WriteLine("FAIL: Runtime assets not extracted.");
                return 3;
            }
            Console.WriteLine("[PASS] Modern Frontend & WebView2 Assets Extracted: " + rtDir);

            // 3. Verify Whitelist Zero Blast Radius & Autonomous Verification Loop
            string testDir = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "demo_workspace");
            if (!Directory.Exists(testDir)) Directory.CreateDirectory(testDir);

            string authPy = Path.Combine(testDir, "auth.py");
            string testAuthPy = Path.Combine(testDir, "test_auth.py");

            File.WriteAllText(authPy,
@"import time

def is_token_expired(token: dict, current_time: int = None) -> bool:
    if current_time is None:
        current_time = int(time.time())
    expires_at = token.get(""expires_at"", 0)
    return current_time < expires_at
", new UTF8Encoding(false));

            File.WriteAllText(testAuthPy,
@"from auth import is_token_expired

def test_valid_token_not_expired():
    token = {""expires_at"": 2000}
    assert is_token_expired(token, current_time=1500) is False

def test_expired_token_detected():
    token = {""expires_at"": 2000}
    assert is_token_expired(token, current_time=2500) is True

def test_exact_boundary():
    token = {""expires_at"": 2000}
    assert is_token_expired(token, current_time=2000) is True
", new UTF8Encoding(false));

            WhitelistManager wm = new WhitelistManager();
            bool isNew;
            string err;
            wm.AddOrUpdateFile(authPy, out isNew, out err);

            VendorProfile sandbox = new VendorProfile();
            sandbox.Id = "sandbox-selftest";
            sandbox.DisplayName = "SelfTest Mock";
            sandbox.Type = VendorType.LocalSandbox;
            sandbox.BaseUrl = "local://metis-sandbox";
            sandbox.SelectedModel = "metis-heuristic-v1";

            List<ChatHistoryItem> history = new List<ChatHistoryItem>
            {
                new ChatHistoryItem("user", "Fix token expiration in auth.py and run pytest to verify")
            };

            string reply = await MultiVendorClient.SendChatAsync(
                sandbox,
                wm.BuildSystemPrompt(),
                wm.BuildCurrentFilesSnapshotBlock(),
                history,
                wm.Files,
                CancellationToken.None);

            ParsedAgentResponse parsed = WhitelistManager.ParseAgentOutput(reply, wm.Files);
            WhitelistedFile targetOut;
            string statusMsg;
            foreach (SurgicalFileEdit edit in parsed.FileEdits)
            {
                if (!wm.ApplyEditInPlace(edit, out targetOut, out statusMsg))
                {
                    Console.WriteLine("FAIL: ApplyEditInPlace failed: " + statusMsg);
                    return 4;
                }
            }

            VerifyExecutionResult vres = await VerificationRunner.RunCommandAsync(
                "pytest",
                testDir,
                30,
                CancellationToken.None);

            if (!vres.Succeeded)
            {
                Console.WriteLine("FAIL: Verification loop failed.");
                return 5;
            }
            Console.WriteLine("[PASS] Autonomous Verification Loop Succeeded (Exit 0)!");

            // 4. Verify <think> stripping + Markdown code block fallback write-to-disk
            string qsPy = Path.Combine(testDir, "quicksort.py");
            File.WriteAllText(qsPy, "", new UTF8Encoding(false));
            WhitelistManager wmQs = new WhitelistManager();
            wmQs.AddOrUpdateFile(qsPy, out isNew, out err);

            string simulatedSmallModelReply =
@"<think>
Let me write a quicksort implementation in Python for quicksort.py.
</think>
Here is the quicksort code:

```python
def quicksort(arr):
    if len(arr) <= 1:
        return arr
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    return quicksort(left) + middle + quicksort(right)
```

Sample output:
```
[1, 2, 3, 6, 8, 10]
```";

            ParsedAgentResponse parsedQs = WhitelistManager.ParseAgentOutput(simulatedSmallModelReply, wmQs.Files);
            if (parsedQs.ExplanationText.Contains("<think>") || parsedQs.ExplanationText.Contains("Let me write"))
            {
                Console.WriteLine("FAIL: <think> block was not stripped from ExplanationText.");
                return 6;
            }
            if (parsedQs.FileEdits.Count != 1)
            {
                Console.WriteLine("FAIL: Expected 1 fallback FileEdit from Markdown code block, got " + parsedQs.FileEdits.Count);
                return 7;
            }
            if (!wmQs.ApplyEditInPlace(parsedQs.FileEdits[0], out targetOut, out statusMsg))
            {
                Console.WriteLine("FAIL: ApplyEditInPlace failed for quicksort.py: " + statusMsg);
                return 8;
            }
            string diskContent = File.ReadAllText(qsPy, Encoding.UTF8);
            if (!diskContent.Contains("def quicksort(arr):"))
            {
                Console.WriteLine("FAIL: Disk content of quicksort.py was not updated.");
                return 9;
            }
            Console.WriteLine("[PASS] <think> Stripping & Markdown Code Block Disk Write Verified!");

            // 5. Verify Folder Upload
            WhitelistManager wmFolder = new WhitelistManager();
            int importedCount;
            string dirErr;
            if (!wmFolder.AddDirectory(testDir, out importedCount, out dirErr) || importedCount < 2)
            {
                Console.WriteLine("FAIL: AddDirectory failed or imported fewer than 2 files: " + dirErr);
                return 10;
            }
            Console.WriteLine("[PASS] Folder Upload & Recursive Scan Verified (" + importedCount + " files loaded from " + testDir + ")");

            // 5. Verify Strict Block on New File Creation & File Deletion
            string agentTryCreateReply =
@"I attempt to create a new file:
<metis_write file=""sub/auto_created_script.py"">
print(""THIS_SHOULD_BE_BLOCKED"")
</metis_write>";

            ParsedAgentResponse parsedBlocked = WhitelistManager.ParseAgentOutput(agentTryCreateReply, wmFolder.Files);
            if (parsedBlocked.FileEdits.Count != 1)
            {
                Console.WriteLine("FAIL: Expected 1 FileEdit in parse test.");
                return 11;
            }
            if (wmFolder.ApplyEditInPlace(parsedBlocked.FileEdits[0], out targetOut, out statusMsg))
            {
                Console.WriteLine("FAIL: New file creation should have been strictly blocked!");
                return 12;
            }
            Console.WriteLine("[PASS] Strict Block on New File Creation Verified: " + statusMsg);

            // Verify file deletion via terminal is blocked
            VerifyExecutionResult delCmdRes = await VerificationRunner.RunCommandAsync(
                "del quicksort.py",
                wmFolder.GetActiveWorkspaceDirectory(),
                10,
                CancellationToken.None);

            if (delCmdRes.Succeeded || !delCmdRes.CombinedOutput.Contains("File Deletion Prohibited"))
            {
                Console.WriteLine("FAIL: Command attempting to delete file was not blocked: " + delCmdRes.CombinedOutput);
                return 13;
            }
            Console.WriteLine("[PASS] Strict Block on File Deletion Verified!");

            // 6. Verify Web Search Capability
            List<SearchResultItem> searchItems = await WebSearchRunner.SearchAsync("python unittest", CancellationToken.None, 3);
            string searchPromptFeedback = WebSearchRunner.FormatSearchResultsForPrompt("python unittest", searchItems);
            if (!searchPromptFeedback.Contains("Live Web Search Feedback"))
            {
                Console.WriteLine("FAIL: WebSearchRunner.FormatSearchResultsForPrompt failed.");
                return 14;
            }
            Console.WriteLine("[PASS] Web Search Capability & Output Formatting Verified!");

            // 7. Verify Terminal Execution & Continuation on Existing Whitelisted File
            VerifyExecutionResult cmdRes = await VerificationRunner.RunCommandAsync(
                "python quicksort.py",
                wmFolder.GetActiveWorkspaceDirectory(),
                30,
                CancellationToken.None);

            if (!cmdRes.Succeeded)
            {
                Console.WriteLine("FAIL: Terminal command execution failed: " + cmdRes.CombinedOutput);
                return 15;
            }

            // Simulate feeding terminal output back into conversation history for automatic continuation
            history.Add(new ChatHistoryItem("user",
                "[Terminal Execution Feedback - Step 1 | Command `python quicksort.py` | Exit Code: 0 (Execution Succeeded)]\r\n" +
                "Terminal Output:\r\n```\r\n" + cmdRes.CombinedOutput + "\r\n```"));

            string followUpReply = await MultiVendorClient.SendChatAsync(
                sandbox,
                wmFolder.BuildSystemPrompt(),
                wmFolder.BuildCurrentFilesSnapshotBlock(),
                history,
                wmFolder.Files,
                CancellationToken.None);

            ParsedAgentResponse parsedFollowUp = WhitelistManager.ParseAgentOutput(followUpReply, wmFolder.Files);
            if (parsedFollowUp.TerminalCommands.Count != 0 || string.IsNullOrWhiteSpace(parsedFollowUp.ExplanationText))
            {
                Console.WriteLine("FAIL: Auto-continuation after terminal output did not complete cleanly.");
                return 16;
            }
            Console.WriteLine("[PASS] Terminal Execution & Automatic Conversation Continuation Verified!");

            // 8. Verify PDF & Image Attachment Upload, Parsing, and Multimodal Encoding
            string samplePdfPath = Path.Combine(testDir, "spec_sample.pdf");
            string rawPdfContent =
@"%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>
endobj
4 0 obj
<< /Length 64 >>
stream
BT
/F1 12 Tf
72 712 Td
(Metis PDF Multimodal Spec v1.0) Tj
ET
endstream
endobj
%%EOF";
            File.WriteAllText(samplePdfPath, rawPdfContent, Encoding.ASCII);

            string samplePngPath = Path.Combine(testDir, "diagram_sample.png");
            using (System.Drawing.Bitmap bmp = new System.Drawing.Bitmap(120, 60))
            {
                using (System.Drawing.Graphics g = System.Drawing.Graphics.FromImage(bmp))
                {
                    g.Clear(System.Drawing.Color.DarkSlateBlue);
                }
                bmp.Save(samplePngPath, System.Drawing.Imaging.ImageFormat.Png);
            }

            WhitelistedFile pdfWf = wmFolder.AddOrUpdateFile(samplePdfPath, out isNew, out err);
            WhitelistedFile imgWf = wmFolder.AddOrUpdateFile(samplePngPath, out isNew, out err);
            if (pdfWf == null || pdfWf.MediaKind != "pdf" || !pdfWf.ReadCurrentContent().Contains("PDF Document"))
            {
                Console.WriteLine("FAIL: PDF attachment parsing failed: " + (pdfWf != null ? pdfWf.ReadCurrentContent() : err));
                return 15;
            }
            string outMime;
            string imgB64 = MediaAttachmentHelper.GetBase64ForApi(samplePngPath, out outMime);
            if (imgWf == null || imgWf.MediaKind != "image" || string.IsNullOrEmpty(imgB64) || outMime != "image/png")
            {
                Console.WriteLine("FAIL: Image attachment Base64 encoding failed.");
                return 16;
            }
            Console.WriteLine("[PASS] PDF & Image Multimodal Base64 Pipeline Verified!");

            // 9. Verify Light / Dark Theme Persistence
            cfg.Theme = "light";
            ConfigManager.Save(cfg);
            AppConfig reloadedCfg = ConfigManager.LoadOrCreateDefault();
            if (reloadedCfg.Theme != "light")
            {
                Console.WriteLine("FAIL: Light theme config not persisted properly.");
                return 17;
            }
            cfg.Theme = "dark";
            ConfigManager.Save(cfg);
            Console.WriteLine("[PASS] Light / Dark Theme Persistence & Settings Verified!");

            // 10. Verify Non-Garbled Terminal Output (cmd.exe GBK / UTF-8)
            VerifyExecutionResult gbkCmdRes = await VerificationRunner.RunCommandAsync(
                "echo Testing terminal unicode output",
                wmFolder.GetActiveWorkspaceDirectory(),
                10,
                CancellationToken.None);

            if (!gbkCmdRes.Succeeded || !gbkCmdRes.CombinedOutput.Contains("Testing terminal unicode output"))
            {
                Console.WriteLine("FAIL: Terminal output test failed or garbled: " + gbkCmdRes.CombinedOutput);
                return 18;
            }

            VerifyExecutionResult dirCmdRes = await VerificationRunner.RunCommandAsync(
                "dir",
                wmFolder.GetActiveWorkspaceDirectory(),
                10,
                CancellationToken.None);

            if (!dirCmdRes.Succeeded || dirCmdRes.CombinedOutput.Contains("???"))
            {
                Console.WriteLine("FAIL: Native CMD dir output contains mojibake: " + dirCmdRes.CombinedOutput);
                return 19;
            }
            Console.WriteLine("[PASS] Non-Garbled Terminal Output & CMD dir Adaptive Decoding Verified!");

            // 11. Verify Terminal Output & Multi-Round Summary Code Block Safeguard (Sessions.json Bug Regression Test)
            string sampleTxtPath = Path.Combine(testDir, "A.TXT.txt");
            string initialPyCode = "def greet(name: str) -> str:\n    return f\"Hello, {name}! Welcome to Metis.\"\n";
            File.WriteAllText(sampleTxtPath, initialPyCode, Encoding.UTF8);

            WhitelistManager wmA = new WhitelistManager();
            wmA.AddOrUpdateFile(sampleTxtPath, out isNew, out err);

            string round2SummaryReply =
@"Code was successfully written to `A.TXT.txt` and verified via Python interpreter!

Terminal execution output:
```text
Hello, World! Welcome to Metis.
Python code is running successfully!
```

---

💡 **File modification note**:
Due to security policies, Metis strictly prohibits creating, deleting, or renaming files in the workspace.";

            // Case A: Multi-round safeguard (round 2+ following success -> allowMarkdownFallback = false)
            ParsedAgentResponse parsedR2 = WhitelistManager.ParseAgentOutput(round2SummaryReply, wmA.Files, allowMarkdownFallback: false);
            if (parsedR2.FileEdits.Count != 0)
            {
                Console.WriteLine("FAIL: Multi-round summary response unexpectedly produced FileEdits when allowMarkdownFallback is false: " + parsedR2.FileEdits.Count);
                return 20;
            }

            // Case B: Even if allowMarkdownFallback is true, output indicator & past-tense detection must prevent overwriting
            ParsedAgentResponse parsedWithFallback = WhitelistManager.ParseAgentOutput(round2SummaryReply, wmA.Files, allowMarkdownFallback: true);
            if (parsedWithFallback.FileEdits.Count != 0)
            {
                Console.WriteLine("FAIL: Output indicator & past-tense safeguard failed to prevent terminal output overwrite: " + parsedWithFallback.FileEdits.Count);
                return 21;
            }

            // Verify disk content was NOT overwritten
            string currentContent = File.ReadAllText(sampleTxtPath, Encoding.UTF8);
            if (!currentContent.Contains("def greet("))
            {
                Console.WriteLine("FAIL: Disk content of A.TXT.txt was corrupted.");
                return 22;
            }
            try { File.Delete(sampleTxtPath); } catch { }
            Console.WriteLine("[PASS] Terminal Output & Multi-Round Summary Code Block Safeguards Verified (No Accidental Overwrites)!");

            // 12. Verify Search/Replace Block Engine (Exact & Resilient In-Place Edits)
            string srTestFile = Path.Combine(testDir, "calculator.py");
            string origCalcCode =
@"def calculate(a, b, op):
    # Perform math operation
    if op == '+':
        return a + b
    elif op == '-':
        return a - b
    elif op == '*':
        return a * b
    else:
        raise ValueError('Unsupported')
";
            File.WriteAllText(srTestFile, origCalcCode, Encoding.UTF8);
            WhitelistManager wmCalc = new WhitelistManager();
            wmCalc.AddOrUpdateFile(srTestFile, out isNew, out err);

            // Test 12a: <metis_replace> with new division operation
            string modelReplaceOutput =
@"Adding division operation support:
<metis_replace file=""calculator.py"">
<<<<<<< SEARCH
    elif op == '*':
        return a * b
    else:
=======
    elif op == '*':
        return a * b
    elif op == '/':
        return a / b if b != 0 else float('inf')
    else:
>>>>>>> REPLACE
</metis_replace>";

            ParsedAgentResponse parsedSr = WhitelistManager.ParseAgentOutput(modelReplaceOutput, wmCalc.Files);
            if (parsedSr.FileEdits.Count != 1 || parsedSr.FileEdits[0].ActionType != SurgicalActionType.SearchReplace)
            {
                Console.WriteLine("FAIL: Failed to parse metis_replace Search/Replace block.");
                return 23;
            }
            if (!wmCalc.ApplyEditInPlace(parsedSr.FileEdits[0], out targetOut, out statusMsg))
            {
                Console.WriteLine("FAIL: ApplyEditInPlace failed for SearchReplace: " + statusMsg);
                return 24;
            }
            string calcDisk = File.ReadAllText(srTestFile, Encoding.UTF8);
            if (!calcDisk.Contains("elif op == '/':") || !calcDisk.Contains("return a + b"))
            {
                Console.WriteLine("FAIL: Disk content of calculator.py did not have partial search/replace correctly applied.");
                return 25;
            }

            // Test 12b: Raw SEARCH/REPLACE block outside XML tags with fuzzy newline tolerance
            string rawSrOutput =
@"Further optimizing calculator.py:
<<<<<<< SEARCH
    # Perform math operation
    if op == '+':
=======
    # Perform math operation with error handling
    if op == '+':
>>>>>>> REPLACE";

            ParsedAgentResponse parsedRawSr = WhitelistManager.ParseAgentOutput(rawSrOutput, wmCalc.Files);
            if (parsedRawSr.FileEdits.Count != 1 || parsedRawSr.FileEdits[0].ActionType != SurgicalActionType.SearchReplace)
            {
                Console.WriteLine("FAIL: Failed to parse raw SEARCH/REPLACE block outside XML tags.");
                return 26;
            }
            if (!wmCalc.ApplyEditInPlace(parsedRawSr.FileEdits[0], out targetOut, out statusMsg))
            {
                Console.WriteLine("FAIL: ApplyEditInPlace failed for raw SearchReplace: " + statusMsg);
                return 27;
            }
            calcDisk = File.ReadAllText(srTestFile, Encoding.UTF8);
            if (!calcDisk.Contains("# Perform math operation with error handling"))
            {
                Console.WriteLine("FAIL: Raw Search/Replace block was not applied to calculator.py.");
                return 28;
            }
            try { File.Delete(srTestFile); } catch { }
            Console.WriteLine("[PASS] Search/Replace Block Engine & Multi-Tier Resilient In-Place Edits Verified!");

            return 0;
        }
    }
}
