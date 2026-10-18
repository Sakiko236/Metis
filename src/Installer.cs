using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using Microsoft.Win32;

[assembly: AssemblyTitle("Metis Setup")]
[assembly: AssemblyDescription("Metis v1.1.0 Windows Installer")]
[assembly: AssemblyProduct("Metis")]
[assembly: AssemblyVersion("1.1.0.0")]
[assembly: AssemblyFileVersion("1.1.0.0")]

namespace MetisSetup
{
    public class InstallerWindow : Window
    {
        private TextBox _installDirBox;
        private CheckBox _desktopShortcutCheck;
        private CheckBox _startMenuShortcutCheck;
        private CheckBox _demoWorkspaceCheck;
        private CheckBox _launchAfterCheck;
        private ProgressBar _progressBar;
        private TextBox _logBox;
        private Button _installBtn;
        private Button _cancelBtn;
        private bool _installFinished = false;
        private string _installedExePath = "";

        private static SolidColorBrush BrushHex(string hex)
        {
            Color c = (Color)ColorConverter.ConvertFromString(hex);
            SolidColorBrush b = new SolidColorBrush(c);
            b.Freeze();
            return b;
        }

        public InstallerWindow()
        {
            Title = "Metis v1.1.0 Setup Wizard";
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

            Width = 650;
            Height = 480;
            ResizeMode = ResizeMode.NoResize;
            WindowStartupLocation = WindowStartupLocation.CenterScreen;
            Background = BrushHex("#090A0F");
            Foreground = BrushHex("#F3F4F6");
            FontFamily = new FontFamily("Segoe UI, Microsoft YaHei UI, sans-serif");

            BuildUI();
        }

