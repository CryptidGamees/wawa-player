[Setup]
AppName=Glass Music Player
AppVersion=1.0
AppPublisher=Me
DefaultDirName={autopf}\GlassPlayer
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=output
OutputBaseFilename=GlassPlayer-Setup
Compression=lzma2
SolidCompression=yes
UninstallDisplayName=Glass Music Player
UninstallDisplayIcon={app}\GlassPlayer.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "dist\GlassPlayer\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Glass Music Player"; Filename: "{app}\GlassPlayer.exe"
Name: "{autodesktop}\Glass Music Player"; Filename: "{app}\GlassPlayer.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\GlassPlayer.exe"; Description: "Launch Glass Music Player"; Flags: nowait postinstall skipifsilent
