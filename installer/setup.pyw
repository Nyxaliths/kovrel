"""Kovrel setup wizard. Built into Kovrel-Setup.exe by installer/build.py.

Installs for the current user only (no admin needed) into
%LOCALAPPDATA%\\Programs\\Kovrel, adds Start menu / Desktop shortcuts and an
entry in Settings › Apps so it can be uninstalled normally. If the app's old name
(Vexari) is installed, it is removed first; Kovrel brings its settings along on first start.

Test flags:  --silent --dir <folder> --no-shortcuts --no-register --no-launch
             --autostart --shortcut-dir <folder>   (shortcuts go to <folder> instead of the real ones)
"""

import ctypes
import os
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
import winreg
import zipfile
from pathlib import Path
from tkinter import filedialog, ttk

APP, VERSION, PUBLISHER = "Kovrel", "2.0.2", "Kovrel"
OLD_APP = "Vexari"  # the name before 2.0
RES = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
PAYLOAD, LOGO, ICON = RES / "payload.zip", RES / "logo.png", RES / "Kovrel.ico"
DEFAULT_DIR = Path(os.environ["LOCALAPPDATA"]) / "Programs" / APP
OLD_DIR = Path(os.environ["LOCALAPPDATA"]) / "Programs" / OLD_APP
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP}"
OLD_UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{OLD_APP}"
NO_WINDOW = 0x08000000
PRIVACY_URL = "https://github.com/Nyxaliths/kovrel/blob/main/PRIVACY.md"

BG, CARD, FG, MUTED, BORDER = "#0E1013", "#171A1F", "#ECEEF1", "#8F96A1", "#2A2F37"
PRIMARY, PRIMARY_HOVER, ON_PRIMARY = "#2FD6A3", "#6EE7C5", "#04291D"
FONT = "Segoe UI"


def make_shortcuts(target_dir, desktop, autostart, folders=None):
    """folders: optional {"Programs"|"Desktop"|"Startup": Path} override, used by tests."""
    # Talks to Windows' shortcut maker directly (fast) instead of starting PowerShell (slow).
    import comtypes
    import comtypes.client
    # The installer calls this on a worker thread, which needs COM set up. (No CoUninitialize:
    # the COM objects are released later by Python, and the thread ends right after anyway.)
    comtypes.CoInitialize()
    sh = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
    exe = target_dir / f"{APP}.exe"
    # SpecialFolders also handles Desktops that OneDrive has moved.
    folder = lambda name: Path(folders[name]) if folders else Path(sh.SpecialFolders(name))

    def link(path, args=""):
        s = sh.CreateShortcut(str(path))
        s.TargetPath, s.Arguments, s.WorkingDirectory = str(exe), args, str(target_dir)
        s.IconLocation, s.Description = str(exe), APP
        s.Save()

    for where in ("Programs", "Desktop", "Startup"):          # the old name's shortcuts
        (folder(where) / f"{OLD_APP}.lnk").unlink(missing_ok=True)
    link(folder("Programs") / f"{APP}.lnk")
    desk, startup = folder("Desktop") / f"{APP}.lnk", folder("Startup") / f"{APP}.lnk"
    link(desk) if desktop else desk.unlink(missing_ok=True)
    link(startup, "--tray") if autostart else startup.unlink(missing_ok=True)


def remove_old_install():
    """Removes an installed 'Vexari' (the app's old name): its program files and Windows entry.
    Settings in %APPDATA%\\Vexari and files in Downloads\\Vexari are left alone; Kovrel moves them."""
    subprocess.run(["taskkill", "/F", "/IM", f"{OLD_APP}.exe"], creationflags=NO_WINDOW, capture_output=True)
    exe = OLD_DIR / f"{OLD_APP}.exe"
    if exe.exists():                       # only if it really was an installed copy, never a source folder
        time.sleep(0.6)                    # let Windows release the files after the app was closed
        shutil.rmtree(OLD_DIR / "_internal", ignore_errors=True)
        for name in (f"{OLD_APP}.exe", "uninstall.ps1"):
            try:
                (OLD_DIR / name).unlink()
            except OSError:
                pass
        try:
            OLD_DIR.rmdir()                # only succeeds if nothing else is in there
        except OSError:
            pass
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, OLD_UNINSTALL_KEY)
    except OSError:
        pass


def register_uninstaller(target_dir, size_kb):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        values = {
            "DisplayName": APP, "DisplayVersion": VERSION, "Publisher": PUBLISHER,
            "DisplayIcon": str(target_dir / f"{APP}.exe"), "InstallLocation": str(target_dir),
            "UninstallString": f'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden '
                               f'-File "{target_dir / "uninstall.ps1"}"',
        }
        for name, val in values.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
        for name, val in {"NoModify": 1, "NoRepair": 1, "EstimatedSize": size_kb}.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, val)


