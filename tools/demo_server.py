"""Demo Kovrel server for README screenshots: nothing private can show up.

    python tools/demo_server.py          # http://localhost:8796, prints the PIN

Same throwaway settings as tests/dev_server.py (own APPDATA, new phones approved after 2 s), but the
things that would show your real PC are replaced: the screen picture is a made-up desktop, the open
windows and the playing track are made up, and the PC is called DESKTOP-DEMO.
"""
import os
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp(prefix="kovrel-demo-")
os.environ["APPDATA"] = tmp
os.environ["KOVREL_FILES_DIR"] = os.path.join(tmp, "files")
sys.path.insert(0, ROOT)
import security, server  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402


def font(size, bold=False):
    for name in ("segoeuib.ttf" if bold else "segoeui.ttf", "arialbd.ttf" if bold else "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def desktop(w=1280, h=720):
    """A clean made-up desktop: dark gradient, one editor-style window, a taskbar."""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            t = (x / w * 0.5 + y / h * 0.5)
            px[x, y] = (int(14 + 20 * t), int(24 + 40 * t), int(34 + 50 * t))
    d = ImageDraw.Draw(img)
    # window
    x0, y0, x1, y1 = 150, 70, 1130, 610
    d.rounded_rectangle((x0, y0, x1, y1), 12, fill=(24, 27, 33), outline=(52, 58, 68))
    d.rounded_rectangle((x0, y0, x1, y0 + 38), 12, fill=(36, 40, 48))
    d.rectangle((x0, y0 + 26, x1, y0 + 38), fill=(36, 40, 48))
    for i, c in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        d.ellipse((x0 + 16 + i * 22, y0 + 12, x0 + 28 + i * 22, y0 + 24), fill=c)
    d.text((x0 + 120, y0 + 9), "notes.md - Editor", font=font(15), fill=(170, 178, 190))
    d.rectangle((x0, y0 + 38, x0 + 190, y1 - 1), fill=(29, 32, 39))
    for i, name in enumerate(("notes.md", "todo.txt", "ideas.md", "plan.md")):
        d.text((x0 + 18, y0 + 58 + i * 30), name, font=font(15), fill=(47, 214, 163) if i == 0 else (140, 148, 160))
    lines = [("# Weekend plan", (236, 238, 241), True), ("", None, False),
             ("- Finish the garden shelf", (190, 197, 208), False), ("- Call the dentist", (190, 197, 208), False),
             ("- Try the new pasta recipe", (190, 197, 208), False), ("", None, False),
             ("## Shopping", (236, 238, 241), True), ("", None, False),
             ("- Screws, wood glue, sandpaper", (190, 197, 208), False), ("- Tomatoes, basil, parmesan", (190, 197, 208), False)]
    for i, (text, color, bold) in enumerate(lines):
        if text:
            d.text((x0 + 230, y0 + 66 + i * 34), text, font=font(22 if bold else 19, bold), fill=color)
    # taskbar
    d.rectangle((0, h - 46, w, h), fill=(18, 20, 25))
    d.rounded_rectangle((w // 2 - 130, h - 38, w // 2 - 98, h - 8), 7, fill=(47, 214, 163))
    for i in range(4):
        d.rounded_rectangle((w // 2 - 80 + i * 52, h - 38, w // 2 - 48 + i * 52, h - 8), 7, fill=(52, 58, 68))
    d.text((w - 90, h - 36), "12:30", font=font(16), fill=(200, 206, 214))
    return img


def _bgra(img):
    r, g, b = img.split()
    return Image.merge("RGBA", (b, g, r, Image.new("L", img.size, 255))).tobytes()


# Two frames that differ by a blinking text caret, so the viewer shows a normal frame rate.
_base = desktop()
_FRAMES = [_bgra(_base)]
_caret = _base.copy()
ImageDraw.Draw(_caret).rectangle((662, 444, 664, 466), fill=(236, 238, 241))
_FRAMES.append(_bgra(_caret))
_count = [0]


def grab_screen(max_width=1280, monitor=0):
    _count[0] += 1
    return _base.width, _base.height, _FRAMES[_count[0] % 2]


server.grab_screen = grab_screen
server.list_windows = lambda: [
    {"id": 1, "title": "notes.md - Editor", "minimized": False, "active": True},
    {"id": 2, "title": "Music", "minimized": False, "active": False},
    {"id": 3, "title": "File Explorer", "minimized": True, "active": False},
]
server.media_info = lambda: {"volume": 42, "muted": False, "playing": True,
                             "now": {"title": "Sunday Morning", "artist": "Demo Artist", "app": "Music", "playing": True}}
server.socket.gethostname = lambda: "DESKTOP-DEMO"


def approve_later(req):
    threading.Timer(2.0, security.approve, args=(req["id"],)).start()


security.approval_callback = approve_later
port = 8796
srv = server.make_server(port, local=True, https=False)
print(f"READY {srv.scheme}://localhost:{port} PIN={security.current_pin()} APPDATA={tmp}", flush=True)
srv.serve_forever()
