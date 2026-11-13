@echo off
cd /d "%~dp0"
setlocal enabledelayedexpansion

:: 1. Detect Python from local virtual environments or system PATH
set "PYTHON_EXE="
set "PYTHONW_EXE="

if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
    set "PYTHONW_EXE=.venv\Scripts\pythonw.exe"
) else if exist "venv\Scripts\python.exe" (
    set "PYTHON_EXE=venv\Scripts\python.exe"
    set "PYTHONW_EXE=venv\Scripts\pythonw.exe"
) else if exist "ENV\Scripts\python.exe" (
    set "PYTHON_EXE=ENV\Scripts\python.exe"
    set "PYTHONW_EXE=ENV\Scripts\pythonw.exe"
) else if exist "env\Scripts\python.exe" (
    set "PYTHON_EXE=env\Scripts\python.exe"
    set "PYTHONW_EXE=env\Scripts\pythonw.exe"
) else (
    where python >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYTHON_EXE=python"
    )
    where pythonw >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYTHONW_EXE=pythonw"
    )
    if "!PYTHON_EXE!"=="" (
        where py >nul 2>&1
        if !errorlevel! equ 0 (
            set "PYTHON_EXE=py"
            set "PYTHONW_EXE=pyw"
        )
    )
)

if "%PYTHON_EXE%"=="" if "%PYTHONW_EXE%"=="" (
    echo [ERROR] Python was not found in PATH or a local virtual environment [.venv / venv].
    echo Please install Python 3.10+ and ensure PyQt6 is installed.
    echo.
    pause
    exit /b 1
)

:: 2. Silent execution if requested
if /i "%~1"=="--silent" goto :run_silent
if /i "%~1"=="-s" goto :run_silent

:: 3. Run in interactive console so any crash / missing library is visible
echo ========================================================
echo Starting mGBA Python Emulator...
echo Python executable: %PYTHON_EXE%
echo ========================================================

if not "%PYTHON_EXE%"=="" (
    "%PYTHON_EXE%" main.py %*
) else (
    "%PYTHONW_EXE%" main.py %*
)

set EXIT_CODE=%errorlevel%
if %EXIT_CODE% neq 0 (
    echo.
    echo [ERROR] The application exited unexpectedly with code %EXIT_CODE%.
    echo Check the error message above to identify missing requirements.
    echo.
    pause
)
exit /b %EXIT_CODE%

:run_silent
if not "%PYTHONW_EXE%"=="" (
    start "" "%PYTHONW_EXE%" main.py %*
) else (
    start "" "%PYTHON_EXE%" main.py %*
)
exit /b 0