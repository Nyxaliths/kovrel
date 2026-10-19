"""Optional virtual game controller (Xbox 360 or DualShock 4) for Kovrel.

Talks to the free ViGEmBus driver through ViGEmClient.dll. Kovrel never installs the
driver: if it isn't there, available() explains how to get it and everything else keeps working.
"""

import ctypes
import sys
import threading
from pathlib import Path

ERR_BUS_NOT_FOUND = 0xE0000001
OK = 0x20000000

# Xbox 360 buttons
X_UP, X_DOWN, X_LEFT, X_RIGHT = 0x0001, 0x0002, 0x0004, 0x0008
X_START, X_BACK = 0x0010, 0x0020
X_LB, X_RB = 0x0100, 0x0200
X_A, X_B, X_X, X_Y = 0x1000, 0x2000, 0x4000, 0x8000
# DualShock 4 buttons
D_SQUARE, D_CROSS, D_CIRCLE, D_TRIANGLE = 0x0010, 0x0020, 0x0040, 0x0080
D_L1, D_R1, D_L2, D_R2 = 0x0100, 0x0200, 0x0400, 0x0800
D_SHARE, D_OPTIONS = 0x1000, 0x2000
HAT = {(0, -1): 0, (1, -1): 1, (1, 0): 2, (1, 1): 3, (0, 1): 4, (-1, 1): 5, (-1, 0): 6, (-1, -1): 7, (0, 0): 8}

X360 = {"a": X_A, "b": X_B, "x": X_X, "y": X_Y, "lb": X_LB, "rb": X_RB, "view": X_BACK, "menu": X_START,
        "up": X_UP, "down": X_DOWN, "left": X_LEFT, "right": X_RIGHT}
DS4 = {"a": D_CROSS, "b": D_CIRCLE, "x": D_SQUARE, "y": D_TRIANGLE, "lb": D_L1, "rb": D_R1,
       "view": D_SHARE, "menu": D_OPTIONS}


class XUSB_REPORT(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("wButtons", ctypes.c_ushort), ("bLeftTrigger", ctypes.c_ubyte), ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", ctypes.c_short), ("sThumbLY", ctypes.c_short),
                ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short)]


class DS4_REPORT(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("bThumbLX", ctypes.c_ubyte), ("bThumbLY", ctypes.c_ubyte),
                ("bThumbRX", ctypes.c_ubyte), ("bThumbRY", ctypes.c_ubyte),
                ("wButtons", ctypes.c_ushort), ("bSpecial", ctypes.c_ubyte),
                ("bTriggerL", ctypes.c_ubyte), ("bTriggerR", ctypes.c_ubyte)]


def _dll_path():
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for p in (base / "ViGEmClient.dll", base / "vendor" / "ViGEmClient.dll"):
        if p.exists():
            return p
    return None


_lock = threading.RLock()
_lib = None
_client = None
_pad = {"kind": None, "target": None}
_load_error = ""


def _load():
    """Loads the DLL and connects to the driver once. Returns None on success, else a message."""
    global _lib, _client, _load_error
    if _client:
        return None
    path = _dll_path()
    if not path:
        _load_error = "The virtual controller helper (ViGEmClient.dll) is missing from this install."
        return _load_error
    lib = ctypes.WinDLL(str(path))
    lib.vigem_alloc.restype = ctypes.c_void_p
    lib.vigem_connect.argtypes = [ctypes.c_void_p]
    lib.vigem_connect.restype = ctypes.c_uint
    lib.vigem_disconnect.argtypes = [ctypes.c_void_p]
    lib.vigem_free.argtypes = [ctypes.c_void_p]
    for fn in ("vigem_target_x360_alloc", "vigem_target_ds4_alloc"):
        getattr(lib, fn).restype = ctypes.c_void_p
    for fn in ("vigem_target_add", "vigem_target_remove"):
        getattr(lib, fn).argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        getattr(lib, fn).restype = ctypes.c_uint
    lib.vigem_target_free.argtypes = [ctypes.c_void_p]
    lib.vigem_target_x360_update.argtypes = [ctypes.c_void_p, ctypes.c_void_p, XUSB_REPORT]
    lib.vigem_target_x360_update.restype = ctypes.c_uint
    lib.vigem_target_ds4_update.argtypes = [ctypes.c_void_p, ctypes.c_void_p, DS4_REPORT]
    lib.vigem_target_ds4_update.restype = ctypes.c_uint
    client = lib.vigem_alloc()
    if not client:
        _load_error = "Couldn't start the virtual controller helper."
        return _load_error
    err = lib.vigem_connect(client)
    if err != OK:
        lib.vigem_free(client)
        _load_error = ("The free ViGEmBus driver isn't installed, so virtual controllers can't be created."
                       if err == ERR_BUS_NOT_FOUND else f"The controller driver refused to connect (code {err:#x}).")
        return _load_error
    _lib, _client, _load_error = lib, client, ""
    return None


