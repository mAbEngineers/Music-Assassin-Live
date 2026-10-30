; Inno Setup script for Music Assassin Live (Windows).
; Built by scripts\build_windows.bat, which passes:
;   MyAppVersion, StageDir (models), ExeDir (PyInstaller output), OutputDir
;   and, when packaging\windows\vendor\VBCABLE_Setup_x64.exe is present,
;   BundleVBCable=1.
;
; NOTE ON SCOPE: this installer gets the binary, models, and the VB-CABLE
; virtual-audio driver onto the machine. The Windows routing backend it feeds
; now exists — backends/windows.py: trap = CABLE Input, capture = CABLE
; Output, default-endpoint switching via IPolicyConfig (ROADMAP D3.3). Its
; state machine is tested and the COM call is verified on real hardware, but
; nobody has yet run the whole path on a Windows machine with a cable
; installed, so a successful install is not yet evidence that audio flows.
;
; WASAPI loopback is NOT the mechanism, despite what earlier notes said. It
; taps the signal where this app has to insert into it — the original audio
; would keep playing underneath the processed one — and sounddevice 0.5.5
; exposes no loopback flag in any case. CABLE Output is a real capture
; endpoint that an ordinary input stream reads.
;
; VB-CABLE (ROADMAP D3.4): downloaded at install time against a pinned
; SHA-256, never bundled. That ships none of VB-Audio's bytes, so nothing is
; redistributed and their >10-unit threshold is never engaged, and it turns
; "install two things and read a web page" into one installer.
;
; Three things about that are worth knowing before changing any of it:
;
;   1. Inno Setup 6.4.3 CANNOT extract .zip. Extract7ZipArchive does .7z
;      only; ExtractArchive with zip support arrived in 6.5, which this
;      project deliberately does not use (6.5.0+ added a paid commercial
;      tier -- see packaging/windows/README.md). VB-CABLE ships as a zip, so
;      extraction goes through a Windows built-in instead: tar.exe, with
;      PowerShell's Expand-Archive as a fallback.
;   2. The pinned hash is tied to a VERSIONED url (…Pack45.zip), so it is
;      stable rather than a hash of a moving target. When VB-Audio publishes
;      Pack46 this pin keeps working against 45; it does not silently start
;      trusting a new file. Bumping it is a deliberate edit.
;   3. One prompt cannot be removed. Windows shows its own
;      "install this device software?" dialog for any driver, and VB-Audio
;      have confirmed it cannot be suppressed. -i -h silences THEIR
;      installer, not the OS. The code below warns the user it is coming, so
;      it does not look like a hang.
;
; BundleVBCable=1 remains for internal/offline builds only. Publishing one is
; redistribution whatever the file is named, and is appropriate only once
; vb-audio-permission-email.md has been sent and answered.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif
#ifndef StageDir
  #define StageDir "..\..\build\win-stage"
#endif
#ifndef ExeDir
  #define ExeDir "..\..\build\pyinstaller\dist"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

#define MyAppName "Music Assassin Live"
#define MyAppPublisher "A-Ahmad-02"
#define MyAppURL "https://github.com/A-Ahmad-02/music-assassin-live"
#define MyAppExeName "music-assassin-live.exe"

