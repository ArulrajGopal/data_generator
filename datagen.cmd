@echo off
REM Usage: datagen run ^| datagen clean ^| datagen plan   (extra args are passed through)
pushd "%~dp0"
python -m datagen %*
set RC=%ERRORLEVEL%
popd
exit /b %RC%
