"""Kovrel server - control this Windows PC from a browser (this PC or your phone).

Normally started by Kovrel.pyw (the desktop app). For a console-only server:
      python server.py            (default port 8765)
      python server.py --local    (only this PC, no network access)
"""

import argparse
import asyncio
import concurrent.futures
import ctypes
import ctypes.wintypes as wt
import io
import ipaddress
import json
import os
import queue
import re
import secrets
import shutil
import socket
import sqlite3
import ssl
import struct
import subprocess
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

try:  # make cursor coordinates match real pixels on scaled displays
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    user32.SetProcessDPIAware()

import security

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
CONFIG_DIR = security.CONFIG_DIR
# Files sent from the phone. KOVREL_FILES_DIR is for tests, so they never touch the real folder.
SHARED_DIR = Path(os.environ.get("KOVREL_FILES_DIR") or Path.home() / "Downloads" / "Kovrel")
OLD_SHARED_DIR = SHARED_DIR.with_name("Vexari")  # the app's name before 2.0
PORT = int(os.environ.get("KOVREL_PORT", 8765))
VERSION = "2.0.1"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: no console flash when run from pythonw

if OLD_SHARED_DIR.is_dir() and not SHARED_DIR.exists():  # keep files people already sent
    try:
        OLD_SHARED_DIR.rename(SHARED_DIR)
    except OSError:
        pass

# Apps the page can launch. Edit freely: "Label": "command or path or URL".
APPS = {
    "Browser": "https://www.google.com",
    "YouTube": "https://www.youtube.com",
    "File Explorer": "explorer.exe",
    "Notepad": "notepad.exe",
    "Calculator": "calc.exe",
    "Task Manager": "taskmgr.exe",
    "Settings": "ms-settings:",
    "Spotify": "spotify:",
}

# ---------------------------------------------------------------- settings
# Stored by security.py (shortcuts, profiles, shared folders, trusted devices…).

CONFIG = security.CFG
config_lock = security._lock
save_config = security.save


def get_pin():
    return security.current_pin()


def reset_pin():
    return security.reset_pin()


# "Pause remote control": a panic switch on the PC that makes every phone read-only-blocked.
PAUSED = {"on": False}


# ---------------------------------------------------------------- input

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
MOUSE_FLAGS = {
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


def _send(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _mouse(flags, data=0):
    i = INPUT(type=INPUT_MOUSE)
    i.u.mi = MOUSEINPUT(0, 0, ctypes.c_ulong(data & 0xFFFFFFFF).value, flags, 0, 0)
    return i


def _key(vk=0, scan=0, flags=0):
    i = INPUT(type=INPUT_KEYBOARD)
    i.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return i


MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x0001, 0x8000, 0x4000


def move_to(x, y):
    """Moves the cursor like a real mouse does (SendInput), so a held button drags windows, selects
    text and moves files. SetCursorPos alone only jumps the pointer, and many apps ignore that while
    a button is down."""
    gsm = user32.GetSystemMetrics
    left, top, w, h = gsm(76), gsm(77), max(gsm(78), 2), max(gsm(79), 2)   # the whole virtual desktop
    x = min(max(int(x), left), left + w - 1)
    y = min(max(int(y), top), top + h - 1)
    i = INPUT(type=INPUT_MOUSE)
    i.u.mi = MOUSEINPUT(round((x - left) * 65535 / (w - 1)), round((y - top) * 65535 / (h - 1)), 0,
                        MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, 0, 0)
    _send(i)


def mouse_move(dx, dy):
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    move_to(pt.x + dx, pt.y + dy)


def mouse_button(button="left", action="click"):
    down, up = MOUSE_FLAGS.get(button, MOUSE_FLAGS["left"])
    if action == "down":
        _send(_mouse(down))
    elif action == "up":
        _send(_mouse(up))
    elif action == "double":
        _send(_mouse(down), _mouse(up), _mouse(down), _mouse(up))
    else:
        _send(_mouse(down), _mouse(up))


def mouse_scroll(dy=0, dx=0):
    if dy:
        _send(_mouse(MOUSEEVENTF_WHEEL, int(dy)))
    if dx:
        _send(_mouse(MOUSEEVENTF_HWHEEL, int(dx)))


def type_text(text):
    events = []
    for ch in text:
        if ch == "\n":
            events += [_key(0x0D), _key(0x0D, flags=KEYEVENTF_KEYUP)]
            continue
        raw = ch.encode("utf-16-le")
        for k in range(0, len(raw), 2):  # handles emoji (surrogate pairs)
            unit = int.from_bytes(raw[k:k + 2], "little")
            events += [_key(scan=unit, flags=KEYEVENTF_UNICODE),
                       _key(scan=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
    if events:
        _send(*events)


VK = {
    "enter": 0x0D, "backspace": 0x08, "tab": 0x09, "esc": 0x1B, "space": 0x20,
    "pgup": 0x21, "pgdn": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "insert": 0x2D, "delete": 0x2E, "printscreen": 0x2C,
    "shift": 0x10, "ctrl": 0x11, "alt": 0x12, "win": 0x5B, "apps": 0x5D,
    "volup": 0xAF, "voldown": 0xAE, "mute": 0xAD,
    "next": 0xB0, "prev": 0xB1, "stop": 0xB2, "playpause": 0xB3,
    **{f"f{n}": 0x6F + n for n in range(1, 13)},
}
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5D,
            0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3}


def _vk(name):
    name = name.strip().lower()
    if name in VK:
        return VK[name]
    if len(name) == 1 and name.isalnum():
        return ord(name.upper())
    raise ValueError(f"unknown key: {name}")


def _flags(vk, up=False):
    return (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED else 0) | (KEYEVENTF_KEYUP if up else 0)


def press_combo(combo):
    """Press e.g. 'enter', 'ctrl+c', 'alt+tab', 'win+shift+s'."""
    codes = [_vk(p) for p in combo.split("+")]
    _send(*[_key(c, flags=_flags(c)) for c in codes],
          *[_key(c, flags=_flags(c, True)) for c in reversed(codes)])


# Held inputs for the game controller: keys ("w", "space") and mouse buttons
# ("mouse_left"). The page refreshes held inputs every ~0.5s; anything not refreshed
# for 1.5s is released, so a dropped phone never leaves a key or trigger stuck down.
HELD = {}
held_lock = threading.Lock()
MOUSE_HOLD = {"mouse_left": "left", "mouse_right": "right", "mouse_middle": "middle"}
WHEEL = {"wheel_up": 120, "wheel_down": -120}


def _input(name, down):
    if name in MOUSE_HOLD:
        mouse_button(MOUSE_HOLD[name], "down" if down else "up")
    else:
        vk = _vk(name)
        _send(_key(vk, flags=_flags(vk, not down)))


def check_binding(name):
    if name not in MOUSE_HOLD and name not in WHEEL:
        _vk(name)  # raises for unknown keys


def key_state(name, down):
    if name in WHEEL:  # wheel "buttons" fire once per press
        if down:
            mouse_scroll(WHEEL[name])
        return
    check_binding(name)
    with held_lock:
        if down:
            if name not in HELD:
                _input(name, True)
            HELD[name] = time.time()
        elif HELD.pop(name, None) is not None:
            _input(name, False)


def refresh_held(names):
    now = time.time()
    with held_lock:
        for n in names:
            if n in HELD:
                HELD[n] = now


def release_all_keys():
    with held_lock:
        for name in list(HELD):
            _input(name, False)
        HELD.clear()


def _held_watchdog():
    while True:
        time.sleep(0.5)
        now = time.time()
        with held_lock:
            for name in [n for n, t in HELD.items() if now - t > 1.5]:
                _input(name, False)
                del HELD[name]


def look(dx, dy):
    """Relative mouse move the way a real mouse does it, so games that read raw
    mouse input (most shooters) turn the camera. (SetCursorPos doesn't do that.)"""
    dx, dy = int(dx), int(dy)
    if dx or dy:
        i = INPUT(type=INPUT_MOUSE)
        i.u.mi = MOUSEINPUT(dx, dy, 0, 0x0001, 0, 0)  # MOUSEEVENTF_MOVE
        _send(i)


threading.Thread(target=_held_watchdog, daemon=True).start()

# ---------------------------------------------------------------- clipboard

CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
user32.GetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]


def _open_clipboard():
    for _ in range(20):  # another app may hold it briefly
        if user32.OpenClipboard(None):
            return
        time.sleep(0.05)
    raise RuntimeError("clipboard is busy, try again")


def clipboard_get():
    _open_clipboard()
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = kernel32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p) if p else ""
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def clipboard_set(text):
    data = text.encode("utf-16-le") + b"\x00\x00"
    _open_clipboard()
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        p = kernel32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        kernel32.GlobalUnlock(h)
        user32.SetClipboardData(CF_UNICODETEXT, h)  # clipboard owns h now
    finally:
        user32.CloseClipboard()


# ---------------------------------------------------------------- windows

GWL_EXSTYLE, WS_EX_TOOLWINDOW = -20, 0x00000080
DWMWA_CLOAKED = 14
SW_MINIMIZE, SW_MAXIMIZE, SW_RESTORE = 6, 3, 9
WM_CLOSE = 0x0010
EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.GetWindowLongW.restype = wt.LONG


def _is_cloaked(hwnd):
    val = wt.DWORD()
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val))
    return val.value != 0


