"""Kovrel - the desktop app. Runs the server and shows a small window with a QR code,
the PIN and connected devices. Closing the window keeps Kovrel running in the system tray.

    pythonw Kovrel.pyw          open the window
    pythonw Kovrel.pyw --tray   start hidden in the tray (used for "Start with Windows")
"""

import ctypes
import os
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

HERE = Path(__file__).resolve().parent
APPDATA = Path(os.environ.get("APPDATA", HERE)) / "Kovrel"
APPDATA.mkdir(parents=True, exist_ok=True)

# pythonw has no console: keep errors somewhere we can read them.
if sys.stderr is None or not sys.stderr.isatty():
    sys.stderr = sys.stdout = open(APPDATA / "log.txt", "a", encoding="utf-8", buffering=1)

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---- single instance: a second launch just asks the first one to show its window
SHOW_EVENT = "Local\\Kovrel_show"
kernel32.CreateEventW.restype = ctypes.c_void_p
kernel32.OpenEventW.restype = ctypes.c_void_p
_existing = kernel32.OpenEventW(0x0002, False, SHOW_EVENT)  # EVENT_MODIFY_STATE
if _existing:
    if "--tray" not in sys.argv:
        kernel32.SetEvent(ctypes.c_void_p(_existing))
    sys.exit(0)
show_event = kernel32.CreateEventW(None, False, False, SHOW_EVENT)

ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Kovrel.App")  # own taskbar icon

import pystray  # noqa: E402
import qrcode  # noqa: E402
from PIL import Image, ImageTk  # noqa: E402

import security  # noqa: E402
import server  # noqa: E402

ICON = HERE / "assets" / "Kovrel.ico"
LOGO = HERE / "assets" / "logo.png"
STARTUP_LNK = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/Kovrel.lnk"
FROZEN = getattr(sys, "frozen", False)  # True when running as the installed Kovrel.exe
PYTHONW = Path(sys.executable).with_name("pythonw.exe")

# Colours match the phone app.
BG, CARD, CARD2, FG, MUTED, BORDER = "#0E1013", "#171A1F", "#22262D", "#ECEEF1", "#8F96A1", "#2A2F37"
PRIMARY, PRIMARY_HOVER, ON_PRIMARY = "#2FD6A3", "#6EE7C5", "#04291D"
GREEN, RED, AMBER = "#2FD6A3", "#F26464", "#F5B342"
FONT = "Segoe UI"
SUPPORT_URL = "https://buymeacoffee.com/nyxaliths"     # opened only when the user clicks "Support Kovrel"
TAILSCALE_URL = "https://tailscale.com/download"


def make_shortcut(path, args=""):
    """Creates a Windows .lnk to this app, asking Windows directly (much faster than PowerShell)."""
    import comtypes
    import comtypes.client
    comtypes.CoInitialize()  # COM may only have been set up on the server's worker thread so far
    if FROZEN:
        exe = Path(sys.executable)
        target, arguments, workdir, icon = exe, args, exe.parent, exe
    else:
        target, arguments, workdir, icon = PYTHONW, f'"{HERE / "Kovrel.pyw"}" {args}', HERE, ICON
    s = comtypes.client.CreateObject("WScript.Shell", dynamic=True).CreateShortcut(str(path))
    s.TargetPath, s.Arguments, s.WorkingDirectory = str(target), arguments, str(workdir)
    s.IconLocation, s.Description = str(icon), "Kovrel"
    s.Save()


def ago(t):
    s = max(0, time.time() - t)
    return "just now" if s < 60 else f"{int(s // 60)} min ago" if s < 3600 else f"{int(s // 3600)} h ago" if s < 86400 else f"{int(s // 86400)} d ago"