def install(target_dir, desktop=True, autostart=False, shortcuts=True, register=True, progress=lambda p, m: None,
            shortcut_folders=None):
    target_dir = Path(target_dir)
    progress(5, "Closing Kovrel if it's running…")
    subprocess.run(["taskkill", "/F", "/IM", f"{APP}.exe"], creationflags=NO_WINDOW, capture_output=True)
    if register:                      # (tests that skip registering also skip touching a real old install)
        remove_old_install()

    progress(10, "Copying files…")
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(target_dir / "_internal", ignore_errors=True)  # old version's libraries
    with zipfile.ZipFile(PAYLOAD) as z:
        members = z.infolist()
        total = sum(m.file_size for m in members) or 1
        done = 0
        for m in members:
            z.extract(m, target_dir)
            done += m.file_size
            progress(10 + int(80 * done / total), "Copying files…")

    if shortcuts:
        progress(92, "Creating shortcuts…")
        make_shortcuts(target_dir, desktop, autostart, shortcut_folders)
    if register:
        progress(96, "Registering with Windows…")
        register_uninstaller(target_dir, total // 1024)
    progress(100, "Done")


# ------------------------------------------------------------------ wizard UI

class Wizard:
    def __init__(self):
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Kovrel.Setup")
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        self.root = tk.Tk()
        self.root.title("Kovrel Setup")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.iconbitmap(default=str(ICON))
        self.dir = tk.StringVar(value=str(DEFAULT_DIR))
        self.desktop = tk.BooleanVar(value=True)
        self.autostart = tk.BooleanVar(value=False)
        self.launch = tk.BooleanVar(value=True)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("R.Horizontal.TProgressbar", troughcolor=CARD, background=PRIMARY,
                        bordercolor=BORDER, lightcolor=PRIMARY, darkcolor=PRIMARY)

        from PIL import Image, ImageTk
        self.logo = ImageTk.PhotoImage(Image.open(LOGO).resize((64, 64), Image.LANCZOS))
        self.body = tk.Frame(self.root, bg=BG, padx=28, pady=24)
        self.body.pack(fill="both", expand=True)
        self.page_welcome()
        self.root.update_idletasks()
        w, h = 480, 470
        self.root.geometry(f"{w}x{h}+{(self.root.winfo_screenwidth() - w) // 2}+{(self.root.winfo_screenheight() - h) // 3}")

    # helpers
    def clear(self):
        for c in self.body.winfo_children():
            c.destroy()

    def header(self, title, sub):
        tk.Label(self.body, image=self.logo, bg=BG).pack(anchor="w")
        tk.Label(self.body, text=title, bg=BG, fg=FG, font=(FONT, 18, "bold")).pack(anchor="w", pady=(12, 2))
        tk.Label(self.body, text=sub, bg=BG, fg=MUTED, font=(FONT, 10), justify="left",
                 wraplength=420).pack(anchor="w")

    def button(self, parent, text, cmd, primary=False):
        bg, hover = (PRIMARY, PRIMARY_HOVER) if primary else (CARD, BORDER)
        b = tk.Label(parent, text=text, bg=bg, fg=ON_PRIMARY if primary else FG, font=(FONT, 10, "bold"),
                     padx=22, pady=10, cursor="hand2")
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.configure(bg=hover))
        b.bind("<Leave>", lambda e: b.configure(bg=bg))
        return b

    def check(self, text, var):
        tk.Checkbutton(self.body, text=text, variable=var, bg=BG, fg=FG, selectcolor=CARD,
                       activebackground=BG, activeforeground=FG, font=(FONT, 10), bd=0,
                       highlightthickness=0, cursor="hand2").pack(anchor="w", pady=2)

    # pages
    def page_welcome(self):
        self.clear()
        self.header("Install Kovrel", "Control this PC from your phone: screen, mouse, keyboard, "
                                      "games, media, files and more. Only for this Windows user, no admin needed.")
        tk.Label(self.body, text="Install to", bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w", pady=(18, 2))
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x")
        tk.Entry(row, textvariable=self.dir, bg=CARD, fg=FG, relief="flat", font=(FONT, 9),
                 insertbackground=FG, state="readonly", readonlybackground=CARD).pack(side="left", fill="x",
                                                                                     expand=True, ipady=7)
        self.button(row, "Change…", self.pick_dir).pack(side="left", padx=(8, 0))
        tk.Frame(self.body, bg=BG, height=10).pack()
        self.check("Put a shortcut on the Desktop", self.desktop)
        self.check("Start Kovrel when Windows starts", self.autostart)
        bottom = tk.Frame(self.body, bg=BG)
        bottom.pack(side="bottom", fill="x")
        self.button(bottom, "Install", self.start_install, primary=True).pack(side="right")
        self.button(bottom, "Cancel", self.root.destroy).pack(side="right", padx=8)
        # Shown before installing (the SignPath code signing terms ask for this).
        privacy = tk.Label(self.body, text="Privacy: no accounts, no tracking. Once a day Kovrel asks GitHub "
                                           "whether a new version is out; turn that off in Settings › Access. "
                                           "Read the privacy policy", bg=BG, fg=MUTED, font=(FONT, 8),
                           justify="left", wraplength=420, cursor="hand2")
        privacy.pack(side="bottom", anchor="w", pady=(0, 12))
        privacy.bind("<Button-1>", lambda e: webbrowser.open(PRIVACY_URL))
        privacy.bind("<Enter>", lambda e: privacy.configure(fg=PRIMARY))
        privacy.bind("<Leave>", lambda e: privacy.configure(fg=MUTED))

    def pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.dir.get(), title="Choose where to install Kovrel")
        if d:
            p = Path(d)
            self.dir.set(str(p if p.name.lower() == APP.lower() else p / APP))

    def start_install(self):
        self.clear()
        self.header("Installing…", "This only takes a moment.")
        self.bar = ttk.Progressbar(self.body, style="R.Horizontal.TProgressbar", maximum=100, length=420)
        self.bar.pack(anchor="w", pady=(26, 8))
        self.status = tk.Label(self.body, text="", bg=BG, fg=MUTED, font=(FONT, 9))
        self.status.pack(anchor="w")

        def progress(p, msg):
            self.root.after(0, lambda: (self.bar.configure(value=p), self.status.configure(text=msg)))

        def work():
            try:
                install(self.dir.get(), self.desktop.get(), self.autostart.get(), progress=progress)
                self.root.after(0, self.page_done)
            except Exception as e:
                self.root.after(0, lambda e=e: self.page_error(e))

        threading.Thread(target=work, daemon=True).start()

    def page_done(self):
        self.clear()
        self.header("Kovrel is installed", "Open it, then scan the QR code with your phone's camera.")
        note = tk.Frame(self.body, bg=CARD, padx=14, pady=12)
        note.pack(fill="x", pady=(18, 10))
        tk.Label(note, text="The first time it starts, Windows asks if Kovrel may use the network.\n"
                            "Click Allow, or your phone won't be able to connect.\n\n"
                            "Each new phone also needs your OK on this PC before it can connect.",
                 bg=CARD, fg=FG, font=(FONT, 9), justify="left").pack(anchor="w")
        self.check("Open Kovrel now", self.launch)
        bottom = tk.Frame(self.body, bg=BG)
        bottom.pack(side="bottom", fill="x")
        self.button(bottom, "Finish", self.finish, primary=True).pack(side="right")

    def page_error(self, err):
        self.clear()
        self.header("Install didn't finish", f"Something went wrong:\n{err}")
        bottom = tk.Frame(self.body, bg=BG)
        bottom.pack(side="bottom", fill="x")
        self.button(bottom, "Close", self.root.destroy).pack(side="right")
        self.button(bottom, "Try again", self.page_welcome, primary=True).pack(side="right", padx=8)

    def finish(self):
        if self.launch.get():
            exe = Path(self.dir.get()) / f"{APP}.exe"
            subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True)
        self.root.destroy()


def main():
    args = sys.argv[1:]
    if "--silent" in args:
        target = args[args.index("--dir") + 1] if "--dir" in args else DEFAULT_DIR
        folders = None
        if "--shortcut-dir" in args:  # tests: put shortcuts in <dir>\Programs, \Desktop, \Startup
            base = Path(args[args.index("--shortcut-dir") + 1])
            folders = {n: base / n for n in ("Programs", "Desktop", "Startup")}
            for p in folders.values():
                p.mkdir(parents=True, exist_ok=True)
        install(target, desktop="--no-shortcuts" not in args, autostart="--autostart" in args,
                shortcuts="--no-shortcuts" not in args, register="--no-register" not in args,
                shortcut_folders=folders)
        if "--no-launch" not in args:
            subprocess.Popen([str(Path(target) / f"{APP}.exe")], cwd=str(target))
        return
    Wizard().root.mainloop()


if __name__ == "__main__":
    main()
