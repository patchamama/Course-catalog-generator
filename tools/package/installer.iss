; ─────────────────────────────────────────────────────────────────────────────
;  Catalog — DMS Lite  |  Inno Setup Installer Script (Windows)
; ─────────────────────────────────────────────────────────────────────────────
;  Requirements: Inno Setup 6+  https://jrsoftware.org/isinfo.php
;  Build:        ISCC tools\package\installer.iss
;  Output:       dist\CatalogDMSLite-Setup.exe
; ─────────────────────────────────────────────────────────────────────────────

#define AppName    "Catalog DMS Lite"
#define AppVersion "1.0.0"
#define AppPublisher "Your Organization"
#define AppURL     "https://github.com/your-org/catalog-dms-lite"
#define AppExe     "Catalog DMS Lite.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
OutputDir=..\..\dist
OutputBaseFilename=CatalogDMSLite-Setup
SetupIconFile=icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked; OnlyBelowVersion: 6.1

[Files]
Source: "..\..\dist\Catalog DMS Lite\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