def list_windows():
    found = []
    fg = user32.GetForegroundWindow()

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # 4 = GW_OWNER
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW or _is_cloaked(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value in ("Program Manager",):
            return True
        found.append({"id": int(hwnd), "title": buf.value,
                      "minimized": bool(user32.IsIconic(hwnd)), "active": hwnd == fg})
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found


def window_action(hwnd, action):
    hwnd = wt.HWND(int(hwnd))
    if not user32.IsWindow(hwnd):
        raise ValueError("window no longer exists")
    if action == "focus":
        if user32.IsIconic(hwnd):
            user32.ShowWindowAsync(hwnd, SW_RESTORE)
            time.sleep(0.15)  # let the restore start before taking focus
        # Tapping Alt lets us steal focus (Windows blocks it otherwise).
        user32.keybd_event(0x12, 0, 0, 0)
        user32.SetForegroundWindow(hwnd)
        user32.keybd_event(0x12, 0, KEYEVENTF_KEYUP, 0)
    elif action == "minimize":
        # Async versions never wait on the target app, so a busy app can't stall the server.
        user32.ShowWindowAsync(hwnd, SW_MINIMIZE)
    elif action == "maximize":
        user32.ShowWindowAsync(hwnd, SW_MAXIMIZE)
    elif action == "restore":
        user32.ShowWindowAsync(hwnd, SW_RESTORE)
    elif action == "close":
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    else:
        raise ValueError(f"unknown window action: {action}")


# ---------------------------------------------------------------- power + sleep timer

def power(action):
    if action == "lock":
        user32.LockWorkStation()
    elif action == "sleep":
        ctypes.windll.powrprof.SetSuspendState(0, 1, 0)
    elif action == "shutdown":
        subprocess.Popen(["shutdown", "/s", "/t", "5"], creationflags=NO_WINDOW)
    elif action == "restart":
        subprocess.Popen(["shutdown", "/r", "/t", "5"], creationflags=NO_WINDOW)
    elif action == "cancel":
        subprocess.Popen(["shutdown", "/a"], creationflags=NO_WINDOW)
        timer_cancel()
    elif action == "monitor_off":
        user32.PostMessageW(0xFFFF, 0x0112, 0xF170, 2)  # HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER
    else:
        raise ValueError(f"unknown power action: {action}")


TIMER = {"timer": None, "deadline": 0, "action": None}
TIMER_ACTIONS = {"sleep", "shutdown", "lock", "monitor_off"}


def timer_set(minutes, action):
    if action not in TIMER_ACTIONS:
        raise ValueError("unknown timer action")
    minutes = max(1, min(int(minutes), 24 * 60))
    timer_cancel()
    t = threading.Timer(minutes * 60, _timer_fire)
    t.daemon = True
    t.start()
    TIMER.update(timer=t, deadline=time.time() + minutes * 60, action=action)


def _timer_fire():
    action = TIMER["action"]
    TIMER.update(timer=None, deadline=0, action=None)
    try:
        press_combo("stop")  # stop media first so it doesn't resume on wake
    except Exception:
        pass
    power(action)


def timer_cancel():
    if TIMER["timer"]:
        TIMER["timer"].cancel()
    TIMER.update(timer=None, deadline=0, action=None)


def timer_info():
    if not TIMER["timer"]:
        return {"active": False}
    return {"active": True, "action": TIMER["action"], "seconds_left": int(TIMER["deadline"] - time.time())}


# ---------------------------------------------------------------- screen

gdi32 = ctypes.WinDLL("gdi32")
user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.BitBlt.argtypes = [wt.HDC] + [ctypes.c_int] * 4 + [wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
gdi32.StretchBlt.argtypes = [wt.HDC] + [ctypes.c_int] * 4 + [wt.HDC] + [ctypes.c_int] * 4 + [wt.DWORD]
gdi32.SetStretchBltMode.argtypes = [wt.HDC, ctypes.c_int]
gdi32.SetBrushOrgEx.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
user32.DrawIconEx.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, wt.HICON, ctypes.c_int, ctypes.c_int,
                              wt.UINT, wt.HBRUSH, wt.UINT]
user32.GetIconInfo.argtypes = [wt.HICON, ctypes.c_void_p]

SRCCOPY, CAPTUREBLT, HALFTONE, DI_NORMAL = 0x00CC0020, 0x40000000, 4, 3


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hCursor", wt.HICON), ("pt", wt.POINT)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


MonitorEnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)
user32.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.c_void_p, MonitorEnumProc, wt.LPARAM]
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.c_void_p]

try:  # Pillow gives smaller, faster JPEG frames; PNG fallback otherwise
    from PIL import Image
except ImportError:
    Image = None

capture_lock = threading.Lock()


def list_monitors():
    """Primary monitor first, then the rest left-to-right. Each: (left, top, width, height)."""
    mons = []

    def cb(hmon, _hdc, _rect, _):
        mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        r = mi.rcMonitor
        mons.append(((r.left, r.top, r.right - r.left, r.bottom - r.top), bool(mi.dwFlags & 1)))
        return True

    user32.EnumDisplayMonitors(None, None, MonitorEnumProc(cb), 0)
    mons.sort(key=lambda m: (not m[1], m[0][0]))
    return [m[0] for m in mons]


def screen_rect(monitor=0):
    """monitor = index into list_monitors(), or -1 for all monitors combined."""
    if monitor >= 0:
        mons = list_monitors()
        if mons:
            return mons[min(monitor, len(mons) - 1)]
    gsm = user32.GetSystemMetrics
    return gsm(76), gsm(77), gsm(78), gsm(79)  # virtual screen


def _draw_cursor(hdc, left, top):
    ci = CURSORINFO(cbSize=ctypes.sizeof(CURSORINFO))
    if not user32.GetCursorInfo(ctypes.byref(ci)) or not ci.flags & 1 or not ci.hCursor:
        return
    ii = ICONINFO()
    hx = hy = 0
    if user32.GetIconInfo(ci.hCursor, ctypes.byref(ii)):
        hx, hy = ii.xHotspot, ii.yHotspot
        for bm in (ii.hbmMask, ii.hbmColor):
            if bm:
                gdi32.DeleteObject(bm)
    user32.DrawIconEx(hdc, ci.pt.x - left - hx, ci.pt.y - top - hy, ci.hCursor, 0, 0, 0, None, DI_NORMAL)


def grab_screen(max_width=1280, monitor=0):
    """Capture a monitor (with cursor), longest side scaled to max_width. Returns (w, h, BGRA bytes)."""
    left, top, sw, sh = screen_rect(monitor)
    scale = min(1.0, max_width / max(sw, sh))
    w, h = max(1, int(sw * scale)), max(1, int(sh * scale))

    screen = user32.GetDC(None)
    full_dc = gdi32.CreateCompatibleDC(screen)
    full_bmp = gdi32.CreateCompatibleBitmap(screen, sw, sh)
    small_dc = gdi32.CreateCompatibleDC(screen)
    small_bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    old_full = gdi32.SelectObject(full_dc, full_bmp)
    old_small = gdi32.SelectObject(small_dc, small_bmp)
    try:
        gdi32.BitBlt(full_dc, 0, 0, sw, sh, screen, left, top, SRCCOPY | CAPTUREBLT)
        _draw_cursor(full_dc, left, top)
        gdi32.SetStretchBltMode(small_dc, HALFTONE)
        gdi32.SetBrushOrgEx(small_dc, 0, 0, None)
        gdi32.StretchBlt(small_dc, 0, 0, w, h, full_dc, 0, 0, sw, sh, SRCCOPY)

        bih = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h,
                               biPlanes=1, biBitCount=32, biCompression=0)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.SelectObject(small_dc, old_small)  # bitmap must not be selected for GetDIBits
        old_small = None
        gdi32.GetDIBits(small_dc, small_bmp, 0, h, buf, ctypes.byref(bih), 0)
        return w, h, buf.raw
    finally:
        gdi32.SelectObject(full_dc, old_full)
        if old_small:
            gdi32.SelectObject(small_dc, old_small)
        gdi32.DeleteObject(full_bmp)
        gdi32.DeleteObject(small_bmp)
        gdi32.DeleteDC(full_dc)
        gdi32.DeleteDC(small_dc)
        user32.ReleaseDC(None, screen)


def _png(w, h, bgra):
    rgb = bytearray(w * h * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    stride = w * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 1))
            + chunk(b"IEND", b""))


def tap_screen(x, y, button="left", action="click", monitor=0):
    """x, y are 0..1 fractions of the shared screen image."""
    left, top, sw, sh = screen_rect(int(monitor))
    move_to(left + min(max(x, 0), 1) * (sw - 1), top + min(max(y, 0), 1) * (sh - 1))
    if action != "move":
        mouse_button(button, action)


