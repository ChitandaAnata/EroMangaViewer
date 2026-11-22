@echo off
chcp 65001 >nul
cd /d "%~dp0"
title MangaShelf dev

REM Run the CURRENT version from source. No packaging, no install.
REM   web/     -> press F5 in the window
REM   server/  -> close the window, run this again
REM   desktop/ -> close the window, run this again (rebuilds, ~20s)
REM The shell is built to this folder as MangaShelf.exe and runs from source.
REM Data lives in this folder's data\, separate from any installed copy.

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY if exist "D:\python\python.exe" set "PY=D:\python\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"

if not defined PY goto nopython
where dotnet >nul 2>nul
if errorlevel 1 goto nodotnet

set "MANGASHELF_DEV=1"
"%PY%" tools\build_dev.py %*
if errorlevel 1 pause
exit /b 0

:nopython
echo.
echo   [ERROR] Python not found. Dev mode needs Python 3.10 or newer.
echo.
pause
exit /b 1

:nodotnet
echo.
echo   [ERROR] dotnet not found. Building the desktop shell needs the .NET 10 SDK.
echo           To tweak the UI only, double-click start.bat and use the browser.
echo.
pause
exit /b 1
