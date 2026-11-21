@echo off
rem Starts the backend, worker, admin website and Expo in one Windows Terminal window. See dev.ps1 for options.
rem Runs dev.ps1 without changing the PC's script policy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev.ps1" %*
