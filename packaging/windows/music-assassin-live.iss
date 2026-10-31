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
; EDITING THE PASCAL BELOW: never begin a line with #13#10. The Inno
; preprocessor reads a leading '#' as a directive and the compile dies with
; "Unknown preprocessor directive" -- which is how the first version of this
; failed. Keep newline literals mid-line, after a string or a '+'.
;
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
; Shortcuts go straight into Programs, NOT into a {group} subfolder: on
; Windows 10/11 a program group is rendered as a collapsed folder in the
; Start menu, so the app showed up as a folder you had to open rather than
; as an app -- which is what the first user asked about. With no group there
; is nothing for the group page to configure either.
DisableProgramGroupPage=yes
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
; Installed as a file of its own so the shortcuts can point at it; see the
; icon-cache note in [Icons]. Same .ico that is embedded in the exe.
Source: "..\icons\music-assassin-live.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageDir}\models\*"; DestDir: "{app}\models"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\model-licenses\*"; DestDir: "{app}\model-licenses"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
#ifdef BundleVBCable
Source: "vendor\VBCABLE_Setup_x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall
#endif

[InstallDelete]
; The program group earlier versions created. Left behind it is an empty
; Start-menu folder sitting next to the new shortcut. Safe to name as a
; directory: the shortcut below is "Music Assassin Live.lnk" in the same
; parent, so the two never collide on disk.
Type: filesandordirs; Name: "{autoprograms}\Music Assassin Live"

[Icons]
; IconFilename is explicit, pointing at a file rather than at the exe's
; embedded icon, because of Explorer's icon cache: it keys on the icon
; source, and an upgrade that changes the artwork inside an exe at a path
; it has already cached keeps drawing the old one. The taskbar updated and
; the desktop shortcut did not, for exactly this reason. Naming a separate
; file gives the cache a key it has never seen.
Name: "{autoprograms}\Music Assassin Live"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\music-assassin-live.ico"
Name: "{autodesktop}\Music Assassin Live"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\music-assassin-live.ico"; Tasks: desktopicon
; NO "Restore my speakers" shortcut. It was added here and removed the same
; day: the first user to see it read it as a second app cluttering the Start
; menu, which is fair -- a recovery tool is not an app. Nothing is lost,
; because launching Music Assassin Live itself runs recover_stale() at
; startup and the uninstaller runs --recover before deleting anything. The
; flag stays for anyone who wants it from a terminal.

[Registry]
; Lets assassin_live/paths.py find the bundled models with zero code changes
; (models_dir() already checks MUSIC_ASSASSIN_MODELS first).
; uninsdeletevalue because without it this survives the uninstall, and a
; stale MUSIC_ASSASSIN_MODELS pointing into a deleted Program Files
; directory is read by paths.models_dir() ahead of everywhere else.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "MUSIC_ASSASSIN_MODELS"; ValueData: "{app}\models"; Flags: preservestringtype uninsdeletevalue

[Run]
; VB-CABLE is NOT run from here any more, in bundled builds either: the
; driver install is sequenced in Pascal (EnsureVBCable) so that download,
; verification, extraction and the reboot flag are one decision with one set
; of error paths. A [Run] entry here as well would install it twice.
; Nudges Explorer to rebuild its icon cache. Belt to the IconFilename
; braces in [Icons]: between them, an upgrade that changes the artwork
; actually shows the new artwork. Harmless if it is absent or refuses.
Filename: "{sys}\ie4uinit.exe"; Parameters: "-show"; Flags: runhidden skipifdoesntexist
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
  { Heuristic only -- used to decide whether to PROMPT, never to skip a step
    that would otherwise run. VB-CABLE registers itself here under both its
    32-bit and 64-bit install paths.

    That restriction is not stylistic. EnsureVBCable used to begin with
    "if VBCableAppearsInstalled then Exit", exactly the thing this comment
    forbids, and it cost a user a working install: VB-CABLE's own Remove
    Driver leaves this key behind, so setup announced that the driver step
    was being skipped while the app -- which tests for the ENDPOINT, not
    the key -- reported no virtual cable installed. Use
    CableEndpointPresent below for any decision that skips work. }
  Result := RegKeyExists(HKLM32, 'SOFTWARE\VB-Audio\Cable') or
            RegKeyExists(HKLM64, 'SOFTWARE\VB-Audio\Cable');
end;

function CableEndpointPresent: Boolean;
var
  ResultCode: Integer;
  Exe: String;
