@echo off
rem Register alarm.bat to run once today at 08:00 (PC must be on and logged in)
schtasks /create /tn "sevenknight-alarm" /tr "\"%~dp0alarm.bat\"" /sc once /st 08:00 /f
schtasks /query /tn "sevenknight-alarm"
pause