def available():
    """(True, "") when a virtual controller can be created, else (False, reason)."""
    with _lock:
        msg = _load()
        return (msg is None), (msg or "")


def _x360_report(state):
    b = sum(X360[k] for k in state["buttons"] if k in X360)
    clamp = lambda v: int(max(-1.0, min(1.0, v)) * 32767)
    lt = 255 if "lt" in state["buttons"] else int(max(0.0, min(1.0, state["lt"])) * 255)
    rt = 255 if "rt" in state["buttons"] else int(max(0.0, min(1.0, state["rt"])) * 255)
    # Xbox sticks use +up, our screen coordinates use +down, so y is flipped.
    return XUSB_REPORT(b, lt, rt, clamp(state["lx"]), clamp(-state["ly"]), clamp(state["rx"]), clamp(-state["ry"]))


def _ds4_report(state):
    b = sum(DS4[k] for k in state["buttons"] if k in DS4)
    dx = ("right" in state["buttons"]) - ("left" in state["buttons"])
    dy = ("down" in state["buttons"]) - ("up" in state["buttons"])
    b |= HAT[(dx, dy)]
    lt = 255 if "lt" in state["buttons"] else int(state["lt"] * 255)
    rt = 255 if "rt" in state["buttons"] else int(state["rt"] * 255)
    if lt > 30:
        b |= D_L2
    if rt > 30:
        b |= D_R2
    byte = lambda v: int(128 + max(-1.0, min(1.0, v)) * 127)  # DS4: 0 is up, 255 is down (same as screen y)
    return DS4_REPORT(byte(state["lx"]), byte(state["ly"]), byte(state["rx"]), byte(state["ry"]), b, 0, lt, rt)


def update(kind, state):
    """kind 'x360' or 'ds4'; state = {buttons: [names], lt, rt, lx, ly, rx, ry} (sticks -1..1, y down)."""
    if kind not in ("x360", "ds4"):
        raise ValueError("Unknown controller type.")
    with _lock:
        msg = _load()
        if msg:
            raise RuntimeError(msg)
        if _pad["kind"] != kind:
            close()
            target = _lib.vigem_target_x360_alloc() if kind == "x360" else _lib.vigem_target_ds4_alloc()
            err = _lib.vigem_target_add(_client, target)
            if err != OK:
                _lib.vigem_target_free(target)
                raise RuntimeError(f"Couldn't create the virtual controller (code {err:#x}).")
            _pad.update(kind=kind, target=target)
        if kind == "x360":
            _lib.vigem_target_x360_update(_client, _pad["target"], _x360_report(state))
        else:
            _lib.vigem_target_ds4_update(_client, _pad["target"], _ds4_report(state))


NEUTRAL = {"buttons": [], "lt": 0.0, "rt": 0.0, "lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0}


def neutral():
    """Lets go of every button and centres the sticks (keeps the controller plugged in)."""
    with _lock:
        if _pad["kind"]:
            try:
                update(_pad["kind"], NEUTRAL)
            except RuntimeError:
                pass


def close():
    """Unplugs the virtual controller."""
    with _lock:
        if _pad["target"] and _client:
            _lib.vigem_target_remove(_client, _pad["target"])
            _lib.vigem_target_free(_pad["target"])
        _pad.update(kind=None, target=None)