begin
  { Asks the app, so that setup and the app cannot hold different opinions
    about whether a cable exists. --check-cable exits 0 for present, 1 for
    absent and 3 for "cannot tell" (not Windows, or pycaw/comtypes
    missing); see assassin_live/__main__.py. Anything other than a clean 0
    or 1 falls back to the registry guess, because an unanswerable question
    must not read as "no cable, install a driver".

    Only callable after the files are installed, which is why every caller
    is in the post-install step. }
  Exe := ExpandConstant('{app}\{#MyAppExeName}');
  if not FileExists(Exe) then
  begin
    Result := VBCableAppearsInstalled;
    Exit;
  end;
  if not Exec(Exe, '--check-cable', ExtractFileDir(Exe), SW_HIDE,
              ewWaitUntilTerminated, ResultCode) then
  begin
    Result := VBCableAppearsInstalled;
    Exit;
  end;
  if ResultCode = 0 then
    Result := True
  else if ResultCode = 1 then
    Result := False
  else
    Result := VBCableAppearsInstalled;
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
            'so nothing is broken -- it just needs installing by hand:' + #13#10#13#10 +
            '  1. Download the ZIP from vb-cable.com' + #13#10 +
            '  2. Extract it' + #13#10 +
            '  3. Right-click VBCABLE_Setup_x64.exe, Run as administrator' + #13#10 +
            '  4. Reboot' + #13#10#13#10 +
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
  { The only test that decides whether work gets skipped -- see the note on
    VBCableAppearsInstalled for what skipping on the registry key cost. }
  if CableEndpointPresent then
    Exit;

  if VBCableAppearsInstalled then
  begin
    { Registered, but Windows is not listing the device. Two causes and no
      way to tell them apart from here: a reboot is pending after an
      install, or VB-CABLE was removed and left its key behind. Running its
      installer again is right for both, so ask rather than guess -- and
      ask, rather than silently install a driver the user was never
      offered, because the task checkbox was hidden on the strength of that
      same key. }
    if MsgBox('Windows is not listing VB-CABLE''s audio device, even though ' +
              'VB-CABLE is registered on this machine.' + #13#10#13#10 +
              'That happens if the driver was removed and left traces ' +
              'behind, or if it was installed and the machine has not been ' +
              'restarted yet. Music Assassin Live cannot route audio ' +
              'without the device.' + #13#10#13#10 +
              'Install or repair VB-CABLE now?',
              mbConfirmation, MB_YESNO) = IDNO then
    begin
      SayManualSteps('VB-CABLE''s audio device is missing and you chose not ' +
                     'to reinstall it now.');
      Exit;
    end;
  end
  else if not WizardIsTaskSelected('vbcable') then
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

function UpdateReadyMemo(const Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  { Says out loud that the driver step is being skipped, and why. Hiding the
    task when a cable is already present is correct -- re-running the
    installer should not re-offer a driver install -- but from the outside it
    reads as the installer having forgotten to ask, which is how it was
    reported. There is no [Components] or [Types] section and the program
    group page is disabled, so directory and tasks are the whole memo. }
  Result := MemoDirInfo;
  if MemoTasksInfo <> '' then
    Result := Result + NewLine + NewLine + MemoTasksInfo;
  if VBCableAppearsInstalled then
    Result := Result + NewLine + NewLine + 'Virtual audio device:' + NewLine +
              Space + 'VB-CABLE looks already installed, so the driver step ' +
              'will be skipped.' + NewLine + Space +
              'If its audio device turns out to be missing, setup will ' +
              'offer to reinstall it.';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    { Before the exe is deleted, while it can still undo its own routing.
      Uninstalling while the filter is on would otherwise leave the cable
      holding the system default and no app left to hand it back. --headless
      keeps it silent: nobody wants a dialog mid-uninstall. }
    if FileExists(ExpandConstant('{app}\{#MyAppExeName}')) then
      Exec(ExpandConstant('{app}\{#MyAppExeName}'), '--recover --headless', '',
           SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;

  if CurUninstallStep = usPostUninstall then
  begin
    if VBCableAppearsInstalled then
      MsgBox('Music Assassin Live has been removed.' + #13#10#13#10 +
             'VB-CABLE has been left installed, which is why "CABLE Input" ' +
             'and "CABLE In 16ch" are still listed among your playback ' +
             'devices. It is a separate driver from VB-Audio, not part of ' +
             'this app, and other software may be using it -- so removing ' +
             'it is not something this uninstaller does for you.' + #13#10#13#10 +
             'To remove it yourself: run VBCABLE_Setup_x64.exe from the ' +
             'VB-CABLE download as administrator and choose Remove Driver, ' +
             'then reboot.' + #13#10#13#10 +
             'If sound is still going nowhere, open Sound settings and ' +
             'choose your speakers or headphones as the output device.',
             mbInformation, MB_OK);
  end;
end;
