@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title InternRegisterWeb 打包
cd /d "%~dp0"

set "VENVPY=%~dp0.venv\Scripts\python.exe"

if not exist "%VENVPY%" (
    echo [首次运行] 正在创建虚拟环境 .venv ...
    python -m venv .venv
    if errorlevel 1 py -m venv .venv
    if not exist "%VENVPY%" (
        echo [错误] 创建 .venv 失败。请安装 Python 3.11+：https://www.python.org/downloads/
        pause
        exit /b 1
    )
)

echo [1/3] 安装依赖...
"%VENVPY%" -m pip install -r requirements.txt
if errorlevel 1 ( echo [错误] 依赖安装失败 & pause & exit /b 1 )

echo [2/3] 安装 PyInstaller...
"%VENVPY%" -m pip install pyinstaller
if errorlevel 1 ( echo [错误] PyInstaller 安装失败 & pause & exit /b 1 )

echo [3/3] 开始打包（单文件）...
"%VENVPY%" -m PyInstaller packaging\web.spec --noconfirm --distpath dist
if errorlevel 1 ( echo [错误] 打包失败，请查看上方日志 & pause & exit /b 1 )

echo.
echo  ✔ 打包完成：dist\InternRegisterWeb.exe
echo    双击该文件即可启动 Web 控制台（自动打开浏览器）。
echo    想再次打包请重新运行本脚本。
pause
