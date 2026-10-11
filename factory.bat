@echo off
rem Ole-K2-Free-Video launcher (Windows). Usage: factory.bat  or  factory.bat make story.txt  or  factory.bat doctor
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 factory.py %*
) else (
  python factory.py %*
)
if errorlevel 1 pause
