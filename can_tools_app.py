#!/usr/bin/env python3
"""
CAN Log Tools - GUI for working with CAN bus log files.

Tabs:
  * Convert - converts candump .log files to Vector .ASC format.
  * Trim    - trims a .log or .asc file to a chosen start/end datetime.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import math
import os
import sys
import re
import subprocess
from datetime import datetime

# ── Conversion logic ───────────────────────────────────────────────────────────

LINE_RE = re.compile(
    r"^\((?P<ts>\d+(?:\.\d+)?)\)\s+(?P<if>\S+)\s+(?P<canid>[0-9A-Fa-f]+)#(?P<data>[0-9A-Fa-f]*)\s*$"
)

DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MON_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

def format_asc_date(dt: datetime) -> str:
    """Vector header date, e.g. 'date Tue Sep 29 03:04:05.123 pm 2026'.
    Names are hard-coded (not strftime) so the output is locale-independent."""
    dow = DOW_NAMES[dt.weekday()]
    mon = MON_NAMES[dt.month - 1]
    ampm = "am" if dt.hour < 12 else "pm"
    hour12 = dt.hour % 12 or 12
    ms = dt.microsecond // 1000
    return (f"date {dow} {mon} {dt.day:02d} {hour12:02d}:{dt.minute:02d}:{dt.second:02d}"
            f".{ms:03d} {ampm} {dt.year}")

def iface_to_channel(iface: str) -> str:
    m = re.search(r"(\d+)$", iface)
    return m.group(1) if m else "1"

def chunk_bytes(hexstr: str):
    hexstr = hexstr.strip()
    if len(hexstr) % 2 != 0:
        hexstr = hexstr[:-1]
    return [hexstr[i:i+2].upper() for i in range(0, len(hexstr), 2)]

def run_conversion(input_path, output_path, use_utc, version, log_callback):
    """Run the conversion; call log_callback(line) for status lines."""
    log_callback(f"Reading: {input_path}")
    frames = []
    skipped = 0

    with open(input_path, "r", encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            m = LINE_RE.match(line)
            if not m:
                skipped += 1
                continue
            ts = float(m.group("ts"))
            iface = m.group("if")
            canid_hex = m.group("canid")
            data_hex = m.group("data") or ""
            frames.append((ts, iface, canid_hex, data_hex))

    if not frames:
        raise ValueError("No valid CAN frames found — check the input file format.")

    log_callback(f"Parsed {len(frames):,} frames  ({skipped} lines skipped)")

    dt0 = datetime.utcfromtimestamp(frames[0][0]) if use_utc else datetime.fromtimestamp(frames[0][0])
    # The header only holds milliseconds, so offsets are measured from that truncated time
    sub_ms = dt0.microsecond % 1000
    dt0 = dt0.replace(microsecond=dt0.microsecond - sub_ms)
    t0 = frames[0][0] - sub_ms / 1_000_000

    log_callback(f"Start timestamp: {dt0.strftime('%Y-%m-%d %H:%M:%S')} ({'UTC' if use_utc else 'local'})")
    log_callback(f"Writing: {output_path}")

    with open(output_path, "w", encoding="utf-8") as out:
        out.write(format_asc_date(dt0) + "\n")
        out.write("base hex  no internal events logged\n")
        out.write(f"// version {version}\n")

        for ts, iface, canid_hex, data_hex in frames:
            rel = ts - t0
            channel = iface_to_channel(iface)
            canid_int = int(canid_hex, 16)
            is_extended = canid_int > 0x7FF
            canid_out = canid_hex.upper() + ("x" if is_extended else "")
            data_bytes = chunk_bytes(data_hex)
            dlc = len(data_bytes)
            out.write(f"{rel:.6f} {channel} {canid_out} Rx d {dlc}")
            if dlc:
                out.write(" " + " ".join(data_bytes))
            out.write("\n")

    log_callback(f"Done — {len(frames):,} frames written.")
    log_callback(f"Output: {output_path}")


# ── Trim logic ─────────────────────────────────────────────────────────────────
# Files are read/written as latin-1 with newline="" so every kept line is
# copied byte-for-byte (original line endings included).

LOG_TS_RE   = re.compile(r"^\s*\((?P<ts>\d+(?:\.\d+)?)\)")
ASC_TS_RE   = re.compile(r"^\s*(?P<ts>\d+\.\d+)(?=\s)")
ASC_BASE_RE = re.compile(r"^\s*base\s+\w+\s+timestamps\s+(?P<mode>absolute|relative)", re.I)
ASC_DATE_RE = re.compile(
    r"(?P<mon>[A-Za-z]{3})\s+(?P<day>\d{1,2})\s+"
    r"(?P<h>\d{1,2}):(?P<m>\d{1,2}):(?P<s>\d{1,2})(?:\.(?P<frac>\d+))?"
    r"(?:\s*(?P<ampm>[AaPp][Mm]))?\s+(?P<year>\d{4})"
)
MONTHS = {m.lower(): i for i, m in enumerate(MON_NAMES, 1)}
EPS = 1e-6   # tolerance for µs rounding when comparing against user-entered times
FILE_ENC = "latin-1"

def parse_asc_date(text: str):
    """Parse an ASC header date such as 'Tue Sep 29 3:04:05 pm 2026' (local time)."""
    m = ASC_DATE_RE.search(text)
    if not m or m.group("mon").lower() not in MONTHS:
        return None
    hour = int(m.group("h"))
    ampm = (m.group("ampm") or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    frac = m.group("frac") or "0"
    micro = int(round(float("0." + frac) * 1_000_000))
    return datetime(int(m.group("year")), MONTHS[m.group("mon").lower()], int(m.group("day")),
                    hour, int(m.group("m")), int(m.group("s")), min(micro, 999_999))

def detect_format(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".log", ".asc"):
        return ext[1:]
    with open(path, "r", encoding=FILE_ENC) as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if s.startswith("("):
                return "log"
            if s.lower().startswith(("date ", "base ", "//")):
                return "asc"
            break
    raise ValueError("Unrecognised file format — expected a candump .log or Vector .asc file.")

def _iter_asc(f):
    """Yield (line, absolute_epoch_or_None, ts_match_or_None) for each line of an ASC file."""
    base_epoch = None
    relative = False
    prev = 0.0
    for line in f:
        m = ASC_TS_RE.match(line)
        if m:
            if base_epoch is None:
                raise ValueError("ASC file has no 'date' header line — cannot determine absolute times.")
            off = float(m.group("ts"))
            if relative:
                prev += off
                off = prev
            yield line, base_epoch + off, m
            continue
        s = line.strip()
        if base_epoch is None and s.lower().startswith("date "):
            dt = parse_asc_date(s[5:])
            if dt:
                base_epoch = dt.timestamp()
        else:
            b = ASC_BASE_RE.match(line)
            if b:
                relative = b.group("mode").lower() == "relative"
        yield line, None, None

def _iter_log(f):
    """Yield (line, epoch_or_None) for each line of a candump .log file."""
    for line in f:
        m = LOG_TS_RE.match(line)
        yield line, (float(m.group("ts")) if m else None)

def scan_can_file(path: str) -> dict:
    """Return format, first/last timestamp (epoch seconds) and frame count."""
    fmt = detect_format(path)
    start = end = None
    count = 0
    with open(path, "r", encoding=FILE_ENC, newline="") as f:
        items = _iter_asc(f) if fmt == "asc" else _iter_log(f)
        for item in items:
            ts = item[1]
            if ts is None:
                continue
            count += 1
            start = ts if start is None else min(start, ts)
            end = ts if end is None else max(end, ts)
    if not count:
        raise ValueError("No timestamped frames found in file.")
    return {"fmt": fmt, "start": start, "end": end, "count": count}

def _rewrite_asc_header_line(line: str, dt: datetime) -> str:
    body = line.rstrip("\r\n")
    eol = line[len(body):]
    low = body.strip().lower()
    if low.startswith("date "):
        return format_asc_date(dt) + eol
    if low.startswith("begin triggerblock") and parse_asc_date(body):
        return "Begin Triggerblock " + format_asc_date(dt)[5:] + eol
    if ASC_BASE_RE.match(body):
        # Output timestamps are always written as absolute offsets
        return re.sub("relative", "absolute", body, flags=re.I) + eol
    return line

def _trim_log(fin, fout, t0, t1):
    kept = 0
    for line, ts in _iter_log(fin):
        if ts is not None and t0 <= ts <= t1:
            fout.write(line)
            kept += 1
    return kept

def _trim_asc(fin, fout, t0, t1):
    header = []       # lines before the first timestamped line in the file
    started = False   # input has reached its first timestamped line
    base = None       # header epoch (whole second) of the trimmed output
    kept = 0
    for line, ts, m in _iter_asc(fin):
        if ts is None:
            if base is not None:
                fout.write(line)
            elif not started:
                header.append(line)
            continue
        started = True
        if not (t0 <= ts <= t1):
            continue
        if base is None:
            base = math.floor(ts)
            dt = datetime.fromtimestamp(base)
            for h in header:
                fout.write(_rewrite_asc_header_line(h, dt))
        rel = ts - base
        fout.write(f"{line[:m.start('ts')]}{rel:.6f}{line[m.end('ts'):]}")
        kept += 1
    return kept

def trim_can_file(input_path, output_path, fmt, t_start, t_end, log_callback):
    """Write frames with t_start <= timestamp <= t_end (epoch seconds) to output_path."""
    log_callback(f"Reading: {input_path}")
    log_callback(f"Range: {fmt_dt(t_start)}  →  {fmt_dt(t_end)}")
    trim = _trim_asc if fmt == "asc" else _trim_log
    with open(input_path, "r", encoding=FILE_ENC, newline="") as fin, \
         open(output_path, "w", encoding=FILE_ENC, newline="") as fout:
        kept = trim(fin, fout, t_start - EPS, t_end + EPS)
    if not kept:
        os.remove(output_path)
        raise ValueError("No frames fall within the selected range — nothing written.")
    log_callback(f"Done — {kept:,} frames written.")
    log_callback(f"Output: {output_path}")


# ── Formatting helpers ─────────────────────────────────────────────────────────

DT_FMT = "%Y-%m-%d %H:%M:%S.%f"
DT_INPUT_FMTS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")

def fmt_dt(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime(DT_FMT)

def parse_user_dt(text: str) -> float:
    """Parse a local datetime typed by the user; return epoch seconds."""
    s = text.strip().replace("T", " ")
    for fmt in DT_INPUT_FMTS:
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            pass
    raise ValueError(f"Invalid datetime '{text.strip()}' — use YYYY-MM-DD HH:MM:SS[.ffffff]")

def fmt_duration(seconds: float) -> str:
    days, rem = divmod(max(seconds, 0.0), 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    out = f"{int(h):02d}:{int(m):02d}:{s:06.3f}"
    return f"{int(days)}d {out}" if days else out

def open_in_explorer(path, log):
    folder = os.path.dirname(os.path.abspath(path))
    try:
        if sys.platform == "win32":
            # On Windows, also select the file in Explorer
            subprocess.Popen(["explorer", "/select,", os.path.abspath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
    except Exception as e:
        log(f"Could not open folder: {e}", "dim")


# ── Colour palette ─────────────────────────────────────────────────────────────
BG        = "#1C1C1E"   # near-black
PANEL     = "#2A2A2E"   # slightly lighter panel
BORDER    = "#3A3A3E"   # subtle rule
AMBER     = "#E8A000"   # signature accent
AMBER_DIM = "#7A5200"   # muted amber for inactive
FG        = "#D4D0C8"   # warm off-white
FG_DIM    = "#7A7870"   # muted text
GREEN     = "#5FBF5F"   # success
RED       = "#C0392B"   # error
MONO      = ("Consolas", 9) if sys.platform == "win32" else ("Courier New", 9)
MONO_LG   = ("Consolas", 10) if sys.platform == "win32" else ("Courier New", 10)
BTN_FONT  = ("Consolas", 11, "bold") if sys.platform == "win32" else ("Courier New", 11, "bold")


class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, _=None):
        x, y, _, _ = self.widget.bbox("insert") if hasattr(self.widget, "bbox") else (0, 0, 0, 0)
        x += self.widget.winfo_rootx() + 20
        y += self.widget.winfo_rooty() + 20
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(self.tip, text=self.text, bg=PANEL, fg=FG_DIM,
                       font=MONO, relief="flat", padx=6, pady=3)
        lbl.pack()

    def hide(self, _=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


# ── Shared widget builders ─────────────────────────────────────────────────────

def make_entry(parent, var, width=None, font=MONO_LG, bg=PANEL):
    kw = {"width": width} if width else {}
    return tk.Entry(parent, textvariable=var,
                    bg=bg, fg=FG, insertbackground=AMBER,
                    font=font, relief="flat",
                    highlightthickness=1, highlightbackground=BORDER,
                    highlightcolor=AMBER, **kw)

def make_file_row(parent, row, label, var, browse_cmd, tip):
    tk.Label(parent, text=label, font=MONO, bg=BG, fg=FG_DIM,
             anchor="w", width=16).grid(row=row, column=0, sticky="w", pady=5)

    entry = make_entry(parent, var)
    entry.grid(row=row, column=1, sticky="ew", padx=(8, 6), pady=5)
    ToolTip(entry, tip)

    btn = tk.Button(parent, text="Browse…", command=browse_cmd,
                    bg=PANEL, fg=FG, activebackground=BORDER,
                    font=MONO, relief="flat", bd=0, padx=10, pady=3,
                    cursor="hand2")
    btn.grid(row=row, column=2, sticky="e", pady=5)

def make_action_button(parent, text, command):
    return tk.Button(
        parent, text=text, command=command,
        bg=AMBER, fg=BG, activebackground=AMBER_DIM, activeforeground=FG,
        font=BTN_FONT, relief="flat", bd=0, padx=20, pady=8, cursor="hand2",
        disabledforeground=AMBER_DIM
    )


# ── Convert tab ────────────────────────────────────────────────────────────────

class ConvertTab(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app

        self._input_var  = tk.StringVar()
        self._output_var = tk.StringVar()
        self._utc_var    = tk.BooleanVar(value=False)
        self._ver_var    = tk.StringVar(value="7.1.0")
        self._busy       = False

        self._build_ui()
        self._update_convert_btn()

        # Watch file fields for changes
        self._input_var.trace_add("write",  lambda *_: self._on_input_changed())
        self._output_var.trace_add("write", lambda *_: self._update_convert_btn())

    def _build_ui(self):
        # ── Main form ──
        form = tk.Frame(self, bg=BG, padx=20, pady=16)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        make_file_row(form, 0, "Input .log file", self._input_var,  self._browse_input,  "Source CAN log file (candump-style)")
        make_file_row(form, 1, "Output .asc file", self._output_var, self._browse_output, "Destination Vector .ASC file")

        # ── Options row ──
        opt_frame = tk.Frame(self, bg=PANEL, padx=20, pady=10)
        opt_frame.pack(fill="x")

        tk.Label(opt_frame, text="OPTIONS", font=MONO, bg=PANEL, fg=FG_DIM).pack(side="left")

        utc_cb = tk.Checkbutton(opt_frame, text="UTC timestamps", variable=self._utc_var,
                                bg=PANEL, fg=FG, selectcolor=BG,
                                activebackground=PANEL, activeforeground=FG,
                                font=MONO, bd=0, highlightthickness=0)
        utc_cb.pack(side="left", padx=(16, 0))
        ToolTip(utc_cb, "Interpret timestamps as UTC (default: local time)")

        tk.Label(opt_frame, text="ASC version:", font=MONO, bg=PANEL, fg=FG_DIM).pack(side="left", padx=(24, 4))
        ver_entry = make_entry(opt_frame, self._ver_var, width=7, font=MONO, bg=BG)
        ver_entry.pack(side="left")
        ToolTip(ver_entry, "Written into the // version comment in the .asc header")

        # ── Convert button ──
        btn_frame = tk.Frame(self, bg=BG, padx=20, pady=10)
        btn_frame.pack(fill="x")

        self._convert_btn = make_action_button(btn_frame, "▶  CONVERT", self._start_conversion)
        self._convert_btn.pack(side="left")

        self._status_lbl = tk.Label(btn_frame, text="", font=MONO, bg=BG, fg=FG_DIM)
        self._status_lbl.pack(side="left", padx=16)

    # ── Browse handlers ────────────────────────────────────────────────────────

    def _browse_input(self):
        path = filedialog.askopenfilename(
            title="Select CAN log file",
            filetypes=[("Log files", "*.log"), ("All files", "*.*")]
        )
        if path:
            self._input_var.set(path)

    def _browse_output(self):
        suggested = ""
        inp = self._input_var.get()
        if inp:
            base = os.path.splitext(inp)[0]
            suggested = base + ".asc"
        path = filedialog.asksaveasfilename(
            title="Save .asc file as",
            initialfile=os.path.basename(suggested) if suggested else "output.asc",
            initialdir=os.path.dirname(suggested) if suggested else os.path.expanduser("~"),
            defaultextension=".asc",
            filetypes=[("ASC files", "*.asc"), ("All files", "*.*")]
        )
        if path:
            self._output_var.set(path)

    def _on_input_changed(self):
        inp = self._input_var.get()
        # Auto-suggest output path if output is blank
        if inp and not self._output_var.get():
            base = os.path.splitext(inp)[0]
            self._output_var.set(base + ".asc")
        self._update_convert_btn()

    # ── Button state ──────────────────────────────────────────────────────────

    def _update_convert_btn(self):
        ready = bool(self._input_var.get() and self._output_var.get()) and not self._busy
        self._convert_btn.configure(state="normal" if ready else "disabled")

    # ── Conversion ────────────────────────────────────────────────────────────

    def _start_conversion(self):
        inp  = self._input_var.get().strip()
        out  = self._output_var.get().strip()
        utc  = self._utc_var.get()
        ver  = self._ver_var.get().strip() or "7.1.0"

        if not os.path.isfile(inp):
            messagebox.showerror("File not found", f"Cannot find:\n{inp}")
            return

        self._busy = True
        self._update_convert_btn()
        self._status_lbl.configure(text="Converting…", fg=AMBER)
        self.app.log_line("─" * 52, "dim")
        self.app.log_line(f"Starting conversion — {datetime.now().strftime('%H:%M:%S')}", "amber")

        def worker():
            try:
                run_conversion(inp, out, utc, ver, self._thread_log)
                self.after(0, self._on_done, True, None)
            except Exception as e:
                self.after(0, self._on_done, False, str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _thread_log(self, msg):
        # Called from worker thread — schedule on main thread
        self.after(0, self.app.log_line, msg, "ok")

    def _on_done(self, success, error):
        self._busy = False
        self._update_convert_btn()
        if success:
            self._status_lbl.configure(text="✓ Conversion complete", fg=GREEN)
            open_in_explorer(self._output_var.get().strip(), self.app.log_line)
        else:
            self._status_lbl.configure(text="✗ Error — see log", fg=RED)
            self.app.log_line(f"ERROR: {error}", "err")


# ── Trim tab ───────────────────────────────────────────────────────────────────

class TrimTab(tk.Frame):
    INFO_FIELDS = (("fmt", "Format"), ("count", "Frames"), ("start", "Start"),
                   ("end", "End"), ("duration", "Duration"))

    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app

        self._input_var  = tk.StringVar()
        self._output_var = tk.StringVar()
        self._start_var  = tk.StringVar()
        self._end_var    = tk.StringVar()
        self._info       = None   # scan result for the current input file
        self._auto_output = ""    # last auto-suggested output path
        self._scan_job   = None
        self._busy       = False

        self._build_ui()
        self._set_info(None)

        self._input_var.trace_add("write",  lambda *_: self._on_input_changed())
        self._output_var.trace_add("write", lambda *_: self._update_selection())
        self._start_var.trace_add("write",  lambda *_: self._update_selection())
        self._end_var.trace_add("write",    lambda *_: self._update_selection())
        self._update_selection()

    def _build_ui(self):
        # ── File selection ──
        form = tk.Frame(self, bg=BG, padx=20, pady=16)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        make_file_row(form, 0, "Input log file", self._input_var, self._browse_input,
                      "Source CAN log file (.log candump or .asc Vector)")
        make_file_row(form, 1, "Output file", self._output_var, self._browse_output,
                      "Destination for the trimmed log (same format as input)")

        # ── File info ──
        info = tk.Frame(self, bg=PANEL, padx=20, pady=10)
        info.pack(fill="x")
        tk.Label(info, text="FILE INFO", font=MONO, bg=PANEL, fg=FG_DIM).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        self._info_lbls = {}
        for i, (key, label) in enumerate(self.INFO_FIELDS, 1):
            tk.Label(info, text=label, font=MONO, bg=PANEL, fg=FG_DIM,
                     anchor="w", width=14).grid(row=i, column=0, sticky="w")
            val = tk.Label(info, text="—", font=MONO_LG, bg=PANEL, fg=FG, anchor="w")
            val.grid(row=i, column=1, sticky="w")
            self._info_lbls[key] = val
        tk.Label(info, text="(local time)", font=MONO, bg=PANEL, fg=FG_DIM).grid(
            row=3, column=2, sticky="w", padx=(12, 0))

        # ── Trim range ──
        rng = tk.Frame(self, bg=BG, padx=20, pady=12)
        rng.pack(fill="x")
        tk.Label(rng, text="New start", font=MONO, bg=BG, fg=FG_DIM,
                 anchor="w", width=16).grid(row=0, column=0, sticky="w", pady=5)
        start_entry = make_entry(rng, self._start_var, width=28)
        start_entry.grid(row=0, column=1, sticky="w", padx=(8, 6), pady=5)
        ToolTip(start_entry, "Local time, YYYY-MM-DD HH:MM:SS[.ffffff]")

        tk.Label(rng, text="New end", font=MONO, bg=BG, fg=FG_DIM,
                 anchor="w", width=16).grid(row=1, column=0, sticky="w", pady=5)
        end_entry = make_entry(rng, self._end_var, width=28)
        end_entry.grid(row=1, column=1, sticky="w", padx=(8, 6), pady=5)
        ToolTip(end_entry, "Local time, YYYY-MM-DD HH:MM:SS[.ffffff]")

        reset_btn = tk.Button(rng, text="Reset", command=self._reset_range,
                              bg=PANEL, fg=FG, activebackground=BORDER,
                              font=MONO, relief="flat", bd=0, padx=10, pady=3,
                              cursor="hand2")
        reset_btn.grid(row=0, column=2, sticky="w", pady=5)
        ToolTip(reset_btn, "Restore the full file range")

        self._sel_lbl = tk.Label(rng, text="", font=MONO, bg=BG, fg=FG_DIM, anchor="w")
        self._sel_lbl.grid(row=1, column=2, sticky="w", pady=5)

        # ── Trim button ──
        btn_frame = tk.Frame(self, bg=BG, padx=20, pady=10)
        btn_frame.pack(fill="x")

        self._trim_btn = make_action_button(btn_frame, "✂  TRIM", self._start_trim)
        self._trim_btn.pack(side="left")

        self._status_lbl = tk.Label(btn_frame, text="", font=MONO, bg=BG, fg=FG_DIM)
        self._status_lbl.pack(side="left", padx=16)

    # ── Browse handlers ────────────────────────────────────────────────────────

    def _browse_input(self):
        path = filedialog.askopenfilename(
            title="Select CAN log file",
            filetypes=[("CAN logs", "*.log *.asc"), ("candump logs", "*.log"),
                       ("ASC files", "*.asc"), ("All files", "*.*")]
        )
        if path:
            self._input_var.set(path)

    def _browse_output(self):
        suggested = self._output_var.get() or "trimmed.log"
        ext = os.path.splitext(suggested)[1] or ".log"
        path = filedialog.asksaveasfilename(
            title="Save trimmed file as",
            initialfile=os.path.basename(suggested),
            initialdir=os.path.dirname(suggested) or os.path.expanduser("~"),
            defaultextension=ext,
            filetypes=[(f"{ext[1:].upper()} files", f"*{ext}"), ("All files", "*.*")]
        )
        if path:
            self._output_var.set(path)

    # ── File scanning ──────────────────────────────────────────────────────────

    def _on_input_changed(self):
        inp = self._input_var.get().strip()
        out = self._output_var.get()
        # Auto-suggest output path unless the user has chosen one
        if inp and (not out or out == self._auto_output):
            base, ext = os.path.splitext(inp)
            self._auto_output = f"{base}_trimmed{ext or '.log'}"
            self._output_var.set(self._auto_output)

        self._set_info(None)
        # Debounce so typing a path doesn't trigger a scan per keystroke
        if self._scan_job:
            self.after_cancel(self._scan_job)
        self._scan_job = self.after(400, self._start_scan)

    def _start_scan(self):
        self._scan_job = None
        path = self._input_var.get().strip()
        if not os.path.isfile(path):
            return
        for lbl in self._info_lbls.values():
            lbl.configure(text="…", fg=FG_DIM)
        self._status_lbl.configure(text="Scanning file…", fg=AMBER)

        def worker():
            try:
                info = scan_can_file(path)
                self.after(0, self._on_scan_done, path, info, None)
            except Exception as e:
                self.after(0, self._on_scan_done, path, None, str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_scan_done(self, path, info, error):
        if path != self._input_var.get().strip():
            return   # input changed while scanning
        self._status_lbl.configure(text="")
        if error:
            self._set_info(None)
            self._status_lbl.configure(text="✗ Could not read file — see log", fg=RED)
            self.app.log_line(f"ERROR reading {os.path.basename(path)}: {error}", "err")
            return
        self._set_info(info)
        self._reset_range()
        self.app.log_line(
            f"Loaded {os.path.basename(path)}: {info['count']:,} frames, "
            f"{fmt_dt(info['start'])} → {fmt_dt(info['end'])}", "amber")

    def _set_info(self, info):
        self._info = info
        if info is None:
            for lbl in self._info_lbls.values():
                lbl.configure(text="—", fg=FG_DIM)
        else:
            values = {
                "fmt": "candump .log" if info["fmt"] == "log" else "Vector .asc",
                "count": f"{info['count']:,}",
                "start": fmt_dt(info["start"]),
                "end": fmt_dt(info["end"]),
                "duration": fmt_duration(info["end"] - info["start"]),
            }
            for key, lbl in self._info_lbls.items():
                lbl.configure(text=values[key], fg=FG)
        self._update_selection()

    # ── Range selection ────────────────────────────────────────────────────────

    def _reset_range(self):
        if self._info:
            self._start_var.set(fmt_dt(self._info["start"]))
            self._end_var.set(fmt_dt(self._info["end"]))

    def _parse_range(self):
        """Return (t_start, t_end) epoch seconds or raise ValueError."""
        t0 = parse_user_dt(self._start_var.get())
        t1 = parse_user_dt(self._end_var.get())
        if self._info:
            f0, f1 = self._info["start"] - EPS, self._info["end"] + EPS
            for name, t in (("Start", t0), ("End", t1)):
                if t < f0:
                    raise ValueError(f"{name} is before the file start")
                if t > f1:
                    raise ValueError(f"{name} is after the file end")
        if t1 < t0:
            raise ValueError("End is before start")
        return t0, t1

    def _update_selection(self):
        valid = False
        if self._info is None:
            self._sel_lbl.configure(text="")
        else:
            try:
                t0, t1 = self._parse_range()
                self._sel_lbl.configure(text=f"Selection: {fmt_duration(t1 - t0)}", fg=FG_DIM)
                valid = True
            except ValueError as e:
                msg = str(e).split(" — ")[0]
                self._sel_lbl.configure(text=f"✗ {msg}", fg=RED)
        ready = valid and bool(self._output_var.get().strip()) and not self._busy
        self._trim_btn.configure(state="normal" if ready else "disabled")

    # ── Trimming ──────────────────────────────────────────────────────────────

    def _start_trim(self):
        inp = self._input_var.get().strip()
        out = self._output_var.get().strip()
        try:
            t0, t1 = self._parse_range()
        except ValueError as e:
            messagebox.showerror("Invalid range", str(e))
            return
        if not os.path.isfile(inp):
            messagebox.showerror("File not found", f"Cannot find:\n{inp}")
            return
        if os.path.normcase(os.path.abspath(inp)) == os.path.normcase(os.path.abspath(out)):
            messagebox.showerror("Invalid output", "Output file must be different from the input file.")
            return
        if os.path.exists(out) and not messagebox.askyesno(
                "Overwrite file?", f"{out}\n\nalready exists. Overwrite it?"):
            return

        fmt = self._info["fmt"]
        self._busy = True
        self._update_selection()
        self._status_lbl.configure(text="Trimming…", fg=AMBER)
        self.app.log_line("─" * 52, "dim")
        self.app.log_line(f"Starting trim — {datetime.now().strftime('%H:%M:%S')}", "amber")

        def worker():
            try:
                trim_can_file(inp, out, fmt, t0, t1, self._thread_log)
                self.after(0, self._on_done, out, None)
            except Exception as e:
                self.after(0, self._on_done, out, str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _thread_log(self, msg):
        # Called from worker thread — schedule on main thread
        self.after(0, self.app.log_line, msg, "ok")

    def _on_done(self, out, error):
        self._busy = False
        self._update_selection()
        if error is None:
            self._status_lbl.configure(text="✓ Trim complete", fg=GREEN)
            open_in_explorer(out, self.app.log_line)
        else:
            self._status_lbl.configure(text="✗ Error — see log", fg=RED)
            self.app.log_line(f"ERROR: {error}", "err")


# ── Main window ────────────────────────────────────────────────────────────────

class CanToolsApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CAN Log Tools")
        self.resizable(True, True)
        self.minsize(700, 640)
        self.configure(bg=BG)

        self._style_notebook()
        self._build_ui()

    def _style_notebook(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook", background=BG, borderwidth=0,
                        bordercolor=BORDER, lightcolor=BG, darkcolor=BG,
                        tabmargins=(20, 10, 20, 0))
        style.configure("TNotebook.Tab", background=PANEL, foreground=FG_DIM,
                        bordercolor=BORDER, lightcolor=PANEL, darkcolor=PANEL,
                        focuscolor=BG, font=MONO_LG, padding=(16, 5))
        style.map("TNotebook.Tab",
                  background=[("selected", BG)],
                  lightcolor=[("selected", BG)],
                  foreground=[("selected", AMBER)])

    def _build_ui(self):
        # ── Header bar ──
        hdr = tk.Frame(self, bg=AMBER, height=3)
        hdr.pack(fill="x", side="top")

        title_frame = tk.Frame(self, bg=BG, pady=14)
        title_frame.pack(fill="x", padx=20)
        tk.Label(title_frame, text="CAN LOG TOOLS", font=("Consolas", 15, "bold"),
                 bg=BG, fg=AMBER).pack(side="left")
        tk.Label(title_frame, text="convert · trim", font=MONO,
                 bg=BG, fg=FG_DIM).pack(side="left", padx=(12, 0))

        sep = tk.Frame(self, bg=BORDER, height=1)
        sep.pack(fill="x", padx=0)

        # ── Tabs ──
        notebook = ttk.Notebook(self)
        notebook.pack(fill="x")
        notebook.add(ConvertTab(notebook, self), text="Convert to ASC")
        notebook.add(TrimTab(notebook, self), text="Trim")

        # ── Log pane (shared by all tabs) ──
        log_hdr = tk.Frame(self, bg=BG, padx=20)
        log_hdr.pack(fill="x")
        tk.Label(log_hdr, text="OUTPUT LOG", font=MONO, bg=BG, fg=FG_DIM).pack(side="left", pady=(8,2))

        self._clear_btn = tk.Button(log_hdr, text="Clear", command=self._clear_log,
                                    bg=PANEL, fg=FG_DIM, activebackground=BORDER,
                                    font=MONO, relief="flat", bd=0, padx=8, pady=1,
                                    cursor="hand2")
        self._clear_btn.pack(side="right", pady=(8,2))

        log_outer = tk.Frame(self, bg=BORDER, padx=1, pady=1)
        log_outer.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        self._log = tk.Text(log_outer, bg=PANEL, fg=FG, font=MONO_LG, height=10,
                            relief="flat", bd=0, state="disabled",
                            insertbackground=AMBER, wrap="none",
                            highlightthickness=0)
        scroll_y = tk.Scrollbar(log_outer, command=self._log.yview, bg=PANEL,
                                troughcolor=BG, activebackground=AMBER_DIM,
                                relief="flat", bd=0, width=10)
        self._log.configure(yscrollcommand=scroll_y.set)
        scroll_y.pack(side="right", fill="y")
        self._log.pack(fill="both", expand=True)

        # Tag colours for the log
        self._log.tag_config("ok",    foreground=GREEN)
        self._log.tag_config("err",   foreground=RED)
        self._log.tag_config("amber", foreground=AMBER)
        self._log.tag_config("dim",   foreground=FG_DIM)

        self.log_line("CAN Log Tools ready.", "amber")
        self.log_line("Choose a tab and select an input file to begin.", "dim")

    # ── Log helpers ───────────────────────────────────────────────────────────

    def log_line(self, msg, tag=""):
        self._log.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        full = f"[{ts}]  {msg}\n"
        if tag:
            self._log.insert("end", full, tag)
        else:
            self._log.insert("end", full)
        self._log.configure(state="disabled")
        self._log.see("end")

    def _clear_log(self):
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")


if __name__ == "__main__":
    app = CanToolsApp()
    app.mainloop()
