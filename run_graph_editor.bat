@echo off
setlocal
cd /d "%~dp0"

PowerShell -NoProfile -Command "try { $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8501/_stcore/health' -TimeoutSec 2; if ($response.StatusCode -eq 200) { exit 0 } } catch { }; exit 1" >nul 2>&1
if not errorlevel 1 (
    echo Graph editor is already running at http://127.0.0.1:8501
    echo The existing browser tab can be used.
    timeout /t 2 >nul
    exit /b 0
)

set "BOOTSTRAP_PYTHON=%LocalAppData%\Programs\Python\Python313\python.exe"
if not exist "%BOOTSTRAP_PYTHON%" set "BOOTSTRAP_PYTHON=python"

if not exist ".venv\Scripts\python.exe" (
    echo Creating local Python environment...
    "%BOOTSTRAP_PYTHON%" -m venv .venv
    if errorlevel 1 goto :error
)

if not exist ".streamlit" mkdir ".streamlit"
if not exist ".streamlit\cookie_secret.txt" (
    echo Generating local Streamlit cookie secret...
    ".venv\Scripts\python.exe" -c "import pathlib,secrets; pathlib.Path(r'.streamlit\cookie_secret.txt').write_text(secrets.token_urlsafe(48), encoding='ascii')"
    if errorlevel 1 goto :error
)
set /p "STREAMLIT_SERVER_COOKIE_SECRET="<".streamlit\cookie_secret.txt"
if not defined STREAMLIT_SERVER_COOKIE_SECRET goto :error

".venv\Scripts\python.exe" -c "import numpy, networkx, openpyxl, plotly, streamlit" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --force-reinstall -r requirements.txt
    if errorlevel 1 goto :error
)

".venv\Scripts\python.exe" -m streamlit run app.py --server.address=127.0.0.1 --server.port=8501 --server.enableCORS=true --server.enableXsrfProtection=true
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo Failed to start. Check Python installation and the messages above.
pause
exit /b 1
