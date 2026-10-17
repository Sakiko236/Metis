using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace Metis
{
    public static class VerificationRunner
    {
        public static async Task<VerifyExecutionResult> RunCommandAsync(
            string command,
            string workingDirectory,
            int timeoutSeconds,
            CancellationToken cancellationToken)
        {
            VerifyExecutionResult result = new VerifyExecutionResult();
            result.Command = command;
            result.WorkingDirectory = string.IsNullOrWhiteSpace(workingDirectory)
                ? Environment.CurrentDirectory
                : workingDirectory;

            Stopwatch sw = Stopwatch.StartNew();

            string violationReason;
            if (IsCommandForbidden(command, out violationReason))
            {
                result.ExitCode = 126;
                result.StdErr = violationReason;
                result.StdOut = "";
                result.DurationSeconds = 0;
                return result;
            }

            return await Task.Run(() =>
            {
                Process proc = new Process();
                MemoryStream stdoutMs = new MemoryStream();
                MemoryStream stderrMs = new MemoryStream();
                Thread stdoutThread = null;
                Thread stderrThread = null;
                string exceptionMessage = null;

                try
                {
                    string effectiveWorkDir = Directory.Exists(result.WorkingDirectory)
                        ? result.WorkingDirectory
                        : Environment.CurrentDirectory;

                    string actualCommand = PrepareCommandWithPytestFallback(command, effectiveWorkDir);

                    ProcessStartInfo psi = new ProcessStartInfo();
                    psi.FileName = "cmd.exe";
                    psi.Arguments = "/d /s /c \"" + actualCommand + "\"";
                    psi.WorkingDirectory = effectiveWorkDir;
                    psi.UseShellExecute = false;
                    psi.CreateNoWindow = true;
                    psi.RedirectStandardOutput = true;
                    psi.RedirectStandardError = true;

                    // Ensure Python and Node output UTF-8 without buffering issues
                    psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                    psi.EnvironmentVariables["PYTHONUTF8"] = "1";

                    proc.StartInfo = psi;
                    proc.Start();

                    stdoutThread = new Thread(() => ReadStreamBounded(proc.StandardOutput.BaseStream, stdoutMs, 512 * 1024));
                    stdoutThread.IsBackground = true;
                    stdoutThread.Start();

                    stderrThread = new Thread(() => ReadStreamBounded(proc.StandardError.BaseStream, stderrMs, 512 * 1024));
                    stderrThread.IsBackground = true;
                    stderrThread.Start();

                    int timeoutMs = Math.Max(5, timeoutSeconds) * 1000;
                    int elapsed = 0;
                    int pollStep = 100;

                    while (!proc.WaitForExit(pollStep))
                    {
                        elapsed += pollStep;
                        if (cancellationToken.IsCancellationRequested)
                        {
                            KillProcessTree(proc.Id);
                            result.Cancelled = true;
                            result.ExitCode = -1;
                            break;
                        }
                        if (elapsed >= timeoutMs)
                        {
                            KillProcessTree(proc.Id);
                            result.TimedOut = true;
                            result.ExitCode = -2;
                            break;
                        }
                    }

                    if (!result.Cancelled && !result.TimedOut)
                    {
                        proc.WaitForExit();
                        result.ExitCode = proc.ExitCode;
                    }

                    if (stdoutThread != null) stdoutThread.Join(1000);
                    if (stderrThread != null) stderrThread.Join(1000);
                }
                catch (Exception ex)
                {
                    exceptionMessage = "Execution error during verification: " + ex.Message;
                    result.ExitCode = -99;
                }
                finally
                {
                    sw.Stop();
                    result.DurationSeconds = sw.Elapsed.TotalSeconds;

                    byte[] outBytes;
                    byte[] errBytes;
                    lock (stdoutMs) { outBytes = stdoutMs.ToArray(); }
                    lock (stderrMs) { errBytes = stderrMs.ToArray(); }

                    result.StdOut = DecodeBytesAdaptive(outBytes);
                    string decodedErr = DecodeBytesAdaptive(errBytes);
                    if (!string.IsNullOrEmpty(exceptionMessage))
                    {
                        result.StdErr = string.IsNullOrEmpty(decodedErr)
                            ? exceptionMessage
                            : exceptionMessage + Environment.NewLine + decodedErr;
                    }
                    else
                    {
                        result.StdErr = decodedErr;
                    }

                    try { stdoutMs.Dispose(); } catch { }
                    try { stderrMs.Dispose(); } catch { }
                    try { proc.Dispose(); } catch { }
                }

                return result;
            });
        }

        private static string PrepareCommandWithPytestFallback(string command, string workingDirectory)
        {
            if (string.IsNullOrWhiteSpace(command)) return command;
            string trimmed = command.Trim();

            if (trimmed.Equals("pytest", StringComparison.OrdinalIgnoreCase) ||
                trimmed.StartsWith("pytest ", StringComparison.OrdinalIgnoreCase))
            {
                if (!IsPytestModuleInstalled())
                {
                    // Provide built-in zero-dependency pytest-compatible runner using standard Python
                    string runnerPy =
                        "import sys, os, glob, importlib.util, traceback; " +
                        "sys.path.insert(0, os.getcwd()); " +
                        "files = sorted(set(glob.glob('test_*.py') + glob.glob('*_test.py'))); " +
                        "passed, failed = 0, 0; " +
                        "print('============================= test session starts ============================='); " +
                        "print('platform ' + sys.platform + ' -- Python ' + sys.version.split()[0] + ' (Metis built-in pytest runner)'); " +
                        "\nfor f in files:\n" +
                        "    mod_name = os.path.splitext(os.path.basename(f))[0]\n" +
                        "    spec = importlib.util.spec_from_file_location(mod_name, f)\n" +
                        "    mod = importlib.util.module_from_spec(spec)\n" +
                        "    spec.loader.exec_module(mod)\n" +
                        "    for name in [n for n in dir(mod) if n.startswith('test_') and callable(getattr(mod, n))]:\n" +
                        "        try:\n" +
                        "            getattr(mod, name)()\n" +
                        "            print(f + '::' + name + ' PASSED')\n" +
                        "            passed += 1\n" +
                        "        except Exception as e:\n" +
                        "            print(f + '::' + name + ' FAILED')\n" +
                        "            traceback.print_exc()\n" +
                        "            failed += 1\n" +
                        "if not files:\n" +
                        "    for pyf in sorted(glob.glob('*.py')):\n" +
                        "        mod_name = os.path.splitext(os.path.basename(pyf))[0]\n" +
                        "        spec = importlib.util.spec_from_file_location(mod_name, pyf)\n" +
                        "        mod = importlib.util.module_from_spec(spec)\n" +
                        "        spec.loader.exec_module(mod)\n" +
                        "        for name in [n for n in dir(mod) if n.startswith('test_') and callable(getattr(mod, n))]:\n" +
                        "            try:\n" +
                        "                getattr(mod, name)()\n" +
                        "                print(pyf + '::' + name + ' PASSED')\n" +
                        "                passed += 1\n" +
                        "            except Exception as e:\n" +
                        "                print(pyf + '::' + name + ' FAILED')\n" +
                        "                traceback.print_exc()\n" +
                        "                failed += 1\n" +
                        "print('=========================== ' + str(passed) + ' passed, ' + str(failed) + ' failed ==========================='); " +
                        "sys.exit(1 if failed > 0 else 0)";

                    string b64 = Convert.ToBase64String(Encoding.UTF8.GetBytes(runnerPy));
                    return "python -c \"import base64; exec(base64.b64decode('" + b64 + "').decode('utf-8'))\"";
                }
                else
                {
                    return "python -m " + trimmed;
                }
            }

            return command;
        }

        private static bool? _pytestInstalledCache;
        private static bool IsPytestModuleInstalled()
        {
            if (_pytestInstalledCache.HasValue) return _pytestInstalledCache.Value;
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = "python";
                psi.Arguments = "-m pytest --version";
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                using (Process p = Process.Start(psi))
                {
                    if (p != null)
                    {
                        p.WaitForExit(2500);
                        _pytestInstalledCache = (p.ExitCode == 0);
                        return _pytestInstalledCache.Value;
                    }
                }
            }
            catch { }
            _pytestInstalledCache = false;
            return false;
        }

        private static void KillProcessTree(int pid)
        {
            try
            {
                ProcessStartInfo killPsi = new ProcessStartInfo();
                killPsi.FileName = "taskkill";
                killPsi.Arguments = "/F /T /PID " + pid;
                killPsi.UseShellExecute = false;
                killPsi.CreateNoWindow = true;
                using (Process kp = Process.Start(killPsi))
                {
                    if (kp != null) kp.WaitForExit(2000);
                }
            }
            catch { }
        }

        private static readonly Encoding StrictUtf8 = new UTF8Encoding(false, true);

        private static void ReadStreamBounded(Stream stream, MemoryStream destination, int maxBytes)
        {
            byte[] buffer = new byte[4096];
            int read;
            try
            {
                while ((read = stream.Read(buffer, 0, buffer.Length)) > 0)
                {
                    lock (destination)
                    {
                        if (destination.Length < maxBytes)
                        {
                            int toWrite = Math.Min(read, maxBytes - (int)destination.Length);
                            destination.Write(buffer, 0, toWrite);
                        }
                    }
                }
            }
            catch
            {
                // Stream was closed or child process terminated
            }
        }

        public static string DecodeBytesAdaptive(byte[] buffer)
        {
            if (buffer == null || buffer.Length == 0)
            {
                return string.Empty;
            }

            int count = buffer.Length;

            // 1. Detect standard BOMs
            if (count >= 3 && buffer[0] == 0xEF && buffer[1] == 0xBB && buffer[2] == 0xBF)
            {
                return TruncateOutput(Encoding.UTF8.GetString(buffer, 3, count - 3));
            }
            if (count >= 2 && buffer[0] == 0xFF && buffer[1] == 0xFE)
            {
                return TruncateOutput(Encoding.Unicode.GetString(buffer, 2, count - 2));
            }
            if (count >= 2 && buffer[0] == 0xFE && buffer[1] == 0xFF)
            {
                return TruncateOutput(Encoding.BigEndianUnicode.GetString(buffer, 2, count - 2));
            }

            // 2. Line-by-line adaptive decoding.
            // In both UTF-8 and GBK/CP936, byte 0x0A ('\n') is never a continuation or second byte.
            // Splitting on 0x0A guarantees chunk boundaries will not split a multi-byte character.
            StringBuilder sb = new StringBuilder();
            int lineStart = 0;

            for (int i = 0; i < count; i++)
            {
                if (buffer[i] == 0x0A)
                {
                    int lineLen = (i + 1) - lineStart;
                    sb.Append(DecodeChunk(buffer, lineStart, lineLen));
                    lineStart = i + 1;
                }
            }

            if (lineStart < count)
            {
                sb.Append(DecodeChunk(buffer, lineStart, count - lineStart));
            }

            return TruncateOutput(sb.ToString());
        }

        private static string DecodeChunk(byte[] buffer, int offset, int count)
        {
            if (count <= 0) return string.Empty;

            // First try strict UTF-8
            try
            {
                string utf8Str = StrictUtf8.GetString(buffer, offset, count);
                // If it decoded as UTF-8, but contains high Latin/Greek characters (U+0080..U+07FF)
                // and no CJK characters, check if CP936 yields valid CJK characters instead.
                if (ContainsSuspiciousLatinGreek(utf8Str))
                {
                    try
                    {
                        string gbkStr = Encoding.GetEncoding(936).GetString(buffer, offset, count);
                        if (ContainsCjk(gbkStr))
                        {
                            return gbkStr;
                        }
                    }
                    catch { }
                }
                return utf8Str;
            }
            catch (DecoderFallbackException)
            {
                // Not valid UTF-8, fall back to GBK (CP936) or system default ANSI
                try
                {
                    return Encoding.GetEncoding(936).GetString(buffer, offset, count);
                }
                catch
                {
                    return Encoding.Default.GetString(buffer, offset, count);
                }
            }
        }

        private static bool ContainsSuspiciousLatinGreek(string s)
        {
            bool hasLatinGreek = false;
            for (int i = 0; i < s.Length; i++)
            {
                char c = s[i];
                if (c >= '\u4E00' && c <= '\u9FFF')
                {
                    return false;
                }
                if (c >= '\u0080' && c <= '\u07FF')
                {
                    hasLatinGreek = true;
                }
            }
            return hasLatinGreek;
        }

        private static bool ContainsCjk(string s)
        {
            for (int i = 0; i < s.Length; i++)
            {
                char c = s[i];
                if (c >= '\u4E00' && c <= '\u9FFF')
                {
                    return true;
                }
            }
            return false;
        }

        private static string TruncateOutput(string text)
        {
            if (string.IsNullOrEmpty(text)) return string.Empty;
            if (text.Length > 64000)
            {
                return text.Substring(0, 64000) + "\r\n... [Output Truncated]";
            }
            return text;
        }

        /// <summary>
        /// Extracts a verification command mentioned conversationally by the user
        /// (e.g. "run pytest for me", "run `python auth.py` to verify") if the LLM omitted the verify command.
        /// </summary>
        public static string ExtractConversationalVerifyCommand(string userMessage, IList<WhitelistedFile> whitelist)
        {
            if (string.IsNullOrWhiteSpace(userMessage)) return "";
            if (userMessage.StartsWith("[Terminal Execution Feedback") || userMessage.StartsWith("【系统终端执行结果反馈") ||
                userMessage.StartsWith("[Auto Verification Feedback") || userMessage.StartsWith("【自动验证闭环反馈"))
            {
                return "";
            }

            // 1. Check backticked command after execution keywords: run/test/exec/verify/check ... `cmd`
            Regex backtickRegex = new Regex(@"(?:run|test|exec|verify|check|terminal|跑一下|跑个|运行|执行|测试|验证|终端执行)[^`]*`([^`\r\n]+)`", RegexOptions.IgnoreCase);
            Match bm = backtickRegex.Match(userMessage);
            if (bm.Success)
            {
                string cand = bm.Groups[1].Value.Trim();
                if (LooksLikeVerifyCommand(cand)) return cand;
            }

            // 2. Check explicit common CLI commands mentioned in the prompt
            Regex cmdRegex = new Regex(@"(?:run|test|verify|check|exec|跑一下|跑个|运行|执行|验证)\s*((?:pytest|python\s+[^\s,，。]+|py\s+[^\s,，。]+|node\s+[^\s,，。]+|npm\s+(?:test|run\s+[^\s,，。]+)|cargo\s+(?:test|run|check)|go\s+(?:test|run\s+[^\s,，。]+)|dotnet\s+(?:test|run|build)|git\s+(?:status|diff|log|branch)|dir|ls|pip\s+list)(?:\s+-[a-zA-Z0-9_-]+|\s+[a-zA-Z0-9_./\\-]+)*)", RegexOptions.IgnoreCase);
            Match cm = cmdRegex.Match(userMessage);
            if (cm.Success)
            {
                string cand = cm.Groups[1].Value.Trim();
                cand = Regex.Replace(cand, @"(看看|试试|一下|吧|呢|please)+$", "").Trim();
                if (LooksLikeVerifyCommand(cand)) return cand;
            }

            // 3. Standalone "pytest" mention anywhere in user message
            if (Regex.IsMatch(userMessage, @"\bpytest\b", RegexOptions.IgnoreCase))
            {
                return "pytest";
            }

            return "";
        }

        private static bool IsCommandForbidden(string cmd, out string reason)
        {
            reason = null;
            if (string.IsNullOrWhiteSpace(cmd)) return false;

            string lower = cmd.ToLowerInvariant();

            // Detect file deletion attempts
            string[] deleteTokens = new string[] {
                "rm ", "rmdir", "del ", "erase ", "remove-item", "unlink", "shred", "rd /", "rd "
            };
            foreach (string dt in deleteTokens)
            {
                if (lower.Contains(dt))
                {
                    reason = "[Security Block - File Deletion Prohibited] Detected file/directory deletion instruction ('" + dt.Trim() + "'). Metis strictly forbids deleting files!";
                    return true;
                }
            }

            // Detect new file creation attempts via shell redirection or touch/New-Item
            string[] createTokens = new string[] {
                "touch ", "new-item ", "type nul >", "echo. >", "fsutil file createnew"
            };
            foreach (string ct in createTokens)
            {
                if (lower.Contains(ct))
                {
                    reason = "[Security Block - File Creation Prohibited] Detected file creation instruction ('" + ct.Trim() + "'). Metis strictly forbids creating new files!";
                    return true;
                }
            }

            return false;
        }

        private static bool LooksLikeVerifyCommand(string cmd)
        {
            if (string.IsNullOrWhiteSpace(cmd)) return false;
            string lower = cmd.ToLowerInvariant().Trim();
            if (lower == "dir" || lower == "ls" || lower == "pwd" || lower == "whoami" || lower == "ver")
            {
                return true;
            }
            return lower.StartsWith("pytest") ||
                   lower.StartsWith("python ") ||
                   lower.StartsWith("py ") ||
                   lower.StartsWith("pip ") ||
                   lower.StartsWith("node ") ||
                   lower.StartsWith("npm ") ||
                   lower.StartsWith("npx ") ||
                   lower.StartsWith("cargo ") ||
                   lower.StartsWith("go ") ||
                   lower.StartsWith("dotnet ") ||
                   lower.StartsWith("git ") ||
                   lower.StartsWith("dir ") ||
                   lower.StartsWith("ls ") ||
                   lower.StartsWith("type ") ||
                   lower.StartsWith("cat ") ||
                   lower.StartsWith("echo ") ||
                   lower.StartsWith("powershell ") ||
                   lower.StartsWith("cmd ") ||
                   lower.StartsWith("gcc ") ||
                   lower.StartsWith("g++ ") ||
                   lower.StartsWith("javac ") ||
                   lower.StartsWith("java ");
        }
    }
}
