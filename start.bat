@echo off
REM Kasif - Windows icin tek tikla kurulum ve calistirma
cd /d "%~dp0"
where python >nul 2>nul || (echo Python bulunamadi. https://www.python.org/downloads/ adresinden kurun ve "Add python.exe to PATH" kutusunu isaretleyin. & pause & exit /b 1)

REM Yarim kalmis bir sanal ortam varsa onar
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python -m pip --version >nul 2>nul || (
    echo Sanal ortam eksik kurulmus, onariliyor...
    .venv\Scripts\python -m ensurepip --upgrade >nul 2>nul || rmdir /s /q .venv
  )
)
if not exist .venv\Scripts\python.exe (
  echo [1/2] Sanal ortam olusturuluyor...
  python -m venv .venv || goto :error
  .venv\Scripts\python -m pip --version >nul 2>nul || .venv\Scripts\python -m ensurepip --upgrade || goto :error
)
if not exist .venv\Lib\site-packages\kasif-*.dist-info (
  echo [2/2] Paketler yukleniyor, bu bir kac dakika surebilir...
  .venv\Scripts\python -m pip install -q --upgrade pip
  .venv\Scripts\python -m pip install -q -e ".[dev]" || goto :error
)
if not exist .env copy .env.example .env >nul
.venv\Scripts\python -m kasif.serve %*
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo Bir hata olustu. Yukaridaki mesaji kopyalayip paylasin.
pause
exit /b 1
