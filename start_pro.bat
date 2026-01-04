@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title Project Chimera: Zaiwen 2API - 智能启动器

:: ==========================================
:: Project Chimera - Smart Launcher (Chinese Edition)
:: 功能:
::   - 自动检测/下载 Python
::   - 自动创建虚拟环境
::   - 实时依赖库安装日志
::   - 极速启动模式 (Marker File)
:: ==========================================

cd /d "%~dp0"

:: 配置
set "APP_NAME=Zaiwen 2API 智能服务"
set "PYTHON_VERSION=3.11.9"
set "PYTHON_DIR=%~dp0python"
set "VENV_DIR=%~dp0venv"
set "MARKER_FILE=%~dp0.env_ready"
set "PYTHON_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
set "GET_PIP_URL=https://bootstrap.pypa.io/get-pip.py"

:: 显示标题
echo.
echo ==========================================
echo    %APP_NAME% - 智能启动器
echo ==========================================
echo.

:: 极速检查 - 如果标记文件存在，跳过完整检查
if exist "%MARKER_FILE%" (
    echo [*] 极速模式: 环境已在之前验证通过
    echo.
    goto :run_app
)

echo [*] 初次运行或环境需要检查...
echo.

:: ==========================================
:: 步骤 1: 检查 Python 环境
:: ==========================================
echo [1/4] 正在检查 Python 环境...

set "PYTHON_EXE="
set "USE_EMBEDDED=0"

:: 优先级 1: 检查嵌入式 Python
if exist "%PYTHON_DIR%\python.exe" (
    set "PYTHON_EXE=%PYTHON_DIR%\python.exe"
    set "USE_EMBEDDED=1"
    echo      [+] 发现嵌入式 Python
    goto :python_found
)

:: 优先级 2: 检查系统 Python
where python >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set "SYSTEM_PY_VER=%%v"
    echo      [+] 发现系统 Python: !SYSTEM_PY_VER!
    
    :: 检查版本 >= 3.8
    for /f "tokens=1,2 delims=." %%a in ("!SYSTEM_PY_VER!") do (
        if %%a geq 3 if %%b geq 8 (
            set "PYTHON_EXE=python"
            echo      [+] 版本符合要求，使用系统 Python
            goto :python_found
        )
    )
    echo      [-] 版本过低，需要 Python 3.8+
)

:: 没有找到合适的 Python，下载嵌入式版本
echo      [-] 未找到合适的 Python，正在下载嵌入式版本...
goto :download_python

:python_found
echo      [OK] Python 环境就绪
echo.
goto :check_venv

:: ==========================================
:: 步骤 2: 下载嵌入式 Python
:: ==========================================
:download_python
echo.
echo [*] 正在下载 Python %PYTHON_VERSION% 嵌入版...
echo     URL: %PYTHON_URL%
echo.

:: 创建 python 目录
if not exist "%PYTHON_DIR%" mkdir "%PYTHON_DIR%"

:: 使用 PowerShell 下载 (带进度条)
set "PYTHON_ZIP=%PYTHON_DIR%\python.zip"
echo     正在下载...
powershell -Command "& {$ProgressPreference = 'Continue'; [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%PYTHON_ZIP%' -UseBasicParsing}"

if not exist "%PYTHON_ZIP%" (
    echo.
    echo [错误] Python 下载失败。请检查网络连接。
    echo         您可以手动安装 Python 3.8+ 并重试。
    pause
    exit /b 1
)

:: 解压
echo     正在解压...
powershell -Command "& {Expand-Archive -Path '%PYTHON_ZIP%' -DestinationPath '%PYTHON_DIR%' -Force}"
del "%PYTHON_ZIP%" 2>nul

:: 启用 pip 支持 - 修改 python311._pth
set "PTH_FILE=%PYTHON_DIR%\python311._pth"
if exist "%PTH_FILE%" (
    echo python311.zip> "%PTH_FILE%"
    echo .>> "%PTH_FILE%"
    echo Lib\site-packages>> "%PTH_FILE%"
    echo import site>> "%PTH_FILE%"
)

:: 下载并安装 pip
echo.
echo [*] 正在安装 pip...
set "GET_PIP=%PYTHON_DIR%\get-pip.py"
powershell -Command "& {$ProgressPreference = 'Continue'; [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%GET_PIP_URL%' -OutFile '%GET_PIP%' -UseBasicParsing}"

