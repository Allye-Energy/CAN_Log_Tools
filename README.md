# CAN Log Tools

A small Windows desktop tool for working with CAN bus log files. It wraps two common jobs in a simple GUI:

- **Convert to ASC**: turns a Linux `candump` `.log` file into a Vector `.asc` file, so it can be opened in Vector tools (CANalyzer, CANoe) and other ASC-compatible viewers.
- **Trim**: cuts a `.log` or `.asc` file down to a chosen start and end time, so you can share or analyse just the section you care about.

The app is a single Python script ([can_tools_app.py](can_tools_app.py)) using only the standard library (Tkinter), packaged into a standalone `can_tools.exe` with PyInstaller.

## Using the tool

Run `can_tools.exe` (or `python can_tools_app.py`). Progress and any errors appear in the **Output Log** pane at the bottom of the window. When a job finishes, the output folder opens in Explorer with the new file selected.

### Convert to ASC

1. Pick the input `candump` `.log` file. The output path is filled in automatically (same name, `.asc` extension) and can be changed.
2. Set the options if needed:
   - **UTC timestamps**: interpret the log's epoch timestamps as UTC. By default they are treated as local time.
   - **ASC version**: the value written into the `// version` line of the ASC header (default `7.1.0`).
3. Click **Convert**.

The input is expected in the standard `candump -l` format, one frame per line:

```
(1727618645.123456) can0 18FF50E5#0102030405060708
```

Notes on the conversion:

- Timestamps in the output are relative to the first frame, and the `date` header line records the absolute start time.
- The channel number is taken from the trailing digits of the interface name (`can0` becomes channel `0`, `can1` becomes `1`). Interfaces without a number map to channel `1`.
- IDs above `0x7FF` are written as extended (29-bit) IDs with an `x` suffix.
- All frames are written as received (`Rx`) data frames. Lines that do not match the format are skipped and counted in the log.

### Trim

1. Pick the input file (`.log` or `.asc`). The tool scans it and shows the format, frame count, start and end time, and duration.
2. The output path defaults to `<input>_trimmed.<ext>` and keeps the same format as the input.
3. Enter the **New start** and **New end** times in local time, as `YYYY-MM-DD HH:MM:SS[.ffffff]`. **Reset** restores the full range of the file.
4. Click **Trim**.

Frames inside the range are copied unchanged. For `.asc` files, the header date is updated to the new start time and frame timestamps are recalculated against it (relative-timestamp files are written out as absolute). The output file must be different from the input file.

## Building the executable

### Requirements

- Windows
- Python 3.8 or later on your `PATH`, with Tkinter (included in the standard python.org installer)
- PyInstaller (the build script installs it automatically if it is missing)

There are no other dependencies.

### Build with the script

From the project folder, run:

```bat
build.bat
```

The script:

1. Checks that Python is available and installs PyInstaller if needed.
2. Runs PyInstaller to produce a single-file, windowed (no console) executable, using [log_to_asc.ico](log_to_asc.ico) as the icon if present.
3. Writes the result to `dist\can_tools.exe` and opens the `dist` folder.

The `build\` and `dist\` folders and the generated `can_tools.spec` are build output and are ignored by Git.

### Building manually

If you prefer to run PyInstaller yourself:

```bat
python -m pip install pyinstaller
python -m PyInstaller --onefile --windowed --name can_tools --icon log_to_asc.ico --clean can_tools_app.py
```

### Running from source

No build is needed for development. Just run:

```bat
python can_tools_app.py
```

## Project files

| File | Purpose |
| --- | --- |
| [can_tools_app.py](can_tools_app.py) | The application: conversion and trim logic, plus the Tkinter GUI |
| [build.bat](build.bat) | Builds `dist\can_tools.exe` with PyInstaller |
| [log_to_asc.ico](log_to_asc.ico) | Application icon |
