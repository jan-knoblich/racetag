; racetag.iss — Windows installer for Racetag (Inno Setup 6.3 or newer)
;
; Wraps the PyInstaller one-directory build (apps/desktop/dist/Racetag) into
; Racetag-Setup-<version>.exe. Per-user install without admin rights, German
; UI only, installs the Microsoft Edge WebView2 runtime when it is missing.
;
; Build (from apps/desktop/installer, see README.md):
;   iscc /DMyAppVersion=0.1.0 racetag.iss
;
; Preprocessor parameters (all optional):
;   /DMyAppVersion=<x.y.z>     numeric version; default: contents of ..\VERSION
;   /DMyVersionSuffix=<text>   appended to the displayed version and the output
;                              file name, e.g. "+abc1234" for CI test builds
;   /DSourceDir=<dir>          PyInstaller output folder, no trailing backslash;
;                              default: ..\dist\Racetag (relative to this file)
;
; Note: SourceDir here is a preprocessor variable only; the [Setup] directive
; of the same name is deliberately not used.
;
; This file must stay UTF-8 with BOM, otherwise ISCC reads the German umlauts
; in the system ANSI code page.

#if VER < EncodeVer(6, 3, 0)
  #error Inno Setup 6.3 or newer is required (x64compatible architecture identifier)
#endif

#ifndef MyAppVersion
  #define VersionFile FileOpen(AddBackslash(SourcePath) + "..\VERSION")
  #if !VersionFile
    #error MyAppVersion was not given and ..\VERSION could not be read
  #endif
  #define MyAppVersion Trim(FileRead(VersionFile))
  #expr FileClose(VersionFile)
  #undef VersionFile
#endif

#ifndef MyVersionSuffix
  #define MyVersionSuffix ""
#endif

#ifndef SourceDir
  #define SourceDir "..\dist\Racetag"
#endif

#define MyAppName "Racetag"
#define MyAppPublisher "Jan Knoblich"
#define MyAppURL "https://github.com/jan-knoblich/racetag"
#define MyAppExeName "Racetag.exe"
; Evergreen bootstrapper from https://go.microsoft.com/fwlink/p/?LinkId=2124703
; (downloaded by CI next to this file, never committed).
#define WebView2Setup "MicrosoftEdgeWebview2Setup.exe"

#if !FileExists(AddBackslash(SourcePath) + WebView2Setup)
  #error MicrosoftEdgeWebview2Setup.exe is missing next to racetag.iss. Download it from https://go.microsoft.com/fwlink/p/?LinkId=2124703 (see README.md).
#endif

