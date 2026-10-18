using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Shell;
using Microsoft.Win32;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;

namespace Metis
{
    public class MainWindow : Window
    {
        private AppConfig _config;
        private SessionStoreData _sessionStore;
        private readonly WhitelistManager _whitelist = new WhitelistManager();
        private WebView2 _webView;
        private CancellationTokenSource _cts;
        private bool _isBusy = false;
        private string _statusText = "Ready";
        private readonly bool _headlessMode;

        [DllImport("user32.dll")]
        private static extern bool ReleaseCapture();

        [DllImport("user32.dll")]
        private static extern IntPtr SendMessage(IntPtr hWnd, int Msg, IntPtr wParam, IntPtr lParam);

        [DllImport("dwmapi.dll")]
        private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int attrValue, int attrSize);

        [DllImport("dwmapi.dll")]
        private static extern int DwmExtendFrameIntoClientArea(IntPtr hWnd, ref MARGINS pMarInset);

        [StructLayout(LayoutKind.Sequential)]
        private struct MARGINS
        {
            public int cxLeftWidth;
            public int cxRightWidth;
            public int cyTopHeight;
            public int cyBottomHeight;
        }

        private const int WM_NCLBUTTONDOWN = 0xA1;
        private const int HTCAPTION = 0x2;

        public MainWindow() : this(false)
        {
        }

        public MainWindow(bool headlessMode)
        {
            _headlessMode = headlessMode;
            _config = ConfigManager.LoadOrCreateDefault();
            _sessionStore = SessionManager.LoadOrCreate();
            BindActiveSessionWhitelist();

            Title = "Metis";
            try
            {
                using (Stream iconStream = Assembly.GetExecutingAssembly().GetManifestResourceStream("Metis.ico"))
                {
                    if (iconStream != null)
                    {
                        Icon = BitmapFrame.Create(iconStream);
                    }
                }
            }
            catch { }

            Width = 1320;
            Height = 860;
            MinWidth = 980;
            MinHeight = 640;
            WindowStartupLocation = WindowStartupLocation.CenterScreen;
            WindowStyle = WindowStyle.None;
            ResizeMode = ResizeMode.CanResize;
            ApplyThemeToWindow(_config != null ? _config.Theme : "light");

            if (!_headlessMode)
            {
                var chrome = new WindowChrome
                {
                    CaptionHeight = 0,
                    ResizeBorderThickness = new Thickness(6),
                    GlassFrameThickness = new Thickness(1),
                    CornerRadius = new CornerRadius(10),
                    UseAeroCaptionButtons = false
                };
                WindowChrome.SetWindowChrome(this, chrome);
                StateChanged += OnWindowStateChanged;

                _webView = new WebView2();
                ApplyThemeToWindow(_config != null ? _config.Theme : "light");
                Content = _webView;
                Loaded += OnWindowLoaded;
            }
        }

        protected override void OnSourceInitialized(EventArgs e)
        {
            base.OnSourceInitialized(e);
            try
            {
                IntPtr handle = new WindowInteropHelper(this).Handle;
                MARGINS margins = new MARGINS { cxLeftWidth = 1, cxRightWidth = 1, cyTopHeight = 1, cyBottomHeight = 1 };
                DwmExtendFrameIntoClientArea(handle, ref margins);

                // DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2 (Windows 11 hardware-accelerated rounded corners)
                int cornerPreference = 2;
                DwmSetWindowAttribute(handle, 33, ref cornerPreference, sizeof(int));
            }
            catch { }
        }

        private void ApplyThemeToWindow(string theme)
        {
            bool isLight = string.Equals(theme, "light", StringComparison.OrdinalIgnoreCase);
            Color bg = isLight ? Color.FromRgb(255, 255, 255) : Color.FromRgb(9, 10, 15);
            Background = new SolidColorBrush(bg);
            if (_webView != null)
            {
                _webView.DefaultBackgroundColor = System.Drawing.Color.FromArgb(255, bg.R, bg.G, bg.B);
            }
        }

        private void OnWindowStateChanged(object sender, EventArgs e)
        {
            Dictionary<string, object> msg = new Dictionary<string, object>();
            msg["type"] = "windowState";
            msg["isMaximized"] = (WindowState == WindowState.Maximized);
            PostToWeb(msg);
        }

        private ChatSession GetActiveSession()
        {
            if (_sessionStore.Sessions == null || _sessionStore.Sessions.Count == 0)
            {
                ChatSession s = new ChatSession();
                s.Title = "Default Session";
                _sessionStore.Sessions = new List<ChatSession> { s };
                _sessionStore.ActiveSessionId = s.Id;
                SessionManager.Save(_sessionStore);
            }

            foreach (ChatSession s in _sessionStore.Sessions)
            {
                if (string.Equals(s.Id, _sessionStore.ActiveSessionId, StringComparison.OrdinalIgnoreCase))
                {
                    return s;
                }
            }

            _sessionStore.ActiveSessionId = _sessionStore.Sessions[0].Id;
            return _sessionStore.Sessions[0];
        }

