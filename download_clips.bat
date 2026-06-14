@echo off
cd /d "%~dp0"

echo Checking dependencies...
where yt-dlp >nul 2>&1
if errorlevel 1 (
    echo yt-dlp not found — installing...
    pip install yt-dlp
)
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: ffmpeg not found on PATH.
    echo Download from https://ffmpeg.org/download.html and add to PATH.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Bumblebee Clip Downloader
echo  VPN must be active before continuing
echo ============================================================
echo.
echo Options:
echo   1. Dry run (preview only - no downloads)
echo   2. Download ALL pending clips
echo   3. Download clips for ONE person
echo   4. Exit
echo.
set /p choice="Select option (1-4): "

if "%choice%"=="1" goto dryrun
if "%choice%"=="2" goto all
if "%choice%"=="3" goto person
if "%choice%"=="4" goto end

:dryrun
echo.
python process_archetype_csv.py --local --dry-run
goto done

:all
echo.
python process_archetype_csv.py --local
goto done

:person
echo.
set /p name="Enter person name (e.g. Julia Child): "
python process_archetype_csv.py --local --person "%name%"
goto done

:done
echo.
pause

:end
