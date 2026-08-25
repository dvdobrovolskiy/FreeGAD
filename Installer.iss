; SPDX-License-Identifier: AGPL-3.0-or-later
; Copyright (C) 2026 Dmitriy Dobrovolskiy dima@dobrovolskiy.com

; FreeGAD - Inno Setup script. Build with make_installer.bat (reads version.txt).
#ifndef MyAppVersion
  #define VerFile FileOpen("version.txt")
  #define MyAppVersion Trim(FileRead(VerFile))
  #expr FileClose(VerFile)
#endif

[Setup]
AppId={{7C1E2B7A-5B3E-4C0F-9D2A-3F1C8A6E5B21}
AppName=FreeGAD
AppVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
AppPublisher=Dmitry Dobrovolskiy
; Per-user install into FreeCAD's user addon folder - no admin rights needed.
PrivilegesRequired=lowest
DefaultDirName={userappdata}\FreeCAD\Mod\FreeGAD
DisableDirPage=yes
DefaultGroupName=FreeGAD
DisableProgramGroupPage=yes
OutputDir=.
OutputBaseFilename=FreeGADSetup
Compression=lzma2
SolidCompression=yes
UninstallFilesDir={app}
WizardStyle=modern

[Files]
Source: "Init.py";        DestDir: "{app}"; Flags: ignoreversion
Source: "InitGui.py";     DestDir: "{app}"; Flags: ignoreversion
Source: "package.xml";    DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE";        DestDir: "{app}"; Flags: ignoreversion
Source: "README.md";      DestDir: "{app}"; Flags: ignoreversion
Source: "version.txt";    DestDir: "{app}"; Flags: ignoreversion
Source: "freegad\*.py";   DestDir: "{app}\freegad"; Flags: ignoreversion
Source: "resources\*";    DestDir: "{app}\resources"; Flags: ignoreversion recursesubdirs

[InstallDelete]
Type: filesandordirs; Name: "{app}\freegad\__pycache__"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\freegad\__pycache__"
Type: filesandordirs; Name: "{app}"

[Messages]
FinishedLabel=FreeGAD has been installed.%n%nStart FreeCAD and open the FreeGAD menu (or switch to the FreeGAD workbench). You can change the API key any time via FreeGAD > Set API key.

[Code]
var
  KeyPage: TInputQueryWizardPage;
  ProviderPage: TInputOptionWizardPage;

function JsonEscape(const S: String): String;
begin
  Result := S;
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, '"', '\"', True);
end;

procedure InitializeWizard;
begin
  ProviderPage := CreateInputOptionPage(wpWelcome,
    'AI provider', 'Which API should FreeGAD talk to?',
    'Both need your own API key. You can switch providers later in FreeGAD > Settings.', True, False);
  ProviderPage.Add('Anthropic - Claude models (api.anthropic.com)');
  ProviderPage.Add('OpenAI-compatible - OpenAI, OpenRouter or any compatible server');
  ProviderPage.Values[0] := True;

  KeyPage := CreateInputQueryPage(ProviderPage.ID,
    'API key', 'Optional - you can also set it later inside FreeCAD (FreeGAD > Set API key)',
    'Paste your API key. Leave empty to skip. It is stored encrypted for your Windows account in ' +
    '%APPDATA%\FreeGAD\config.json. The base URL is used only for OpenAI-compatible providers ' +
    '(OpenAI: https://api.openai.com/v1, OpenRouter: https://openrouter.ai/api/v1).');
  KeyPage.Add('API key:', True);
  KeyPage.Add('Base URL (OpenAI-compatible only):', False);
  KeyPage.Values[1] := 'https://api.openai.com/v1';
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = KeyPage.ID then
    KeyPage.Edits[1].Enabled := ProviderPage.Values[1];
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Key: String;
  Provider: String;
begin
  if CurStep <> ssPostInstall then exit;
  Key := Trim(KeyPage.Values[0]);
  if Key = '' then exit;
  if ProviderPage.Values[1] then Provider := 'openai' else Provider := 'anthropic';
  ForceDirectories(ExpandConstant('{userappdata}\FreeGAD'));
  { The plugin reads this file on first load, stores the key DPAPI-encrypted in config.json and deletes it. }
  SaveStringToFile(ExpandConstant('{userappdata}\FreeGAD\apikey.pending'),
    '{"provider":"' + Provider + '","apiKey":"' + JsonEscape(Key) + '","baseUrl":"' + JsonEscape(Trim(KeyPage.Values[1])) + '"}', False);
end;
