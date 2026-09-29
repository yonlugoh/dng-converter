@echo off
REM Builds dist\PhotoSorter.exe. Run from a Windows path (e.g. copy the repo to C:\) -
REM PyInstaller can misbehave on \\wsl$ paths.
python -m pip install --upgrade pyinstaller || exit /b 1
python -m PyInstaller --onefile --windowed --name PhotoSorter photo_sorter.py || exit /b 1
echo.
echo Built dist\PhotoSorter.exe
