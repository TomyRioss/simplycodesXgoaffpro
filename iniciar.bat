@echo off
setlocal

set REPO_URL=https://github.com/TomyRioss/simplycodesXgoaffpro.git
set APP_DIR=%~dp0app

where git >nul 2>nul
if errorlevel 1 (
    echo Falta instalar Git. Descargalo de https://git-scm.com/download/win y volve a correr este archivo.
    pause
    exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
    echo Falta instalar Python. Descargalo de https://www.python.org/downloads/ ^(tildar "Add to PATH" al instalar^) y volve a correr este archivo.
    pause
    exit /b 1
)

if exist "%APP_DIR%\.git" (
    echo Buscando actualizaciones...
    git -C "%APP_DIR%" pull --ff-only
) else (
    echo Instalando por primera vez...
    git clone "%REPO_URL%" "%APP_DIR%"
)
if errorlevel 1 (
    echo Hubo un error instalando/actualizando. Avisale a tu proveedor.
    pause
    exit /b 1
)

echo Instalando dependencias...
python -m pip install --quiet --disable-pip-version-check -r "%APP_DIR%\requirements.txt"

echo Abriendo programa...
cd /d "%APP_DIR%"
python webui.py

pause
