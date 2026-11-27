@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PYEXE="
if exist "%~dp0runtime\python\pythonw.exe" set "PYEXE=%~dp0runtime\python\pythonw.exe"
if not defined PYEXE set "PYEXE=pythonw"

start "" "%PYEXE%" recognize_main.py
