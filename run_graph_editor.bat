@echo off
setlocal
cd /d "%~dp0"

set "BOOTSTRAP_PYTHON=%LocalAppData%\Python\pythoncore-3.14-64\python.exe"
if not exist "%BOOTSTRAP_PYTHON%" set "BOOTSTRAP_PYTHON=python"

if not exist ".venv\Scripts\python.exe" (
    echo Creating local Python environment...
    "%BOOTSTRAP_PYTHON%" -m venv .venv
    if errorlevel 1 goto :error
)

".venv\Scripts\python.exe" -c "import numpy, networkx, openpyxl, plotly, streamlit" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --force-reinstall -r requirements.txt
    if errorlevel 1 goto :error
)

".venv\Scripts\python.exe" -m streamlit run app.py
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo Failed to start. Check Python installation and the messages above.
pause
exit /b 1
