@echo off
rem Publishes the mobile app with EAS Update, pointed at the Render backend. See publish-app.ps1 for options.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish-app.ps1" %*