[Setup]
; AppId identifies the installation for upgrades and uninstall. Never change it.
AppId={{569B73B6-CB78-4CB2-803C-50BD659F6062}
AppName={#MyAppName}
AppVersion={#MyAppVersion}{#MyVersionSuffix}
AppVerName={#MyAppName} {#MyAppVersion}{#MyVersionSuffix}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
VersionInfoVersion={#MyAppVersion}
VersionInfoProductTextVersion={#MyAppVersion}{#MyVersionSuffix}
VersionInfoDescription={#MyAppName} Setup

; Per-user install: no UAC prompt, works for operators without admin rights.
; The data directory %USERPROFILE%\.racetag (races, riders, settings, logs) is
; not part of the installation, so upgrades and uninstalling never touch it.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#MyAppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
; Inno Setup hides the welcome page by default; it carries the SmartScreen hint.
DisableWelcomePage=no

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

; A running Racetag is closed via the Restart Manager before files are
; replaced. It is not restarted automatically: the finish page offers
; "Racetag starten", and a second start would hit the single-instance dialog.
CloseApplications=yes
RestartApplications=no

SetupIconFile=..\icons\racetag.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
SetupLogging=yes

OutputDir=output
OutputBaseFilename=Racetag-Setup-{#MyAppVersion}{#MyVersionSuffix}

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Messages]
WelcomeLabel2=Dieser Assistent installiert [name/ver] für Ihr Benutzerkonto. Administratorrechte sind nicht nötig.%n%nHinweis zu Windows SmartScreen: Racetag ist nicht digital signiert. Falls Windows beim Öffnen des Installers „Der Computer wurde durch Windows geschützt“ anzeigt, klicken Sie auf „Weitere Informationen“ und dann auf „Trotzdem ausführen“. Das installierte Racetag startet danach ohne diese Warnung.%n%nIhre Rennen, Fahrer und Einstellungen bleiben bei Installation, Aktualisierung und Deinstallation erhalten.

[CustomMessages]
AutostartTask=Racetag beim Anmelden an Windows automatisch starten
OtherTasks=Weitere Optionen:

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "{cm:AutostartTask}"; GroupDescription: "{cm:OtherTasks}"; Flags: unchecked

[InstallDelete]
; PyInstaller's bundle layout changes between versions. Remove the previous
; bundle so no stale modules or DLLs survive an upgrade. User data lives in
; %USERPROFILE%\.racetag and is never touched.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Extracted on demand by the [Code] section only when WebView2 is missing.
Source: "{#WebView2Setup}"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: autostart
; Upgrading with the task unticked removes an autostart entry set earlier.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "{#MyAppName}"; Flags: deletevalue; Tasks: not autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[Code]
const
  // Registry location of the Evergreen WebView2 runtime. The value "pv" holds
  // the installed version; a missing or empty value, or "0.0.0.0", means the
  // runtime is not installed.
  WebView2ClientKey = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

  WebView2DownloadUrl = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';

  // Operator-visible texts used from code. They are kept here instead of
  // [CustomMessages] because the uninstall message is shown by the uninstaller.
  // Line breaks are added where the texts are used.
  WebView2InstallingText = 'Microsoft WebView2-Laufzeit wird installiert. Das kann einige Minuten dauern und braucht eine Internetverbindung …';
  WebView2FailedText1 = 'Die Microsoft WebView2-Laufzeit konnte nicht installiert werden. Racetag braucht sie, um sein Fenster anzuzeigen.';
  WebView2FailedText2 = 'Racetag wird trotzdem fertig installiert. Verbinden Sie den Computer mit dem Internet und starten Sie den Installer danach noch einmal, oder installieren Sie die Laufzeit von dieser Seite:';
  DataKeptText = 'Racetag wurde entfernt. Ihre Rennen, Fahrer, Einstellungen und Protokolle wurden nicht gelöscht. Sie liegen weiterhin hier:';

function ReadWebView2Version(const RootKey: Integer; const SubKey: String): String;
var
  Version: String;
begin
  Result := '';
  if RegQueryStringValue(RootKey, SubKey, 'pv', Version) then
  begin
    Version := Trim(Version);
    if (Version <> '') and (Version <> '0.0.0.0') then
      Result := Version;
  end;
end;

function IsWebView2RuntimeInstalled: Boolean;
begin
  // Per-machine runtime (32-bit registry node on 64-bit Windows), then the
  // per-user runtime that a non-admin bootstrapper run installs.
  Result := (ReadWebView2Version(HKEY_LOCAL_MACHINE, 'SOFTWARE\WOW6432Node\' + WebView2ClientKey) <> '')
    or (ReadWebView2Version(HKEY_CURRENT_USER, 'Software\' + WebView2ClientKey) <> '');
end;

procedure InstallWebView2Runtime;
var
  Bootstrapper: String;
  ResultCode: Integer;
begin
  WizardForm.StatusLabel.Caption := WebView2InstallingText;
  WizardForm.FilenameLabel.Caption := '';
  WizardForm.ProgressGauge.Style := npbstMarquee;
  try
    ExtractTemporaryFile('{#WebView2Setup}');
    Bootstrapper := ExpandConstant('{tmp}\{#WebView2Setup}');
    Log('WebView2 runtime not found, running ' + Bootstrapper + ' /silent /install');
    if Exec(Bootstrapper, '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      Log('WebView2 bootstrapper exit code: ' + IntToStr(ResultCode))
    else
      Log('WebView2 bootstrapper could not be started: ' + SysErrorMessage(ResultCode));
  except
    Log('WebView2 bootstrapper failed: ' + GetExceptionMessage);
  end;
  WizardForm.ProgressGauge.Style := npbstNormal;

  // The app itself also checks at start and offers the download link, so a
  // failure here must not abort the installation.
  if IsWebView2RuntimeInstalled then
    Log('WebView2 runtime is installed now')
  else
    SuppressibleMsgBox(WebView2FailedText1 + #13#10#13#10 + WebView2FailedText2 + #13#10 + WebView2DownloadUrl,
      mbError, MB_OK, IDOK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if IsWebView2RuntimeInstalled then
      Log('WebView2 runtime already installed')
    else
      InstallWebView2Runtime;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox(DataKeptText + #13#10 + ExpandConstant('{%USERPROFILE}') + '\.racetag', mbInformation, MB_OK);
end;