        private void BuildUI()
        {
            Grid root = new Grid();
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); // Banner
            root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) }); // Body
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); // Footer

            // 1. Top Banner
            Border banner = new Border
            {
                Background = BrushHex("#10131C"),
                BorderBrush = BrushHex("#1E2436"),
                BorderThickness = new Thickness(0, 0, 0, 1),
                Padding = new Thickness(24, 18, 24, 18)
            };

            StackPanel bannerStack = new StackPanel();
            StackPanel titleRow = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 0, 6) };

            Border badge = new Border
            {
                Background = BrushHex("#082F49"),
                BorderBrush = BrushHex("#0EA5E9"),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(6),
                Padding = new Thickness(8, 2, 8, 2),
                Margin = new Thickness(0, 0, 10, 0),
                VerticalAlignment = VerticalAlignment.Center
            };
            badge.Child = new TextBlock
            {
                Text = "SETUP v1.1.0",
                Foreground = BrushHex("#38BDF8"),
                FontWeight = FontWeights.Bold,
                FontSize = 11
            };
            titleRow.Children.Add(badge);

            titleRow.Children.Add(new TextBlock
            {
                Text = "Install Metis Desktop",
                FontSize = 18,
                FontWeight = FontWeights.Bold,
                Foreground = BrushHex("#F9FAFB"),
                VerticalAlignment = VerticalAlignment.Center
            });
            bannerStack.Children.Add(titleRow);

            bannerStack.Children.Add(new TextBlock
            {
                Text = "File-Centric Mini Coding Agent with Zero Blast Radius",
                FontSize = 12,
                Foreground = BrushHex("#9CA3AF")
            });

            banner.Child = bannerStack;
            Grid.SetRow(banner, 0);
            root.Children.Add(banner);

            // 2. Body
            StackPanel body = new StackPanel { Margin = new Thickness(24, 18, 24, 12) };

            body.Children.Add(new TextBlock
            {
                Text = "Installation Directory:",
                FontSize = 12.5,
                FontWeight = FontWeights.SemiBold,
                Foreground = BrushHex("#D1D5DB"),
                Margin = new Thickness(0, 0, 0, 6)
            });

            Grid dirRow = new Grid { Margin = new Thickness(0, 0, 0, 14) };
            dirRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            dirRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

            string defaultDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Programs",
                "Metis");

            _installDirBox = new TextBox
            {
                Text = defaultDir,
                Height = 32,
                Background = BrushHex("#141720"),
                Foreground = BrushHex("#F3F4F6"),
                CaretBrush = BrushHex("#F3F4F6"),
                BorderBrush = BrushHex("#2D3548"),
                BorderThickness = new Thickness(1),
                Padding = new Thickness(10, 5, 10, 5),
                FontSize = 12.5,
                VerticalContentAlignment = VerticalAlignment.Center
            };
            Grid.SetColumn(_installDirBox, 0);
            dirRow.Children.Add(_installDirBox);

            Button resetDirBtn = new Button
            {
                Content = "Default Path",
                Margin = new Thickness(8, 0, 0, 0),
                Padding = new Thickness(12, 5, 12, 5),
                Background = BrushHex("#1A1E2B"),
                Foreground = BrushHex("#D1D5DB"),
                BorderBrush = BrushHex("#2D3548"),
                BorderThickness = new Thickness(1),
                Cursor = Cursors.Hand
            };
            resetDirBtn.Click += (s, e) => _installDirBox.Text = defaultDir;
            Grid.SetColumn(resetDirBtn, 1);
            dirRow.Children.Add(resetDirBtn);

            body.Children.Add(dirRow);

            // Options
            _desktopShortcutCheck = CreateCheck("Create Desktop Shortcut", true);
            _startMenuShortcutCheck = CreateCheck("Create Start Menu Shortcut", true);
            _demoWorkspaceCheck = CreateCheck("Include Automated Verification Demo Workspace", true);
            _launchAfterCheck = CreateCheck("Launch Metis after installation", true);

            body.Children.Add(_desktopShortcutCheck);
            body.Children.Add(_startMenuShortcutCheck);
            body.Children.Add(_demoWorkspaceCheck);
            body.Children.Add(_launchAfterCheck);

            // Progress & Log
            _progressBar = new ProgressBar
            {
                Height = 8,
                Minimum = 0,
                Maximum = 100,
                Value = 0,
                Background = BrushHex("#141720"),
                Foreground = BrushHex("#0EA5E9"),
                BorderThickness = new Thickness(0),
                Margin = new Thickness(0, 12, 0, 8)
            };
            body.Children.Add(_progressBar);

            _logBox = new TextBox
            {
                Height = 96,
                IsReadOnly = true,
                Background = BrushHex("#06080C"),
                Foreground = BrushHex("#9CA3AF"),
                BorderBrush = BrushHex("#1E2436"),
                BorderThickness = new Thickness(1),
                Padding = new Thickness(8),
                FontFamily = new FontFamily("Consolas, monospace"),
                FontSize = 11.5,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                Text = "Ready. Click 'Install' to deploy Metis v1.1.0 to your system..."
            };
            body.Children.Add(_logBox);

            Grid.SetRow(body, 1);
            root.Children.Add(body);

            // 3. Footer
            Border footer = new Border
            {
                Background = BrushHex("#10131C"),
                BorderBrush = BrushHex("#1E2436"),
                BorderThickness = new Thickness(0, 1, 0, 0),
                Padding = new Thickness(24, 12, 24, 12)
            };

            DockPanel footerDock = new DockPanel();
            TextBlock footNote = new TextBlock
            {
                Text = "Can be uninstalled at any time from Windows Apps & Features",
                FontSize = 11.5,
                Foreground = BrushHex("#6B7280"),
                VerticalAlignment = VerticalAlignment.Center
            };
            DockPanel.SetDock(footNote, Dock.Left);
            footerDock.Children.Add(footNote);

            StackPanel btnPanel = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
            _cancelBtn = new Button
            {
                Content = "Cancel",
                Width = 84,
                Height = 32,
                Background = BrushHex("#1A1E2B"),
                Foreground = BrushHex("#D1D5DB"),
                BorderBrush = BrushHex("#2D3548"),
                BorderThickness = new Thickness(1),
                Margin = new Thickness(0, 0, 10, 0),
                Cursor = Cursors.Hand
            };
            _cancelBtn.Click += (s, e) => Close();
            btnPanel.Children.Add(_cancelBtn);

            _installBtn = new Button
            {
                Content = "Install",
                Width = 136,
                Height = 32,
                Background = BrushHex("#0EA5E9"),
                Foreground = Brushes.White,
                FontWeight = FontWeights.Bold,
                BorderThickness = new Thickness(0),
                Cursor = Cursors.Hand
            };
            _installBtn.Click += OnInstallClicked;
            btnPanel.Children.Add(_installBtn);

            DockPanel.SetDock(btnPanel, Dock.Right);
            footerDock.Children.Add(btnPanel);

            footer.Child = footerDock;
            Grid.SetRow(footer, 2);
            root.Children.Add(footer);

            Content = root;
        }

        private CheckBox CreateCheck(string text, bool isChecked)
        {
            return new CheckBox
            {
                Content = text,
                IsChecked = isChecked,
                Foreground = BrushHex("#D1D5DB"),
                FontSize = 12.5,
                Margin = new Thickness(0, 0, 0, 6)
            };
        }

        private void AppendLog(string line)
        {
            _logBox.AppendText(Environment.NewLine + line);
            _logBox.ScrollToEnd();
        }

        private async void OnInstallClicked(object sender, RoutedEventArgs e)
        {
            if (_installFinished)
            {
                if (_launchAfterCheck.IsChecked == true && File.Exists(_installedExePath))
                {
                    try
                    {
                        ProcessStartInfo psi = new ProcessStartInfo(_installedExePath);
                        psi.WorkingDirectory = Path.GetDirectoryName(_installedExePath);
                        Process.Start(psi);
                    }
                    catch { }
                }
                Close();
                return;
            }

            string targetDir = (_installDirBox.Text ?? "").Trim();
            if (string.IsNullOrEmpty(targetDir))
            {
                MessageBox.Show(this, "Please specify a valid installation directory.", "Notice", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }

            _installBtn.IsEnabled = false;
            _installDirBox.IsEnabled = false;
            bool makeDesktop = _desktopShortcutCheck.IsChecked == true;
            bool makeStartMenu = _startMenuShortcutCheck.IsChecked == true;
            bool makeDemo = _demoWorkspaceCheck.IsChecked == true;

            try
            {
                _progressBar.Value = 15;
                AppendLog("[1/5] Creating installation directory: " + targetDir);
                await Task.Run(() =>
                {
                    if (!Directory.Exists(targetDir)) Directory.CreateDirectory(targetDir);
                });

                _progressBar.Value = 45;
                _installedExePath = Path.Combine(targetDir, "Metis.exe");
                AppendLog("[2/5] Extracting Metis.exe ...");
                await Task.Run(() =>
                {
                    InstallerProgram.ExtractResource("Metis.exe", _installedExePath);
                });

                if (makeDemo)
                {
                    _progressBar.Value = 65;
                    AppendLog("[3/5] Deploying demo workspace ...");
                    await Task.Run(() =>
                    {
                        InstallerProgram.DeployDemoFiles(targetDir);
                    });
                }

                _progressBar.Value = 85;
                AppendLog("[4/5] Configuring shortcuts and uninstaller registry entries ...");
                await Task.Run(() =>
                {
                    InstallerProgram.CreateUninstallerAndRegistry(targetDir, _installedExePath);
                    if (makeDesktop)
                    {
                        string desk = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                        InstallerProgram.CreateShortcut(Path.Combine(desk, "Metis.lnk"), _installedExePath, targetDir);
                    }
                    if (makeStartMenu)
                    {
                        string programs = Environment.GetFolderPath(Environment.SpecialFolder.Programs);
                        InstallerProgram.CreateShortcut(Path.Combine(programs, "Metis.lnk"), _installedExePath, targetDir);
                    }
                });

                _progressBar.Value = 100;
                AppendLog("[5/5] Installation complete! All components are ready.");
                _installFinished = true;
                _installBtn.Content = "Finish";
                _installBtn.IsEnabled = true;
                _cancelBtn.Visibility = Visibility.Collapsed;
            }
            catch (Exception ex)
            {
                AppendLog("Installation failed: " + ex.Message);
                MessageBox.Show(this, "An error occurred during installation: " + ex.Message, "Installation Error", MessageBoxButton.OK, MessageBoxImage.Error);
                _installBtn.IsEnabled = true;
                _installDirBox.IsEnabled = true;
            }
        }
    }

    public static class InstallerProgram
    {
        [STAThread]
        public static int Main(string[] args)
        {
            if (args != null && args.Length > 0 && string.Equals(args[0], "--silent", StringComparison.OrdinalIgnoreCase))
            {
                string targetDir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "Programs",
                    "Metis");
                if (args.Length > 1 && !string.IsNullOrWhiteSpace(args[1]))
                {
                    targetDir = args[1];
                }
                return RunSilentInstall(targetDir);
            }

            Application app = new Application();
            InstallerWindow win = new InstallerWindow();
            app.Run(win);
            return 0;
        }

        private static int RunSilentInstall(string targetDir)
        {
            try
            {
                if (!Directory.Exists(targetDir)) Directory.CreateDirectory(targetDir);
                string exePath = Path.Combine(targetDir, "Metis.exe");
                ExtractResource("Metis.exe", exePath);
                DeployDemoFiles(targetDir);
                CreateUninstallerAndRegistry(targetDir, exePath);
                return File.Exists(exePath) ? 0 : 1;
            }
            catch
            {
                return 2;
            }
        }

        public static void ExtractResource(string resourceName, string destPath)
        {
            Assembly asm = Assembly.GetExecutingAssembly();
            using (Stream s = asm.GetManifestResourceStream(resourceName))
            {
                if (s == null) throw new Exception("Resource not found in installer package: " + resourceName);
                using (FileStream fs = new FileStream(destPath, FileMode.Create, FileAccess.Write, FileShare.Read))
                {
                    s.CopyTo(fs);
                }
            }
        }

        public static void DeployDemoFiles(string targetDir)
        {
            string demoDir = Path.Combine(targetDir, "demo_workspace");
            if (!Directory.Exists(demoDir)) Directory.CreateDirectory(demoDir);

            UTF8Encoding utf8 = new UTF8Encoding(false);
            File.WriteAllText(Path.Combine(demoDir, "auth.py"),
@"import time

def create_token(user_id: str, ttl_seconds: int = 3600) -> dict:
    now = int(time.time())
    return {
        ""user_id"": user_id,
        ""issued_at"": now,
        ""expires_at"": now + ttl_seconds
    }

def is_token_expired(token: dict, current_time: int = None) -> bool:
    """"""Check whether token has expired. Returns True when current_time >= expires_at.""""""
    if current_time is None:
        current_time = int(time.time())
    expires_at = token.get(""expires_at"", 0)
    # BUG: Inverted comparison condition causing non-expired tokens to be flagged as expired
    return current_time < expires_at
", utf8);

            File.WriteAllText(Path.Combine(demoDir, "test_auth.py"),
@"from auth import create_token, is_token_expired

def test_valid_token_not_expired():
    token = {""user_id"": ""alice"", ""issued_at"": 1000, ""expires_at"": 2000}
    assert is_token_expired(token, current_time=1500) is False

def test_expired_token_detected():
    token = {""user_id"": ""alice"", ""issued_at"": 1000, ""expires_at"": 2000}
    assert is_token_expired(token, current_time=2500) is True

def test_exact_expiration_boundary():
    token = {""user_id"": ""alice"", ""issued_at"": 1000, ""expires_at"": 2000}
    assert is_token_expired(token, current_time=2000) is True
", utf8);
        }

        public static void CreateShortcut(string shortcutPath, string targetExe, string workingDir)
        {
            try
            {
                Type shellType = Type.GetTypeFromProgID("WScript.Shell");
                if (shellType == null) return;
                object shell = Activator.CreateInstance(shellType);
                object shortcut = shellType.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { shortcutPath });
                if (shortcut == null) return;

                Type scType = shortcut.GetType();
                scType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, shortcut, new object[] { targetExe });
                scType.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, shortcut, new object[] { workingDir });
                scType.InvokeMember("Description", BindingFlags.SetProperty, null, shortcut, new object[] { "Metis - File-Centric Mini Coding Agent" });
                scType.InvokeMember("IconLocation", BindingFlags.SetProperty, null, shortcut, new object[] { targetExe + ",0" });
                scType.InvokeMember("Save", BindingFlags.InvokeMethod, null, shortcut, null);
            }
            catch { }
        }

        public static void CreateUninstallerAndRegistry(string targetDir, string installedExePath)
        {
            try
            {
                string uninstallBat = Path.Combine(targetDir, "Uninstall.bat");
                string batContent =
                    "@echo off\r\n" +
                    "chcp 65001 >nul\r\n" +
                    "echo Uninstalling Metis ...\r\n" +
                    "taskkill /F /IM Metis.exe >nul 2>&1\r\n" +
                    "reg delete \"HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Metis\" /f >nul 2>&1\r\n" +
                    "del /f /q \"%USERPROFILE%\\Desktop\\Metis.lnk\" >nul 2>&1\r\n" +
                    "del /f /q \"%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Metis.lnk\" >nul 2>&1\r\n" +
                    "echo Shortcuts and registry entries removed.\r\n";
                File.WriteAllText(uninstallBat, batContent, new UTF8Encoding(false));

                using (RegistryKey key = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\Metis"))
                {
                    if (key != null)
                    {
                        key.SetValue("DisplayName", "Metis");
                        key.SetValue("DisplayVersion", "1.1.0");
                        key.SetValue("Publisher", "Metis Team");
                        key.SetValue("DisplayIcon", installedExePath);
                        key.SetValue("InstallLocation", targetDir);
                        key.SetValue("UninstallString", "\"" + uninstallBat + "\"");
                        key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                        key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                    }
                }
            }
            catch { }
        }
    }
}
