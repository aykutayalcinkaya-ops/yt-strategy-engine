@echo off
REM ────────────────────────────────────────────────────────────────────
REM  scripts/build.bat  —  Windows EXE derleme betiği
REM  Çalıştır: scripts\build.bat
REM ────────────────────────────────────────────────────────────────────

setlocal EnableDelayedExpansion
cd /d "%~dp0.."

echo.
echo  yt-manual-analyzer  ^|  Windows EXE Derleyici
echo  ════════════════════════════════════════════
echo.

REM ── 1. Gerekli paketleri kontrol et / yükle ─────────────────────── #
echo  [1/4] Bagimliliklar kontrol ediliyor...
pip install pyinstaller pillow customtkinter --quiet
if %ERRORLEVEL% neq 0 (
    echo  HATA: pip install basarisiz.
    pause & exit /b 1
)

REM ── 2. Ikonu oluştur ────────────────────────────────────────────── #
echo  [2/4] Ikon olusturuluyor...
python assets\create_icon.py
if %ERRORLEVEL% neq 0 (
    echo  UYARI: Ikon olusturulamadi. Devam ediliyor...
)

REM ── 3. Eski dist/ klasörünü temizle ─────────────────────────────── #
echo  [3/4] Eski derleme temizleniyor...
if exist dist\yt-manual-analyzer.exe (
    del /f dist\yt-manual-analyzer.exe
)
if exist build\ (
    rmdir /s /q build
)

REM ── 4. PyInstaller derleme ──────────────────────────────────────── #
echo  [4/4] EXE derleniyor...  (2-5 dakika surebilir)
pyinstaller build_config.spec --noconfirm --clean
if %ERRORLEVEL% neq 0 (
    echo.
    echo  HATA: Derleme basarisiz oldu!
    echo  Yukaridaki hata mesajini inceleyin.
    pause & exit /b 1
)

echo.
echo  ════════════════════════════════════════════
echo  BASARILI!  dist\yt-manual-analyzer.exe
echo.
echo  EXE'yi calistirmadan once yanina kopyalayin:
echo    - .env            (ANTHROPIC_API_KEY=sk-ant-...)
echo    - Analizler\      (YouTube Studio CSV dosyalari)
echo  ════════════════════════════════════════════
echo.
pause
