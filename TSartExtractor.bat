@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Preparando ambiente virtual...
    python -m venv .venv
    if errorlevel 1 goto :erro
)

.venv\Scripts\python.exe -c "import requests, requests_pkcs12, reportlab, cryptography" >nul 2>&1
if errorlevel 1 (
    echo Instalando dependencias...
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 goto :erro
)

.venv\Scripts\python.exe main.py
if errorlevel 1 goto :erro
exit /b 0

:erro
echo.
echo Nao foi possivel iniciar o TSart Extractor.
echo Revise as mensagens acima e pressione uma tecla para sair.
pause
exit /b 1
