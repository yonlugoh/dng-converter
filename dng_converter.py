"""DNG Converter: move JPGs into a jpg\\ subfolder and convert ARW -> DNG in parallel.

Run with Windows Python:  python dng_converter.py            (GUI)
                          python dng_converter.py --cli DIR  (headless, for testing)
"""
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

APP_NAME = "DNG Converter"
DEFAULT_BROWSE_DIR = r"F:\nikon\wildlife"
DEFAULT_CONVERTER = r"C:\Program Files\Adobe\Adobe DNG Converter\Adobe DNG Converter.exe"
JPG_SUBDIR = "jpg"
TMP_SUBDIR = ".dng_tmp"  # converter writes here first, so a killed run never leaves a partial .dng
JPG_EXTS = {".jpg", ".jpeg"}
ARW_EXT = ".arw"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def default_workers():
    return max(2, (os.cpu_count() or 4) // 2)


def find_converter():
    """Return the Adobe DNG Converter path, or None if it can't be found."""
    for candidate in (os.environ.get("DNG_CONVERTER"), DEFAULT_CONVERTER):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def list_files(folder, exts):
    return sorted(p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in exts)


def unique_path(path):
    """Return path, or path with _1, _2, ... appended to the stem if it already exists."""
    if not path.exists():
        return path
    n = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def move_jpgs(folder, on_progress):
    """Move top-level JPGs into folder/jpg. on_progress(done, total, message)."""
    jpgs = list_files(folder, JPG_EXTS)
    if not jpgs:
        on_progress(0, 0, "No JPGs to move")
        return 0
    dest_dir = Path(folder) / JPG_SUBDIR
    dest_dir.mkdir(exist_ok=True)
    for i, src in enumerate(jpgs, 1):
        dest = unique_path(dest_dir / src.name)
        try:
            os.replace(src, dest)  # instant rename on the same drive
        except OSError:
            shutil.move(str(src), str(dest))
        on_progress(i, len(jpgs), None)
    return len(jpgs)


def dng_path_for(arw):
    return arw.with_suffix(".dng")


def convert_one(converter, arw, tmp_dir):
    """Convert one ARW. Returns (ok, message)."""
    target = dng_path_for(arw)
    tmp_dng = tmp_dir / target.name
    result = subprocess.run(
        [converter, "-c", "-p1", "-d", str(tmp_dir), str(arw)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,  # Adobe prints harmless "*** Error: ... model" noise
        creationflags=CREATE_NO_WINDOW,
    )
    if result.returncode != 0 or not tmp_dng.is_file() or tmp_dng.stat().st_size == 0:
        tmp_dng.unlink(missing_ok=True)
        return False, f"converter failed (exit code {result.returncode})"
    os.replace(tmp_dng, target)
    return True, None


def convert_arws(folder, converter, workers, on_progress, cancel_event):
    """Convert top-level ARWs to DNGs next to them.

    on_progress(done, total, arw_name, status) where status is "ok", "skipped",
    "failed: ..." or "cancelled". Returns a dict of counts.
    """
    arws = list_files(folder, {ARW_EXT})
    todo, counts = [], {"ok": 0, "skipped": 0, "failed": 0, "cancelled": 0}
    total = len(arws)
    done = 0
    for arw in arws:
        dng = dng_path_for(arw)
        if dng.is_file() and dng.stat().st_size > 0:
            done += 1
            counts["skipped"] += 1
            on_progress(done, total, arw.name, "skipped")
        else:
            todo.append(arw)
    if not todo:
        return counts

    tmp_dir = Path(folder) / TMP_SUBDIR
    tmp_dir.mkdir(exist_ok=True)

    def task(arw):
        if cancel_event.is_set():
            return "cancelled"
        ok, msg = convert_one(converter, arw, tmp_dir)
        return "ok" if ok else f"failed: {msg}"

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(task, arw): arw for arw in todo}
            for fut in as_completed(futures):
                arw = futures[fut]
                try:
                    status = fut.result()
                except Exception as e:  # e.g. converter vanished mid-run
                    status = f"failed: {e}"
                counts[status.split(":")[0]] += 1
                if status != "cancelled":
                    done += 1
                on_progress(done, total, arw.name, status)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return counts


def fmt_duration(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60:02d}s"


# ---------------------------------------------------------------- GUI

def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText

    root = tk.Tk()
    root.title(APP_NAME)
    root.geometry("640x460")
    root.minsize(520, 380)

    events = queue.Queue()
    cancel_event = threading.Event()
    state = {"running": False, "converter": find_converter()}

    folder_var = tk.StringVar()
    jpg_var = tk.StringVar(value="JPG: –")
    dng_var = tk.StringVar(value="DNG: –")
    rate_var = tk.StringVar(value="")

    frm = ttk.Frame(root, padding=12)
    frm.pack(fill="both", expand=True)
    frm.columnconfigure(1, weight=1)
    frm.rowconfigure(6, weight=1)

    ttk.Label(frm, text="Folder:").grid(row=0, column=0, sticky="w")
    ttk.Entry(frm, textvariable=folder_var).grid(row=0, column=1, sticky="ew", padx=6)

    def browse():
        current = folder_var.get().strip()
        initial = current if current and Path(current).is_dir() else DEFAULT_BROWSE_DIR
        d = filedialog.askdirectory(title="Select photo folder", initialdir=initial)
        if d:
            folder_var.set(os.path.normpath(d))

    browse_btn = ttk.Button(frm, text="Browse…", command=browse)
    browse_btn.grid(row=0, column=2)

    ttk.Label(frm, textvariable=jpg_var).grid(row=1, column=0, columnspan=3, sticky="w", pady=(12, 0))
    bar = ttk.Progressbar(frm, mode="determinate")
    bar.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 2))
    ttk.Label(frm, textvariable=dng_var).grid(row=3, column=0, columnspan=2, sticky="w")
    ttk.Label(frm, textvariable=rate_var).grid(row=3, column=2, sticky="e")

    btns = ttk.Frame(frm)
    btns.grid(row=4, column=0, columnspan=3, pady=10)
    start_btn = ttk.Button(btns, text="Start")
    cancel_btn = ttk.Button(btns, text="Cancel", state="disabled")
    start_btn.pack(side="left", padx=4)
    cancel_btn.pack(side="left", padx=4)

    ttk.Label(frm, text="Log:").grid(row=5, column=0, sticky="w")
    log = ScrolledText(frm, height=10, state="disabled", font=("Consolas", 9))
    log.grid(row=6, column=0, columnspan=3, sticky="nsew")

    def write_log(text):
        log.configure(state="normal")
        log.insert("end", text + "\n")
        log.see("end")
        log.configure(state="disabled")

    def worker(folder, converter):
        try:
            moved = move_jpgs(folder, lambda d, t, m: events.put(("jpg", d, t)))
            events.put(("log", f"Moved {moved} JPG(s) to {JPG_SUBDIR}\\"))
            start = time.monotonic()
            converted = [0]

            def on_dng(done, total, name, status):
                if status == "ok":
                    converted[0] += 1
                events.put(("dng", done, total, converted[0], time.monotonic() - start))
                if status.startswith("failed"):
                    events.put(("log", f"✗ {name}: {status[8:]}"))

            counts = convert_arws(folder, converter, default_workers(), on_dng, cancel_event)
            elapsed = fmt_duration(time.monotonic() - start)
            events.put(("log", f"DNG: {counts['ok']} converted, {counts['skipped']} skipped (already done), "
                               f"{counts['failed']} failed, {counts['cancelled']} cancelled · {elapsed}"))
        except Exception as e:
            events.put(("log", f"Error: {e}"))
        events.put(("finished",))

    def start():
        folder = folder_var.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showerror(APP_NAME, "Please select a valid folder.")
            return
        if not state["converter"]:
            messagebox.showinfo(APP_NAME, "Adobe DNG Converter not found. Please locate it.")
            path = filedialog.askopenfilename(title="Locate Adobe DNG Converter.exe",
                                              filetypes=[("Programs", "*.exe")])
            if not path:
                return
            state["converter"] = path
        cancel_event.clear()
        state["running"] = True
        start_btn.configure(state="disabled")
        browse_btn.configure(state="disabled")
        cancel_btn.configure(state="normal")
        bar["value"] = 0
        jpg_var.set("JPG: moving…")
        dng_var.set("DNG: scanning…")
        rate_var.set("")
        write_log(f"── {folder}  ({default_workers()} parallel conversions)")
        threading.Thread(target=worker, args=(folder, state["converter"]), daemon=True).start()

    def cancel():
        cancel_event.set()
        cancel_btn.configure(state="disabled")
        write_log("Cancelling – finishing conversions already in progress…")

    start_btn.configure(command=start)
    cancel_btn.configure(command=cancel)

    def poll():
        try:
            while True:
                ev = events.get_nowait()
                kind = ev[0]
                if kind == "jpg":
                    _, done, total = ev
                    jpg_var.set(f"JPG: {done}/{total} moved" if total else "JPG: none to move")
                elif kind == "dng":
                    _, done, total, converted, elapsed = ev
                    bar["maximum"] = max(total, 1)
                    bar["value"] = done
                    dng_var.set(f"DNG: {done}/{total} processed")
                    if converted and elapsed > 0:
                        rate = converted / elapsed
                        remaining = (total - done) / rate if rate else 0
                        rate_var.set(f"{rate:.1f} files/s · ETA {fmt_duration(remaining)}")
                elif kind == "log":
                    write_log(ev[1])
                elif kind == "finished":
                    state["running"] = False
                    start_btn.configure(state="normal")
                    browse_btn.configure(state="normal")
                    cancel_btn.configure(state="disabled")
                    rate_var.set("Done")
        except queue.Empty:
            pass
        root.after(100, poll)

    def on_close():
        if state["running"]:
            if not messagebox.askyesno(APP_NAME, "Conversion is running. Cancel and quit?"):
                return
            cancel_event.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    poll()
    root.mainloop()


# ---------------------------------------------------------------- CLI (for testing)

def run_cli(folder):
    converter = find_converter()
    if not converter:
        sys.exit("Adobe DNG Converter not found (set DNG_CONVERTER to its path).")
    moved = move_jpgs(folder, lambda d, t, m: None)
    print(f"Moved {moved} JPG(s)")
    start = time.monotonic()

    def on_dng(done, total, name, status):
        print(f"[{done}/{total}] {name}: {status}", flush=True)

    counts = convert_arws(folder, converter, default_workers(), on_dng, threading.Event())
    print(f"{counts} in {fmt_duration(time.monotonic() - start)}")
    sys.exit(1 if counts["failed"] else 0)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--cli":
        run_cli(sys.argv[2])
    else:
        run_gui()
