# CLAUDE.md

## What this is
A single-file Windows desktop tool (`photo_sorter.py`, Python + tkinter). For a user-selected folder, it:
- moves top-level `.jpg`/`.jpeg` (any case) into `<folder>\jpg\`
- converts top-level `.ARW` to `.dng` **in the same folder as the ARW**, using Adobe DNG Converter

The source photos are Sony RAW+JPEG (`SRX*.ARW`, about 20 MB each).

## Hard rules (the user asked for these)
- Folder layout: JPGs go to the `jpg\` subfolder (lowercase). DNGs sit next to their ARWs. Never put DNGs in a subfolder.
- Never delete, modify or move the ARW originals.
- Only process the top level. Don't recurse into subfolders.
- Must be safe to re-run:
  - Skip an ARW if a non-empty `.dng` with the same name already exists.
  - JPG name clashes get a `_1`, `_2` suffix and are never overwritten.

## How conversion works
- Converter: `C:\Program Files\Adobe\Adobe DNG Converter\Adobe DNG Converter.exe`. The `DNG_CONVERTER` env var overrides this path.
- Command, run once per file: `"Adobe DNG Converter.exe" -c -p1 -d <folder>\.dng_tmp <file.ARW>`
  - `-c` = lossless compressed, `-p1` = medium JPEG preview, `-d` = output dir.
- Output goes to `.dng_tmp\` first and is then moved into place with `os.replace`. This way a killed or cancelled run never leaves a partial `.dng` that the re-run check would skip. The temp dir is deleted at the end.
- Success means exit code 0 and a non-empty output file.
- Adobe prints `*** Error: ... model not present` lines to stderr even when it succeeds. These are harmless, which is why stderr is discarded.
- Speed: one file takes about 1.7 s. A `ThreadPoolExecutor` with `max(2, cpu_count // 2)` workers runs one converter process per file. On this 20-core machine, 18 files take about 4 s.
  - The bottleneck is the converter and disk I/O, not Python. Rewriting in Rust or Go won't speed it up.

## GUI threading model
- A background thread does all the file work.
- It sends events (`jpg`, `dng`, `log`, `finished`) into a `queue.Queue`. `root.after(100, poll)` reads the queue on the Tk thread.
- Never touch Tk widgets from worker threads.
- Cancel sets a `threading.Event`: tasks not yet started are skipped, and conversions already running finish.

## Running from WSL (dev environment)
The code lives in WSL (`~/Projects/dng-converter`), but it must run with **Windows** Python so it can call the Windows converter:
```
cd /mnt/c/Users/yon-l
python.exe "$(wslpath -w ~/Projects/dng-converter/photo_sorter.py)"                 # GUI
python.exe "$(wslpath -w ~/Projects/dng-converter/photo_sorter.py)" --cli 'C:\path'  # headless
```
Run from a `/mnt/c` directory. Windows Python dislikes a UNC current directory.

## Testing
- The test data is `F:\nikon\wildlife\Test` (18 ARW + 18 JPG). **Never run the tool on it directly.** Copy it first, e.g. to `C:\Users\yon-l\AppData\Local\Temp\dngtest`, and delete the copy afterwards.
- Use `--cli` for automated checks. Expected result: 18 ok, `jpg\` holds 18 files, the top level holds 18 ARW + 18 dng. A second run should report 18 skipped.

## Build
`build.bat` (PyInstaller `--onefile --windowed`) produces `dist\PhotoSorter.exe`. Run it from a Windows path, not `\\wsl$`.
