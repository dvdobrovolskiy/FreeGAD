@echo off
rem SPDX-License-Identifier: AGPL-3.0-or-later
rem Copyright (C) 2026 Dmitriy Dobrovolskiy dima@dobrovolskiy.com

setlocal
cd /d "%~dp0"

rem Auto-increment patch version so every installer build is a newer version
set VERSION=1.0.0
if exist version.txt set /p VERSION=<version.txt
for /f "tokens=1-3 delims=." %%a in ("%VERSION%") do (
    set MAJOR=%%a
    set MINOR=%%b
    set /a PATCH=%%c+1
)
set VERSION=%MAJOR%.%MINOR%.%PATCH%
>version.txt echo %VERSION%
echo Building FreeGAD installer version %VERSION%

rem Keep package.xml / freegad/__init__.py / ui.py in sync with version.txt
powershell -NoProfile -Command ^
  "(Get-Content package.xml) -replace '<version>.*</version>', '<version>%VERSION%</version>' | Set-Content package.xml;" ^
  "(Get-Content freegad\__init__.py) -replace '__version__ = \".*\"', '__version__ = \"%VERSION%\"' | Set-Content freegad\__init__.py;" ^
  "(Get-Content freegad\ui.py) -replace 'VERSION = \".*\"', 'VERSION = \"%VERSION%\"' | Set-Content freegad\ui.py"

rem Locate Inno Setup compiler
set ISCC=
for %%p in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe") do (
    if not defined ISCC if exist %%p set "ISCC=%%~p"
)
if not defined ISCC for /f "delims=" %%i in ('where ISCC 2^>nul') do if not defined ISCC set "ISCC=%%i"
if not defined ISCC (
    echo ERROR: Inno Setup ISCC.exe not found. Install from https://jrsoftware.org/isinfo.php
    exit /b 1
)

"%ISCC%" /DMyAppVersion=%VERSION% Installer.iss
if errorlevel 1 exit /b 1
echo Installer built: FreeGADSetup.exe (v%VERSION%)
