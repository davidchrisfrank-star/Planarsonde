@echo off
cd /d "%~dp0"
python main_analyse_gui.py
if errorlevel 1 (
    echo.
    echo Fehler beim Starten. Ist Python installiert und im PATH?
    pause
)
