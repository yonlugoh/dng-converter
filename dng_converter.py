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
DEFAULT_BROWSE_DIR = os.environ.get("DNG_BROWSE_DIR", str(Path.home() / "Pictures"))
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

# Darkroom palette: graphite surfaces, one amber safelight accent, red only for failures.
BG, SURFACE, SURFACE_HI, LINE = "#16171a", "#202226", "#2a2c31", "#303238"
TEXT, MUTED, FAINT = "#ebe8e3", "#a29e97", "#65625c"
AMBER, AMBER_HI, AMBER_LO, INK = "#f2a93b", "#ffbd5c", "#b98128", "#1c1509"
RED = "#ef7164"


def windows_dpi_aware():
    """Render crisp on scaled displays instead of letting Windows blur a 96-dpi bitmap."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def windows_dark_title_bar(root):
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        on = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))  # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE
    except (AttributeError, OSError):
        pass


def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter import font as tkfont

    windows_dpi_aware()
    root = tk.Tk()
    root.title(APP_NAME)
    root.configure(bg=BG)
    scale = root.winfo_fpixels("1i") / 96

    def px(n):
        return round(n * scale)

    root.geometry(f"{px(640)}x{px(580)}")
    root.minsize(px(520), px(480))

    families = set(tkfont.families(root))

    def pick(*names):
        return next((n for n in names if n in families), "TkDefaultFont")

    ui = pick("Segoe UI Variable Text", "Segoe UI")
    ui_semi = pick("Segoe UI Variable Text Semibold", "Segoe UI Semibold")
    display_light = pick("Segoe UI Variable Display Light", "Segoe UI Light")
    display_semi = pick("Segoe UI Variable Display Semib", "Segoe UI Semibold")
    mono = pick("Cascadia Mono", "Consolas")

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, font=(ui, 10), bordercolor=LINE,
                    lightcolor=SURFACE, darkcolor=SURFACE, troughcolor=BG, focuscolor=BG,
                    selectbackground=AMBER_LO, selectforeground=INK, insertcolor=AMBER)
    style.configure("Muted.TLabel", foreground=MUTED, font=(ui, 9))
    style.configure("Fail.TLabel", foreground=RED, font=(ui_semi, 9))
    style.configure("Title.TLabel", font=(display_semi, 14))
    style.configure("Count.TLabel", font=(display_light, 36))

    def button_style(name, bg, fg, hover, pressed, font, focus_ring):
        states = [("disabled", SURFACE), ("pressed", pressed), ("active", hover)]
        style.configure(name, background=bg, foreground=fg, bordercolor=bg, lightcolor=bg, darkcolor=bg,
                        focuscolor=bg, font=font, padding=(px(18), px(7)))
        style.map(name, background=states, lightcolor=states, darkcolor=states, focuscolor=states,
                  foreground=[("disabled", FAINT)],
                  bordercolor=[("disabled", SURFACE), ("focus", focus_ring), ("pressed", pressed), ("active", hover)])

    button_style("TButton", SURFACE, TEXT, SURFACE_HI, LINE, (ui, 10), AMBER)
    button_style("Accent.TButton", AMBER, INK, AMBER_HI, AMBER_LO, (ui_semi, 10), TEXT)

    style.configure("TEntry", fieldbackground=SURFACE, foreground=TEXT, bordercolor=LINE,
                    lightcolor=SURFACE, darkcolor=SURFACE, padding=(px(10), px(7)))
    style.map("TEntry", bordercolor=[("focus", AMBER)], lightcolor=[("focus", SURFACE)])

    style.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    style.configure("Vertical.TScrollbar", background=LINE, troughcolor=BG, bordercolor=BG,
                    lightcolor=LINE, darkcolor=LINE, arrowsize=px(6), gripcount=0)
    style.map("Vertical.TScrollbar", background=[("active", FAINT)],
              lightcolor=[("active", FAINT)], darkcolor=[("active", FAINT)])

    events = queue.Queue()
    cancel_event = threading.Event()
    state = {"running": False, "converter": find_converter(), "dng_seen": False}

    folder_var = tk.StringVar()
    converter_var = tk.StringVar()
    count_var = tk.StringVar(value="Ready")
    dng_var = tk.StringVar(value="Pick a folder, then Convert")
    jpg_var = tk.StringVar()
    rate_var = tk.StringVar()

    frm = ttk.Frame(root, padding=(px(28), px(24), px(28), 0))
    frm.pack(fill="both", expand=True)
    frm.columnconfigure(0, weight=1)
    frm.rowconfigure(7, weight=1)

    header = ttk.Frame(frm)
    header.grid(row=0, column=0, columnspan=2, sticky="ew")
    ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(side="left")
    converter_label = ttk.Label(header, textvariable=converter_var)
    converter_label.pack(side="right")

    def show_converter():
        if state["converter"]:
            converter_var.set("Adobe DNG Converter ready")
            converter_label.configure(style="Muted.TLabel")
        else:
            converter_var.set("Adobe DNG Converter not found")
            converter_label.configure(style="Fail.TLabel")

    show_converter()

    ttk.Label(frm, text="Photo folder", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(px(22), px(6)))
    entry = ttk.Entry(frm, textvariable=folder_var, font=(ui, 10))
    entry.grid(row=2, column=0, sticky="ew")

    def browse():
        current = folder_var.get().strip()
        initial = current if current and Path(current).is_dir() else DEFAULT_BROWSE_DIR
        d = filedialog.askdirectory(title="Select photo folder", initialdir=initial)
        if d:
            folder_var.set(os.path.normpath(d))

    browse_btn = ttk.Button(frm, text="Browse…", command=browse)
    browse_btn.grid(row=2, column=1, sticky="ns", padx=(px(8), 0))

    stats = ttk.Frame(frm)
    stats.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(px(26), 0))
    stats.columnconfigure(0, weight=1)
    ttk.Label(stats, textvariable=count_var, style="Count.TLabel").grid(row=0, column=0, rowspan=2, sticky="w")
    ttk.Label(stats, textvariable=jpg_var, style="Muted.TLabel").grid(row=0, column=1, sticky="se")
    ttk.Label(stats, textvariable=rate_var, style="Muted.TLabel").grid(row=1, column=1, sticky="ne")
    dng_label = ttk.Label(stats, textvariable=dng_var, style="Muted.TLabel")
    dng_label.grid(row=2, column=0, columnspan=2, sticky="w")

    # Hand-drawn bar so it can ease toward each new value instead of jumping.
    bar_h = px(4)
    bar = tk.Canvas(frm, height=bar_h, bg=SURFACE_HI, highlightthickness=0, bd=0)
    bar.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(px(14), 0))
    bar_fill = bar.create_rectangle(0, 0, 0, bar_h, fill=AMBER, width=0)
    prog = {"cur": 0.0, "target": 0.0}

    def draw_bar(_event=None):
        bar.coords(bar_fill, 0, 0, bar.winfo_width() * prog["cur"], bar_h)

    def animate_bar():
        gap = prog["target"] - prog["cur"]
        prog["cur"] = prog["target"] if abs(gap) < 0.002 else prog["cur"] + gap * 0.18  # exponential ease-out
        draw_bar()
        if prog["cur"] != prog["target"]:
            root.after(16, animate_bar)

    def set_progress(fraction):
        settled = prog["cur"] == prog["target"]
        prog["target"] = fraction
        if settled:
            animate_bar()

    bar.bind("<Configure>", draw_bar)

    btns = ttk.Frame(frm)
    btns.grid(row=5, column=0, columnspan=2, sticky="w", pady=(px(22), px(22)))
    start_btn = ttk.Button(btns, text="Convert", style="Accent.TButton")
    cancel_btn = ttk.Button(btns, text="Cancel", state="disabled")
    start_btn.pack(side="left")
    cancel_btn.pack(side="left", padx=(px(8), 0))

    tk.Frame(frm, height=1, bg=LINE).grid(row=6, column=0, columnspan=2, sticky="ew")

    log_frame = ttk.Frame(frm)
    log_frame.grid(row=7, column=0, columnspan=2, sticky="nsew")
    log_frame.columnconfigure(0, weight=1)
    log_frame.rowconfigure(0, weight=1)
    log = tk.Text(log_frame, height=8, state="disabled", wrap="none", font=(mono, 9),
                  bg=BG, fg=MUTED, bd=0, highlightthickness=0, padx=0, pady=px(12),
                  spacing1=px(2), selectbackground=AMBER_LO, selectforeground=INK,
                  insertwidth=0, cursor="arrow")
    log.grid(row=0, column=0, sticky="nsew")
    scroll = ttk.Scrollbar(log_frame, orient="vertical", command=log.yview)
    scroll.grid(row=0, column=1, sticky="ns", pady=px(12))
    def on_scroll(first, last):
        scroll.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            scroll.grid_remove()  # nothing to scroll: hide the empty track
        else:
            scroll.grid()

    log.configure(yscrollcommand=on_scroll)
    log.tag_configure("ok", foreground=TEXT)
    log.tag_configure("fail", foreground=RED)
    log.tag_configure("head", foreground=AMBER)

    def write_log(text):
        tag = ("ok" if text.startswith("✓") else "fail" if text.startswith(("✗", "Error", "Failed")) else
               "head" if text.startswith("──") else "")
        log.configure(state="normal")
        log.insert("end", text + "\n", tag)
        log.see("end")
        log.configure(state="disabled")

    def worker(folder, converter):
        counts = None
        try:
            moved = move_jpgs(folder, lambda d, t, m: events.put(("jpg", d, t)))
            events.put(("log", f"Moved {moved} JPG(s) to {JPG_SUBDIR}\\"))
            start = time.monotonic()
            converted, failed = [], []

            def on_dng(done, total, name, status):
                if status == "ok":
                    converted.append(name)
                    events.put(("log", f"✓ {name}"))
                elif status.startswith("failed"):
                    failed.append(name)
                    events.put(("log", f"✗ {name}: {status[8:]}"))
                events.put(("dng", done, total, len(converted), time.monotonic() - start))

            counts = convert_arws(folder, converter, default_workers(), on_dng, cancel_event)
            elapsed = fmt_duration(time.monotonic() - start)
            events.put(("log", f"DNG: {counts['ok']} converted, {counts['skipped']} skipped (already done), "
                               f"{counts['failed']} failed, {counts['cancelled']} cancelled · {elapsed}"))
            for title, names in (("Converted", converted), ("Failed", failed)):
                if names:
                    events.put(("log", f"{title} ({len(names)}):\n" + "\n".join(f"  {n}" for n in sorted(names))))
        except Exception as e:
            events.put(("log", f"Error: {e}"))
        events.put(("finished", counts))

    def set_running(running):
        state["running"] = running
        start_btn.configure(state="disabled" if running else "normal")
        browse_btn.configure(state="disabled" if running else "normal")
        cancel_btn.configure(state="normal" if running else "disabled")

    def start(_event=None):
        if state["running"]:
            return
        folder = folder_var.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showerror(APP_NAME, "That folder doesn't exist. Pick a folder of photos with Browse.")
            return
        if not state["converter"]:
            messagebox.showinfo(APP_NAME, "Adobe DNG Converter wasn't found in Program Files. "
                                          "Point to Adobe DNG Converter.exe to continue.")
            path = filedialog.askopenfilename(title="Locate Adobe DNG Converter.exe",
                                              filetypes=[("Programs", "*.exe")])
            if not path:
                return
            state["converter"] = path
            show_converter()
        cancel_event.clear()
        set_running(True)
        state["dng_seen"] = False
        prog.update(cur=0.0, target=0.0)
        bar.itemconfigure(bar_fill, fill=AMBER)
        draw_bar()
        count_var.set("0 / 0")
        dng_label.configure(style="Muted.TLabel")
        dng_var.set("Scanning for ARW files…")
        jpg_var.set("Moving JPGs…")
        rate_var.set("")
        write_log(f"── {folder}  ({default_workers()} parallel conversions)")
        threading.Thread(target=worker, args=(folder, state["converter"]), daemon=True).start()

    def cancel():
        cancel_event.set()
        cancel_btn.configure(state="disabled")
        rate_var.set("Cancelling…")
        write_log("Cancelling – finishing conversions already in progress…")

    start_btn.configure(command=start)
    cancel_btn.configure(command=cancel)
    entry.bind("<Return>", start)

    def finish(counts):
        set_running(False)
        rate_var.set("")
        if not counts:  # worker raised before converting
            dng_label.configure(style="Fail.TLabel")
            dng_var.set("Stopped by an error. See the log below.")
            return
        if not state["dng_seen"]:
            count_var.set("0")
            dng_var.set("No ARW files in this folder")
            return
        summary = " · ".join(f"{n} {label}" for n, label in (
            (counts["ok"], "converted"), (counts["skipped"], "already done"), (counts["cancelled"], "cancelled")) if n)
        if counts["failed"]:
            dng_label.configure(style="Fail.TLabel")
            bar.itemconfigure(bar_fill, fill=RED)
            summary = " · ".join(filter(None, (f"{counts['failed']} failed", summary))) + ". Failed files are listed below."
        dng_var.set(summary)

    def poll():
        try:
            while True:
                ev = events.get_nowait()
                kind = ev[0]
                if kind == "jpg":
                    _, done, total = ev
                    jpg_var.set(f"{done} of {total} JPGs moved to {JPG_SUBDIR}\\" if total else "No JPGs to move")
                elif kind == "dng":
                    _, done, total, converted, elapsed = ev
                    state["dng_seen"] = True
                    set_progress(done / max(total, 1))
                    count_var.set(f"{done} / {total}")
                    dng_var.set("DNGs processed")
                    if converted and elapsed > 0 and done < total:
                        rate = converted / elapsed
                        rate_var.set(f"{rate:.1f} files/s · {fmt_duration((total - done) / rate)} left")
                elif kind == "log":
                    write_log(ev[1])
                elif kind == "finished":
                    finish(ev[1])
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
    root.update_idletasks()
    windows_dark_title_bar(root)
    root.withdraw()  # re-show so Windows repaints the title bar dark
    root.deiconify()
    entry.focus_set()
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

    results = []

    def on_dng(done, total, name, status):
        results.append((name, status))
        print(f"[{done}/{total}] {name}: {status}", flush=True)

    counts = convert_arws(folder, converter, default_workers(), on_dng, threading.Event())
    print(f"{counts} in {fmt_duration(time.monotonic() - start)}")
    for status in ("ok", "failed"):
        names = sorted(n for n, s in results if s.split(":")[0] == status)
        if names:
            print(f"{'Converted' if status == 'ok' else 'Failed'} ({len(names)}):")
            print("".join(f"  {n}\n" for n in names), end="")
    sys.exit(1 if counts["failed"] else 0)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--cli":
        run_cli(sys.argv[2])
    else:
        run_gui()
