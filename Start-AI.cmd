@echo off
setlocal enabledelayedexpansion
title Portable AI
cd /d "%~dp0"

set "ROOT=%~dp0"
set "PY=%ROOT%runtime\python\python.exe"
if not exist "%PY%" set "PY="
if not defined PY (
  where python >nul 2>nul
  if errorlevel 1 (
    echo.
    echo   Portable Python runtime not found.
    echo   Run  tools\fetch-runtime.ps1  once, or install Python 3.11+ on this PC.
    echo.
    pause
    exit /b 1
  )
  set "PY=python"
)

if not exist "%ROOT%models\Qwen3-4B-Instruct-2507-Q4_K_M.gguf" (
  if not exist "%ROOT%models\*.gguf" (
    echo.
    echo   No model found in the models folder.
    echo   Run  tools\fetch-model.ps1  once while online ^(~2.4 GB^).
    echo.
    pause
    exit /b 1
  )
)

echo Starting Portable AI...
"%PY%" "%ROOT%app\server.py" %*
if errorlevel 1 (
  echo.
  echo Portable AI stopped with an error. See logs\ for details.
  pause
)
endlocal
