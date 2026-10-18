@echo off
setlocal
chcp 65001 >nul

for %%I in ("%~dp0..") do set "PROJECT_ROOT=%%~fI"
set "TEMP=%PROJECT_ROOT%\temp"
set "TMP=%PROJECT_ROOT%\temp"
set "TMPDIR=%PROJECT_ROOT%\temp"
set "npm_config_cache=%PROJECT_ROOT%\cache\npm"
set "PATH=%PROJECT_ROOT%\runtime\node;%PROJECT_ROOT%\runtime\ffmpeg\bin;%PATH%"

if not exist "%TEMP%" mkdir "%TEMP%"
if not exist "%npm_config_cache%" mkdir "%npm_config_cache%"

cd /d "%PROJECT_ROOT%\frontend"
if not exist "node_modules\vite\bin\vite.js" (
  echo Устанавливаю frontend-зависимости локально на диск проекта...
  "%PROJECT_ROOT%\runtime\node\npm.cmd" ci --cache "%npm_config_cache%" --no-audit --no-fund
  if errorlevel 1 (
    echo [ОШИБКА] Не удалось установить frontend-зависимости.
    pause
    exit /b 1
  )
)

echo Frontend: http://localhost:5173
"%PROJECT_ROOT%\runtime\node\npm.cmd" run dev -- --host 127.0.0.1

echo.
echo Frontend остановлен или не смог запуститься.
pause
endlocal
