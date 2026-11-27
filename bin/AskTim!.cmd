@echo off
rem The AskTim! command on Windows. Windows cannot run the script file AskTim! (it has no
rem extension), so its wheels have this instead: the same as asktim, which pip puts next to it.
"%~dp0asktim.exe" %*
exit /b %ERRORLEVEL%
