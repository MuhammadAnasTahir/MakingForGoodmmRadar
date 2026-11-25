@echo off
cd /d "%~dp0"
set "RADAR_PY=D:\download\radar_toolbox_4_30_00_06\tools\visualizers\Applications_Visualizer\Industrial_Visualizer\.venv\Scripts\python.exe"
if not exist "%RADAR_PY%" (
 echo Python environment not found. Edit RADAR_PY in this file or see README-ZH.md.
 pause
 exit /b 1
)
"%RADAR_PY%" bootstrap.py
pause