def screenshot_png(monitor=0):
    """A full-resolution PNG of one monitor (or all of them with monitor=-1)."""
    with capture_lock:
        w, h, bgra = grab_screen(100000, int(monitor))
    if Image:
        out = io.BytesIO()
        Image.frombuffer("RGB", (w, h), bgra, "raw", "BGRX", 0, 1).save(out, "PNG", compress_level=3)
        return out.getvalue()
    return _png(w, h, bgra)


def video_stream(write, width=1280, monitor=0, quality=65, max_fps=30):
    """Pushes the screen as a never-ending run of JPEG frames, each as [4-byte length][bytes].

    One long-lived connection replaces a request per frame, so there is no round trip between
    frames. Unchanged screens are skipped (a heartbeat goes out every second), and when the
    phone's link can't keep up the picture quality and size are lowered automatically."""
    width = max(320, min(int(width), 3840))
    target_q, q, scale = max(30, min(int(quality), 90)), max(30, min(int(quality), 90)), 1.0
    last_sum, last_sent, slow, fast = None, 0.0, 0, 0
    write(None, {"X-Format": "jpeg-frames"})
    while True:
        t0 = time.perf_counter()
        with capture_lock:
            w, h, bgra = grab_screen(max(320, int(width * scale)), int(monitor))
        s = zlib.adler32(bgra)
        if s == last_sum and time.time() - last_sent < 1.0:
            time.sleep(max(0.0, 1 / max_fps - (time.perf_counter() - t0)))
            continue
        last_sum = s
        if Image:
            out = io.BytesIO()
            Image.frombuffer("RGB", (w, h), bgra, "raw", "BGRX", 0, 1).save(out, "JPEG", quality=q)
            data = out.getvalue()
        else:
            data = _png(w, h, bgra)
        t1 = time.perf_counter()
        write(struct.pack(">I", len(data)) + data, None)   # blocks when the phone is behind
        last_sent, spent = time.time(), time.perf_counter() - t1
        if spent > 0.12:                                    # link too slow: smaller and blurrier
            slow, fast = slow + 1, 0
            if slow >= 2:
                if q > 40:
                    q -= 8
                elif scale > 0.5:
                    scale = max(0.5, scale - 0.15)
                slow = 0
        elif spent < 0.03:                                  # plenty of room: recover
            fast, slow = fast + 1, 0
            if fast >= 30:
                if scale < 1.0:
                    scale = min(1.0, scale + 0.15)
                elif q < target_q:
                    q = min(target_q, q + 5)
                fast = 0
        else:
            slow = fast = 0
        time.sleep(max(0.0, 1 / max_fps - (time.perf_counter() - t0)))


# ---------------------------------------------------------------- volume + now playing
# COM (pycaw) and WinRT both want a thread that initialised COM, so all of that work
# runs on one dedicated worker thread.

def _com_init():
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass


com_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, initializer=_com_init)


def _on_com(fn, *args):
    return com_pool.submit(fn, *args).result(timeout=5)


def _endpoint():
    from pycaw.pycaw import AudioUtilities
    return AudioUtilities.GetSpeakers().EndpointVolume


def _volume_get():
    ev = _endpoint()
    return {"volume": round(ev.GetMasterVolumeLevelScalar() * 100), "muted": bool(ev.GetMute())}


def _volume_set(level=None, muted=None):
    ev = _endpoint()
    if level is not None:
        ev.SetMasterVolumeLevelScalar(max(0.0, min(float(level) / 100, 1.0)), None)
    if muted is not None:
        ev.SetMute(1 if muted else 0, None)
    return _volume_get()


def _pretty_app(app_id):
    name = re.split(r"[!\\/]", app_id or "")[-1]
    name = re.sub(r"\.exe$", "", name, flags=re.I).split(".")[-1] or name
    if re.fullmatch(r"[A-Z0-9]{16,}", name):  # installed web apps (PWAs) report a random-looking id
        return "Browser"
    return {"msedge": "Edge", "chrome": "Chrome", "firefox": "Firefox", "spotify": "Spotify",
            "zunemusic": "Media Player", "vlc": "VLC"}.get(name.lower(), name[:1].upper() + name[1:])


def _now_playing():
    from winrt.windows.media.control import \
        GlobalSystemMediaTransportControlsSessionManager as Manager

    async def go():
        mgr = await Manager.request_async()
        s = mgr.get_current_session()
        if not s:
            return None
        props = await s.try_get_media_properties_async()
        status = s.get_playback_info().playback_status
        return {"title": props.title or "", "artist": props.artist or "",
                "app": _pretty_app(s.source_app_user_model_id),
                "playing": int(status) == 4}  # 4 = Playing

    return asyncio.run(go())


def media_info():
    info = {"volume": None, "muted": None, "now": None}
    try:
        info.update(_on_com(_volume_get))
    except Exception:
        pass
    try:
        info["now"] = _on_com(_now_playing)
    except Exception:
        pass
    return info


# ---------------------------------------------------------------- PC status

try:
    import psutil
    psutil.cpu_percent(None)  # prime: the first reading is always 0
except ImportError:
    psutil = None

_gpu_cache = {"t": 0, "v": None}


def _gpu():
    if time.time() - _gpu_cache["t"] < 3:
        return _gpu_cache["v"]
    v = None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, creationflags=NO_WINDOW).stdout.strip()
        if out:
            name, temp, util = [p.strip() for p in out.splitlines()[0].split(",")]
            v = {"name": name.replace("NVIDIA GeForce ", ""), "temp": int(temp), "load": int(util)}
    except Exception:
        pass
    _gpu_cache.update(t=time.time(), v=v)
    return v


def pc_status():
    s = {"gpu": _gpu(), "uptime": int(kernel32.GetTickCount64() / 1000), "battery": None}
    if psutil:
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
        s.update(cpu=round(psutil.cpu_percent(None)), ram=round(vm.percent),
                 ram_used=round(vm.used / 2**30, 1), ram_total=round(vm.total / 2**30, 1),
                 disk=round(disk.percent), disk_free=round(disk.free / 2**30))
        bat = psutil.sensors_battery()
        if bat:
            s["battery"] = {"percent": round(bat.percent), "plugged": bool(bat.power_plugged)}
    return s


# ---------------------------------------------------------------- notifications
# Windows keeps recent toasts in a local SQLite database; we read it read-only.

NOTIF_DB = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Notifications/wpndatabase.db"


def notifications(limit=30):
    if not NOTIF_DB.exists():
        return []
    con = sqlite3.connect(f"file:{NOTIF_DB}?mode=ro", uri=True, timeout=2)
    try:
        rows = con.execute(
            "SELECT n.Id, n.Payload, n.ArrivalTime, h.PrimaryId FROM Notification n "
            "LEFT JOIN NotificationHandler h ON h.RecordId = n.HandlerId "
            "WHERE n.Type = 'toast' ORDER BY n.ArrivalTime DESC LIMIT ?", (limit,)).fetchall()
    finally:
        con.close()
    out = []
    for nid, payload, arrival, app in rows:
        try:
            xml = payload.decode("utf-8", "ignore") if isinstance(payload, bytes) else payload
            texts = [t.text.strip() for t in ET.fromstring(xml).iter("text") if t.text and t.text.strip()]
        except Exception:
            continue
        if not texts:
            continue
        out.append({
            "id": nid,
            "app": _pretty_app(app or ""),
            "title": texts[0][:200],
            "body": " ".join(texts[1:])[:500],
            "time": int((arrival - 116444736000000000) / 1e7) if arrival else 0,  # FILETIME -> unix
        })
    return out


# ---------------------------------------------------------------- modifier keys for clicks (Ctrl+click…)

def mods_down(mods):
    codes = [_vk(m) for m in (mods or []) if m in ("ctrl", "shift", "alt", "win")]
    if codes:
        _send(*[_key(c, flags=_flags(c)) for c in codes])
    return codes


def mods_up(codes):
    if codes:
        _send(*[_key(c, flags=_flags(c, True)) for c in reversed(codes)])


# ---------------------------------------------------------------- per-app volume

def _app_label(exe):
    base = re.sub(r"\.exe$", "", exe, flags=re.I)
    return base[:1].upper() + base[1:]


def _mixer_list():
    from pycaw.pycaw import AudioUtilities
    apps = {}
    for s in AudioUtilities.GetAllSessions():
        if not s.Process:
            continue
        try:
            name = s.Process.name()
        except Exception:
            continue
        vol = s.SimpleAudioVolume
        apps.setdefault(name.lower(), {"name": name, "label": _app_label(name),
                                       "volume": round(vol.GetMasterVolume() * 100), "muted": bool(vol.GetMute())})
    return sorted(apps.values(), key=lambda a: a["label"].lower())


def _mixer_set(name, level=None, muted=None):
    from pycaw.pycaw import AudioUtilities
    hit = False
    for s in AudioUtilities.GetAllSessions():
        try:
            if not s.Process or s.Process.name().lower() != str(name).lower():
                continue
        except Exception:
            continue
        vol, hit = s.SimpleAudioVolume, True
        if level is not None:
            vol.SetMasterVolume(max(0.0, min(float(level) / 100, 1.0)), None)
        if muted is not None:
            vol.SetMute(1 if muted else 0, None)
    if not hit:
        raise ValueError("That app isn't playing sound any more.")


