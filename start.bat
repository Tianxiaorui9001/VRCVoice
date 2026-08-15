@echo off
cd /d "%~dp0"
rem Prevent double instance: two instances race on model files and break local ASR.
powershell -NoProfile -WindowStyle Hidden -Command "$c = (Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -match 'main\.py' } | Measure-Object).Count; if ($c -gt 0) { exit 1 } else { exit 0 }"
if %errorlevel% gtr 0 (
    echo [VRCVoice] Already running, skip.
    timeout /t 3 >nul
    exit /b 0
)
start "" ".venv\Scripts\pythonw.exe" main.py
