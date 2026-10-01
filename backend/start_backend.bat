@echo off
title videopril-backend
cd /d X:\videopril\backend

set "VIDEOPRIL_PYTHON=C:\Users\Niyaz\AppData\Local\Programs\Python\Python312\python.exe"
set "PYTHONPATH=C:\Users\Niyaz\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\LocalCache\local-packages\Python312\site-packages"

if not exist "%VIDEOPRIL_PYTHON%" (
  set "VIDEOPRIL_PYTHON=python"
)

echo Starting videopril API on http://127.0.0.1:8000
"%VIDEOPRIL_PYTHON%" -m uvicorn main:app --host 0.0.0.0 --port 8000

echo.
echo Backend stopped or failed to start.
pause
