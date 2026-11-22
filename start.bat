@echo off
chcp 65001 >nul
cd /d "%~dp0"
title MangaShelf 本地漫画书架

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY if exist "D:\python\python.exe" set "PY=D:\python\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"

if not defined PY (
    echo.
    echo [错误] 没有找到 Python。请先安装 Python 3.9 或更高版本：
    echo        https://www.python.org/downloads/
    echo        安装时记得勾选 "Add Python to PATH"。
    echo.
    pause
    exit /b 1
)

"%PY%" -c "import py7zr, rarfile, PIL" >nul 2>nul
if errorlevel 1 (
    echo [初始化] 首次运行，正在安装解压与图像处理依赖…
    "%PY%" -m pip install --quiet py7zr rarfile pillow
    if errorlevel 1 (
        echo.
        echo [警告] 依赖安装失败，ZIP/CBZ 仍可正常使用，RAR/7z 可能读取不了。
        echo.
    )
)

"%PY%" app.py %*

if errorlevel 1 pause
