@echo off
setlocal
chcp 65001 >nul

for %%I in ("%~dp0..") do set "PROJECT_ROOT=%%~fI"
set "TEMP=%PROJECT_ROOT%\temp"
set "TMP=%PROJECT_ROOT%\temp"
set "TMPDIR=%PROJECT_ROOT%\temp"
set "HF_HOME=%PROJECT_ROOT%\models_cache"
set "PIP_CACHE_DIR=%PROJECT_ROOT%\cache\pip"
set "PATH=%PROJECT_ROOT%\runtime\ffmpeg\bin;%PROJECT_ROOT%\runtime\python;%PROJECT_ROOT%\runtime\python\Scripts;%PATH%"

if not exist "%TEMP%" mkdir "%TEMP%"
if not exist "%HF_HOME%" mkdir "%HF_HOME%"
if not exist "%PIP_CACHE_DIR%" mkdir "%PIP_CACHE_DIR%"

cd /d "%PROJECT_ROOT%\backend"
echo Backend: http://127.0.0.1:8000
"%PROJECT_ROOT%\runtime\python\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000

echo.
echo Backend остановлен или не смог запуститься.
pause
endlocal