# ---------------------------------------------------------------- monitor brightness (DDC/CI, laptops via WMI)

dxva2 = ctypes.WinDLL("dxva2")
dxva2.GetPhysicalMonitorsFromHMONITOR.argtypes = [wt.HMONITOR, wt.DWORD, ctypes.c_void_p]
dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [wt.HMONITOR, ctypes.POINTER(wt.DWORD)]
dxva2.DestroyPhysicalMonitors.argtypes = [wt.DWORD, ctypes.c_void_p]
dxva2.GetMonitorBrightness.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.DWORD)] * 3
dxva2.SetMonitorBrightness.argtypes = [wt.HANDLE, wt.DWORD]


class PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [("handle", wt.HANDLE), ("desc", wt.WCHAR * 128)]


display_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)


def _physical_monitors():
    """[(PHYSICAL_MONITOR array, index)] for every monitor Windows can talk DDC/CI to."""
    found = []

    def cb(hmon, _hdc, _rect, _):
        n = wt.DWORD()
        if dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(n)) and n.value:
            arr = (PHYSICAL_MONITOR * n.value)()
            if dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, n.value, arr):
                found.append(arr)
        return True

    user32.EnumDisplayMonitors(None, None, MonitorEnumProc(cb), 0)
    return found


def _brightness_list():
    out, i = [], 0
    for arr in _physical_monitors():
        for pm in arr:
            lo, cur, hi = wt.DWORD(), wt.DWORD(), wt.DWORD()
            for attempt in range(3):  # DDC/CI is a slow, flaky channel: monitors sometimes ignore a request
                if dxva2.GetMonitorBrightness(pm.handle, ctypes.byref(lo), ctypes.byref(cur), ctypes.byref(hi)) and hi.value > lo.value:
                    pct = round((cur.value - lo.value) * 100 / (hi.value - lo.value))
                    out.append({"id": f"ddc:{i}", "name": pm.desc or f"Monitor {i + 1}", "level": pct})
                    break
                time.sleep(0.12)
            i += 1
        dxva2.DestroyPhysicalMonitors(len(arr), arr)
    if not out and psutil and psutil.sensors_battery():  # a laptop's own screen
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness"],
                               capture_output=True, text=True, timeout=8, creationflags=NO_WINDOW)
            out.append({"id": "wmi", "name": "Laptop screen", "level": int(r.stdout.strip().splitlines()[0])})
        except Exception:
            pass
    return out


def _brightness_set(mon_id, level):
    level = max(0, min(int(level), 100))
    if mon_id == "wmi":
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        "Invoke-CimMethod -InputObject (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods) "
                        f"-MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness={level}}}"],
                       capture_output=True, timeout=8, creationflags=NO_WINDOW, check=True)
        return
    want, i = int(str(mon_id).split(":")[1]), 0
    for arr in _physical_monitors():
        for pm in arr:
            if i == want:
                lo, cur, hi = wt.DWORD(), wt.DWORD(), wt.DWORD()
                if not dxva2.GetMonitorBrightness(pm.handle, ctypes.byref(lo), ctypes.byref(cur), ctypes.byref(hi)):
                    raise ValueError("This monitor doesn't allow brightness control.")
                dxva2.SetMonitorBrightness(pm.handle, lo.value + round(level * (hi.value - lo.value) / 100))
            i += 1
        dxva2.DestroyPhysicalMonitors(len(arr), arr)


def _on_display(fn, *args):
    return display_pool.submit(fn, *args).result(timeout=10)


# ---------------------------------------------------------------- running programs (task manager)

PROTECTED_PROCESSES = {"system", "system idle process", "registry", "smss.exe", "csrss.exe", "wininit.exe", "services.exe",
                       "lsass.exe", "winlogon.exe", "fontdrvhost.exe", "dwm.exe", "svchost.exe", "memory compression",
                       "kovrel.exe", "sihost.exe", "ctfmon.exe", "audiodg.exe", "lsm.exe"}
_proc_cache = {}


def _mine(info):
    user = (info.get("username") or "").lower()
    return user.endswith("\\" + os.environ.get("USERNAME", "?").lower())


def processes(limit=80, query=""):
    """Your programs grouped by name, biggest memory users first. `query` filters by name."""
    if not psutil:
        raise ValueError("Process list isn't available.")
    query = str(query or "").strip().lower()[:40]
    me, cores, groups, alive = os.getpid(), psutil.cpu_count() or 1, {}, set()
    for p in psutil.process_iter(["pid", "name", "memory_info", "username"]):
        info = p.info
        name = info.get("name") or ""
        if not name or info["pid"] == me or name.lower() in PROTECTED_PROCESSES or not _mine(info) or not info.get("memory_info"):
            continue
        alive.add(info["pid"])
        if query and query not in name.lower():
            continue
        cached = _proc_cache.setdefault(info["pid"], p)
        try:
            cpu = cached.cpu_percent(None) / cores
        except psutil.Error:
            cpu = 0.0
        g = groups.setdefault(name.lower(), {"name": name, "mb": 0.0, "cpu": 0.0, "count": 0})
        g["mb"] += info["memory_info"].rss / 2**20
        g["cpu"] += cpu
        g["count"] += 1
    for pid in set(_proc_cache) - alive:
        _proc_cache.pop(pid, None)
    top = sorted(groups.values(), key=lambda g: -g["mb"])[:limit]
    return [{"name": g["name"], "mb": round(g["mb"]), "cpu": round(g["cpu"], 1), "count": g["count"]} for g in top]


def kill_process(name):
    name = str(name).lower()
    if name in PROTECTED_PROCESSES:
        raise ValueError("Windows needs that one. It can't be closed from here.")
    hit = []
    for p in psutil.process_iter(["pid", "name", "username"]):
        if (p.info.get("name") or "").lower() == name and p.info["pid"] != os.getpid() and _mine(p.info):
            hit.append(p)
    if not hit:
        raise ValueError("That program isn't running any more.")
    for p in hit:
        try:
            p.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(hit, timeout=2)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    return len(hit)


# ---------------------------------------------------------------- frontmost window (for game profiles)

kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]


def active_window():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return {"title": "", "exe": ""}
    n = user32.GetWindowTextLengthW(hwnd)
    title = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, title, n + 1)
    pid, exe = wt.DWORD(), ""
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        size, buf = wt.DWORD(520), ctypes.create_unicode_buffer(520)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            exe = os.path.basename(buf.value)
        kernel32.CloseHandle(h)
    return {"title": title.value, "exe": exe}


# ---------------------------------------------------------------- game profiles (synced to the PC)

PROFILE_KEYS = {"style", "preset", "sens", "invert", "showScreen", "lstick", "rstick", "bind", "output"}


def _clean_profile(cfg):
    if not isinstance(cfg, dict) or len(json.dumps(cfg)) > 20000:
        raise ValueError("That profile is invalid.")
    cfg = {k: v for k, v in cfg.items() if k in PROFILE_KEYS}
    cfg["bind"] = {str(k)[:12]: str(v)[:20] for k, v in (cfg.get("bind") or {}).items()}
    for v in cfg["bind"].values():
        if v:
            check_binding(v)
    return cfg


def profile_save(pid, name, match, cfg):
    name, match = str(name or "").strip()[:40], str(match or "").strip()[:80]
    if not name:
        raise ValueError("Give the profile a name.")
    cfg = _clean_profile(cfg)
    with config_lock:
        existing = next((p for p in CONFIG["profiles"] if p["id"] == pid), None)
        if existing:
            existing.update(name=name, match=match, cfg=cfg)
        else:
            if len(CONFIG["profiles"]) >= 50:
                raise ValueError("That's a lot of profiles. Delete one first.")
            existing = {"id": secrets.token_hex(4), "name": name, "match": match, "cfg": cfg}
            CONFIG["profiles"].append(existing)
        save_config()
    return existing["id"]


def profile_delete(pid):
    with config_lock:
        CONFIG["profiles"] = [p for p in CONFIG["profiles"] if p["id"] != pid]
        save_config()


# ---------------------------------------------------------------- wake another PC (magic packet)

MAC_RE = re.compile(r"[0-9A-Fa-f]{2}([:-]?[0-9A-Fa-f]{2}){5}")


def wake_add(name, mac):
    if not MAC_RE.fullmatch(str(mac).strip()):
        raise ValueError("That isn't a MAC address. It looks like AA:BB:CC:DD:EE:FF.")
    raw = re.sub(r"[^0-9A-Fa-f]", "", mac).upper()
    with config_lock:
        CONFIG["wake_targets"].append({"id": secrets.token_hex(4), "name": str(name or "PC").strip()[:40] or "PC",
                                       "mac": ":".join(raw[i:i + 2] for i in range(0, 12, 2))})
        save_config()


