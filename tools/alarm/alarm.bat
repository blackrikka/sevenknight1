@echo off
rem Play the YouTube video 5x at volume 100 on infinite loop. Run by Task Scheduler at 08:00.
cd /d "%~dp0"

rem 1) Max out Windows master volume (volume-up key x50)
powershell -NoProfile -Command "$s=New-Object -ComObject WScript.Shell; 1..50 | %% { $s.SendKeys([char]175) }"

rem 2) YouTube embeds fail from file://, so serve the page from a local web server
start "alarm-server" /min python -m http.server 8765 --bind 127.0.0.1
ping -n 3 127.0.0.1 >nul

rem 3) Open a separate browser window that allows autoplay with sound (Chrome, else Edge)
set "URL=http://127.0.0.1:8765/alarm.html"
set "FLAGS=--autoplay-policy=no-user-gesture-required --user-data-dir=%TEMP%\alarm-browser --start-maximized --no-first-run"
set "BROWSER="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "BROWSER=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "BROWSER=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "BROWSER=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not defined BROWSER if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"

if defined BROWSER (
  start "" "%BROWSER%" %FLAGS% "%URL%"
) else (
  start "" "%URL%"
)