"%PYTHON_DIR%\python.exe" "%GET_PIP%"
del "%GET_PIP%" 2>nul

set "PYTHON_EXE=%PYTHON_DIR%\python.exe"
set "USE_EMBEDDED=1"
echo      [OK] 嵌入式 Python 安装完成
echo.

:: ==========================================
:: 步骤 3: 检查/创建虚拟环境
:: ==========================================
:check_venv
echo [2/4] 正在检查虚拟环境...

:: 对于嵌入式 Python，跳过 venv (直接使用嵌入式环境)
if "%USE_EMBEDDED%"=="1" (
    echo      [+] 使用嵌入式 Python，跳过虚拟环境创建
    set "PIP_EXE=%PYTHON_DIR%\Scripts\pip.exe"
    if not exist "!PIP_EXE!" set "PIP_EXE=%PYTHON_DIR%\python.exe -m pip"
    echo.
    goto :check_deps
)

:: 检查 venv 是否存在
if exist "%VENV_DIR%\Scripts\python.exe" (
    echo      [OK] 虚拟环境已存在
    set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
    set "PIP_EXE=%VENV_DIR%\Scripts\pip.exe"
    echo.
    goto :check_deps
)

:: 创建虚拟环境
echo      [+] 正在创建虚拟环境...
python -m venv "%VENV_DIR%"
if %errorlevel% neq 0 (
    echo [错误] 虚拟环境创建失败
    pause
    exit /b 1
)

set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "PIP_EXE=%VENV_DIR%\Scripts\pip.exe"
echo      [OK] 虚拟环境创建成功
echo.

:: ==========================================
:: 步骤 4: 检查/安装依赖库
:: ==========================================
:check_deps
echo [3/4] 正在检查依赖库...

:: 快速检查 - 尝试导入关键模块
"%PYTHON_EXE%" -c "import fastapi; import uvicorn; import httpx; import loguru; import aiosqlite; import PySide6; import multipart" 2>nul
if %errorlevel% equ 0 (
    echo      [OK] 所有依赖库已安装
    echo.
    goto :create_marker
)

:: 安装缺失的依赖库 (显示完整输出以便查看)
echo.
echo      [-] 发现缺失依赖，正在安装...
echo      ============================================
echo.

if "%USE_EMBEDDED%"=="1" (
    echo [pip] 正在使用 requirements.txt 安装...
    "%PYTHON_EXE%" -m pip install -r requirements.txt --no-warn-script-location
) else (
    echo [pip] 正在使用 requirements.txt 安装...
    "%PIP_EXE%" install -r requirements.txt
)

echo.
if %errorlevel% neq 0 (
    echo [错误] 部分依赖安装失败
    echo         请检查网络连接或更换 PyPI 源。
    pause
    exit /b 1
)
echo      ============================================
echo      [OK] 依赖库安装成功！
echo.

:: ==========================================
:: 步骤 5: 创建标记文件
:: ==========================================
:create_marker
echo [4/4] 正在完成设置...

:: 创建带时间戳的标记文件
echo Environment validated on %date% %time%> "%MARKER_FILE%"
echo Python: %PYTHON_EXE%>> "%MARKER_FILE%"
echo      [OK] 环境准备就绪
echo.

:: ==========================================
:: 运行应用程序
:: ==========================================
:run_app
echo ==========================================
echo    正在启动 Zaiwen 2API 服务...
echo    服务地址: http://127.0.0.1:8000
echo    请勿关闭此窗口
echo ==========================================
echo.

:: 确定 Python 执行路径 (再次确认以防万一)
if exist "%VENV_DIR%\Scripts\python.exe" (
    set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
) else if exist "%PYTHON_DIR%\python.exe" (
    set "PYTHON_EXE=%PYTHON_DIR%\python.exe"
) else (
    set "PYTHON_EXE=python"
)

:: 运行主程序
"%PYTHON_EXE%" main.py

if %errorlevel% neq 0 (
    echo.
    echo [错误] 应用程序异常退出，错误代码: %errorlevel%
    echo.
    :: 删除标记文件以强制下次重新检查
    del "%MARKER_FILE%" 2>nul
    pause
)

endlocal
