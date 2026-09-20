@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title Intern-Register-Tool 一键启动
cd /d "%~dp0"

set "VENVPY=%~dp0.venv\Scripts\python.exe"

rem ============ 0. 带参数直接跑（自动化 / 命令行复用） ============
if not "%~1"=="" (
    call :ensure_env
    if errorlevel 1 exit /b 1
    call :ensure_deps
    if errorlevel 1 exit /b 1
    call :ensure_envfile
    if errorlevel 1 exit /b 1
    "%VENVPY%" run.py %*
    set "rc=!errorlevel!"
    echo.
    echo [cli] run.py 退出码：!rc!
    exit /b !rc!
)

rem ============ 1. 确保 .venv 存在 ============
if not exist "%VENVPY%" (
    echo [首次运行] 未发现 .venv，正在创建虚拟环境...
    python -m venv .venv
    if errorlevel 1 py -m venv .venv
    if not exist "%VENVPY%" (
        echo [错误] 创建 .venv 失败。请确认已安装 Python 3.11+ 且已加入 PATH：
        echo        https://www.python.org/downloads/
        pause
        exit /b 1
    )
)

rem ============ 2. 确保依赖已安装 ============
"%VENVPY%" -c "import curl_cffi, requests, playwright" >nul 2>nul
if errorlevel 1 (
    echo [首次运行] 正在安装依赖（requirements.txt）...
    "%VENVPY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络后重试。
        pause
        exit /b 1
    )
)

rem ============ 3. 确保 .env 已配置 ============
if not exist "%~dp0.env" (
    copy /y "%~dp0.env.example" "%~dp0.env" >nul
    echo.
    echo   ⚠ 已从 .env.example 生成 .env
    echo     请编辑 .env 填入凭据后，重新双击本脚本：
    echo     - worker 模式：IR_WORKER_ADMIN_TOKEN / IR_WORKER_BASE / IR_WORKER_DOMAIN
    echo     - yyds 模式：  IR_MAIL_PROVIDER=yyds + IR_YYDS_API_KEY
    echo     - 浏览器：IR_CHROME_PATH 指向本机 chrome.exe（不填则用默认路径）
    start notepad "%~dp0.env"
    pause
    exit /b 1
)

rem ============ 4. 交互菜单 ============
:menu
cls
echo.
echo   ┌─────────────────────────────────────────────────┐
echo   │       Intern-Register-Tool  一键启动           │
echo   └─────────────────────────────────────────────────┘
echo.
echo     1) 跑 1 个账号（无头，默认）
echo     2) 跑 N 个账号（输入数量）
echo     3) 跑 N 个账号并导出到指定 JSON 文件
echo     4) 有头模式跑 N 个（弹浏览器窗口）
echo     5) 自定义参数（直接传给 run.py）
echo     0) 退出
echo.
set "CHOICE="
set /p "CHOICE=输入序号回车："
if not defined CHOICE set "CHOICE=1"

if "!CHOICE!"=="1" (
    call :run run.py --headless
    goto :back
)
if "!CHOICE!"=="2" (
    call :ask_count
    call :run run.py --count !COUNT!
    goto :back
)
if "!CHOICE!"=="3" (
    call :ask_count
    set "OUT="
    set /p "OUT=输出文件名（如 results_1.json，直接回车=不指定）："
    if not defined OUT (
        call :run run.py --count !COUNT!
    ) else (
        call :run run.py --count !COUNT! --out "!OUT!"
    )
    goto :back
)
if "!CHOICE!"=="4" (
    call :ask_count
    call :run run.py --count !COUNT! --headful
    goto :back
)
if "!CHOICE!"=="5" (
    set "EXTRA="
    set /p "EXTRA=额外参数（如 --count 10 --workers 4 --ignore-quota）："
    call :run run.py !EXTRA!
    goto :back
)
if "!CHOICE!"=="0" exit /b 0

echo   无效输入，请重新选择。
timeout /t 2 >nul
goto :menu

:back
echo.
set "AGAIN="
set /p "AGAIN=回车继续 / 输入 0 退出："
if "!AGAIN!"=="0" exit /b 0
goto :menu

rem ============ 子过程 ============
:ask_count
set "COUNT="
set /p "COUNT=账号数量（默认 1）："
if not defined COUNT set "COUNT=1"
exit /b 0

:run
echo.
echo [cli] 执行：%*
"%VENVPY%" %*
echo.
echo [cli] 执行完毕（退出码 !errorlevel!）
pause
exit /b 0

:ensure_env
if exist "%VENVPY%" exit /b 0
echo [首次运行] 正在创建虚拟环境 .venv ...
python -m venv .venv
if errorlevel 1 py -m venv .venv
if not exist "%VENVPY%" (
    echo [错误] 创建 .venv 失败。请安装 Python 3.11+：https://www.python.org/downloads/
    exit /b 1
)
exit /b 0

:ensure_deps
"%VENVPY%" -c "import curl_cffi, requests, playwright" >nul 2>nul
if errorlevel 1 (
    echo [首次运行] 正在安装依赖（requirements.txt）...
    "%VENVPY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络后重试。
        exit /b 1
    )
)
exit /b 0

:ensure_envfile
if exist "%~dp0.env" exit /b 0
copy /y "%~dp0.env.example" "%~dp0.env" >nul
echo [提示] 已生成 .env，请编辑填入凭据后重试。
exit /b 1