def wake_delete(wid):
    with config_lock:
        CONFIG["wake_targets"] = [w for w in CONFIG["wake_targets"] if w["id"] != wid]
        save_config()


def wake_send(wid):
    t = next((w for w in CONFIG["wake_targets"] if w["id"] == wid), None)
    if not t:
        raise ValueError("PC not found")
    mac = bytes.fromhex(t["mac"].replace(":", ""))
    packet = b"\xff" * 6 + mac * 16
    targets = {("255.255.255.255", 9), ("255.255.255.255", 7)}
    ip = lan_ip()
    if ip.count(".") == 3 and not ip.startswith("127."):
        targets.add((ip.rsplit(".", 1)[0] + ".255", 9))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for addr in targets:
            try:
                s.sendto(packet, addr)
            except OSError:
                pass
    security.audit("wake_sent", pc=t["name"])


# ---------------------------------------------------------------- files

def _safe_name(name):
    name = os.path.basename(urllib.parse.unquote(name or "")).strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(". ") or "file"
    return name[:150]


def _unique_path(directory, name):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stem, ext = os.path.splitext(name)
    p, n = directory / name, 1
    while p.exists():
        p = directory / f"{stem} ({n}){ext}"
        n += 1
    return p


def _roots():
    """Folders the phone may open: the Kovrel folder, plus any shared on the PC."""
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    roots = [{"name": "Kovrel folder", "path": SHARED_DIR}]
    for f in CONFIG["shared_folders"]:
        if Path(f["path"]).is_dir():
            roots.append({"name": f["name"], "path": Path(f["path"])})
    return roots


def _in_root(root, rel=""):
    """(base, path) for a path inside a shared folder. Anything that resolves outside it
    (.., symlinks, absolute paths) is refused."""
    roots = _roots()
    idx = int(root)
    if not 0 <= idx < len(roots):
        raise ValueError("that folder isn't shared")
    base = roots[idx]["path"].resolve()
    p = (base / str(rel or "").lstrip("/\\")).resolve()
    if p != base and not p.is_relative_to(base):
        raise ValueError("outside the shared folder")
    return base, p


def browse(root=0, rel=""):
    base, p = _in_root(root, rel)
    if not p.is_dir():
        raise ValueError("folder not found")
    dirs, files = [], []
    try:
        with os.scandir(p) as it:
            for e in it:
                if e.name.startswith((".", "$")):
                    continue
                try:
                    st = e.stat()
                    is_dir = e.is_dir()
                except OSError:
                    continue
                (dirs if is_dir else files).append(
                    {"name": e.name, "dir": is_dir, "size": 0 if is_dir else st.st_size, "time": int(st.st_mtime)})
    except PermissionError:
        raise ValueError("no permission to open that folder")
    dirs.sort(key=lambda x: x["name"].lower())
    files.sort(key=lambda x: -x["time"])
    entries = dirs + files
    return {"roots": [r["name"] for r in _roots()], "root": int(root),
            "path": "" if p == base else str(p.relative_to(base)).replace("\\", "/"),
            "entries": entries[:500], "more": len(entries) > 500}


def shared_file(root, rel):
    _, p = _in_root(root, rel)
    if not p.is_file():
        raise ValueError("file not found")
    return p


# One-time download links: the phone's browser saves big files itself (straight to storage)
# instead of the page holding the whole file in memory first. Each link works once, for a minute.
_tickets, _tickets_lock = {}, threading.Lock()
TICKET_SECONDS = 60


def download_ticket(root, rel, device):
    p = shared_file(root, rel)
    ticket = secrets.token_urlsafe(24)
    now = time.time()
    with _tickets_lock:
        for k in [k for k, v in _tickets.items() if v["until"] < now]:
            del _tickets[k]
        _tickets[ticket] = {"path": p, "until": now + TICKET_SECONDS, "device": device}
    return ticket


def use_ticket(ticket):
    with _tickets_lock:
        t = _tickets.pop(ticket, None)
    return t if t and t["until"] >= time.time() and t["path"].is_file() else None


def shared_folder_add(path, name=None):
    p = Path(path)
    if not p.is_dir():
        raise ValueError("that folder doesn't exist")
    with config_lock:
        if any(Path(f["path"]) == p for f in CONFIG["shared_folders"]):
            return
        CONFIG["shared_folders"].append({"name": (name or p.name or str(p))[:40], "path": str(p)})
        save_config()
    security.audit("folder_shared", path=p)


def shared_folder_remove(path):
    with config_lock:
        CONFIG["shared_folders"] = [f for f in CONFIG["shared_folders"] if f["path"] != str(path)]
        save_config()
    security.audit("folder_unshared", path=path)


# ---------------------------------------------------------------- shortcuts (custom buttons)

SHORTCUT_TYPES = {"url", "keys", "text", "macro"}
MACRO_MAX_STEPS, MACRO_MAX_TOTAL_WAIT, MACRO_MAX_ONE_WAIT = 60, 60.0, 30.0
_macro = {"thread": None, "stop": threading.Event()}


