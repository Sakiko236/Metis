@echo off
echo [1/2] Building Metis.exe ...
set FW=C:\Windows\Microsoft.NET\Framework64\v4.0.30319
set WV=lib\webview2

"%FW%\csc.exe" /nologo /target:winexe /platform:x64 /out:Metis.exe /win32icon:src\Metis.ico ^
  /r:"%FW%\WPF\PresentationFramework.dll" ^
  /r:"%FW%\WPF\PresentationCore.dll" ^
  /r:"%FW%\WPF\WindowsBase.dll" ^
  /r:"%FW%\System.Xaml.dll" ^
  /r:"%FW%\System.Drawing.dll" ^
  /r:"%FW%\System.Web.Extensions.dll" ^
  /r:"%FW%\System.Net.Http.dll" ^
  /r:%WV%\Microsoft.Web.WebView2.Core.dll ^
  /r:%WV%\Microsoft.Web.WebView2.Wpf.dll ^
  /resource:%WV%\Microsoft.Web.WebView2.Core.dll,Microsoft.Web.WebView2.Core.dll ^
  /resource:%WV%\Microsoft.Web.WebView2.Wpf.dll,Microsoft.Web.WebView2.Wpf.dll ^
  /resource:%WV%\WebView2Loader.dll,WebView2Loader.dll ^
  /resource:src\frontend\app.html,app.html ^
  /resource:src\Metis.ico,Metis.ico ^
  src\Models.cs src\ConfigManager.cs src\MediaAttachmentHelper.cs src\WhitelistManager.cs src\VerificationRunner.cs src\WebSearchRunner.cs src\MultiVendorClient.cs src\MainWindow.cs src\Program.cs

if %ERRORLEVEL% NEQ 0 (
    echo [FAIL] Failed to build Metis.exe, exit code: %ERRORLEVEL%
    exit /b %ERRORLEVEL%
)

echo [2/2] Building Metis-Setup.exe ...
"%FW%\csc.exe" /nologo /target:winexe /platform:x64 /out:Metis-Setup.exe /win32icon:src\Metis.ico ^
  /r:"%FW%\WPF\PresentationFramework.dll" ^
  /r:"%FW%\WPF\PresentationCore.dll" ^
  /r:"%FW%\WPF\WindowsBase.dll" ^
  /r:"%FW%\System.Xaml.dll" ^
  /resource:Metis.exe,Metis.exe ^
  /resource:src\Metis.ico,Metis.ico ^
  src\Installer.cs

if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Built Metis.exe and Metis-Setup.exe
) else (
    echo [FAIL] Failed to build Metis-Setup.exe, exit code: %ERRORLEVEL%
    exit /b %ERRORLEVEL%
)
