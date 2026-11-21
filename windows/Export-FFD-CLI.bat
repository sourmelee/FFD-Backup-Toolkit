@echo off
cd /d "%~dp0"
py -3 ffd_backup.py --cli --out "%USERPROFILE%\Documents"
pause
