@echo off
rem Close the alarm browser window and the local web server
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and ($_.CommandLine -like '*alarm-browser*' -or $_.CommandLine -like '*http.server 8765*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo Alarm stopped.
