; EduCore Windows installer (Inno Setup 6)
; Build prerequisites:
;   1. frontend/dist exists (npm run build)
;   2. dist/EduCore/EduCore.exe exists (pyinstaller packaging/educore.spec)
;   3. ISCC.exe available from Inno Setup 6

#define MyAppName "EduCore"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "EduCore"
#define MyAppExeName "EduCore.exe"

[Setup]
AppId={{B4A1C0E8-6B0E-4B1D-9E3F-7C2D5A6F8B11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=EduCore_Setup_{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
Source: "..\dist\EduCore\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 EduCore"; Flags: nowait postinstall skipifsilent