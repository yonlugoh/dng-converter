@echo off
REM Builds dist\DNGConverter.exe. Run from a Windows path (e.g. copy the repo to C:\) -
REM PyInstaller can misbehave on \\wsl$ paths.
uv run pyinstaller --onefile --windowed --name DNGConverter dng_converter.py || exit /b 1
echo.
echo Built dist\DNGConverter.exe