class App:
    def __init__(self, start_hidden):
        self.httpd, self.error, self.scheme, self.url = None, None, "http", ""
        self.approvals = {}      # request id -> its dialog window
        self.sec_win = None
        self.qr_mode = "home"    # which address the QR code shows: "home" (Wi-Fi) or "away" (Tailscale)
        self.ts = {"installed": False, "running": False, "ip": "", "name": "", "cert_domains": [], "error": "",
                   "checking": True}   # until the first look at Tailscale finishes (don't offer an install yet)
        self.update = None       # (version, page) when a newer Kovrel is out
        self.start_server()

        self.root = tk.Tk()
        self.root.title("Kovrel")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.iconbitmap(default=str(ICON))
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.style()
        self.build()
        self.place_center(self.root, 440, 700)
        if start_hidden:
            self.root.withdraw()

        security.approval_callback = self.on_approval_request   # runs on a server thread
        self.tray = pystray.Icon("Kovrel", Image.open(LOGO), "Kovrel", menu=pystray.Menu(
            pystray.MenuItem("Open Kovrel", lambda: self.root.after(0, self.show), default=True),
            pystray.MenuItem("Pause remote control", lambda: self.root.after(0, self.toggle_pause),
                             checked=lambda item: server.PAUSED["on"]),
            pystray.MenuItem("Quit", lambda: self.root.after(0, self.quit)),
        ))
        self.tray.run_detached()
        threading.Thread(target=self.wait_for_show, daemon=True).start()
        threading.Thread(target=self.poll_tailscale, daemon=True).start()
        threading.Thread(target=self.poll_updates, daemon=True).start()
        self.refresh()

    # ------------------------------------------------------------ server
    def start_server(self):
        self.stop_server()
        try:
            self.httpd = server.make_server(server.PORT)
            threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
            self.error = None
        except OSError:
            self.httpd = None
            self.error = (f"Port {server.PORT} is already in use by another program. "
                          "Quit any other copy of Kovrel (tray icon → Quit) and reopen it.")
        except Exception as e:
            self.httpd = None
            self.error = f"Couldn't start the secure connection: {e}"
        self.scheme = self.httpd.scheme if self.httpd else "http"
        self.url = f"{self.scheme}://{server.lan_ip()}:{server.PORT}"

    def stop_server(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        server.close_all_connections()

    # ------------------------------------------------------------ look
    def style(self):
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=FG, fieldbackground=CARD2, bordercolor=BORDER, font=(FONT, 10))
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", background=CARD, foreground=MUTED, padding=(14, 8), borderwidth=0, font=(FONT, 10, "bold"))
        st.map("TNotebook.Tab", background=[("selected", CARD2)], foreground=[("selected", FG)])
        st.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=FG, rowheight=30, borderwidth=0, font=(FONT, 10))
        st.configure("Treeview.Heading", background=CARD2, foreground=MUTED, font=(FONT, 9, "bold"), borderwidth=0)
        st.map("Treeview", background=[("selected", PRIMARY)], foreground=[("selected", ON_PRIMARY)])
        st.configure("TCombobox", fieldbackground=CARD2, background=CARD2, foreground=FG, arrowcolor=FG, padding=6)
        st.map("TCombobox", fieldbackground=[("readonly", CARD2)], foreground=[("readonly", FG)])
        self.root.option_add("*TCombobox*Listbox.background", CARD2)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)

    def card(self, parent, **pack):
        inner = tk.Frame(parent, bg=CARD, padx=18, pady=14)
        inner.pack(fill="x", **pack)
        return inner

    def button(self, parent, text, cmd, primary=False, danger=False):
        bg, hover = (PRIMARY, PRIMARY_HOVER) if primary else (CARD2, BORDER)
        b = tk.Label(parent, text=text, bg=bg, fg=RED if danger else ON_PRIMARY if primary else FG,
                     font=(FONT, 10, "bold"), padx=16, pady=10, cursor="hand2")
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.configure(bg=hover))
        b.bind("<Leave>", lambda e: b.configure(bg=bg))
        return b

    def check(self, parent, text, var, cmd):
        return tk.Checkbutton(parent, text=text, variable=var, command=cmd, bg=parent["bg"], fg=FG, selectcolor=CARD2,
                              activebackground=parent["bg"], activeforeground=FG, font=(FONT, 10), bd=0,
                              highlightthickness=0, cursor="hand2", anchor="w", justify="left")

    def place_center(self, win, w, h):
        win.update_idletasks()
        w, h = max(w, win.winfo_reqwidth()), max(h, win.winfo_reqheight())
        win.geometry(f"{w}x{h}+{(win.winfo_screenwidth() - w) // 2}+{(win.winfo_screenheight() - h) // 3}")

    # ------------------------------------------------------------ main window
    def build(self):
        pad = tk.Frame(self.root, bg=BG, padx=22, pady=16)
        pad.pack(fill="both", expand=True)

        head = self.head = tk.Frame(pad, bg=BG)
        head.pack(fill="x")
        self.logo_img = ImageTk.PhotoImage(Image.open(LOGO).resize((44, 44), Image.LANCZOS))
        tk.Label(head, image=self.logo_img, bg=BG).pack(side="left")
        tk.Label(head, text="Kovrel", bg=BG, fg=FG, font=(FONT, 18, "bold")).pack(side="left", padx=12)
        self.status = tk.Label(head, bg=BG, font=(FONT, 10))
        self.status.pack(side="right")

        self.paused_bar = tk.Label(pad, text="Remote control is paused. Phones can look, but not control.", bg=AMBER,
                                   fg="#1a1300", font=(FONT, 9, "bold"), pady=6)
        self.update_bar = tk.Frame(pad, bg=CARD2, padx=12, pady=6)
        self.update_lbl = tk.Label(self.update_bar, bg=CARD2, fg=FG, font=(FONT, 9, "bold"))
        self.update_lbl.pack(side="left")
        self.button(self.update_bar, "Download", lambda: webbrowser.open(self.update[1]), primary=True).pack(side="right")

        c = self.card(pad, pady=(14, 0))
        self.mode_row = tk.Frame(c, bg=CARD2, padx=3, pady=3)   # shown only while remote access is on
        self.mode_btns = {}
        for mode, text in (("home", "At home"), ("away", "Away")):
            b = tk.Label(self.mode_row, text=text, font=(FONT, 9, "bold"), padx=18, pady=5, cursor="hand2")
            b.bind("<Button-1>", lambda e, m=mode: self.set_qr_mode(m))
            b.pack(side="left")
            self.mode_btns[mode] = b
        self.scan_lbl = tk.Label(c, text="Scan with your phone's camera", bg=CARD, fg=FG, font=(FONT, 11, "bold"))
        self.scan_lbl.pack()
        self.qr_label = tk.Label(c, bg=CARD)
        self.qr_label.pack(pady=8)
        tk.Label(c, text="or open this address on your phone:", bg=CARD, fg=MUTED, font=(FONT, 9)).pack()
        self.url_entry = tk.Entry(c, justify="center", font=(FONT, 11, "bold"), bg=CARD, fg=FG, relief="flat",
                                  readonlybackground=CARD, insertbackground=FG)
        self.url_entry.pack(fill="x", pady=(2, 0))
        self.secure_lbl = tk.Label(c, bg=CARD, font=(FONT, 8), wraplength=360, justify="center")
        self.secure_lbl.pack(pady=(4, 0))
        # Shown only on "Away" while Tailscale isn't installed on this PC (see draw_qr).
        self.ts_btn = self.button(c, "Install Tailscale", lambda: webbrowser.open(TAILSCALE_URL), primary=True)

        c = self.card(pad, pady=(10, 0))
        row = tk.Frame(c, bg=CARD)
        row.pack(fill="x")
        left = tk.Frame(row, bg=CARD)
        left.pack(side="left")
        self.secret_title = tk.Label(left, text="PIN", bg=CARD, fg=MUTED, font=(FONT, 9))
        self.secret_title.pack(anchor="w")
        self.pin = tk.Label(left, bg=CARD, fg=FG, font=("Consolas", 24, "bold"))
        self.pin.pack(anchor="w")
        self.pin_btn = self.button(row, "New PIN", self.new_pin)
        self.pin_btn.pack(side="right")

        c = self.card(pad, pady=(10, 0))
        tk.Label(c, text="Connected", bg=CARD, fg=MUTED, font=(FONT, 9)).pack(anchor="w")
        self.devices = tk.Label(c, bg=CARD, fg=FG, font=(FONT, 10), justify="left", anchor="w")
        self.devices.pack(fill="x")

        row = tk.Frame(pad, bg=BG)
        row.pack(fill="x", pady=(14, 0))
        self.button(row, "Open on this PC", lambda: webbrowser.open(f"{self.scheme}://localhost:{server.PORT}"), primary=True).pack(side="left")
        self.button(row, "Settings…", lambda: self.open_security()).pack(side="left", padx=8)
        self.pause_btn = self.button(row, "Pause", self.toggle_pause)
        self.pause_btn.pack(side="right")

        self.autostart = tk.BooleanVar(value=STARTUP_LNK.exists())
        self.check(pad, "Start Kovrel with Windows", self.autostart, self.toggle_autostart).pack(anchor="w", pady=(12, 0))
        tk.Label(pad, text="Closing this window keeps Kovrel running in the tray (bottom-right).",
                 bg=BG, fg=MUTED, font=(FONT, 8)).pack(anchor="w", pady=(4, 0))
        # A small, quiet way to say thanks. Nothing is contacted unless this is clicked.
        support = tk.Label(pad, text="Support Kovrel · buy me a coffee", bg=BG, fg=MUTED, font=(FONT, 9), cursor="hand2")
        support.pack(anchor="w", pady=(10, 0))
        support.bind("<Button-1>", lambda e: webbrowser.open(SUPPORT_URL))
        support.bind("<Enter>", lambda e: support.configure(fg=PRIMARY))
        support.bind("<Leave>", lambda e: support.configure(fg=MUTED))
        self.support_lbl = support

    @staticmethod
    def qr_image(url, size=180):
        if not url:   # nothing to scan: a plain grey square keeps the layout still
            return ImageTk.PhotoImage(Image.new("RGB", (size, size), CARD2))
        qr = qrcode.QRCode(border=2, box_size=6)
        qr.add_data(url)
        return ImageTk.PhotoImage(qr.make_image(fill_color="black", back_color="white").get_image()
                                  .convert("RGB").resize((size, size), Image.NEAREST))

    def shown_url(self):
        if self.qr_mode == "away" and security.get("remote"):
            return server.remote_url(self.ts)
        return self.url

    def set_qr_mode(self, mode):
        self.qr_mode = mode
        self.refresh_now()

    def draw_qr(self):
        url, away = self.shown_url(), self.qr_mode == "away" and security.get("remote")
        self.qr_img = self.qr_image(url)
        self.qr_label.configure(image=self.qr_img)
        self.url_entry.configure(state="normal")
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, url.split("://", 1)[1] if url else "")
        self.url_entry.configure(state="readonly")
        for mode, b in self.mode_btns.items():
            on = mode == self.qr_mode
            b.configure(bg=CARD if on else CARD2, fg=FG if on else MUTED)
        checking = self.ts.get("checking")
        installer = away and not url and not self.ts["installed"] and not checking
        if installer and not self.ts_btn.winfo_ismapped():
            self.ts_btn.pack(pady=(10, 0))
        elif not installer and self.ts_btn.winfo_ismapped():
            self.ts_btn.pack_forget()
        if away and not url and checking:
            self.secure_lbl.configure(text="Checking Tailscale…", fg=MUTED)
        elif installer:
            self.secure_lbl.configure(text="Away from home, Kovrel needs Tailscale (free). It isn't installed on this PC yet.", fg=AMBER)
        elif away and not url:
            self.secure_lbl.configure(text="Tailscale is installed but not connected. Open it and sign in, then this QR code appears.", fg=AMBER)
        elif away:
            self.secure_lbl.configure(text="Turn on Tailscale on your phone first. A new phone needs your OK on this PC once.", fg=MUTED)
        elif self.scheme == "https":
            self.secure_lbl.configure(text="Encrypted. Phones show a one-time security warning: that's expected.", fg=GREEN)
        else:
            self.secure_lbl.configure(text="Phone and PC on the same Wi-Fi. On shared Wi-Fi, turn on the secure connection in Settings.", fg=MUTED)
        self._drawn_url = url
        self._drawn_state = self.qr_state()
        self.fit_height()

    def qr_state(self):
        """What the QR card depends on besides the address: redraw when it changes (e.g. Tailscale gets installed)."""
        return (self.qr_mode, bool(security.get("remote")), self.ts["installed"], self.ts["running"], bool(self.ts.get("checking")))

    def refresh(self):
        remote_on = bool(security.get("remote"))
        if remote_on and not self.mode_row.winfo_ismapped():
            self.mode_row.pack(before=self.scan_lbl, pady=(0, 10))
        elif not remote_on and self.mode_row.winfo_ismapped():
            self.mode_row.pack_forget()
            self.qr_mode = "home"
        if (self.qr_label.cget("image") == "" or getattr(self, "_drawn_url", None) != self.shown_url()
                or getattr(self, "_drawn_state", None) != self.qr_state()):
            self.draw_qr()
        pin = server.get_pin()
        if pin:
            self.secret_title.configure(text="PIN")
            self.pin.configure(text=f"{pin[:3]} {pin[3:]}", font=("Consolas", 24, "bold"))
            self.pin_btn.configure(text="New PIN")
        else:
            self.secret_title.configure(text="Sign-in")
            self.pin.configure(text="Password set", font=(FONT, 15, "bold"))
            self.pin_btn.configure(text="Use a PIN")
        if self.error:
            self.status.configure(text="● Not running", fg=RED)
            self.devices.configure(text=self.error, fg=RED, wraplength=360)
        else:
            self.status.configure(text="● Running", fg=GREEN)
            devs = server.connected_devices()
            self.devices.configure(fg=FG, text="\n".join(f"•  {d['name']}" for d in devs) or "No devices connected right now")
        if self.update and not self.update_bar.winfo_ismapped():
            self.update_lbl.configure(text=f"Kovrel {self.update[0]} is available")
            self.update_bar.pack(fill="x", pady=(10, 0), after=self.head)
        elif not self.update and self.update_bar.winfo_ismapped():
            self.update_bar.pack_forget()
        if server.PAUSED["on"]:
            self.paused_bar.pack(fill="x", pady=(10, 0), after=self.head)
            self.pause_btn.configure(text="Resume")
        else:
            self.paused_bar.pack_forget()
            self.pause_btn.configure(text="Pause")
        self.fit_height()
        self.root.after(1500, self.refresh)

    def fit_height(self):
        """The window is a fixed width but grows or shrinks to show everything (the Away switch, the Tailscale
        install button and the update bar come and go). Skipped while it is hidden in the tray."""
        if self.root.state() != "normal":
            return
        self.root.update_idletasks()
        need = self.root.winfo_reqheight()
        if abs(self.root.winfo_height() - need) > 2:
            self.root.geometry(f"{self.root.winfo_width()}x{need}")

    # ------------------------------------------------------------ actions
    def new_pin(self):
        if security.secret_kind() == "password" and not messagebox.askyesno(
                "Use a PIN", "Replace your password with a new 6-digit PIN?", parent=self.root):
            return
        server.reset_pin()

    def toggle_pause(self):
        server.set_paused(not server.PAUSED["on"])

    def open_files(self):
        server.SHARED_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(server.SHARED_DIR)

    def toggle_autostart(self):
        try:
            if self.autostart.get():
                make_shortcut(STARTUP_LNK, "--tray")
            else:
                STARTUP_LNK.unlink(missing_ok=True)
        except Exception as e:
            self.autostart.set(STARTUP_LNK.exists())
            print("autostart:", e)

    # ------------------------------------------------------------ approving new devices
    def on_approval_request(self, req):
        self.root.after(0, lambda: self.show_approval(req))
        try:
            self.tray.notify(f"{req['name']} wants to control this PC. Allow or deny it in the Kovrel window.", "New device")
        except Exception:
            pass

    def show_approval(self, req):
        if req["id"] in self.approvals:
            return
        top = tk.Toplevel(self.root)
        self.approvals[req["id"]] = top
        top.title("Allow this device?")
        top.configure(bg=BG, padx=26, pady=22)
        top.resizable(False, False)
        top.attributes("-topmost", True)
        tk.Label(top, text="A new device wants to control this PC", bg=BG, fg=MUTED, font=(FONT, 10)).pack(anchor="w")
        tk.Label(top, text=req["name"], bg=BG, fg=FG, font=(FONT, 20, "bold")).pack(anchor="w", pady=(2, 0))
        tk.Label(top, text=f"Network address {req['ip']}", bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w")
        tk.Label(top, text="Only click Allow if it's your own phone and you just tried to connect.\n"
                           "If you don't recognise it, click Deny and change your PIN.",
                 bg=BG, fg=FG, font=(FONT, 10), justify="left").pack(anchor="w", pady=(14, 14))
        left = tk.Label(top, bg=BG, fg=MUTED, font=(FONT, 9))
        left.pack(anchor="w")
        row = tk.Frame(top, bg=BG)
        row.pack(fill="x", pady=(10, 0))

        def close():
            self.approvals.pop(req["id"], None)
            top.destroy()

        def allow():
            security.approve(req["id"])
            close()

        def deny():
            security.deny(req["id"])
            close()

        self.button(row, "Allow", allow, primary=True).pack(side="right")
        self.button(row, "Deny", deny).pack(side="right", padx=8)
        top.protocol("WM_DELETE_WINDOW", deny)
        started = time.time()

        def tick():
            if req["id"] not in self.approvals:
                return
            remaining = int(security.APPROVAL_SECONDS - (time.time() - started))
            if remaining <= 0 or not any(r["id"] == req["id"] for r in security.pending_requests()):
                return close()
            left.configure(text=f"The phone is waiting… {remaining}s")
            top.after(500, tick)

        tick()
        self.place_center(top, 440, 300)
        top.lift()
        top.focus_force()

    # ------------------------------------------------------------ security & sharing window
    def open_security(self, tab=0):
        if self.sec_win and self.sec_win.winfo_exists():
            self.sec_win.lift()
            self.sec_win.focus_force()
            return
        win = self.sec_win = tk.Toplevel(self.root)
        win.title("Kovrel settings")
        win.configure(bg=BG, padx=16, pady=14)
        win.iconbitmap(default=str(ICON))
        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True)
        tabs = {}
        for name in ("Devices", "Access", "Remote", "Sharing", "Activity"):
            f = tk.Frame(nb, bg=BG, padx=14, pady=14)
            nb.add(f, text=name)
            tabs[name] = f
        self.build_devices_tab(win, tabs["Devices"])
        self.build_access_tab(win, tabs["Access"])
        self.build_remote_tab(win, tabs["Remote"])
        self.build_sharing_tab(win, tabs["Sharing"])
        self.build_activity_tab(win, tabs["Activity"])
        nb.select(tab)
        self.place_center(win, 660, 600)

    # --- Devices
    def build_devices_tab(self, win, f):
        tk.Label(f, text="Phones that have been allowed to control this PC.", bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w")
        cols = ("name", "seen", "where")
        tree = ttk.Treeview(f, columns=cols, show="headings", height=8, selectmode="browse")
        for c, w, t in (("name", 200, "Device"), ("seen", 120, "Last used"), ("where", 140, "Address")):
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor="w")
        tree.pack(fill="both", expand=True, pady=(8, 8))

        def fill():
            sel = tree.selection()
            tree.delete(*tree.get_children())
            for d in sorted(security.devices(), key=lambda d: -d["last_seen"]):
                label = d["name"] + ("  (automation key)" if d["scope"] == "automation" else "")
                tree.insert("", "end", iid=d["id"], values=(label, "online now" if d["online"] else ago(d["last_seen"]), d["ip"] or "–"))
            if sel and tree.exists(sel[0]):
                tree.selection_set(sel[0])

        def reload():
            if not win.winfo_exists():
                return
            fill()
            win.after(3000, reload)

        def remove():
            sel = tree.selection()
            if sel and messagebox.askyesno("Remove device", "This device will have to be approved again before it can connect.", parent=win):
                security.revoke(sel[0])
                fill()

        def sign_out_all():
            if messagebox.askyesno("Sign out everyone", "Every phone will be signed out and must be approved again.", parent=win):
                security.revoke_all()
                fill()

        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.button(row, "Remove selected", remove).pack(side="left")
        self.button(row, "Sign out everyone", sign_out_all, danger=True).pack(side="left", padx=8)

        auto = tk.Frame(f, bg=BG)
        auto.pack(fill="x", pady=(18, 0))
        tk.Label(auto, text="Automation key", bg=BG, fg=FG, font=(FONT, 10, "bold")).pack(anchor="w")
        tk.Label(auto, text="A limited key (it can only run your My buttons) for iPhone Shortcuts, Tasker and similar.",
                 bg=BG, fg=MUTED, font=(FONT, 9), wraplength=540, justify="left").pack(anchor="w")
        self.button(auto, "Create an automation key…", lambda: self.automation_key(win)).pack(anchor="w", pady=(8, 0))
        reload()

    def automation_key(self, parent):
        _, key = security.create_automation_key("Automation")
        dlg = tk.Toplevel(parent)
        dlg.title("Automation key")
        dlg.configure(bg=BG, padx=22, pady=18)
        dlg.attributes("-topmost", True)
        tk.Label(dlg, text="Copy this key now. It won't be shown again.", bg=BG, fg=FG, font=(FONT, 10, "bold")).pack(anchor="w")
        e = tk.Entry(dlg, font=("Consolas", 10), bg=CARD2, fg=FG, relief="flat", width=56, readonlybackground=CARD2)
        e.insert(0, key)
        e.configure(state="readonly")
        e.pack(fill="x", pady=8, ipady=6)
        howto = (f"In Shortcuts: Get Contents of URL\n   URL:     {self.url}/api/shortcut_run\n   Method:  POST\n"
                 "   Headers: X-Token = the key above, Content-Type = application/json\n"
                 '   Body:    {"name": "the name of one of your My buttons"}')
        tk.Label(dlg, text=howto, bg=BG, fg=MUTED, font=("Consolas", 9), justify="left").pack(anchor="w", pady=(0, 10))

        def copy():
            dlg.clipboard_clear()
            dlg.clipboard_append(key)

        row = tk.Frame(dlg, bg=BG)
        row.pack(fill="x")
        self.button(row, "Done", dlg.destroy, primary=True).pack(side="right")
        self.button(row, "Copy key", copy).pack(side="right", padx=8)
        self.place_center(dlg, 560, 260)

    # --- Access
    def build_access_tab(self, win, f):
        def section(title, text):
            tk.Label(f, text=title, bg=BG, fg=FG, font=(FONT, 10, "bold")).pack(anchor="w", pady=(10, 0))
            if text:
                tk.Label(f, text=text, bg=BG, fg=MUTED, font=(FONT, 9), wraplength=540, justify="left").pack(anchor="w")

        section("Secure connection (HTTPS)", "Encrypts everything between your phone and this PC. Recommended on shared or public Wi-Fi. "
                "Phones show a one-time warning because the certificate is made by this PC, not a public authority, and they have to sign in again.")
        https = tk.BooleanVar(value=bool(security.get("https")))

        def toggle_https():
            on = https.get()
            if not messagebox.askokcancel("Secure connection", ("Turn on" if on else "Turn off") +
                                          " the secure connection?\n\nPhones will be disconnected and must open the new address "
                                          "(scan the QR code again) and be approved again.", parent=win):
                https.set(not on)
                return
            security.put("https", on)
            security.audit("secure_connection", on=on)
            self.start_server()
            self.refresh_now()

        self.check(f, "Use a secure connection (HTTPS)", https, toggle_https).pack(anchor="w", pady=(4, 0))

        section("Sign-in", "Phones sign in with this, then you approve each new phone here on the PC.")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=(6, 0))
        self.button(row, "New PIN", lambda: (self.new_pin(), self.refresh_now())).pack(side="left")
        self.button(row, "Set a password…", lambda: self.set_password(win)).pack(side="left", padx=8)

        section("Stay signed in", "A phone that hasn't connected for this long has to be approved again.")
        days = tk.StringVar(value=f"{security.get('session_days', 30)} days")
        box = ttk.Combobox(f, textvariable=days, state="readonly", values=[f"{d} day" + ("s" if d > 1 else "") for d in security.SESSION_CHOICES], width=12)
        box.pack(anchor="w", pady=(6, 0))
        box.bind("<<ComboboxSelected>>", lambda e: security.set_session_days(int(days.get().split()[0])))

        section("Updates", "")
        upd = tk.BooleanVar(value=bool(security.get("update_check", True)))

        def toggle_updates():
            security.put("update_check", upd.get())
            if not upd.get():
                self.update = None
        self.check(f, "Tell me when a new version is out (asks GitHub once a day; nothing about you is sent)",
                   upd, toggle_updates).pack(anchor="w", pady=(4, 0))

        section("Pause remote control", "Stops every phone from controlling this PC at once. They can still look. Also in the tray menu.")
        paused = tk.BooleanVar(value=server.PAUSED["on"])
        self.check(f, "Remote control is paused", paused, lambda: (server.set_paused(paused.get()), self.refresh_now())).pack(anchor="w", pady=(4, 0))

    def set_password(self, parent, min_len=None, then=None):
        """then: called after the password is saved (e.g. to turn remote access on)."""
        min_len = min_len or (security.REMOTE_MIN_PASSWORD if security.get("remote") else 8)
        dlg = tk.Toplevel(parent)
        dlg.title("Set a password")
        dlg.configure(bg=BG, padx=22, pady=18)
        dlg.attributes("-topmost", True)
        tk.Label(dlg, text=f"Choose a password (at least {min_len} characters)", bg=BG, fg=FG, font=(FONT, 10, "bold")).pack(anchor="w")
        if min_len > 8:
            tk.Label(dlg, text="Remote access needs a longer password than a PIN.\nA few unrelated words work well.",
                     bg=BG, fg=MUTED, font=(FONT, 9), justify="left").pack(anchor="w", pady=(2, 0))
        entries = []
        for label in ("New password", "Type it again"):
            tk.Label(dlg, text=label, bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w", pady=(10, 0))
            e = tk.Entry(dlg, show="•", font=(FONT, 11), bg=CARD2, fg=FG, insertbackground=FG, relief="flat", width=34)
            e.pack(fill="x", ipady=6)
            entries.append(e)
        err = tk.Label(dlg, bg=BG, fg=RED, font=(FONT, 9))
        err.pack(anchor="w", pady=(8, 0))

        def save():
            a, b = entries[0].get(), entries[1].get()
            if a != b:
                return err.configure(text="The two passwords don't match.")
            if len(a) < min_len:
                return err.configure(text=f"Use at least {min_len} characters.")
            try:
                security.set_secret(a, "password")
            except ValueError as ex:
                return err.configure(text=str(ex))
            dlg.destroy()
            if then:
                then()
            self.refresh_now()

        row = tk.Frame(dlg, bg=BG)
        row.pack(fill="x", pady=(10, 0))
        self.button(row, "Save", save, primary=True).pack(side="right")
        self.button(row, "Cancel", dlg.destroy).pack(side="right", padx=8)
        entries[0].focus_set()
        self.place_center(dlg, 400, 300)

    # --- Remote
    def build_remote_tab(self, win, f):
        def text(s, fg=MUTED, bold=False, pady=(0, 0)):
            lbl = tk.Label(f, text=s, bg=BG, fg=fg, font=(FONT, 10 if bold else 9, "bold" if bold else "normal"),
                           wraplength=560, justify="left")
            lbl.pack(anchor="w", pady=pady)
            return lbl

        text("Use Kovrel away from home", FG, True)
        text("Through Tailscale: a free private network between your own devices. Nothing is opened to the "
             "internet, and every new phone still needs your OK on this PC.")
        status = text("", FG, pady=(10, 0))
        get_ts = self.button(f, "Get Tailscale…", lambda: webbrowser.open(TAILSCALE_URL))

        on = tk.BooleanVar(value=bool(security.get("remote")))

        def turn_on():
            try:
                security.set_remote(True)
            except ValueError:
                on.set(False)
                return
            on.set(True)
            self.qr_mode = "away"
            update()
            self.refresh_now()

        def toggle():
            if not on.get():
                security.set_remote(False)
                return update()
            if security.strong_enough_for_remote():
                return turn_on()
            on.set(False)
            if messagebox.askokcancel("Remote access", "Remote access needs a password of at least "
                                      f"{security.REMOTE_MIN_PASSWORD} characters instead of the 6-digit PIN.\n\n"
                                      "Set one now? Phones will sign in with it from now on.", parent=win):
                self.set_password(win, security.REMOTE_MIN_PASSWORD, then=turn_on)

        self.check(f, "Allow remote access through Tailscale", on, toggle).pack(anchor="w", pady=(10, 0))

        row = tk.Frame(f, bg=BG)
        row.pack(anchor="w", pady=(6, 0))
        tk.Label(row, text="Sign a phone out when it's away and unused for", bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left")
        idle = tk.StringVar(value=f"{security.get('remote_idle_min', 30)} minutes")
        box = ttk.Combobox(row, textvariable=idle, state="readonly", width=12,
                           values=[f"{m} minutes" for m in security.REMOTE_IDLE_CHOICES])
        box.pack(side="left", padx=8)
        box.bind("<<ComboboxSelected>>", lambda e: security.set_remote_idle(int(idle.get().split()[0])))

        addr_row = tk.Frame(f, bg=BG)
        addr_row.pack(fill="x", pady=(12, 0))
        qr = tk.Label(addr_row, bg=BG)
        qr.pack(side="left")
        right = tk.Frame(addr_row, bg=BG)
        right.pack(side="left", fill="x", expand=True, padx=(14, 0))
        tk.Label(right, text="Address on your phone (with Tailscale on):", bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w")
        addr = tk.Entry(right, font=(FONT, 10, "bold"), bg=CARD2, fg=FG, relief="flat", readonlybackground=CARD2)
        addr.pack(fill="x", ipady=6, pady=(2, 6))
        copy = self.button(right, "Copy address", lambda: (win.clipboard_clear(), win.clipboard_append(addr.get())))
        copy.pack(anchor="w")

        text("No warning on the phone (optional)", FG, True, pady=(14, 0))
        text("With the secure connection (Access tab) on, Kovrel can use Tailscale's own trusted certificate. "
             "Turn on HTTPS Certificates in the Tailscale admin console first (login.tailscale.com › DNS).")
        cert_row = tk.Frame(f, bg=BG)
        cert_row.pack(fill="x", pady=(6, 0))
        cert_lbl = tk.Label(cert_row, bg=BG, fg=MUTED, font=(FONT, 9))

        def get_cert():
            cert_lbl.configure(text="Asking Tailscale…", fg=MUTED)

            def work():
                try:
                    name = server.tailscale_get_cert()
                    msg, ok = f"Certificate ready for {name}.", True
                except Exception as e:
                    msg, ok = str(e), False
                win.after(0, lambda: (cert_lbl.configure(text=msg, fg=GREEN if ok else RED),
                                      ok and (self.start_server(), self.refresh_now()), update()))
            threading.Thread(target=work, daemon=True).start()

        self.button(cert_row, "Get certificate", get_cert).pack(side="left")
        cert_lbl.pack(side="left", padx=10)

        def update():
            if not win.winfo_exists():
                return
            ts = self.ts
            if ts.get("checking"):
                status.configure(text="●  Checking Tailscale…", fg=MUTED)
                get_ts.pack_forget()
            elif not ts["installed"]:
                status.configure(text="●  Tailscale isn't installed on this PC.", fg=AMBER)
                get_ts.pack(anchor="w", pady=(6, 0), after=status)
            elif not ts["running"]:
                status.configure(text="●  Tailscale is installed but not connected. Open it and sign in.", fg=AMBER)
                get_ts.pack_forget()
            else:
                status.configure(text=f"●  Tailscale connected: {ts['name'] or ts['ip']}", fg=GREEN)
                get_ts.pack_forget()
            on.set(bool(security.get("remote")))
            url = server.remote_url(ts) if security.get("remote") else ""
            if getattr(qr, "_url", None) != url:
                qr.img = self.qr_image(url, 120)
                qr.configure(image=qr.img)
                qr._url = url
            addr.configure(state="normal")
            addr.delete(0, "end")
            addr.insert(0, url or ("Turn on remote access above" if ts["running"] else "Connect Tailscale first"))
            addr.configure(state="readonly")
            name, left = server.tailscale_cert_info()
            if name and not cert_lbl.cget("text").startswith("Asking"):
                cert_lbl.configure(text=f"Certificate for {name}, {left} more days (renewed automatically).",
                                   fg=GREEN if security.get("https") else MUTED)

        def loop():
            if win.winfo_exists():
                update()
                win.after(3000, loop)
        text("Setup: 1. Install Tailscale on this PC and your phone, signed in to the same account.  "
             "2. Turn on remote access here.  3. Open the address below on the phone and click Allow on this PC.  "
             "4. Away from home: open Tailscale on the phone, then the address above.", pady=(14, 0))
        loop()

    # --- Sharing
    def build_sharing_tab(self, win, f):
        tk.Label(f, text="Folders your phone can open from More › Files. The Kovrel folder is always shared.",
                 bg=BG, fg=MUTED, font=(FONT, 9), wraplength=540, justify="left").pack(anchor="w")
        lb = tk.Listbox(f, bg=CARD, fg=FG, selectbackground=PRIMARY, selectforeground=ON_PRIMARY, relief="flat",
                        font=(FONT, 10), height=9, activestyle="none", highlightthickness=1, highlightbackground=BORDER)
        lb.pack(fill="both", expand=True, pady=(8, 8))

        def reload():
            lb.delete(0, "end")
            lb.insert("end", f"Kovrel folder   {server.SHARED_DIR}")
            for item in security.get("shared_folders", []):
                lb.insert("end", f"{item['name']}   {item['path']}")

        def add():
            d = filedialog.askdirectory(parent=win, title="Choose a folder to share with your phone")
            if d:
                server.shared_folder_add(d)
                reload()

        def remove():
            sel = lb.curselection()
            if not sel or sel[0] == 0:
                return
            server.shared_folder_remove(security.get("shared_folders", [])[sel[0] - 1]["path"])
            reload()

        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.button(row, "Add a folder…", add, primary=True).pack(side="left")
        self.button(row, "Stop sharing selected", remove).pack(side="left", padx=8)
        self.button(row, "Open Kovrel folder", self.open_files).pack(side="left")
        tk.Label(f, text="Phones can read and add files in shared folders (and their subfolders), nothing else on your PC.",
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w", pady=(10, 0))
        reload()

    # --- Activity
    def build_activity_tab(self, win, f):
        tk.Label(f, text="Recent sign-ins and important actions. Passwords and keys are never written here.",
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w")
        txt = tk.Text(f, bg=CARD, fg=FG, relief="flat", font=("Consolas", 9), height=14, wrap="none",
                      highlightthickness=1, highlightbackground=BORDER, padx=8, pady=6)
        txt.pack(fill="both", expand=True, pady=(8, 8))

        def reload():
            lines = []
            try:
                lines = security.AUDIT_FILE.read_text(encoding="utf-8").splitlines()[-200:]
            except OSError:
                pass
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            txt.insert("end", "\n".join(reversed(lines)) or "Nothing yet.")
            txt.configure(state="disabled")

        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.button(row, "Refresh", reload).pack(side="left")
        self.button(row, "Open log file", lambda: os.startfile(security.AUDIT_FILE) if security.AUDIT_FILE.exists() else None).pack(side="left", padx=8)
        reload()

    # ------------------------------------------------------------ window plumbing
    def refresh_now(self):
        self.draw_qr()

    def poll_tailscale(self):
        """Keeps self.ts current (Tailscale's state), off the window's thread: asking takes a moment."""
        while True:
            self.ts = server.tailscale_status()
            self.renew_tailscale_cert()
            time.sleep(15)

    def poll_updates(self):
        """Once a day (first check a minute after start): is a newer Kovrel on GitHub?"""
        time.sleep(60)
        told = None
        while True:
            if security.get("update_check", True):
                try:
                    self.update = server.check_for_update()
                except Exception as e:
                    print("update check:", e)
                if self.update and self.update[0] != told:
                    told = self.update[0]
                    try:
                        self.tray.notify(f"Kovrel {told} is available. Open Kovrel to download it.", "Update available")
                    except Exception:
                        pass
            else:
                self.update = None
            time.sleep(24 * 3600)

    def renew_tailscale_cert(self):
        """Tailscale certificates last 90 days. Renew one in use when it has under 20 days left."""
        name, left = server.tailscale_cert_info()
        if not (name and security.get("remote") and security.get("https") and left < 20 and self.ts["cert_domains"]):
            return
        if time.time() - getattr(self, "_renew_tried", 0) < 6 * 3600:
            return
        self._renew_tried = time.time()
        try:
            server.tailscale_get_cert()
            self.root.after(0, lambda: (self.start_server(), self.refresh_now()))   # load the new certificate
        except Exception as e:
            print("tailscale cert renewal:", e)

    def wait_for_show(self):
        while True:
            kernel32.WaitForSingleObject(ctypes.c_void_p(show_event), 0xFFFFFFFF)
            self.root.after(0, self.show)

    def show(self):
        self.root.deiconify()
        self.fit_height()
        self.root.lift()
        self.root.focus_force()

    def hide(self):
        self.root.withdraw()

    def quit(self):
        server.release_all_keys()
        server.pad_release()
        self.tray.stop()
        self.stop_server()
        self.root.destroy()


if __name__ == "__main__":
    App(start_hidden="--tray" in sys.argv).root.mainloop()
