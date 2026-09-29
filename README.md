# DNG Converter (Photo Sorter)

A Windows tool for Sony RAW+JPEG folders:

1. Pick a folder.
2. Every top-level `.jpg`/`.JPG` is moved into `<folder>\jpg\`.
3. Every top-level `.ARW` is converted to `.dng`, written next to the ARW, using Adobe DNG Converter with several conversions in parallel.

It shows live progress ("DNG: 50/300 processed · 2.1 files/s · ETA 2m"). The original ARWs are never modified or deleted. It's safe to re-run: DNGs that already exist are skipped.

## Requirements
- Windows with Python 3.10+ (tkinter is included)
- [Adobe DNG Converter](https://helpx.adobe.com/camera-raw/using/adobe-dng-converter.html), installed at its default path or set with the `DNG_CONVERTER` environment variable

## Usage
```
python photo_sorter.py              # GUI
python photo_sorter.py --cli DIR    # headless, prints progress
```

## Build a standalone .exe
Run `build.bat` from a Windows path. The result is `dist\PhotoSorter.exe`.
