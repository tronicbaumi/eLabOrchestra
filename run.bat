@echo off
REM eLabOrchestra launcher
REM Adjust PYTHON path if needed
set PYTHON=C:\SynologyDrive\_AI\Orange\python.exe
if not exist "%PYTHON%" set PYTHON=python
cd /d "%~dp0"
"%PYTHON%" main.py