def parse_macro(text):
    """One step per line:  keys ctrl+l | type hello | wait 500 (ms) or wait 2s | open <url/app> | click left"""
    steps, total = [], 0.0
    for n, raw in enumerate(str(text).splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cmd, _, arg = line.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        if cmd == "keys":
            if not arg:
                raise ValueError(f"Line {n}: 'keys' needs a combo, like keys ctrl+l")
            try:
                [_vk(p) for p in arg.split("+")]
            except ValueError as e:
                raise ValueError(f"Line {n}: {e}")
            steps.append(("keys", arg))
        elif cmd == "type":
            if not arg:
                raise ValueError(f"Line {n}: 'type' needs some text")
            steps.append(("type", arg))
        elif cmd == "wait":
            m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(ms|s)?", arg.lower())
            if not m:
                raise ValueError(f"Line {n}: 'wait' needs a time, like wait 500 or wait 2s")
            secs = float(m.group(1)) / (1 if m.group(2) == "s" else 1000)
            if secs > MACRO_MAX_ONE_WAIT:
                raise ValueError(f"Line {n}: a single wait can be at most {int(MACRO_MAX_ONE_WAIT)} seconds")
            total += secs
            steps.append(("wait", secs))
        elif cmd == "open":
            if not arg:
                raise ValueError(f"Line {n}: 'open' needs a website or app")
            steps.append(("open", arg))
        elif cmd == "click":
            if (arg or "left") not in ("left", "right", "middle", "double"):
                raise ValueError(f"Line {n}: click what? left, right, middle or double")
            steps.append(("click", arg or "left"))
        else:
            raise ValueError(f"Line {n}: unknown step '{cmd}'. Use keys, type, wait, open or click.")
    if not steps:
        raise ValueError("A macro needs at least one step.")
    if len(steps) > MACRO_MAX_STEPS:
        raise ValueError(f"A macro can have at most {MACRO_MAX_STEPS} steps.")
    if total > MACRO_MAX_TOTAL_WAIT:
        raise ValueError(f"All the waits together can be at most {int(MACRO_MAX_TOTAL_WAIT)} seconds.")
    return steps


def _run_macro(steps):
    stop = _macro["stop"]
    for kind, arg in steps:
        if stop.is_set():
            break
        if kind == "keys":
            press_combo(arg)
        elif kind == "type":
            type_text(arg)
        elif kind == "wait":
            stop.wait(arg)
        elif kind == "open":
            os.startfile(arg)
        elif kind == "click":
            mouse_button("left", "double") if arg == "double" else mouse_button(arg)
        time.sleep(0.03)


def macro_start(steps):
    t = _macro["thread"]
    if t and t.is_alive():
        raise ValueError("A macro is already running. Stop it first.")
    _macro["stop"].clear()
    _macro["thread"] = threading.Thread(target=_run_macro, args=(steps,), daemon=True)
    _macro["thread"].start()


def macro_stop():
    _macro["stop"].set()


def shortcut_add(name, kind, value, icon="star"):
    if kind not in SHORTCUT_TYPES:
        raise ValueError("unknown button type")
    name = str(name).strip()[:40]
    value = str(value).strip()[:4000 if kind == "macro" else 2000]
    if not name or not value:
        raise ValueError("name and action are both needed")
    if kind == "keys":
        [_vk(p) for p in value.split("+")]  # validate now, not when tapped
    elif kind == "macro":
        parse_macro(value)
    with config_lock:
        CONFIG["shortcuts"].append({"id": secrets.token_hex(4), "name": name, "type": kind,
                                    "value": value, "icon": str(icon)[:20]})
        save_config()


def shortcut_delete(sid):
    with config_lock:
        CONFIG["shortcuts"] = [s for s in CONFIG["shortcuts"] if s["id"] != sid]
        save_config()


def shortcut_run(sid=None, name=None):
    s = next((s for s in CONFIG["shortcuts"] if (sid and s["id"] == sid)
              or (name and s["name"].lower() == str(name).lower())), None)
    if not s:
        raise ValueError("button not found")
    if s["type"] == "keys":
        press_combo(s["value"])
    elif s["type"] == "text":
        type_text(s["value"])
    elif s["type"] == "macro":
        macro_start(parse_macro(s["value"]))
    else:
        os.startfile(s["value"])
    return s["name"]


# ---------------------------------------------------------------- PC sound streaming

def audio_stream(write):
    """Streams the PC's speaker output as raw 16-bit PCM until the client disconnects."""
    import pyaudiowpatch as pa

    p = pa.PyAudio()
    stream = None
    try:
        dev = p.get_default_wasapi_loopback()
        rate, ch = int(dev["defaultSampleRate"]), int(dev["maxInputChannels"])
        q = queue.Queue(maxsize=100)

        def cb(data, frames, info, status):
            try:
                q.put_nowait(data)
            except queue.Full:
                pass
            return None, pa.paContinue

        stream = p.open(format=pa.paInt16, channels=ch, rate=rate, input=True,
                        input_device_index=dev["index"], frames_per_buffer=rate // 50, stream_callback=cb)
        write(None, {"X-Rate": str(rate), "X-Channels": str(ch)})  # headers first
        silence = bytes(rate // 10 * ch * 2)
        while True:
            try:
                data = q.get(timeout=0.1)
            except queue.Empty:
                data = silence  # Windows sends nothing while silent; keep the timeline going
            write(data, None)
    finally:
        if stream:
            stream.stop_stream()
            stream.close()
        p.terminate()


# ---------------------------------------------------------------- devices

def connected_devices():
    """Phones that have talked to the PC in the last 15 seconds (not automation keys)."""
    return [{"name": d["name"], "ip": d["ip"], "last_seen": d["last_seen"]}
            for d in security.devices() if d["online"] and d["scope"] == "full"]


# ---------------------------------------------------------------- API

import vigem

PAD = {"last": 0.0, "active": False}
PAD_AXES = ("lx", "ly", "rx", "ry", "lt", "rt")


def pad_update(body):
    """One controller tick from the phone: buttons held + analog sticks/triggers."""
    kind = "ds4" if body.get("kind") == "ds4" else "x360"
    state = {"buttons": [str(b)[:8] for b in list(body.get("buttons") or [])[:20]]}
    for k in PAD_AXES:
        state[k] = max(-1.0, min(1.0, float(body.get(k) or 0)))
    vigem.update(kind, state)
    PAD["last"] = time.time()
    PAD["active"] = bool(state["buttons"]) or any(abs(state[k]) > 0.05 for k in PAD_AXES)


def pad_release():
    PAD["active"] = False
    vigem.close()  # unplugs it, so games don't keep a phantom controller around


def _pad_watchdog():
    while True:  # a phone that went quiet must never leave a trigger or stick held down
        time.sleep(0.5)
        if PAD["active"] and time.time() - PAD["last"] > 1.5:
            PAD["active"] = False
            vigem.neutral()


threading.Thread(target=_pad_watchdog, daemon=True).start()


def set_paused(on):
    """The panic switch: while paused, phones can still see the PC but can't control anything."""
    PAUSED["on"] = bool(on)
    if on:
        release_all_keys()
        pad_release()
        macro_stop()
    security.audit("remote_control_paused" if on else "remote_control_resumed")


def handle_api(cmd, body, dev=None):
    who = (dev or {}).get("name", "?")
    if cmd == "ping":
        return {"ok": True, "host": socket.gethostname(), "version": VERSION, "paused": PAUSED["on"],
                "remote": bool((dev or {}).get("remote"))}
    if cmd == "move":
        mouse_move(body.get("dx", 0), body.get("dy", 0))
    elif cmd == "click":
        held = mods_down(body.get("mods"))
        try:
            mouse_button(body.get("button", "left"), body.get("action", "click"))
        finally:
            mods_up(held)
    elif cmd == "scroll":
        mouse_scroll(body.get("dy", 0), body.get("dx", 0))
    elif cmd == "type":
        type_text(str(body.get("text", ""))[:5000])
    elif cmd == "key":
        press_combo(str(body.get("combo", "")))
    elif cmd == "game":
        # One message per controller tick: presses, releases, still-held inputs and camera look.
        for k in body.get("up", []):
            key_state(str(k), False)
        for k in body.get("down", []):
            key_state(str(k), True)
        refresh_held(str(k) for k in body.get("held", []))
        look(*(body.get("look") or (0, 0)))
    elif cmd == "release_all":
        release_all_keys()
        pad_release()
    elif cmd == "pad":
        pad_update(body)
    elif cmd == "pad_status":
        ok, why = vigem.available()
        return {"ok": True, "available": ok, "reason": why}
    elif cmd == "windows":
        return {"ok": True, "windows": list_windows()}
    elif cmd == "window":
        if body.get("action") == "close":
            security.audit("window_closed", device=who)
        window_action(body["id"], body["action"])
    elif cmd == "apps":
        return {"ok": True, "apps": list(APPS)}
    elif cmd == "launch":
        target = APPS.get(body.get("name"))
        if not target:
            raise ValueError("unknown app")
        os.startfile(target)
    elif cmd == "power":
        security.audit("power", action=body.get("action"), device=who)
        power(body.get("action"))
    elif cmd == "tap":
        held = mods_down(body.get("mods"))
        try:
            tap_screen(float(body["x"]), float(body["y"]), body.get("button", "left"),
                       body.get("action", "click"), body.get("monitor", 0))
        finally:
            mods_up(held)
    elif cmd == "monitors":
        return {"ok": True, "monitors": [{"w": m[2], "h": m[3]} for m in list_monitors()]}
    elif cmd == "clipboard_get":
        return {"ok": True, "text": clipboard_get()}
    elif cmd == "clipboard_set":
        clipboard_set(str(body.get("text", ""))[:100000])
    elif cmd == "media":
        return {"ok": True, **media_info(), "timer": timer_info()}
    elif cmd == "volume":
        return {"ok": True, **_on_com(_volume_set, body.get("level"), body.get("muted"))}
    elif cmd == "timer":
        if body.get("minutes"):
            timer_set(body["minutes"], body.get("action", "sleep"))
        else:
            timer_cancel()
        return {"ok": True, "timer": timer_info()}
    elif cmd == "status":
        return {"ok": True, **pc_status()}
    elif cmd == "notifications":
        return {"ok": True, "items": notifications()}
    elif cmd == "browse":
        return {"ok": True, **browse(body.get("root", 0), body.get("path", ""))}
    elif cmd == "shortcuts":
        return {"ok": True, "items": CONFIG["shortcuts"]}
    elif cmd == "shortcut_add":
        shortcut_add(body.get("name"), body.get("type"), body.get("value"), body.get("icon", "star"))
        return {"ok": True, "items": CONFIG["shortcuts"]}
    elif cmd == "shortcut_delete":
        shortcut_delete(body.get("id"))
        return {"ok": True, "items": CONFIG["shortcuts"]}
    elif cmd == "shortcut_run":
        name = shortcut_run(body.get("id"), body.get("name"))
        return {"ok": True, "name": name}
    elif cmd == "macro_stop":
        macro_stop()
    elif cmd == "devices":
        return {"ok": True, "devices": connected_devices()}
    elif cmd == "logout":
        security.revoke(dev["id"])
    elif cmd == "rename_device":
        security.rename_device(dev["id"], body.get("name"))
    elif cmd == "mixer":
        return {"ok": True, "apps": _on_com(_mixer_list)}
    elif cmd == "mixer_set":
        _on_com(_mixer_set, body.get("name"), body.get("level"), body.get("muted"))
    elif cmd == "brightness":
        displays = _on_display(_brightness_list)
        return {"ok": True, "displays": displays,
                "reason": "" if displays else "None of your monitors answered. Not every monitor lets Windows "
                                              "change its brightness, and some only do when DDC/CI is turned on "
                                              "in the monitor's own menu."}
    elif cmd == "brightness_set":
        _on_display(_brightness_set, body.get("id"), body.get("level"))
    elif cmd == "processes":
        return {"ok": True, "items": processes(query=body.get("q"))}
    elif cmd == "process_kill":
        n = kill_process(body.get("name"))
        security.audit("program_closed", name=body.get("name"), count=n, device=who)
        return {"ok": True, "closed": n}
    elif cmd == "download_link":
        return {"ok": True, "url": "/dl/" + download_ticket(body.get("root", 0), body.get("path", ""), who)}
    elif cmd == "active_window":
        return {"ok": True, **active_window()}
    elif cmd == "profiles":
        return {"ok": True, "items": CONFIG["profiles"]}
    elif cmd == "profile_save":
        pid = profile_save(body.get("id"), body.get("name"), body.get("match"), body.get("cfg"))
        return {"ok": True, "id": pid, "items": CONFIG["profiles"]}
    elif cmd == "profile_delete":
        profile_delete(body.get("id"))
        return {"ok": True, "items": CONFIG["profiles"]}
    elif cmd == "wake_list":
        return {"ok": True, "items": CONFIG["wake_targets"]}
    elif cmd == "wake_add":
        wake_add(body.get("name"), body.get("mac"))
        return {"ok": True, "items": CONFIG["wake_targets"]}
    elif cmd == "wake_delete":
        wake_delete(body.get("id"))
        return {"ok": True, "items": CONFIG["wake_targets"]}
    elif cmd == "wake":
        wake_send(body.get("id"))
    else:
        raise ValueError(f"unknown command: {cmd}")
    return {"ok": True}


PUBLIC_COMMANDS = {"info", "login", "login_status"}   # usable before signing in
AUTOMATION_COMMANDS = {"ping", "shortcut_run"}        # the only things an automation key may do
# While control is paused phones can still look (screen, sound, status, lists) but can't change anything.
WHILE_PAUSED = {"ping", "devices", "logout", "rename_device", "video", "screenshot", "audio", "monitors",
                "status", "notifications", "media", "mixer", "brightness", "processes", "browse", "profiles",
                "shortcuts", "wake_list", "active_window", "pad_status", "windows", "apps"}


STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/manifest.json": ("manifest.json", "application/manifest+json"),
    "/favicon.png": ("favicon.png", "image/png"),
    "/icon-180.png": ("icon-180.png", "image/png"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/icon-maskable.png": ("icon-maskable.png", "image/png"),
    # iPhones also look for the home-screen icon at these fixed addresses; without them
    # "Add to Home Screen" can fall back to a plain letter tile.
    "/apple-touch-icon.png": ("icon-180.png", "image/png"),
    "/apple-touch-icon-precomposed.png": ("icon-180.png", "image/png"),
}


# The home-screen icon goes inside the page itself. iPhones fetch a linked icon separately, and with
# the PC's own (self-signed) certificate that fetch fails, so the phone would show a plain letter instead.
try:
    import base64
    APPLE_ICON_INLINE = b'href="data:image/png;base64,' + base64.b64encode((WEB / "icon-180.png").read_bytes()) + b'"'
except OSError:
    APPLE_ICON_INLINE = b'href="/apple-touch-icon.png"'

_conns, _conns_lock = set(), threading.Lock()


def close_all_connections():
    """Drops every open phone connection (used when the connection type changes, so nothing
    keeps running over the old, possibly unencrypted, channel)."""
    with _conns_lock:
        socks = list(_conns)
    for s in socks:
        try:
            s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            s.close()
        except OSError:
            pass


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.1 lets the phone reuse one connection instead of opening a new one per
    # tap or controller tick, which noticeably cuts input lag.
    protocol_version = "HTTP/1.1"
    timeout = 20  # drop connections a sleeping/minimized phone abandoned

    def log_message(self, fmt, *args):
        pass  # keep quiet (mouse moves are very chatty)

    def setup(self):
        with _conns_lock:
            _conns.add(self.request)
        if isinstance(self.request, ssl.SSLSocket):
            # The handshake happens here, in this connection's own thread, so one slow or
            # confused browser can never stall everyone else's connections.
            self.request.settimeout(10)
            self.request.do_handshake()
        super().setup()

    def finish(self):
        with _conns_lock:
            _conns.discard(self.request)
        try:
            super().finish()
        except OSError:
            pass

    def handle(self):
        try:
            super().handle()
        except (ConnectionError, TimeoutError, OSError):
            pass  # phone went away mid-request (screen off, app switched); nothing to do

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    # Nothing is loaded from the internet: the page, its fonts and icons all come from this PC.
    CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
           "img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; "
           "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

    def _send(self, code, data, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", self.CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _send_file(self, p, device):
        security.audit("file_downloaded", file=p.name, device=device)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(p.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(p.name))
        self.end_headers()
        with open(p, "rb") as f:
            shutil.copyfileobj(f, self.wfile, 1 << 20)

    def do_GET(self):
        if not self._zone_ok():
            return
        path = self.path.split("?")[0]
        if path.startswith("/dl/"):
            if PAUSED["on"]:
                return self._json(423, {"ok": False, "error": "Remote control is paused on the PC."})
            t = use_ticket(path[4:])
            if not t:
                return self._json(404, {"ok": False, "error": "That download link has expired. Tap the file again."})
            return self._send_file(t["path"], t["device"])
        if path in STATIC:
            fname, ctype = STATIC[path]
            try:
                data = (WEB / fname).read_bytes()
                if fname == "index.html":
                    data = data.replace(b'href="/apple-touch-icon.png"', APPLE_ICON_INLINE)
                self._send(200, data, ctype)
            except OSError:
                self._json(404, {"ok": False, "error": "not found"})
        else:
            self._json(404, {"ok": False, "error": "not found"})

    _unread = True  # becomes False once this request's body has been read

    def _reject(self, code, error):
        # Only drop the connection when the body is still unread; otherwise keep it, because
        # a game controller sends ~60 messages a second and must not reconnect after an error.
        if self._unread:
            self.close_connection = True
        self._json(code, {"ok": False, "error": error})

    MAX_BODY = 1 << 20  # 1 MB for everything except file uploads
    remote = False      # this connection comes from away from home (Tailscale)

    def _zone_ok(self):
        """Refuses the open internet always, and Tailscale while remote access is off."""
        zone = client_zone(self.client_address[0])
        self.remote = zone == "tailnet"
        if zone == "public" or (self.remote and not security.get("remote")):
            self.close_connection = True
            self._json(403, {"ok": False, "error": "Remote access is turned off on the PC." if self.remote
                             else "Kovrel only answers on your home network or your Tailscale network."})
            return False
        return True

    def _public(self, cmd, raw):
        """Commands usable before signing in: what kind of secret to ask for, and logging in."""
        ip = self.client_address[0]
        if cmd == "info":
            return self._json(200, {"ok": True, "kind": security.secret_kind(), "version": VERSION,
                                    "host": socket.gethostname(),
                                    "https": getattr(self.server, "scheme", "http") == "https"})
        try:
            body = json.loads(raw or b"{}")
            if cmd == "login":
                res = security.login(str(body.get("secret", ""))[:200], str(body.get("device", "")),
                                     body.get("name"), ip, remote=self.remote)
            else:
                res = security.login_status(str(body.get("req", ""))[:40], str(body.get("device", "")))
        except security.AuthError as e:
            return self._json(e.status, {"ok": False, "error": e.message, "retry": e.retry})
        self._json(200, {"ok": True, "host": socket.gethostname(), **res})

    def do_POST(self):
        self._unread = True  # this handler object serves many requests on one connection
        if not self._zone_ok():
            return
        if not self.path.startswith("/api/"):
            return self._reject(404, "not found")
        cmd = self.path[5:].split("?")[0]
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length < 0:
                return self._reject(400, "Bad request.")
            raw = b""
            if cmd != "upload":  # read the small JSON body up front so errors can keep the connection alive
                if length > (4096 if cmd in PUBLIC_COMMANDS else self.MAX_BODY):
                    return self._reject(413, "That request is too big.")
                raw = self.rfile.read(length) if length else b""
                self._unread = False
            if cmd in PUBLIC_COMMANDS:
                return self._public(cmd, raw)
            dev = security.authenticate(self.headers.get("X-Token", ""), self.client_address[0], remote=self.remote)
            if not dev:
                return self._reject(401, "Please sign in again.")
            if dev.get("scope") == "automation" and cmd not in AUTOMATION_COMMANDS:
                return self._reject(403, "This key can only run your buttons.")
            if PAUSED["on"] and cmd not in WHILE_PAUSED:
                return self._reject(423, "Remote control is paused on the PC.")
            if cmd == "upload":
                return self._upload(length, dev)
            body = json.loads(raw or b"{}")
            if cmd == "video":
                return self._video(body)
            if cmd == "screenshot":
                data = screenshot_png(body.get("monitor", 0))
                security.audit("screenshot", device=dev["name"])
                return self._send(200, data, "image/png",
                                  {"Content-Disposition": 'attachment; filename="kovrel-screenshot.png"'})
            if cmd == "audio":
                return self._audio()
            self._json(200, handle_api(cmd, body, {**dev, "remote": self.remote}))
        except (ConnectionError, TimeoutError):
            raise
        except Exception as e:
            self._reject(400, str(e))  # e.g. a failed upload may leave its body unread

    def _upload(self, length, dev):
        if length <= 0:
            raise ValueError("empty file")
        _, folder = _in_root(self.headers.get("X-Root", "0"), urllib.parse.unquote(self.headers.get("X-Dir", "")))
        if not folder.is_dir():
            raise ValueError("folder not found")
        path = _unique_path(folder, _safe_name(self.headers.get("X-Filename", "file")))
        security.audit("file_uploaded", file=path.name, device=dev["name"])
        part = path.with_name(path.name + ".part")
        try:
            with open(part, "wb") as f:
                left = length
                while left:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        raise ConnectionError("upload interrupted")
                    f.write(chunk)
                    left -= len(chunk)
            part.replace(path)
        finally:
            part.unlink(missing_ok=True)
        self._json(200, {"ok": True, "name": path.name})

    def _stream_writer(self):
        """write(data, headers) for endless responses (video, audio): headers first, then chunks."""
        self.close_connection = True

        def write(data, headers):
            if headers is not None:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")  # endless stream: no length, so no reuse
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
            if data:
                self.wfile.write(data)
                self.wfile.flush()

        return write

    def _audio(self):
        audio_stream(self._stream_writer())

    def _video(self, body):
        try:  # a small send buffer makes a slow phone push back quickly instead of piling up old frames
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 131072)
        except OSError:
            pass
        video_stream(self._stream_writer(), body.get("width", 1280), body.get("monitor", 0),
                     body.get("quality", 65), max(5, min(int(body.get("fps", 30)), 60)))


# ---------------------------------------------------------------- running

def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packets sent; just picks the LAN interface
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


# ---------------------------------------------------------------- where a connection comes from
# Kovrel never answers the open internet. Home-network addresses are always allowed; Tailscale
# addresses (100.64.0.0/10, a private network between your own devices) only when remote access
# is turned on. Port-forwarding Kovrel on a router therefore does nothing, on purpose.

_TAILNET = ipaddress.ip_network("100.64.0.0/10")


def client_zone(ip):
    """'local' (this PC), 'lan' (home network), 'tailnet' (Tailscale) or 'public'."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return "public"
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    if a.is_loopback:
        return "local"
    if a.version == 4 and a in _TAILNET:
        return "tailnet"
    if a.is_private or a.is_link_local:
        return "lan"
    return "public"


# ---------------------------------------------------------------- Tailscale (remote access)

TS_CERT_FILE, TS_KEY_FILE = CONFIG_DIR / "tailscale-cert.pem", CONFIG_DIR / "tailscale-key.pem"


def _tailscale_exe():
    found = shutil.which("tailscale")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")):
        p = Path(base) / "Tailscale" / "tailscale.exe"
        if base and p.is_file():
            return str(p)
    return None


def parse_tailscale_status(data):
    """Picks what Kovrel needs out of `tailscale status --json`."""
    me = data.get("Self") or {}
    ips = [i for i in me.get("TailscaleIPs") or [] if "." in i]
    return {"installed": True, "running": data.get("BackendState") == "Running" and bool(ips),
            "ip": ips[0] if ips else "", "name": (me.get("DNSName") or "").rstrip("."),
            "cert_domains": [d.rstrip(".") for d in data.get("CertDomains") or []], "error": ""}


def tailscale_status():
    exe = _tailscale_exe()
    if not exe:
        return {"installed": False, "running": False, "ip": "", "name": "", "cert_domains": [], "error": ""}
    try:
        r = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=6, creationflags=NO_WINDOW)
        return parse_tailscale_status(json.loads(r.stdout or "{}"))
    except Exception as e:
        return {"installed": True, "running": False, "ip": "", "name": "", "cert_domains": [], "error": str(e)}


def tailscale_get_cert():
    """Asks Tailscale for a real, trusted HTTPS certificate for this PC's Tailscale name
    (needs "HTTPS certificates" turned on in the Tailscale admin console). Returns the name."""
    st = tailscale_status()
    if not st["running"]:
        raise ValueError("Tailscale isn't connected on this PC.")
    if not st["cert_domains"]:
        raise ValueError("Turn on HTTPS certificates in the Tailscale admin console "
                         "(login.tailscale.com › DNS › HTTPS Certificates), then try again.")
    name = st["cert_domains"][0]
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([_tailscale_exe(), "cert", "--cert-file", str(TS_CERT_FILE), "--key-file", str(TS_KEY_FILE), name],
                       capture_output=True, text=True, timeout=90, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise ValueError((r.stderr or r.stdout or "Tailscale couldn't make the certificate.").strip()[:300])
    security.audit("tailscale_certificate", name=name)
    return name


def tailscale_cert_info():
    """(name, days_left) for a saved Tailscale certificate, or (None, 0)."""
    if not (TS_CERT_FILE.is_file() and TS_KEY_FILE.is_file()):
        return None, 0
    try:
        import datetime
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(TS_CERT_FILE.read_bytes())
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
        left = (cert.not_valid_after_utc - datetime.datetime.now(datetime.timezone.utc)).days
        return (names[0] if names else None), left
    except Exception:
        return None, 0


def remote_url(st=None):
    """The address a phone uses away from home, or '' when remote access isn't possible right now.
    st: a tailscale_status() result, if you already have one."""
    st = st or tailscale_status()
    if not st["running"]:
        return ""
    name, left = tailscale_cert_info()
    if security.get("https") and name and left > 0:
        return f"https://{name}:{PORT}"
    return f"{'https' if security.get('https') else 'http'}://{st['ip']}:{PORT}"


# ---------------------------------------------------------------- update check
# Asks GitHub for the newest release. Nothing about the PC or its user is sent; the download
# itself is left to the person (it opens the release page), so nothing installs by itself.

RELEASES_API = "https://api.github.com/repos/Nyxaliths/kovrel/releases/latest"
RELEASES_PAGE = "https://github.com/Nyxaliths/kovrel/releases/latest"


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3]) or (0,)


def check_for_update(current=VERSION, fetch=None):
    """(newer_version, page_url) when GitHub has a newer release, else None."""
    if fetch is None:
        import urllib.request

        def fetch():
            req = urllib.request.Request(RELEASES_API, headers={"Accept": "application/vnd.github+json",
                                                                "User-Agent": f"Kovrel/{VERSION}"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read(200_000))
    data = fetch()
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "").lstrip("vV")
    if tag and version_tuple(tag) > version_tuple(current):
        url = str(data.get("html_url") or "")
        return tag, url if url.startswith("https://github.com/") else RELEASES_PAGE
    return None


CERT_FILE, KEY_FILE = CONFIG_DIR / "kovrel-cert.pem", CONFIG_DIR / "kovrel-key.pem"


def ensure_cert():
    """Makes (or renews) a self-signed certificate for this PC. It lists this PC's name and
    current network address, so it's regenerated if the address changes or it's nearly expired."""
    import datetime
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    ips = {lan_ip(), "127.0.0.1"}
    names = {socket.gethostname(), "localhost"}
    if CERT_FILE.exists() and KEY_FILE.exists():
        try:
            cert = x509.load_pem_x509_certificate(CERT_FILE.read_bytes())
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            have = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
            soon = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)
            if ips <= have and cert.not_valid_after_utc > soon:
                return CERT_FILE, KEY_FILE
        except Exception:
            pass
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Kovrel"), x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Kovrel")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=365))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in sorted(names)]
                                                       + [x509.IPAddress(ipaddress.ip_address(i)) for i in sorted(ips)]), False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
            .sign(key, hashes.SHA256()))
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    security.audit("https_certificate_created")
    return CERT_FILE, KEY_FILE


class KovrelServer(ThreadingHTTPServer):
    ssl_context = None

    def get_request(self):
        sock, addr = self.socket.accept()
        if self.ssl_context:  # handshake is finished later, in the connection's own thread
            sock = self.ssl_context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
        return sock, addr

    def handle_error(self, request, client_address):
        pass  # failed handshakes and dropped phones are normal; nothing useful to print


def make_server(port=PORT, local=False, https=None):
    """https=None follows the saved setting."""
    use_https = security.get("https") if https is None else https
    server = KovrelServer(("127.0.0.1" if local else "0.0.0.0", port), Handler)
    if use_https:
        def context(cert, key):
            c = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            c.minimum_version = ssl.TLSVersion.TLSv1_2
            c.load_cert_chain(cert, key)
            return c
        ctx = context(*ensure_cert())
        ts_name, ts_left = tailscale_cert_info()
        if ts_name and ts_left > 0:
            # A phone that asks for this PC by its Tailscale name gets Tailscale's trusted
            # certificate (no warning); everyone else gets the PC's own one.
            try:
                ts_ctx = context(TS_CERT_FILE, TS_KEY_FILE)

                def pick(sock, name, _ctx):
                    if name and name.lower() == ts_name.lower():
                        sock.context = ts_ctx
                ctx.sni_callback = pick
            except (OSError, ssl.SSLError):
                pass
        server.ssl_context = ctx
    server.scheme = "https" if use_https else "http"
    return server


def main():
    ap = argparse.ArgumentParser(description="Control this PC from a browser.")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--local", action="store_true", help="only allow this PC (localhost)")
    args = ap.parse_args()

    def ask(req):  # console server: approve new devices right here
        def run():
            ans = input(f"\n  New device '{req['name']}' ({req['ip']}) wants to connect. Allow? [y/N] ")
            (security.approve if ans.strip().lower() == "y" else security.deny)(req["id"])
        threading.Thread(target=run, daemon=True).start()
    security.approval_callback = ask

    server = make_server(args.port, args.local)
    pin = get_pin()
    print("=" * 50)
    print("  Kovrel is running")
    print(f"  This PC:  {server.scheme}://localhost:{args.port}")
    if not args.local:
        print(f"  Phone:    {server.scheme}://{lan_ip()}:{args.port}")
    print(f"  {'PIN' if pin else 'Password'}:      {pin or '(the custom password you set)'}")
    print("  New devices need your OK here. Press Ctrl+C to stop.")
    print("=" * 50)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        release_all_keys()
        pad_release()


if __name__ == "__main__":
    main()
