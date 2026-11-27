@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PYEXE="
if exist "%~dp0runtime\python\python.exe" set "PYEXE=%~dp0runtime\python\python.exe"
if not defined PYEXE set "PYEXE=python"

"%PYEXE%" main.py
