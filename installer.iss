[Setup]
; AppId and install folder stay the same as the old version so it upgrades in place
AppId=Glass Music Player
AppName=Wawa Player
AppVersion=1.1
AppPublisher=Me
DefaultDirName={autopf}\GlassPlayer
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=output
OutputBaseFilename=WawaPlayer-Setup
Compression=lzma2
SolidCompression=yes
UninstallDisplayName=Wawa Player
UninstallDisplayIcon={app}\WawaPlayer.exe

[InstallDelete]
Type: files; Name: "{app}\GlassPlayer.exe"
Type: files; Name: "{autoprograms}\Glass Music Player.lnk"
Type: files; Name: "{autodesktop}\Glass Music Player.lnk"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "dist\WawaPlayer\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Wawa Player"; Filename: "{app}\WawaPlayer.exe"
Name: "{autodesktop}\Wawa Player"; Filename: "{app}\WawaPlayer.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\WawaPlayer.exe"; Description: "Launch Wawa Player"; Flags: nowait postinstall skipifsilent
