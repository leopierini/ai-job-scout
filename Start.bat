@echo off
rem Starts AI Job Scout. Double-click this file.
setlocal
cd /d "%~dp0"

echo Starting AI Job Scout... the first launch downloads what it needs and can take a few minutes.

set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul
if not errorlevel 1 goto :have_uv

echo Installing uv, the tool that sets up Python for this app...
rem A terminal running PowerShell 7 passes it a module path that Windows PowerShell cannot load.
set "PSModulePath="
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
if errorlevel 1 goto :uv_install_failed
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 goto :uv_missing
goto :have_uv

:uv_install_failed
echo Could not install uv. Check your internet connection and open this file again.
goto :failed

:uv_missing
echo uv was installed but could not be found. Close this window and open this file again.
goto :failed

:have_uv
rem The runtime lives in the app-data folder (%LOCALAPPDATA%\ai-job-scout), outside this folder,
rem so replacing this folder with a newer download keeps working.
if defined JOBSCOUT_HOME (
  set "UV_PROJECT_ENVIRONMENT=%JOBSCOUT_HOME%\runtime"
) else (
  set "UV_PROJECT_ENVIRONMENT=%LOCALAPPDATA%\ai-job-scout\runtime"
)

uv sync --frozen --no-dev --no-editable --python 3.13 --reinstall-package ai-job-scout
if errorlevel 1 (
  echo Could not set up the app. Check your internet connection.
  echo If it still fails, you may have an old version of uv: run "uv self update" in a terminal, or reinstall uv from https://docs.astral.sh/uv/, then open this file again.
  goto :failed
)

if defined JOBSCOUT_LAUNCHER_CHECK (
  "%UV_PROJECT_ENVIRONMENT%\Scripts\python.exe" -m jobscout.cli paths
  if errorlevel 1 (
    echo The app was set up but could not run.
    goto :failed
  )
  exit /b 0
)

"%UV_PROJECT_ENVIRONMENT%\Scripts\python.exe" -m jobscout.cli start
if errorlevel 1 (
  echo The app stopped with an error.
  goto :failed
)
exit /b 0

:failed
rem Keep the window open so the message can be read. Skipped when checking the launcher.
if not defined JOBSCOUT_LAUNCHER_CHECK pause
exit /b 1
