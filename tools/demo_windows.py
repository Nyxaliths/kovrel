"""Takes the PC-window screenshots for the README: docs/screenshot-pc.png and docs/screenshot-remote.png.

    python tools/demo_windows.py

Runs the real Kovrel window with throwaway settings (own APPDATA, own port, its own single-instance
name, so a Kovrel that is already running is left alone) and made-up network details: a fake home
address and a fake Tailscale name. The windows come up on top of everything for a few seconds, so
don't touch the mouse while it runs.
"""
import ctypes
import os
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = tempfile.mkdtemp(prefix="kovrel-shots-")
os.environ["APPDATA"] = tmp
os.environ["KOVREL_FILES_DIR"] = os.path.join(tmp, "files")
os.environ["KOVREL_PORT"] = "8797"
sys.path.insert(0, str(ROOT))
ctypes.windll.shcore.SetProcessDpiAwareness(1)   # real pixels, so the screenshots are sharp

from PIL import Image, ImageDraw, ImageFilter, ImageGrab  # noqa: E402

# Load Kovrel.pyw under another single-instance name, so it doesn't poke the real, running Kovrel.
src = (ROOT / "Kovrel.pyw").read_text("utf-8-sig").replace("Local\\\\Kovrel_show", "Local\\\\Kovrel_shots_show")
ns = {"__name__": "kovrel_shots", "__file__": str(ROOT / "Kovrel.pyw")}
exec(compile(src, str(ROOT / "Kovrel.pyw"), "exec"), ns)
security, server = ns["security"], ns["server"]

server.lan_ip = lambda: "192.168.1.20"
server.tailscale_status = lambda: {"installed": True, "running": True, "ip": "100.101.102.103",
                                   "name": "desktop-demo.tail1234.ts.net", "cert_domains": [], "error": ""}
server.connected_devices = lambda: [{"name": "My phone"}]
for mod in (server, ns["server"]):
    mod.socket.gethostname = lambda: "DESKTOP-DEMO"


def frame_rect(win):
    """The window including its title bar and border, in screen pixels."""
    hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
    r = wintypes.RECT()
    ctypes.windll.dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), 9, ctypes.byref(r), ctypes.sizeof(r))
    return r.left, r.top, r.right, r.bottom


def shot(win, name, margin=36):
    win.attributes("-topmost", True)
    win.lift()
    win.update()
    win.after(400)
    win.update()
    img = ImageGrab.grab(frame_rect(win), all_screens=True).convert("RGB")
    # Put it on a plain backdrop with a soft shadow, so no part of the real desktop shows around it.
    w, h = img.size
    canvas = Image.new("RGB", (w + 2 * margin, h + 2 * margin))
    px = canvas.load()
    for y in range(canvas.height):
        for x in range(canvas.width):
            t = (x / canvas.width + y / canvas.height) / 2
            px[x, y] = (int(16 + 14 * t), int(30 + 34 * t), int(42 + 44 * t))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((margin, margin + 10, margin + w, margin + h + 10), 10, fill=(0, 0, 0, 150))
    canvas.paste(Image.alpha_composite(canvas.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(14))).convert("RGB"))
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), 8, fill=255)
    canvas.paste(img, (margin, margin), mask)
    out = ROOT / "docs" / name
    canvas.save(out, optimize=True)
    print("saved", out, canvas.size, flush=True)


app = ns["App"](start_hidden=False)
root = app.root
# The server really listens on 8797 (the real Kovrel may be using 8765); show the usual port.
server.PORT = 8765
app.url = f"{app.scheme}://{server.lan_ip()}:{server.PORT}"
app.draw_qr()


def step_main():
    shot(root, "screenshot-pc.png")
    # turn on remote access (needs a long password) and open the Remote tab
    security.set_secret("demo-password-1234", "password")
    security.set_remote(True)
    app.open_security(2)
    root.after(2500, step_remote)


def step_remote():
    shot(app.sec_win, "screenshot-remote.png")
    app.quit()


root.after(2500, step_main)
root.mainloop()
