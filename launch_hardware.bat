@echo off
setlocal enableextensions
title Squid_BL (hardware mode)

REM =====================================================================
REM  Squid_BL - launch in REAL HARDWARE mode from a clean (Explorer) env.
REM
REM  This machine has TWO conda installs: anaconda3 (has all the deps:
REM  qtpy, napari, pyqtgraph, ...) and miniconda3 (does NOT). Because
REM  miniconda3 is on the persistent PATH, a plain "conda activate base"
REM  resolves to miniconda3 -> ModuleNotFoundError: No module named 'qtpy'.
REM  There are also C:\Python314 and a Windows Store python stub on PATH.
REM  So we DO NOT rely on name-based activation: we point at the anaconda3
REM  interpreter by full path and prepend its dirs so it always wins.
REM =====================================================================

set "CONDA_ROOT=C:\Users\CZC4457GCG\anaconda3"

if not exist "%CONDA_ROOT%\python.exe" (
    echo.
    echo [ERROR] Could not find the Anaconda python at:
    echo         %CONDA_ROOT%\python.exe
    echo         Edit CONDA_ROOT in this file if Anaconda lives elsewhere.
    echo.
    pause
    exit /b 1
)

REM Prepend the Anaconda base env dirs so its python + DLLs beat any other
REM Python / miniconda3 on PATH; keep git reachable (GitPython needs it).
set "PATH=%CONDA_ROOT%;%CONDA_ROOT%\Library\mingw-w64\bin;%CONDA_ROOT%\Library\usr\bin;%CONDA_ROOT%\Library\bin;%CONDA_ROOT%\Scripts;%CONDA_ROOT%\bin;%PATH%"
if exist "C:\Program Files\Git\cmd\git.exe" set "PATH=C:\Program Files\Git\cmd;%PATH%"
set "GIT_PYTHON_REFRESH=quiet"

REM Launch in REAL HARDWARE mode (no --simulation). Call the interpreter by
REM full path so a stray python on PATH can never shadow it.
cd /d "%~dp0software\src"
echo Starting Squid_BL in hardware mode...
"%CONDA_ROOT%\python.exe" main_hcs.py %*
set "RC=%errorlevel%"

if not "%RC%"=="0" (
    echo.
    echo *** Squid exited with error code %RC%. ***
    echo *** Window kept open so you can read the message above. Close it when done. ***
    pause
)
endlocal
