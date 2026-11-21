@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 ffd_backup.py
) else (
    python ffd_backup.py
)
if errorlevel 1 pause
