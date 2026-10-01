@echo off
echo ==============================================
echo     videopril — AI Video Highlight Clipper
echo ==============================================
echo.

:: Start Backend
echo [1/2] Запускаю Backend (FastAPI)...
start "videopril-backend" cmd /k call "X:\videopril\backend\start_backend.bat"

echo Ожидаю готовности Backend на порту 8000...
set "BACKEND_READY="
for /l %%I in (1,1,30) do (
  powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8000/api/health; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1" > nul 2>&1
  if not errorlevel 1 (
    set "BACKEND_READY=1"
    goto backend_ready
  )
  timeout /t 1 /nobreak > nul
)

:backend_ready
if not defined BACKEND_READY (
  echo [ОШИБКА] Backend не запустился. Посмотрите сообщение в окне videopril-backend.
  pause
  exit /b 1
)
echo Backend готов.

:: Start Frontend
echo [2/2] Запускаю Frontend (Vite)...
start "videopril-frontend" cmd /k "cd /d x:\videopril\frontend && node x:\nodejs\node_modules\npm\bin\npm-cli.js run dev"

timeout /t 3 /nobreak > nul

:: Open browser
echo.
echo Открываю приложение в браузере...
start "" "http://localhost:5173"

echo.
echo Готово! Приложение доступно на http://localhost:5173
echo Backend API:  http://localhost:8000
pause