        private void BindActiveSessionWhitelist()
        {
            ChatSession active = GetActiveSession();
            if (active.Whitelist == null) active.Whitelist = new List<WhitelistedFile>();
            if (active.AllowedDirectories == null) active.AllowedDirectories = new List<string>();
            _whitelist.BindSessionList(active.Whitelist, active.AllowedDirectories);
        }

        private VendorProfile GetActiveVendor()
        {
            foreach (VendorProfile vp in _config.Vendors)
            {
                if (string.Equals(vp.Id, _config.ActiveVendorId, StringComparison.OrdinalIgnoreCase))
                {
                    return vp;
                }
            }
            return _config.Vendors[0];
        }

        private async void OnWindowLoaded(object sender, RoutedEventArgs e)
        {
            try
            {
                string runtimeDir = Program.EnsureRuntimeAssetsExtracted();
                CoreWebView2Environment.SetLoaderDllFolderPath(runtimeDir);

                string userDataDir = Path.Combine(ConfigManager.GetAppDataDir(), "WebView2Data");
                CoreWebView2Environment env = await CoreWebView2Environment.CreateAsync(null, userDataDir);
                await _webView.EnsureCoreWebView2Async(env);

                _webView.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
                _webView.CoreWebView2.Settings.IsStatusBarEnabled = false;
                _webView.CoreWebView2.WebMessageReceived += OnWebMessageReceived;

                _webView.CoreWebView2.SetVirtualHostNameToFolderMapping(
                    "metis.local",
                    runtimeDir,
                    CoreWebView2HostResourceAccessKind.Allow);

                _webView.CoreWebView2.Navigate("https://metis.local/app.html?v=" + DateTime.Now.Ticks);
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    this,
                    "Failed to initialize WebView2 host environment: " + ex.Message,
                    "Metis Startup Error",
                    MessageBoxButton.OK,
                    MessageBoxImage.Error);
            }
        }

        private void PostToWeb(object payload)
        {
            if (_webView == null) return;
            if (!Dispatcher.CheckAccess())
            {
                Dispatcher.BeginInvoke(new Action(() => PostToWeb(payload)));
                return;
            }
            if (_webView.CoreWebView2 == null) return;
            try
            {
                JavaScriptSerializer ser = new JavaScriptSerializer();
                ser.MaxJsonLength = int.MaxValue;
                string json = ser.Serialize(payload);
                _webView.CoreWebView2.PostWebMessageAsJson(json);
            }
            catch { }
        }

        private void BroadcastFullState(string toast = null, string prefillPrompt = null)
        {
            if (_webView != null && !Dispatcher.CheckAccess())
            {
                Dispatcher.BeginInvoke(new Action(() => BroadcastFullState(toast, prefillPrompt)));
                return;
            }

            BindActiveSessionWhitelist();
            SessionManager.Save(_sessionStore);

            Dictionary<string, object> msg = new Dictionary<string, object>();
            msg["type"] = "fullState";
            msg["config"] = _config;
            msg["sessions"] = _sessionStore.Sessions;
            msg["activeSessionId"] = _sessionStore.ActiveSessionId;
            msg["activeWorkspaceDir"] = _whitelist.GetActiveWorkspaceDirectory();
            msg["isBusy"] = _isBusy;
            msg["statusText"] = _statusText;
            msg["configFilePath"] = ConfigManager.GetConfigPath();
            msg["isMaximized"] = (WindowState == WindowState.Maximized);
            if (!string.IsNullOrEmpty(toast)) msg["toast"] = toast;
            if (!string.IsNullOrEmpty(prefillPrompt)) msg["prefillPrompt"] = prefillPrompt;

            PostToWeb(msg);
        }

        private void UpdateBusyState(bool busy, string statusText)
        {
            _isBusy = busy;
            _statusText = statusText;
            Dictionary<string, object> msg = new Dictionary<string, object>();
            msg["type"] = "busyStatus";
            msg["isBusy"] = _isBusy;
            msg["statusText"] = _statusText;
            PostToWeb(msg);
        }

        private void SendStreamingEventUpdate(string eventId, string displayContent)
        {
            Dictionary<string, object> msg = new Dictionary<string, object>();
            msg["type"] = "streamUpdate";
            msg["eventId"] = eventId;
            msg["content"] = displayContent;
            PostToWeb(msg);
        }

        private async void OnWebMessageReceived(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            try
            {
                string raw = null;
                try { raw = e.TryGetWebMessageAsString(); } catch { }
                if (string.IsNullOrEmpty(raw))
                {
                    raw = e.WebMessageAsJson;
                }

                JavaScriptSerializer ser = new JavaScriptSerializer();
                ser.MaxJsonLength = int.MaxValue;
                Dictionary<string, object> data = ser.Deserialize<Dictionary<string, object>>(raw);
                if (data == null || !data.ContainsKey("action")) return;

                string action = data["action"] != null ? data["action"].ToString() : "";

                if (action == "uiReady")
                {
                    BroadcastFullState();
                }
                else if (action == "createSession")
                {
                    ChatSession ns = new ChatSession();
                    ns.Title = "Session " + (_sessionStore.Sessions.Count + 1);
                    _sessionStore.Sessions.Insert(0, ns);
                    _sessionStore.ActiveSessionId = ns.Id;
                    BroadcastFullState("New session created.");
                }
                else if (action == "selectSession")
                {
                    string sid = data.ContainsKey("sessionId") && data["sessionId"] != null ? data["sessionId"].ToString() : "";
                    if (!string.IsNullOrEmpty(sid))
                    {
                        _sessionStore.ActiveSessionId = sid;
                        BroadcastFullState();
                    }
                }
                else if (action == "renameSession")
                {
                    string sid = data.ContainsKey("sessionId") && data["sessionId"] != null ? data["sessionId"].ToString() : "";
                    string title = data.ContainsKey("title") && data["title"] != null ? data["title"].ToString() : "";
                    foreach (ChatSession s in _sessionStore.Sessions)
                    {
                        if (s.Id == sid && !string.IsNullOrWhiteSpace(title))
                        {
                            s.Title = title.Trim();
                            s.UpdatedAt = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
                            break;
                        }
                    }
                    BroadcastFullState("Session renamed.");
                }
                else if (action == "deleteSession")
                {
                    string sid = data.ContainsKey("sessionId") && data["sessionId"] != null ? data["sessionId"].ToString() : "";
                    for (int i = 0; i < _sessionStore.Sessions.Count; i++)
                    {
                        if (_sessionStore.Sessions[i].Id == sid)
                        {
                            _sessionStore.Sessions.RemoveAt(i);
                            break;
                        }
                    }
                    if (_sessionStore.Sessions.Count == 0)
                    {
                        ChatSession fallback = new ChatSession { Title = "Default Session" };
                        _sessionStore.Sessions.Add(fallback);
                    }
                    if (_sessionStore.ActiveSessionId == sid)
                    {
                        _sessionStore.ActiveSessionId = _sessionStore.Sessions[0].Id;
                    }
                    BroadcastFullState("Session deleted.");
                }
                else if (action == "saveConfig")
                {
                    if (data.ContainsKey("config") && data["config"] != null)
                    {
                        string cfgJson = ser.Serialize(data["config"]);
                        AppConfig updated = ser.Deserialize<AppConfig>(cfgJson);
                        if (updated != null && updated.Vendors != null && updated.Vendors.Count > 0)
                        {
                            _config = updated;
                            ConfigManager.Save(_config);
                            ApplyThemeToWindow(_config.Theme);
                            BroadcastFullState("Settings saved.");
                        }
                    }
                }
                else if (action == "setTheme")
                {
                    string theme = data.ContainsKey("theme") && data["theme"] != null ? data["theme"].ToString() : "dark";
                    _config.Theme = theme;
                    ConfigManager.Save(_config);
                    ApplyThemeToWindow(theme);
                    BroadcastFullState();
                }
                else if (action == "setThinkingIntensity")
                {
                    string intensity = data.ContainsKey("intensity") && data["intensity"] != null ? data["intensity"].ToString() : "medium";
                    _config.ThinkingIntensity = intensity;
                    ConfigManager.Save(_config);
                    BroadcastFullState();
                }
                else if (action == "windowMin")
                {
                    WindowState = WindowState.Minimized;
                }
                else if (action == "windowMax" || action == "windowToggleMaximize")
                {
                    WindowState = (WindowState == WindowState.Maximized) ? WindowState.Normal : WindowState.Maximized;
                }
                else if (action == "windowClose")
                {
                    Close();
                }
                else if (action == "windowDrag")
                {
                    try
                    {
                        IntPtr handle = new WindowInteropHelper(this).Handle;
                        ReleaseCapture();
                        SendMessage(handle, WM_NCLBUTTONDOWN, (IntPtr)HTCAPTION, IntPtr.Zero);
                    }
                    catch { }
                }
                else if (action == "windowResize")
                {
                    try
                    {
                        string edge = data.ContainsKey("edge") && data["edge"] != null ? data["edge"].ToString().ToLowerInvariant() : "";
                        IntPtr handle = new WindowInteropHelper(this).Handle;
                        ReleaseCapture();
                        int ht = 0;
                        switch (edge)
                        {
                            case "left": ht = 10; break;
                            case "right": ht = 11; break;
                            case "top": ht = 12; break;
                            case "topleft": ht = 13; break;
                            case "topright": ht = 14; break;
                            case "bottom": ht = 15; break;
                            case "bottomleft": ht = 16; break;
                            case "bottomright": ht = 17; break;
                        }
                        if (ht > 0)
                        {
                            SendMessage(handle, WM_NCLBUTTONDOWN, (IntPtr)ht, IntPtr.Zero);
                        }
                    }
                    catch { }
                }
                else if (action == "probeOllama")
                {
                    string baseUrl = data.ContainsKey("baseUrl") && data["baseUrl"] != null ? data["baseUrl"].ToString() : "";
                    List<string> models = await MultiVendorClient.FetchOllamaModelsAsync(baseUrl);
                    Dictionary<string, object> resp = new Dictionary<string, object>();
                    resp["type"] = "ollamaModels";
                    resp["models"] = models;
                    resp["toast"] = models.Count > 0
                        ? string.Format("Detected {0} local Ollama models.", models.Count)
                        : "No Ollama service detected. Ensure 'ollama serve' is running.";
                    PostToWeb(resp);
                }
                else if (action == "pickFiles")
                {
                    OpenFileDialog dlg = new OpenFileDialog();
                    dlg.Title = "Select files to attach (code, scripts, PDF documents, images)";
                    dlg.Multiselect = true;
                    dlg.Filter = "All Supported Files (*.*)|*.*|Documents & Images (*.pdf;*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp)|*.pdf;*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp|Python (*.py)|*.py|JavaScript/TypeScript (*.js;*.ts)|*.js;*.ts|C#/C++/Rust/Go (*.cs;*.cpp;*.rs;*.go)|*.cs;*.cpp;*.rs;*.go";
                    if (dlg.ShowDialog(this) == true)
                    {
                        BindFilesToActiveSession(dlg.FileNames);
                    }
                }
                else if (action == "pickMedia")
                {
                    OpenFileDialog dlg = new OpenFileDialog();
                    dlg.Title = "Select PDF document or image for multimodal analysis";
                    dlg.Multiselect = true;
                    dlg.Filter = "Documents & Images (*.pdf;*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp)|*.pdf;*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp|PDF Documents (*.pdf)|*.pdf|Image Files (*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp)|*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp|All Files (*.*)|*.*";
                    if (dlg.ShowDialog(this) == true)
                    {
                        BindFilesToActiveSession(dlg.FileNames);
                    }
                }
                else if (action == "pasteImage")
                {
                    string base64Data = data.ContainsKey("base64") && data["base64"] != null ? data["base64"].ToString() : "";
                    try
                    {
                        string savedPath = MediaAttachmentHelper.SavePastedImageToDisk(base64Data);
                        if (!string.IsNullOrEmpty(savedPath))
                        {
                            BindFilesToActiveSession(new string[] { savedPath });
                        }
                    }
                    catch (Exception pex)
                    {
                        AddSessionSecurityEvent("Failed to paste image: " + pex.Message);
                        BroadcastFullState("Failed to paste image");
                    }
                }
                else if (action == "pickFolder")
                {
                    string selectedFolder = PickFolderDialog(this, "Select workspace folder to mount");
                    if (!string.IsNullOrWhiteSpace(selectedFolder))
                    {
                        BindFilesToActiveSession(new string[] { selectedFolder });
                    }
                }
                else if (action == "dropFiles")
                {
                    List<string> dropped = new List<string>();
                    if (e.AdditionalObjects != null)
                    {
                        foreach (object obj in e.AdditionalObjects)
                        {
                            CoreWebView2File file = obj as CoreWebView2File;
                            if (file != null && !string.IsNullOrEmpty(file.Path))
                            {
                                dropped.Add(file.Path);
                            }
                        }
                    }
                    if (dropped.Count > 0)
                    {
                        BindFilesToActiveSession(dropped);
                    }
                }
                else if (action == "removeDirectory")
                {
                    string dirPath = data.ContainsKey("path") && data["path"] != null ? data["path"].ToString() : "";
                    _whitelist.RemoveDirectory(dirPath);
                    BroadcastFullState("Unmounted folder and its files.");
                }
                else if (action == "refreshWorkspace")
                {
                    int added = _whitelist.RefreshWorkspaceDirectories();
                    BroadcastFullState(added > 0 ? string.Format("Workspace refreshed, discovered {0} new files.", added) : "Workspace files are up to date.");
                }
                else if (action == "removeFile")
                {
                    string path = data.ContainsKey("path") && data["path"] != null ? data["path"].ToString() : "";
                    _whitelist.RemoveFile(path);
                    BroadcastFullState("Removed file from current session.");
                }
                else if (action == "clearWhitelist")
                {
                    _whitelist.Clear();
                    BroadcastFullState("Cleared all files and folders in current session.");
                }
                else if (action == "inspectFile")
                {
                    string path = data.ContainsKey("path") && data["path"] != null ? data["path"].ToString() : "";
                    WhitelistedFile wf = _whitelist.ResolveWhitelistedFile(path);
                    if (wf != null)
                    {
                        Dictionary<string, object> preview = new Dictionary<string, object>();
                        preview["type"] = "fileContentPreview";
                        preview["fileName"] = wf.FileName;
                        preview["fullPath"] = wf.FullPath;
                        preview["editCount"] = wf.EditCount;
                        preview["mediaKind"] = wf.MediaKind ?? "code";
                        preview["mimeType"] = wf.MimeType ?? "text/plain";
                        preview["isBinaryMedia"] = wf.IsBinaryMedia;
                        preview["content"] = wf.ReadCurrentContent();
                        if (string.Equals(wf.MediaKind, "image", StringComparison.OrdinalIgnoreCase))
                        {
                            string outMime;
                            string b64 = MediaAttachmentHelper.GetBase64ForApi(wf.FullPath, out outMime);
                            if (!string.IsNullOrEmpty(b64))
                            {
                                preview["dataUrl"] = "data:" + outMime + ";base64," + b64;
                            }
                        }
                        PostToWeb(preview);
                    }
                }
                else if (action == "revertFile")
                {
                    string path = data.ContainsKey("path") && data["path"] != null ? data["path"].ToString() : "";
                    WhitelistedFile wf = _whitelist.ResolveWhitelistedFile(path);
                    string msg;
                    if (_whitelist.RevertFileToInitial(wf, out msg))
                    {
                        AddSessionNoticeEvent("↩️ " + msg);
                        BroadcastFullState(msg);
                    }
                }
                else if (action == "revealFile")
                {
                    string path = data.ContainsKey("path") && data["path"] != null ? data["path"].ToString() : "";
                    if (!string.IsNullOrEmpty(path))
                    {
                        if (File.Exists(path))
                        {
                            try { Process.Start("explorer.exe", "/select,\"" + path + "\""); } catch { }
                        }
                        else if (Directory.Exists(path))
                        {
                            try { Process.Start("explorer.exe", "\"" + path + "\""); } catch { }
                        }
                    }
                }
                else if (action == "sendPrompt")
                {
                    string prompt = data.ContainsKey("prompt") && data["prompt"] != null ? data["prompt"].ToString() : "";
                    if (!string.IsNullOrWhiteSpace(prompt))
                    {
                        await ExecuteAutonomousLoopAsync(prompt.Trim());
                    }
                }
                else if (action == "pauseLoop")
                {
                    if (_isBusy && !_isPaused)
                    {
                        _isPaused = true;
                        UpdateBusyState(true, "⏸ Pause requested — execution will pause after the current step (model response/command)");
                    }
                }
                else if (action == "resumeLoop")
                {
                    if (_isPaused)
                    {
                        _isPaused = false;
                    }
                }
                else if (action == "stopLoop")
                {
                    _isPaused = false;
                    if (_cts != null && !_cts.IsCancellationRequested)
                    {
                        _cts.Cancel();
                    }
                }
            }
            catch (Exception ex)
            {
                AddSessionSecurityEvent("Operation exception: " + ex.Message);
                BroadcastFullState();
            }
        }

        private void BindFilesToActiveSession(IEnumerable<string> paths)
        {
            BindActiveSessionWhitelist();
            int addedFiles = 0;
            int addedFolders = 0;

            foreach (string p in paths)
            {
                if (string.IsNullOrWhiteSpace(p)) continue;

                if (Directory.Exists(p))
                {
                    int loadedCount;
                    string dirErr;
                    if (_whitelist.AddDirectory(p, out loadedCount, out dirErr))
                    {
                        addedFolders++;
                        addedFiles += loadedCount;
                        AddSessionNoticeEvent(string.Format("📁 Folder mounted: {0} ({1} files)", Path.GetFileName(p.TrimEnd('\\', '/')), loadedCount));
                    }
                    else if (!string.IsNullOrEmpty(dirErr))
                    {
                        AddSessionSecurityEvent(dirErr);
                    }
                }
                else
                {
                    bool isNew;
                    string err;
                    WhitelistedFile wf = _whitelist.AddOrUpdateFile(p, out isNew, out err);
                    if (wf != null)
                    {
                        addedFiles++;
                        if (isNew)
                        {
                            if (string.Equals(wf.MediaKind, "pdf", StringComparison.OrdinalIgnoreCase))
                            {
                                AddSessionNoticeEvent(string.Format("📄 Attached PDF: {0}", wf.FileName));
                            }
                            else if (string.Equals(wf.MediaKind, "image", StringComparison.OrdinalIgnoreCase))
                            {
                                AddSessionNoticeEvent(string.Format("🖼️ Attached image: {0}", wf.FileName));
                            }
                            else
                            {
                                AddSessionNoticeEvent(string.Format("📄 Attached file: {0}", wf.FileName));
                            }
                        }
                    }
                    else if (!string.IsNullOrEmpty(err))
                    {
                        AddSessionSecurityEvent(err);
                    }
                }
            }

            string toast = null;
            if (addedFolders > 0)
            {
                toast = string.Format("Mounted {0} folder(s) ({1} files)", addedFolders, addedFiles);
            }
            else if (addedFiles > 0)
            {
                toast = string.Format("Attached {0} file(s)", addedFiles);
            }
            BroadcastFullState(toast);
        }

        private volatile bool _isPaused;

        private async Task WaitIfPausedAsync(CancellationToken token)
        {
            if (!_isPaused) return;
            UpdateBusyState(true, "Paused");
            while (_isPaused)
            {
                token.ThrowIfCancellationRequested();
                await Task.Delay(150, token);
            }
            token.ThrowIfCancellationRequested();
            UpdateBusyState(true, "Resuming execution...");
        }

        private async Task ExecuteAutonomousLoopAsync(string userPrompt)
        {
            if (_isBusy) return;

            ChatSession session = GetActiveSession();
            BindActiveSessionWhitelist();

            if (session.ApiHistory.Count == 0 && (session.Title == "Default Session" || session.Title == "默认会话" ||
                session.Title.StartsWith("Session ") || session.Title.StartsWith("会话 ") ||
                session.Title == "New Session" || session.Title == "新建会话"))
            {
                session.Title = userPrompt.Length > 18 ? userPrompt.Substring(0, 18) + "..." : userPrompt;
            }
            session.UpdatedAt = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");

            session.UiEvents.Add(new UiStreamEvent
            {
                Type = "user",
                Content = userPrompt
            });
            session.ApiHistory.Add(new ChatHistoryItem("user", userPrompt));

            _cts = new CancellationTokenSource();
            CancellationToken token = _cts.Token;
            _isPaused = false;

            string conversationalVerifyCmd = VerificationRunner.ExtractConversationalVerifyCommand(userPrompt, _whitelist.Files);

            UpdateBusyState(true, "Preparing...");
            BroadcastFullState();

            try
            {
                // Track whether previous round's terminal commands all succeeded.
                // If previous round succeeded and round > 1, the model is providing a summary response;
                // do NOT allow markdown code blocks to fall back into overwriting files.
                bool prevRoundAllSucceeded = false;

                // No round cap: keep going until the model finishes (no more terminal commands), or the user stops.
                for (int round = 1; ; round++)
                {
                    token.ThrowIfCancellationRequested();
                    await WaitIfPausedAsync(token);

                    VendorProfile vendor = GetActiveVendor();
                    UpdateBusyState(true, string.Format("[Step {0}] Thinking...", round));

                    UiStreamEvent assistantEvent = new UiStreamEvent
                    {
                        Type = "assistant",
                        Content = "Thinking...",
                        VendorName = vendor.DisplayName,
                        ModelName = vendor.SelectedModel,
                        Round = round
                    };
                    session.UiEvents.Add(assistantEvent);
                    BroadcastFullState();

                    string sysPrompt = _whitelist.BuildSystemPrompt();
                    string filesSnapshot = _whitelist.BuildCurrentFilesSnapshotBlock();

                    string rawModelReply = await MultiVendorClient.SendChatAsync(
                        vendor,
                        sysPrompt,
                        filesSnapshot,
                        session.ApiHistory,
                        _whitelist.Files,
                        token,
                        (partialDisplay) =>
                        {
                            assistantEvent.Content = partialDisplay;
                            SendStreamingEventUpdate(assistantEvent.Id, partialDisplay);
                        },
                        _config.ThinkingIntensity);

                    token.ThrowIfCancellationRequested();

                    session.ApiHistory.Add(new ChatHistoryItem("assistant", rawModelReply));
                    bool allowMarkdownFallback = !(round > 1 && prevRoundAllSucceeded);
                    ParsedAgentResponse parsed = WhitelistManager.ParseAgentOutput(rawModelReply, _whitelist.Files, allowMarkdownFallback);

                    assistantEvent.Content = parsed.ExplanationText;

                    string lastToast = null;
                    foreach (SurgicalFileEdit edit in parsed.FileEdits)
                    {
                        WhitelistedFile modifiedFile;
                        string editStatus;
                        bool ok = _whitelist.ApplyEditInPlace(edit, out modifiedFile, out editStatus);
                        if (ok && modifiedFile != null)
                        {
                            session.UiEvents.Add(new UiStreamEvent
                            {
                                Type = "file_edit",
                                Content = editStatus,
                                FilePath = modifiedFile.FullPath,
                                FileName = !string.IsNullOrEmpty(modifiedFile.RelativePath) ? modifiedFile.RelativePath : modifiedFile.FileName,
                                EditCount = modifiedFile.EditCount
                            });
                            lastToast = "Modified: " + modifiedFile.FileName;
                        }
                        else
                        {
                            AddSessionSecurityEvent(editStatus);
                        }
                    }

                    BroadcastFullState(lastToast);

                    StringBuilder feedbackSb = new StringBuilder();

                    // 1. Process Web Search Queries
                    if (parsed.SearchQueries != null && parsed.SearchQueries.Count > 0)
                    {
                        foreach (string q in parsed.SearchQueries)
                        {
                            token.ThrowIfCancellationRequested();
                            await WaitIfPausedAsync(token);
                            UpdateBusyState(true, string.Format("[Step {0}] Searching web: {1}", round, q));

                            List<SearchResultItem> searchItems = await WebSearchRunner.SearchAsync(q, token);

                            session.UiEvents.Add(new UiStreamEvent
                            {
                                Type = "search_result",
                                SearchQuery = q,
                                SearchResults = searchItems,
                                Round = round
                            });
                            BroadcastFullState("Web search completed: " + q);

                            string searchFeedback = WebSearchRunner.FormatSearchResultsForPrompt(q, searchItems);
                            feedbackSb.AppendLine(searchFeedback);
                        }
                    }

                    // 2. Collect terminal commands to run in this round
                    List<string> cmdsToRun = new List<string>();
                    if (parsed.TerminalCommands != null && parsed.TerminalCommands.Count > 0)
                    {
                        cmdsToRun.AddRange(parsed.TerminalCommands);
                    }
                    else if (round == 1 && !string.IsNullOrWhiteSpace(conversationalVerifyCmd))
                    {
                        cmdsToRun.Add(conversationalVerifyCmd);
                    }

                    if (cmdsToRun.Count == 0 && (parsed.SearchQueries == null || parsed.SearchQueries.Count == 0))
                    {
                        // No more commands and no search queries: conversation turn is complete!
                        UpdateBusyState(false, "Ready");
                        BroadcastFullState();
                        break;
                    }

                    string workDir = _whitelist.GetActiveWorkspaceDirectory();
                    bool allSucceeded = true;

                    for (int cIdx = 0; cIdx < cmdsToRun.Count; cIdx++)
                    {
                        string cmdToRun = cmdsToRun[cIdx];
                        await WaitIfPausedAsync(token);
                        UpdateBusyState(true, string.Format("[Step {0}] Executing: {1}", round, cmdToRun));

                        VerifyExecutionResult verifyRes = await VerificationRunner.RunCommandAsync(
                            cmdToRun,
                            workDir,
                            _config.CommandTimeoutSeconds,
                            token);

                        // Refresh workspace directories in case files on disk were modified
                        _whitelist.RefreshWorkspaceDirectories();

                        session.UiEvents.Add(new UiStreamEvent
                        {
                            Type = "verify_result",
                            Command = cmdToRun,
                            WorkingDirectory = verifyRes.WorkingDirectory,
                            Round = round,
                            MaxRounds = 0,
                            ExitCode = verifyRes.ExitCode,
                            DurationSeconds = verifyRes.DurationSeconds,
                            Succeeded = verifyRes.Succeeded,
                            TerminalOutput = verifyRes.CombinedOutput
                        });

                        BroadcastFullState();

                        if (verifyRes.Cancelled)
                        {
                            throw new OperationCanceledException();
                        }

                        if (!verifyRes.Succeeded)
                        {
                            allSucceeded = false;
                        }

                        feedbackSb.AppendLine(string.Format(
                            "[Terminal Execution Feedback - Step {0} | Command `{1}` | Exit Code: {2} ({3})]",
                            round,
                            cmdToRun,
                            verifyRes.ExitCode,
                            verifyRes.Succeeded ? "Execution Succeeded" : "Execution Failed / Non-Zero Exit Code"));
                        feedbackSb.AppendLine(string.Format("Working Directory: `{0}`", verifyRes.WorkingDirectory));
                        feedbackSb.AppendLine("Terminal Output (stdout/stderr):");
                        feedbackSb.AppendLine("```");
                        feedbackSb.AppendLine(verifyRes.CombinedOutput);
                        feedbackSb.AppendLine("```");
                        feedbackSb.AppendLine();
                    }

                    if (allSucceeded)
                    {
                        feedbackSb.AppendLine("Based on the execution and search results above, continue the interaction:");
                        feedbackSb.AppendLine("1. If tasks and verifications are complete, summarize your answer directly for the user (**do NOT output another `<metis_cmd>` tag when finished**);");
                        feedbackSb.AppendLine("2. If further in-place modifications or commands are needed, continue with `<metis_replace file=\"...\">`, `<metis_search>`, or `<metis_cmd>` (**creating or deleting files is strictly prohibited**).");

                        AddSessionNoticeEvent(string.Format("Step {0} executed successfully, continuing...", round));
                    }
                    else
                    {
                        feedbackSb.AppendLine("Terminal command returned a non-zero exit code (execution error). Analyze the error output and continue the interaction:");
                        feedbackSb.AppendLine("Use `<metis_replace file=\"...\">` to fix the code in-place (**creating or deleting files is strictly prohibited**), and emit `<metis_cmd>` to re-run verification or `<metis_search>` to look up solutions.");

                        AddSessionNoticeEvent(string.Format("Step {0} execution error, retrying...", round));
                    }

                    prevRoundAllSucceeded = allSucceeded;

                    session.ApiHistory.Add(new ChatHistoryItem("user", feedbackSb.ToString()));
                    BroadcastFullState();
                }
            }
            catch (OperationCanceledException)
            {
                AddSessionNoticeEvent("Stopped.");
                UpdateBusyState(false, "Stopped");
                BroadcastFullState();
            }
            catch (Exception ex)
            {
                AddSessionSecurityEvent(ex.Message);
                UpdateBusyState(false, "Error occurred");
                BroadcastFullState();
            }
        }

        private void AddSessionNoticeEvent(string text)
        {
            ChatSession s = GetActiveSession();
            s.UiEvents.Add(new UiStreamEvent { Type = "notice", Content = text });
        }

        private void AddSessionSecurityEvent(string text)
        {
            ChatSession s = GetActiveSession();
            s.UiEvents.Add(new UiStreamEvent { Type = "security_block", Content = text });
        }

        // Native Windows Modern Explorer-style Folder Picker (IFileOpenDialog with FOS_PICKFOLDERS)
        private static string PickFolderDialog(Window owner, string title)
        {
            try
            {
                IFileOpenDialog dialog = (IFileOpenDialog)new FileOpenDialogRCW();
                uint options;
                dialog.GetOptions(out options);
                // FOS_PICKFOLDERS = 0x20, FOS_FORCEFILESYSTEM = 0x40, FOS_PATHMUSTEXIST = 0x800
                dialog.SetOptions(options | 0x20u | 0x40u | 0x800u);
                if (!string.IsNullOrEmpty(title))
                {
                    dialog.SetTitle(title);
                }

                IntPtr hwnd = owner != null ? new WindowInteropHelper(owner).Handle : IntPtr.Zero;
                int hr = dialog.Show(hwnd);
                if (hr == 0) // S_OK
                {
                    IShellItem item;
                    dialog.GetResult(out item);
                    if (item != null)
                    {
                        string path;
                        // SIGDN_FILESYSPATH = 0x80058000
                        item.GetDisplayName(0x80058000u, out path);
                        return path;
                    }
                }
                return null;
            }
            catch
            {
                // Fallback using OpenFileDialog folder trick
                OpenFileDialog fallback = new OpenFileDialog();
                fallback.Title = title ?? "Select Folder";
                fallback.ValidateNames = false;
                fallback.CheckFileExists = false;
                fallback.CheckPathExists = true;
                fallback.FileName = "Select this folder";
                if (fallback.ShowDialog(owner) == true)
                {
                    return Path.GetDirectoryName(fallback.FileName);
                }
                return null;
            }
        }

        [ComImport]
        [Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")]
        private class FileOpenDialogRCW
        {
        }

        [ComImport]
        [Guid("42f85136-db7e-439c-85f1-e4075d135fc8")]
        [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IFileOpenDialog
        {
            [PreserveSig] int Show([In] IntPtr parent);
            void SetFileTypes([In] uint cFileTypes, [In] IntPtr rgFilterSpec);
            void SetFileTypeIndex([In] uint iFileType);
            void GetFileTypeIndex(out uint piFileType);
            void Advise([In, MarshalAs(UnmanagedType.Interface)] object pfde, out uint pdwCookie);
            void Unadvise([In] uint dwCookie);
            void SetOptions([In] uint fos);
            void GetOptions(out uint pfos);
            void SetDefaultFolder([In, MarshalAs(UnmanagedType.Interface)] IShellItem psi);
            void SetFolder([In, MarshalAs(UnmanagedType.Interface)] IShellItem psi);
            void GetFolder([MarshalAs(UnmanagedType.Interface)] out IShellItem ppsi);
            void GetCurrentSelection([MarshalAs(UnmanagedType.Interface)] out IShellItem ppsi);
            void SetFileName([In, MarshalAs(UnmanagedType.LPWStr)] string pszName);
            void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string pszName);
            void SetTitle([In, MarshalAs(UnmanagedType.LPWStr)] string pszTitle);
            void SetOkButtonLabel([In, MarshalAs(UnmanagedType.LPWStr)] string pszText);
            void SetFileNameLabel([In, MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
            void GetResult([MarshalAs(UnmanagedType.Interface)] out IShellItem ppsi);
            void AddPlace([In, MarshalAs(UnmanagedType.Interface)] IShellItem psi, int fdap);
            void SetDefaultExtension([In, MarshalAs(UnmanagedType.LPWStr)] string pszDefaultExtension);
            void Close([MarshalAs(UnmanagedType.Error)] int hr);
            void SetClientGuid([In] ref Guid guid);
            void ClearClientData();
            void SetFilter([MarshalAs(UnmanagedType.Interface)] IntPtr pFilter);
            void GetResults([MarshalAs(UnmanagedType.Interface)] out IntPtr ppenum);
            void GetSelectedItems([MarshalAs(UnmanagedType.Interface)] out IntPtr ppsai);
        }

        [ComImport]
        [Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe")]
        [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IShellItem
        {
            void BindToHandler([In] IntPtr pbc, [In] ref Guid bhid, [In] ref Guid riid, out IntPtr ppv);
            void GetParent([MarshalAs(UnmanagedType.Interface)] out IShellItem ppsi);
            void GetDisplayName([In] uint sigdnName, [MarshalAs(UnmanagedType.LPWStr)] out string ppszName);
            void GetAttributes([In] uint sfgaoMask, out uint psfgaoAttribs);
            void Compare([In, MarshalAs(UnmanagedType.Interface)] IShellItem psi, [In] uint hint, out int piOrder);
        }
    }
}
