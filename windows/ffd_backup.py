#!/usr/bin/env python3
"""Rootless, read-only Android APK/split-APK/OBB backup utility.

Requires Python 3.10+ and Google's Android Platform Tools (adb).
No third-party Python dependencies; no rooting, patching or uninstalling.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from typing import Callable

DEFAULT_PACKAGE = "com.square_enix.android_googleplay.ffl_gp"
PACKAGE_RE = re.compile(r"^[a-zA-Z][\w]*(?:\.[a-zA-Z][\w]*)+$")
HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class BackupError(RuntimeError):
    pass


class Cancelled(BackupError):
    pass


def find_adb(explicit: str | None = None) -> str:
    options = [explicit, os.environ.get("ADB_PATH"),
               str(Path(__file__).resolve().parent / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")),
               shutil.which("adb")]
    for option in options:
        if option and (Path(option).is_file() or shutil.which(option)):
            return option
    raise BackupError("ADB not found. Install Google's Android Platform Tools, add adb to PATH, "
                      "or select adb.exe using the GUI.")


def safe_package(package: str) -> str:
    if not PACKAGE_RE.fullmatch(package):
        raise BackupError("Invalid Android package identifier.")
    return package


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _unique_folder(base: Path) -> Path:
    if not base.exists():
        return base
    for n in range(1, 10_000):
        alt = base.with_name(f"{base.name}_{n:03d}")
        if not alt.exists():
            return alt
    raise BackupError("Could not create a unique backup directory")


class Adb:
    def __init__(self, binary: str, serial: str | None = None,
                 stop: threading.Event | None = None) -> None:
        self.binary = binary
        self.serial = serial
        self.stop = stop or threading.Event()
        self.current_process: subprocess.Popen | None = None

    def check_cancelled(self) -> None:
        if self.stop.is_set():
            raise Cancelled("Export cancelled. Previously copied files were retained.")

    def run(self, *args: str, timeout: int = 90, check: bool = True) -> subprocess.CompletedProcess[str]:
        self.check_cancelled()
        cmd = [self.binary]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += list(args)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as e:
            raise BackupError(f"ADB command timed out: {' '.join(args)}") from e
        except OSError as e:
            raise BackupError(f"Cannot run ADB: {e}") from e
        self.check_cancelled()
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise BackupError(f"ADB {args[0]} failed: {detail[:800]}")
        return result

    def pull(self, remote: str, local: Path) -> None:
        """Copy a single remote file; ensure partial files never masquerade as complete."""
        self.check_cancelled()
        local.parent.mkdir(parents=True, exist_ok=True)
        tmp = local.with_name(local.name + ".partial")
        tmp.unlink(missing_ok=True)
        cmd = [self.binary]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += ["pull", remote, str(tmp)]
        try:
            # Poll so the GUI Cancel control works during a long OBB transfer.
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    stdin=subprocess.DEVNULL)
            self.current_process = proc
            while proc.poll() is None:
                if self.stop.wait(0.20):
                    proc.kill()
                    proc.communicate()
                    raise Cancelled("Export cancelled during file transfer; partial copy retained.")
            stdout, stderr = proc.communicate()
            if proc.returncode:
                raise BackupError(f"ADB pull failed ({remote}): "
                                  f"{(stderr or stdout).decode('utf-8', errors='replace')[-600:]}")
            if not tmp.is_file():
                raise BackupError(f"ADB said it copied {remote}, but output file is missing")
            tmp.replace(local)
        except OSError as e:
            raise BackupError(f"Could not copy {remote}: {e}") from e
        finally:
            self.current_process = None
            # Do not destroy a partial transfer for forensic/recovery purposes.

    def devices(self) -> list[tuple[str, str, str]]:
        output = self.run("devices", "-l", check=True).stdout
        records = []
        for line in output.splitlines()[1:]:
            parts = line.split(None, 2)
            if len(parts) >= 2:
                records.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
        return records

    def apk_paths(self, package: str) -> list[str]:
        result = self.run("shell", "pm", "path", package)
        paths = [x.partition("package:")[2].strip() for x in result.stdout.splitlines()
                 if x.startswith("package:")]
        if not paths:
            raise BackupError(f"No APK paths returned for {package}; check the package and installation.")
        if len(paths) != len(set(paths)):
            raise BackupError("Duplicate APK paths in package manager output")
        for path in paths:
            if not path.startswith("/") or not path.endswith(".apk"):
                raise BackupError(f"Unexpected APK path: {path!r}")
        return paths

    def package_version(self, package: str) -> dict[str, str | None]:
        response = self.run("shell", "dumpsys", "package", package, check=False, timeout=60)
        output = response.stdout
        name = re.search(r"\bversionName=([^\s]+)", output)
        code = re.search(r"\bversionCode=(\d+)", output)
        return {"version_name": name.group(1) if name else None,
                "version_code": code.group(1) if code else None}

    def obb_paths(self, package: str) -> tuple[list[str], str]:
        root = f"/sdcard/Android/obb/{package}"
        # Output paths are *filenames*, not command fragments. Package is strictly validated.
        res = self.run("shell", "find", root, "-type", "f", check=False, timeout=60)
        # Certain ROMs block shell access to obb; never report that as "no OBB".
        if res.returncode != 0 or "Permission denied" in (res.stderr + res.stdout):
            error = (res.stderr or res.stdout).strip()
            if "No such file" in error or "No such file or directory" in error:
                return [], "directory_absent"
            return [], f"unavailable: {error[:400] or 'find failed'}"
        paths = []
        for line in res.stdout.splitlines():
            path = line.strip()
            if path.startswith(root + "/") and path.lower().endswith(".obb"):
                paths.append(path)
        return sorted(set(paths)), "found" if paths else "none_found"

    def device_hash(self, remote: str) -> str | None:
        # Remote paths can contain spaces; use a shell-quoted single argument.
        import shlex
        res = self.run("shell", f"sha256sum {shlex.quote(remote)}", check=False, timeout=3600)
        if res.returncode:
            return None
        result = res.stdout.split(None, 1)[0] if res.stdout.strip() else ""
        return result.lower() if HEX_RE.fullmatch(result) else None


def export(adb: Adb, destination: Path, package: str = DEFAULT_PACKAGE,
           verify_device: bool = False, on_log: Callable[[str], None] = print) -> Path:
    package = safe_package(package)
    if not adb.serial:
        devices = [d for d in adb.devices() if d[1] == "device"]
        if len(devices) != 1:
            raise BackupError("Specify an authorized device with --device (or select one in the GUI).")
        adb.serial = devices[0][0]
    dest_root = destination.expanduser().resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    serial = re.sub(r"[^\w.-]", "_", adb.serial)[:50]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = _unique_folder(dest_root / f"FFD_Backup_{stamp}_{serial}")
    out.mkdir()
    manifest_file = out / "manifest.json"
    manifest = {
        "schema": 1, "tool": "FFD Backup Toolkit", "status": "in_progress",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "package": package, "device_serial": adb.serial, "version": {},
        "obb_status": "not_checked", "files": [], "warnings": [],
        "notes": "Files copied read-only; no app data, licenses, or save files exported."
    }

    def write_manifest() -> None:
        scratch = manifest_file.with_suffix(".json.partial")
        scratch.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        scratch.replace(manifest_file)

    def copy_record(remote: str, relative: Path, kind: str) -> None:
        adb.check_cancelled()
        target = out / relative
        on_log(f"Copying {kind}: {remote}")
        adb.pull(remote, target)
        record = {"type": kind, "path": relative.as_posix(), "source": remote,
                  "bytes": target.stat().st_size, "sha256": hash_file(target)}
        if verify_device:
            original_hash = adb.device_hash(remote)
            record["device_sha256"] = original_hash
            if original_hash and original_hash != record["sha256"]:
                target.rename(target.with_name(target.name + ".HASH_MISMATCH"))
                raise BackupError(f"SHA-256 mismatch for {remote}")
            if original_hash is None:
                manifest["warnings"].append(f"Device-side SHA-256 unavailable for {remote}")
        manifest["files"].append(record)
        write_manifest()
        on_log(f"Verified locally: {relative.as_posix()} ({record['bytes']:,} bytes)")

    try:
        on_log(f"Connected to {adb.serial}; checking {package} ...")
        paths = adb.apk_paths(package)
        manifest["version"] = adb.package_version(package)
        write_manifest()
        seen = set()
        for path in paths:
            name = PurePosixPath(path).name
            if name in seen:
                raise BackupError(f"Two installed APK files have the same name: {name}")
            seen.add(name)
            copy_record(path, Path("apks") / name, "apk")
        obbs, status = adb.obb_paths(package)
        manifest["obb_status"] = status
        write_manifest()
        if status.startswith("unavailable:"):
            manifest["warnings"].append("OBB inspection blocked by Android/ROM: " + status)
            on_log("WARNING: OBB directory not readable; see manifest.")
        elif not obbs:
            on_log(f"OBB: {status}; there may be no external expansion files for this installation.")
        for remote in obbs:
            # Keep nested directory names while preventing path traversal.
            rel = PurePosixPath(remote).relative_to(PurePosixPath(f"/sdcard/Android/obb/{package}"))
            if ".." in rel.parts or not rel.parts:
                raise BackupError(f"Unsafe OBB path: {remote}")
            copy_record(remote, Path("obb") / Path(*rel.parts), "obb")
        manifest["status"] = "completed_with_warnings" if manifest["warnings"] else "completed"
        on_log(f"Backup {manifest['status']}: {len(manifest['files'])} files, at {out}")
    except BaseException as e:
        manifest["status"] = "cancelled" if isinstance(e, Cancelled) else "failed"
        manifest["warnings"].append(str(e))
        on_log(f"Backup {manifest['status']}: {e}")
        raise
    finally:
        write_manifest()
    return out


def gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from queue import Empty, Queue

    app = tk.Tk()
    app.title("FFD Backup Toolkit · rootless APK + OBB exporter")
    app.geometry("760x580")
    app.minsize(630, 460)
    main = ttk.Frame(app, padding=18)
    main.pack(fill="both", expand=True)
    main.columnconfigure(1, weight=1)
    ttk.Label(main, text="Final Fantasy Dimensions · Original File Backup",
              font=("Segoe UI", 15, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 13))
    adb_var = tk.StringVar(value=shutil.which("adb") or "")
    pkg_var = tk.StringVar(value=DEFAULT_PACKAGE)
    out_var = tk.StringVar(value=str(Path.home() / "Documents"))
    device_var = tk.StringVar()
    verify_var = tk.BooleanVar(value=False)
    active = threading.Event()
    stop = threading.Event()
    events: Queue[tuple[str, object]] = Queue()
    known_devices: list[str] = []

    def row_input(i: int, label: str, var: tk.StringVar, browse=None) -> None:
        ttk.Label(main, text=label).grid(row=i, column=0, sticky="w", pady=5, padx=(0, 12))
        ttk.Entry(main, textvariable=var).grid(row=i, column=1, sticky="ew", pady=5)
        if browse:
            ttk.Button(main, text="Browse…", command=browse).grid(row=i, column=2, padx=(7, 0))

    row_input(1, "ADB executable", adb_var, lambda: adb_var.set(
        filedialog.askopenfilename(title="Select adb executable") or adb_var.get()))
    row_input(2, "Android package", pkg_var)
    row_input(3, "Backup destination", out_var, lambda: out_var.set(
        filedialog.askdirectory(title="Choose backup parent folder") or out_var.get()))
    ttk.Label(main, text="Authorized device").grid(row=4, column=0, sticky="w", pady=8)
    combo = ttk.Combobox(main, textvariable=device_var, state="readonly")
    combo.grid(row=4, column=1, sticky="ew")

    def refresh() -> None:
        if active.is_set():
            return
        try:
            adb = Adb(find_adb(adb_var.get() or None))
            states = adb.devices()
            known_devices[:] = [serial for serial, state, _ in states if state == "device"]
            combo["values"] = known_devices
            if known_devices:
                device_var.set(known_devices[0])
            else:
                device_var.set("")
            details = ", ".join(f"{serial} ({state})" for serial, state, _ in states)
            log("Devices: " + (details or "none; enable USB debugging and approve your computer"))
        except Exception as e:
            messagebox.showerror("ADB error", str(e))

    ttk.Button(main, text="Refresh devices", command=refresh).grid(row=4, column=2, padx=(7, 0))
    ttk.Checkbutton(main, text="Also verify SHA-256 on device (slower, when supported)",
                    variable=verify_var).grid(row=5, column=0, columnspan=3, sticky="w", pady=(7, 7))
    button_bar = ttk.Frame(main)
    button_bar.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(3, 10))
    button_bar.columnconfigure(2, weight=1)
    button_export = ttk.Button(button_bar, text="Export APKs + OBBs")
    button_export.grid(row=0, column=0, padx=(0, 8))
    button_cancel = ttk.Button(button_bar, text="Cancel", state="disabled")
    button_cancel.grid(row=0, column=1)
    status_var = tk.StringVar(value="Connect phone via USB and enable USB debugging.")
    ttk.Label(main, textvariable=status_var, wraplength=685).grid(
        row=7, column=0, columnspan=3, sticky="w", pady=(0, 8))
    pane = ttk.Frame(main)
    pane.grid(row=8, column=0, columnspan=3, sticky="nsew")
    main.rowconfigure(8, weight=1)
    pane.rowconfigure(0, weight=1)
    pane.columnconfigure(0, weight=1)
    text_log = tk.Text(pane, wrap="word", state="disabled", font=("Consolas", 10))
    text_log.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(pane, command=text_log.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    text_log.configure(yscrollcommand=scrollbar.set)

    def log(msg: str) -> None:
        text_log.configure(state="normal")
        text_log.insert("end", msg + "\n")
        text_log.see("end")
        text_log.configure(state="disabled")

    def start() -> None:
        if active.is_set():
            return
        try:
            binary = find_adb(adb_var.get() or None)
            package = safe_package(pkg_var.get().strip())
            serial = device_var.get().strip()
            if not serial or serial not in known_devices:
                raise BackupError("Refresh devices and select an authorized Android device.")
            destination = Path(out_var.get())
            if not str(destination).strip():
                raise BackupError("Select a backup destination.")
        except Exception as e:
            messagebox.showerror("Cannot start export", str(e))
            return
        active.set()
        stop.clear()
        button_export.configure(state="disabled")
        button_cancel.configure(state="normal")
        status_var.set("Export running. Original game files will not be changed.")

        def task() -> None:
            try:
                path = export(Adb(binary, serial, stop), destination, package, verify_var.get(),
                              lambda s: events.put(("log", s)))
                events.put(("done", str(path)))
            except Exception as e:
                events.put(("error", str(e)))
            finally:
                events.put(("finished", ""))

        threading.Thread(target=task, daemon=True).start()

    def cancel() -> None:
        stop.set()
        status_var.set("Cancelling after the current operation…")

    button_export.configure(command=start)
    button_cancel.configure(command=cancel)

    def poll() -> None:
        try:
            while True:
                name, message = events.get_nowait()
                if name == "log":
                    log(str(message))
                elif name == "done":
                    status_var.set("Completed: " + str(message))
                    messagebox.showinfo("Backup complete", f"Files and manifest saved to:\n{message}")
                elif name == "error":
                    status_var.set("Incomplete backup: " + str(message))
                    messagebox.showwarning("Backup incomplete", str(message))
                elif name == "finished":
                    active.clear()
                    button_export.configure(state="normal")
                    button_cancel.configure(state="disabled")
        except Empty:
            pass
        app.after(100, poll)

    app.after(100, poll)
    app.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only rootless FFD Android APK + OBB backup")
    parser.add_argument("--cli", action="store_true", help="Use CLI rather than GUI")
    parser.add_argument("--list-devices", action="store_true", help="List ADB devices and exit")
    parser.add_argument("--adb", help="Path to adb or adb.exe")
    parser.add_argument("--device", help="ADB serial (required when more than one device is connected)")
    parser.add_argument("--package", default=DEFAULT_PACKAGE, help="Android package ID")
    parser.add_argument("--out", type=Path, default=Path.cwd(), help="Parent output directory")
    parser.add_argument("--verify-device", action="store_true", help="Compare local/device SHA-256")
    args = parser.parse_args()
    if not args.cli and not args.list_devices and not args.device and len(sys.argv) == 1:
        gui()
        return 0
    try:
        adb = Adb(find_adb(args.adb), args.device)
        if args.list_devices:
            for serial, status, details in adb.devices():
                print(f"{serial}\t{status}\t{details}")
            return 0
        path = export(adb, args.out, args.package, args.verify_device)
        print(f"\nExport directory: {path}")
        return 0
    except BackupError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
