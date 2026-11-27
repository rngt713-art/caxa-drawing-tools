@echo off
chcp 65001 >nul
set "PYEXE="
if exist "%~dp0runtime\python\python.exe" set "PYEXE=%~dp0runtime\python\python.exe"
if not defined PYEXE set "PYEXE=python"
"%PYEXE%" "%~dp0tools\caxa_explore.py"
pause
