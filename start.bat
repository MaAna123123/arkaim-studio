@echo off
setlocal
chcp 65001 >nul

set "PROJECT_ROOT=%~dp0"
set "TEMP=%PROJECT_ROOT%temp"
set "TMP=%PROJECT_ROOT%temp"
set "TMPDIR=%PROJECT_ROOT%temp"
set "HF_HOME=%PROJECT_ROOT%models_cache"
set "PIP_CACHE_DIR=%PROJECT_ROOT%cache\pip"
set "npm_config_cache=%PROJECT_ROOT%cache\npm"
set "PATH=%PROJECT_ROOT%runtime\ffmpeg\bin;%PROJECT_ROOT%runtime\python;%PROJECT_ROOT%runtime\python\Scripts;%PROJECT_ROOT%runtime\node;%PATH%"

if not exist "%TEMP%" mkdir "%TEMP%"
if not exist "%HF_HOME%" mkdir "%HF_HOME%"
if not exist "%PIP_CACHE_DIR%" mkdir "%PIP_CACHE_DIR%"
if not exist "%npm_config_cache%" mkdir "%npm_config_cache%"

if not exist "%PROJECT_ROOT%runtime\python\python.exe" (
  echo [ОШИБКА] Не найден локальный Python: runtime\python\python.exe
  pause
  exit /b 1
)

if not exist "%PROJECT_ROOT%runtime\node\node.exe" (
  echo [ОШИБКА] Не найден локальный Node.js: runtime\node\node.exe
  pause
  exit /b 1
)

if not exist "%PROJECT_ROOT%runtime\ffmpeg\bin\ffmpeg.exe" (
  echo [ОШИБКА] Не найден локальный FFmpeg: runtime\ffmpeg\bin\ffmpeg.exe
  pause
  exit /b 1
)

echo ==============================================
echo     videopril — локальный запуск с диска E:
echo ==============================================
echo.

echo [1/2] Запускаю Backend...
start "videopril-backend" cmd /k call "%PROJECT_ROOT%backend\start_backend.bat"

echo Ожидаю Backend на порту 8000...
set "BACKEND_READY="
for /l %%I in (1,1,60) do (
  powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8000/api/health; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>&1
  if not errorlevel 1 (
    set "BACKEND_READY=1"
    goto backend_ready
  )
  timeout /t 1 /nobreak >nul
)

:backend_ready
if not defined BACKEND_READY (
  echo [ОШИБКА] Backend не запустился. Проверьте окно videopril-backend.
  pause
  exit /b 1
)

echo [2/2] Запускаю Frontend...
start "videopril-frontend" cmd /k call "%PROJECT_ROOT%frontend\start_frontend.bat"

timeout /t 3 /nobreak >nul
set "APP_URL=http://127.0.0.1:5173/"
set "CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" set "CHROME_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" set "CHROME_EXE=%LocalAppData%\Google\Chrome\Application\chrome.exe"

if exist "%CHROME_EXE%" (
  start "" "%CHROME_EXE%" --incognito "%APP_URL%"
) else (
  echo [ПРЕДУПРЕЖДЕНИЕ] Google Chrome не найден. Открываю браузер по умолчанию.
  start "" "%APP_URL%"
)

echo.
echo Приложение: %APP_URL% ^(Chrome Incognito^)
echo Backend API: http://127.0.0.1:8000
echo Все runtime, зависимости, кэши и временные файлы находятся в:
echo %PROJECT_ROOT%
pause
endlocal
