; X-Ray-lab Inno Setup installer script
; Compile with Inno Setup (https://jrsoftware.org/isinfo.php)
; Before compiling: run build.ps1 so that dist\Xray_labs exists

#define MyAppName "X-Ray-lab"
#ifndef MyAppVersion
#define MyAppVersion "0.0.9"
#endif
#define MyAppPublisher "Granik115"
#define MyAppURL "https://github.com/Granik115/Xray_labs"
#define MyAppExeName "Xray_labs.exe"

[Setup]
AppId={{B7C8D9E0-F1A2-3456-BCDE-F78901234567}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=..\releases
OutputBaseFilename=Xray_labs-{#MyAppVersion}-setup
SetupIconFile=..\icon_cat.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
CloseApplications=yes
RestartApplications=no
MinVersion=10.0

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\Xray_labs\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