[Setup]
; Fixed GUID — do not regenerate; Inno Setup uses this to recognise
; upgrades/uninstalls across versions.
AppId={{4F2B6C1A-8E1B-4B7B-9C2E-6C1F6D6B9A11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\Music Assassin Live
DefaultGroupName=Music Assassin Live
LicenseFile=..\..\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename=music-assassin-live-setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; Admin needed for Program Files + (when bundled) the VB-CABLE driver install.
PrivilegesRequired=admin
DisableWelcomePage=no
; Generate this once from assassin_live/ui/assets/icon.png, e.g.:
;   magick icon.png -define icon:auto-resize=256,128,64,48,32,16 music-assassin-live.ico
SetupIconFile=..\icons\music-assassin-live.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
; Pre-checked, so the default path is one installer and no reading, but still
; shown: this downloads and installs a third-party kernel-mode audio driver,
; which the user is entitled to know about and decline. Hidden entirely once
; a cable is already present, so re-running the installer is not noisy.
Name: "vbcable"; Description: "Install VB-CABLE virtual audio driver (required to route audio)"; GroupDescription: "Virtual audio device:"; Check: ShouldOfferCableTask

[Files]
Source: "{#ExeDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageDir}\models\*"; DestDir: "{app}\models"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\model-licenses\*"; DestDir: "{app}\model-licenses"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
#ifdef BundleVBCable
Source: "vendor\VBCABLE_Setup_x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall
#endif

[Icons]
Name: "{group}\Music Assassin Live"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall Music Assassin Live"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Music Assassin Live"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Lets assassin_live/paths.py find the bundled models with zero code changes
; (models_dir() already checks MUSIC_ASSASSIN_MODELS first).
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "MUSIC_ASSASSIN_MODELS"; ValueData: "{app}\models"; Flags: preservestringtype

[Run]
; VB-CABLE is NOT run from here any more, in bundled builds either: the
; driver install is sequenced in Pascal (EnsureVBCable) so that download,
; verification, extraction and the reboot flag are one decision with one set
; of error paths. A [Run] entry here as well would install it twice.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Music Assassin Live"; Flags: nowait postinstall skipifsilent

[Code]
const
  { Prefixed because Inno Setup predefines some of these itself -- an
    unprefixed HWND_BROADCAST aborted the very first compile this script was
    ever put through ("Duplicate identifier"). Which names are built in
    varies by version, so rather than track that, none of ours share a
    namespace with it. }
  MA_WM_SETTINGCHANGE = $001A;
  MA_HWND_BROADCAST = $FFFF;
  MA_SMTO_ABORTIFHUNG = $0002;

function SendMessageTimeoutA(hWnd: Integer; Msg: Integer; wParam: Integer;
  lParam: String; fuFlags: Integer; uTimeout: Integer;
  var lpdwResult: Integer): Integer;
  external 'SendMessageTimeoutA@user32.dll stdcall';

procedure RefreshEnvironment;
var
  ResultCode: Integer;
begin
  { Broadcast WM_SETTINGCHANGE so a freshly-launched app (e.g. the one this
    installer just offered to run) sees MUSIC_ASSASSIN_MODELS without the
    user needing to log off/on. }
  SendMessageTimeoutA(MA_HWND_BROADCAST, MA_WM_SETTINGCHANGE, 0, 'Environment',
    MA_SMTO_ABORTIFHUNG, 5000, ResultCode);
end;

const
  { Versioned url, so the hash below pins a specific file rather than
    whatever is currently being served. See note 2 in the header. }
  CableUrl = 'https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip';
  CableZipName = 'VBCABLE_Driver_Pack45.zip';
  CableSha256 = 'b950e39f01af1d04ea623c8f6d8eb9b6ea5c477c637295fabf20631c85116bfb';
  CableSetupName = 'VBCABLE_Setup_x64.exe';

var
  { Set only when we actually installed the driver. VB-CABLE's endpoints do
    not appear until a reboot, so NeedRestart below reports this rather than
    guessing. }
  CableWasInstalled: Boolean;

function VBCableAppearsInstalled: Boolean;
begin
  { Heuristic only -- used to decide whether to prompt, never to skip a step
    that would otherwise run. VB-CABLE registers itself here under both its
    32-bit and 64-bit install paths. }
  Result := RegKeyExists(HKLM32, 'SOFTWARE\VB-Audio\Cable') or
            RegKeyExists(HKLM64, 'SOFTWARE\VB-Audio\Cable');
end;

function ShouldOfferCableTask: Boolean;
begin
#ifdef BundleVBCable
  { A bundled build carries the installer; nothing to download. }
  Result := not VBCableAppearsInstalled;
#else
  Result := not VBCableAppearsInstalled;
#endif
end;

procedure SayManualSteps(const Why: String);
var
  ErrorCode: Integer;
begin
  if MsgBox(Why + #13#10#13#10 +
            'Music Assassin Live needs VB-CABLE, a free virtual audio ' +
            'driver, to route system sound through it. The app will tell ' +
            'you the same thing and refuse to start until it is present, ' +
            'so nothing is broken -- it just needs installing by hand:' +
            #13#10#13#10 +
            '  1. Download the ZIP from vb-cable.com' + #13#10 +
            '  2. Extract it' + #13#10 +
            '  3. Right-click VBCABLE_Setup_x64.exe, Run as administrator' +
            #13#10 + '  4. Reboot' + #13#10#13#10 +
            'VB-CABLE is donationware from VB-Audio. Open the page now?',
            mbInformation, MB_YESNO) = IDYES then
    ShellExecAsOriginalUser('open', 'https://vb-audio.com/Cable/', '', '',
      SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

function OnCableDownloadProgress(const Url, FileName: String;
  const Progress, ProgressMax: Int64): Boolean;
begin
  { IntToStr rather than Format: Inno's Format is thin and its behaviour with
    Int64 arguments is not worth discovering during someone's install. }
  if ProgressMax > 0 then
    WizardForm.StatusLabel.Caption := 'Downloading VB-CABLE... ' +
      IntToStr((Progress * 100) div ProgressMax) + '%'
  else
    WizardForm.StatusLabel.Caption := 'Downloading VB-CABLE...';
  Result := True;
end;

function UnpackZip(const ZipPath, DestDir: String): Boolean;
var
  ResultCode: Integer;
  Params: String;
begin
  { tar.exe first: it is bsdtar, handles zip, and has shipped in Windows
    since 10/1803. Doubled apostrophes are one literal apostrophe in Pascal,
    so each path reaches the shell single-quoted and spaces are safe. }
  Params := '-xf "' + ZipPath + '" -C "' + DestDir + '"';
  if Exec(ExpandConstant('{sys}\tar.exe'), Params, DestDir, SW_HIDE,
          ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
  begin
    Result := True;
    Exit;
  end;

  { Fallback for anything older than 1803, where tar.exe is absent.
    Expand-Archive needs PowerShell 5, which is Windows 10 and Server 2016. }
  Params := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ' +
            '"Expand-Archive -LiteralPath ''' + ZipPath +
            ''' -DestinationPath ''' + DestDir + ''' -Force"';
  Result := Exec('powershell.exe', Params, DestDir, SW_HIDE,
                 ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

function InstallCableFrom(const SetupExe: String): Boolean;
var
  ResultCode: Integer;
begin
  { -i -h is VB-CABLE's documented silent install. It silences THEIR
    installer; Windows still shows its own driver-signing dialog, which is
    why the user was warned above. }
  WizardForm.StatusLabel.Caption := 'Installing VB-CABLE audio driver...';
  if not Exec(SetupExe, '-i -h', ExtractFileDir(SetupExe), SW_SHOW,
              ewWaitUntilTerminated, ResultCode) then
  begin
    Result := False;
    Exit;
  end;
  { Trust the registry over the exit code: VB-CABLE's installer has been
    reported returning non-zero on a reboot-pending success. }
  Result := VBCableAppearsInstalled or (ResultCode = 0);
end;

procedure EnsureVBCable;
var
  TempDir, WorkDir, ZipPath, SetupExe: String;
begin
  if VBCableAppearsInstalled then
    Exit;
  if not WizardIsTaskSelected('vbcable') then
  begin
    SayManualSteps('VB-CABLE was not installed, because you chose to skip it.');
    Exit;
  end;

  TempDir := ExpandConstant('{tmp}');
  WorkDir := TempDir + '\vbcable';
  if not ForceDirectories(WorkDir) then
  begin
    SayManualSteps('Could not create a temporary folder for the download.');
    Exit;
  end;

#ifdef BundleVBCable
  { Offline/internal build: the installer is already beside us, no download. }
  SetupExe := TempDir + '\' + CableSetupName;
#else
  MsgBox('Music Assassin Live will now download and install VB-CABLE, a ' +
         'free virtual audio driver from VB-Audio (vb-cable.com), which it ' +
         'needs to route system sound.' + #13#10#13#10 +
         'Windows will ask you to confirm installing the audio device ' +
         'driver. That prompt comes from Windows itself and cannot be ' +
         'skipped -- please accept it when it appears.' + #13#10#13#10 +
         'A reboot will be needed afterwards.',
         mbInformation, MB_OK);

  try
    { Raises on a hash mismatch as well as on a transport failure, so an
      unverified file is never written where the next step would run it. }
    DownloadTemporaryFile(CableUrl, CableZipName, CableSha256,
                          @OnCableDownloadProgress);
  except
    SayManualSteps('The VB-CABLE download could not be completed or did not ' +
                   'match its expected checksum.' + #13#10#13#10 +
                   'If this machine is offline, that is the likely cause. ' +
                   'If it is online, VB-Audio may have replaced the file ' +
                   'this installer was built against, in which case the ' +
                   'installer needs updating rather than retrying -- ' +
                   'nothing unverified will be run.');
    Exit;
  end;

  ZipPath := TempDir + '\' + CableZipName;
  if not UnpackZip(ZipPath, WorkDir) then
  begin
    SayManualSteps('VB-CABLE was downloaded and verified, but could not be ' +
                   'extracted.');
    Exit;
  end;
  SetupExe := WorkDir + '\' + CableSetupName;
#endif

  if not FileExists(SetupExe) then
  begin
    SayManualSteps('VB-CABLE''s own installer was not where it was expected ' +
                   'inside the download.');
    Exit;
  end;

  if InstallCableFrom(SetupExe) then
    CableWasInstalled := True
  else
    SayManualSteps('VB-CABLE''s installer ran but did not report success.');
end;

function NeedRestart: Boolean;
begin
  { Only when we installed the driver. Its endpoints do not exist until the
    machine comes back up, and the app would otherwise start, find no cable,
    and tell the user to install the thing that was just installed. }
  Result := CableWasInstalled;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    EnsureVBCable;
    RefreshEnvironment;
  end;
end;
