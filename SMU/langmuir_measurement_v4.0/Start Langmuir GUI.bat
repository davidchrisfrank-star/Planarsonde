@echo off
cd /d "%~dp0"
python main_v2_gui.py
if %errorlevel% neq 0 (
    echo.
    echo Fehler beim Starten. Ist Python installiert und im PATH?
    pause
)
