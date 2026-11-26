@echo off
cd /d "%~dp0"
set "RADAR_PY=D:\download\radar_toolbox_4_30_00_06\tools\visualizers\Applications_Visualizer\Industrial_Visualizer\.venv\Scripts\python.exe"
rem Fall back to the Python on PATH (needs: pip install -r requirements.txt) when the Visualizer venv is absent.
if not exist "%RADAR_PY%" set "RADAR_PY=python"
"%RADAR_PY%" bootstrap.py
pause
