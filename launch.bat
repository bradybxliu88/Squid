@echo off
REM Launch Squid microscope software.
REM Usage:
REM   launch.bat              - real hardware
REM   launch.bat --simulation - simulated hardware

setlocal
cd /d "%~dp0software\src"
python main_hcs.py %*
endlocal
