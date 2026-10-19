"""Kovrel security core: settings store, PIN/password, trusted devices, sessions,
new-device approval, brute-force limits and the activity log.

Nothing here talks HTTP; server.py calls these functions.
"""

import base64
import collections
import ctypes
import ctypes.wintypes as wt
import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

APP = "Kovrel"
APPDATA = Path(os.environ.get("APPDATA", Path.home()))
CONFIG_DIR = APPDATA / APP
CONFIG_FILE = CONFIG_DIR / "config.json"
AUDIT_FILE = CONFIG_DIR / "activity.log"
OLD_CONFIG_DIR = APPDATA / "Vexari"  # the app's name before 2.0

PIN_LOGIN_FAILS = 5            # wrong tries from one address before it is locked out
GLOBAL_FAIL_LIMIT = 25         # wrong tries from everywhere within 10 minutes locks all logins
APPROVAL_SECONDS = 90          # how long the PC waits for "Allow / Deny"
DENY_BLOCK_SECONDS = 300
SESSION_CHOICES = (1, 7, 30, 90)
REMOTE_MIN_PASSWORD = 12       # remote access needs a real password, not a 6-digit PIN
REMOTE_IDLE_CHOICES = (15, 30, 60, 240)   # minutes without use before a phone away from home must sign in again

_lock = threading.RLock()


class AuthError(Exception):
    def __init__(self, status, message, retry=None):
        super().__init__(message)
        self.status, self.message, self.retry = status, message, retry


# ---------------------------------------------------------------- Windows DPAPI
# The PIN is stored encrypted for this Windows user, so copying config.json to another
# account or PC (or a backup leaking) doesn't reveal it.

class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]


def _dpapi(fn, data):
    buf = ctypes.create_string_buffer(data, len(data))
    src = _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _BLOB()
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(text):
    try:
        return "dpapi:" + base64.b64encode(_dpapi(_crypt32.CryptProtectData, text.encode())).decode()
    except OSError:
        return "plain:" + base64.b64encode(text.encode()).decode()


def unprotect(blob):
    kind, _, b64 = (blob or "").partition(":")
    raw = base64.b64decode(b64 or "")
    if kind == "dpapi":
        return _dpapi(_crypt32.CryptUnprotectData, raw).decode()
    return raw.decode() if kind == "plain" else ""


# ---------------------------------------------------------------- settings file

DEFAULT_SHORTCUTS = [
    {"id": "s1", "name": "YouTube", "type": "url", "value": "https://www.youtube.com", "icon": "play"},
    {"id": "s2", "name": "Show desktop", "type": "keys", "value": "win+d", "icon": "monitor"},
]


def _move_old_settings():
    """First start after the rename from Vexari: bring everything along (PIN, trusted phones,
    buttons, profiles, certificate, activity log). It's the same Windows user, so the encrypted
    PIN still opens and approved phones keep working without being approved again."""
    if CONFIG_FILE.exists() or not (OLD_CONFIG_DIR / "config.json").is_file():
        return
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    renamed = {"vexari-cert.pem": "kovrel-cert.pem", "vexari-key.pem": "kovrel-key.pem"}
    try:
        for f in OLD_CONFIG_DIR.iterdir():
            dest = CONFIG_DIR / renamed.get(f.name, f.name)
            if f.is_file() and not dest.exists():
                shutil.copy2(f, dest)
    except OSError:
        return
    shutil.rmtree(OLD_CONFIG_DIR, ignore_errors=True)   # don't leave a second copy of the PIN behind


def _load():
    _move_old_settings()
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    cfg.setdefault("shortcuts", [dict(s) for s in DEFAULT_SHORTCUTS])
    cfg.setdefault("devices", {})
    cfg.setdefault("https", False)
    cfg.setdefault("session_days", 30)
    cfg.setdefault("shared_folders", [])
    cfg.setdefault("profiles", [])
    cfg.setdefault("wake_targets", [])
    cfg.setdefault("secret_kind", "pin")
    cfg.setdefault("remote", False)
    cfg.setdefault("remote_idle_min", 30)
    cfg.setdefault("update_check", True)
    return cfg


CFG = _load()
_dirty_at = 0.0


def save(force=True):
    """Writes settings atomically. force=False batches the frequent last-seen updates."""
    global _dirty_at
    with _lock:
        if not force:
            _dirty_at = _dirty_at or time.time()
            if time.time() - _dirty_at < 60:
                return
        _dirty_at = 0.0
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(CFG, indent=2), encoding="utf-8")
        tmp.replace(CONFIG_FILE)


def get(key, default=None):
    return CFG.get(key, default)


def put(key, value):
    with _lock:
        CFG[key] = value
        save()


# ---------------------------------------------------------------- activity log

RECENT = collections.deque(maxlen=100)


def audit(event, **fields):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} | {event}"
    if fields:
        line += " | " + " ".join(f"{k}={v}" for k, v in fields.items())
    RECENT.append(line)
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if AUDIT_FILE.exists() and AUDIT_FILE.stat().st_size > 256 * 1024:
            AUDIT_FILE.replace(AUDIT_FILE.with_suffix(".old"))
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- PIN / password

def new_pin():
    return f"{secrets.randbelow(10**6):06d}"


def set_secret(value, kind):
    if kind == "pin":
        if not re.fullmatch(r"\d{6}", value):
            raise ValueError("A PIN is exactly 6 digits.")
    elif len(value) < 8:
        raise ValueError("A password needs at least 8 characters.")
    elif len(value) > 128:
        raise ValueError("That password is too long.")
    with _lock:
        CFG["secret"], CFG["secret_kind"] = protect(value), kind
        weak = CFG.get("remote") and (kind != "password" or len(value) < REMOTE_MIN_PASSWORD)
        if weak:
            CFG["remote"] = False
        save()
    audit("secret_changed", kind=kind)
    if weak:
        audit("remote_access", on=False, reason="sign-in no longer strong enough")


def reset_pin():
    pin = new_pin()
    set_secret(pin, "pin")
    return pin


def secret_kind():
    return CFG.get("secret_kind", "pin")


def current_pin():
    """The PIN to show on the PC, or None when a custom password is in use."""
    if secret_kind() != "pin":
        return None
    try:
        return unprotect(CFG.get("secret"))
    except Exception:
        return None


def strong_enough_for_remote():
    if secret_kind() != "password":
        return False
    try:
        return len(unprotect(CFG.get("secret"))) >= REMOTE_MIN_PASSWORD
    except Exception:
        return False


def set_remote(on):
    """Remote access (through Tailscale) only with a password of 12+ characters."""
    if on and not strong_enough_for_remote():
        raise ValueError(f"Remote access needs a password of at least {REMOTE_MIN_PASSWORD} characters. Set one first.")
    put("remote", bool(on))
    audit("remote_access", on=bool(on))


def set_remote_idle(minutes):
    if minutes not in REMOTE_IDLE_CHOICES:
        raise ValueError("Pick 15, 30, 60 or 240 minutes.")
    put("remote_idle_min", minutes)


def verify_secret(candidate):
    try:
        real = unprotect(CFG.get("secret"))
    except Exception:
        return False
    return bool(real) and hmac.compare_digest(candidate.encode(), real.encode())


if not CFG.get("secret"):
    set_secret(new_pin(), "pin")
else:
    try:
        unprotect(CFG["secret"])
    except Exception:  # config copied from another Windows account: start fresh
        set_secret(new_pin(), "pin")


# ---------------------------------------------------------------- rate limits

_ips = {}                                     # ip -> {"n", "until", "strikes"}
_global_fails = collections.deque()
_state = {"global_until": 0.0}


def _check_lock(ip):
    with _lock:
        _check_lock_unlocked(ip)


def _check_lock_unlocked(ip):
    now = time.time()
    if now < _state["global_until"]:
        raise AuthError(429, "Too many wrong attempts. Logins are paused for a few minutes.",
                        retry=int(_state["global_until"] - now))
    rec = _ips.get(ip)
    if rec and now < rec["until"]:
        raise AuthError(429, f"Too many wrong attempts. Try again in {int(rec['until'] - now) + 1} seconds.",
                        retry=int(rec["until"] - now) + 1)


def _fail(ip):
    with _lock:
        _fail_unlocked(ip)


def _fail_unlocked(ip):
    now = time.time()
    rec = _ips.setdefault(ip, {"n": 0, "until": 0.0, "strikes": 0})
    rec["n"] += 1
    if rec["n"] >= PIN_LOGIN_FAILS:
        rec["strikes"] += 1
        rec["n"] = 0
        rec["until"] = now + min(60 * 2 ** (rec["strikes"] - 1), 900)  # 1, 2, 4 … 15 minutes
    _global_fails.append(now)
    while _global_fails and now - _global_fails[0] > 600:
        _global_fails.popleft()
    if len(_global_fails) >= GLOBAL_FAIL_LIMIT:
        _state["global_until"] = now + 300
        _global_fails.clear()
        audit("logins_paused", reason="many wrong attempts from many places")
    audit("login_wrong_secret", ip=ip)


# ---------------------------------------------------------------- devices and tokens

_ID_OK = re.compile(r"[A-Za-z0-9_-]{6,64}")
_pending = {}                                 # request id -> request
_denied_until = {}                            # device id -> time
approval_callback = None                      # set by the PC app: called with a request dict
_seen = {}                                    # device id -> last seen (memory only)


def _clean_name(name):
    name = re.sub(r"[\x00-\x1f<>]", "", str(name or "")).strip()[:40]
    return name or "Phone"


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _issue(dev_id, name, ip, scope="full"):
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        dev = CFG["devices"].setdefault(dev_id, {"created": now})
        dev.update(name=name, token_hash=_hash(token), scope=scope, last_seen=now, last_ip=ip)
        save()
    _seen[dev_id] = now
    return token


def login(secret, dev_id, name, ip, remote=False):
    """Checks the PIN/password. Known devices get a session straight away; new devices
    must be approved on the PC first. remote=True: the phone is away from home (Tailscale);
    new phones still need Allow clicked on the PC itself. Returns {"status": "ok"|"pending", ...}."""
    _check_lock(ip)
    if remote and not (CFG.get("remote") and strong_enough_for_remote()):
        raise AuthError(403, "Remote access is turned off on the PC.")
    if not verify_secret(secret or ""):
        _fail(ip)
        raise AuthError(401, "Wrong PIN" if secret_kind() == "pin" else "Wrong password")
    with _lock:
        _ips.pop(ip, None)
    if not _ID_OK.fullmatch(dev_id or ""):
        raise AuthError(400, "Unrecognised device. Reload the page and try again.")
    name = _clean_name(name)
    now = time.time()
    with _lock:
        dev = CFG["devices"].get(dev_id)
        if dev and dev.get("scope", "full") == "full":
            token = _issue(dev_id, name, ip)
            audit("login_ok", device=name, ip=ip, **({"remote": True} if remote else {}))
            return {"status": "ok", "token": token}
        for req in _pending.values():
            if req["dev_id"] == dev_id and req["status"] == "pending":
                return {"status": "pending", "req": req["id"]}
        if _denied_until.get(dev_id, 0) > now:
            raise AuthError(403, "That request was denied on the PC. Try again in a few minutes.")
        live = [r for r in _pending.values() if r["status"] == "pending" and now - r["created"] < APPROVAL_SECONDS]
        if len(live) >= 3:
            raise AuthError(429, "Other devices are already waiting for approval.")
        req = {"id": secrets.token_urlsafe(9), "dev_id": dev_id, "name": name, "ip": ip,
               "created": now, "status": "pending", "token": None}
        _pending[req["id"]] = req
    audit("device_waiting_for_approval", device=name, ip=ip)
    cb = approval_callback
    if cb:
        try:
            cb(dict(req))
        except Exception:
            pass
    else:
        deny(req["id"], quiet=True)           # nobody can approve: console-only server
    return {"status": "pending", "req": req["id"]}


def login_status(req_id, dev_id):
    now = time.time()
    with _lock:
        for k in [k for k, r in _pending.items() if now - r["created"] > APPROVAL_SECONDS + 600]:
            del _pending[k]
        req = _pending.get(req_id)
        if not req or req["dev_id"] != dev_id:
            return {"status": "expired"}
        if req["status"] == "pending" and now - req["created"] > APPROVAL_SECONDS:
            req["status"] = "expired"
        if req["status"] == "approved":
            token, req["token"] = req["token"], None
            if token:
                return {"status": "ok", "token": token}
            return {"status": "expired"}
        return {"status": req["status"]}


def approve(req_id, ip=None):
    with _lock:
        req = _pending.get(req_id)
        if not req or req["status"] != "pending":
            return False
        req["token"] = _issue(req["dev_id"], req["name"], req["ip"])
        req["status"] = "approved"
    audit("device_approved", device=req["name"], ip=req["ip"])
    return True


def deny(req_id, quiet=False):
    with _lock:
        req = _pending.get(req_id)
        if not req or req["status"] != "pending":
            return False
        req["status"] = "denied"
        _denied_until[req["dev_id"]] = time.time() + DENY_BLOCK_SECONDS
    if not quiet:
        audit("device_denied", device=req["name"], ip=req["ip"])
    return True


def pending_requests():
    now = time.time()
    with _lock:
        return [dict(r) for r in _pending.values() if r["status"] == "pending" and now - r["created"] < APPROVAL_SECONDS]


def authenticate(token, ip="", remote=False):
    """Returns the device record for a valid session token, else None. remote=True (away from
    home): a session unused for remote_idle_min minutes is locked; the phone stays trusted and
    just signs in again with the password."""
    if not token:
        return None
    h = _hash(token)
    now = time.time()
    with _lock:
        for dev_id, dev in list(CFG["devices"].items()):
            if not hmac.compare_digest(h, dev.get("token_hash", "")):
                continue
            idle_limit = None if dev.get("scope") == "automation" else CFG.get("session_days", 30) * 86400
            if idle_limit and now - dev.get("last_seen", 0) > idle_limit:
                del CFG["devices"][dev_id]
                save()
                audit("session_expired", device=dev.get("name"))
                return None
            if remote and dev.get("scope") != "automation" and now - dev.get("last_seen", 0) > CFG.get("remote_idle_min", 30) * 60:
                dev["token_hash"] = ""
                save()
                audit("remote_session_locked", device=dev.get("name"))
                return None
            dev["last_seen"], dev["last_ip"] = now, ip
            _seen[dev_id] = now
            save(force=False)
            return {"id": dev_id, **dev}
    return None


def rename_device(dev_id, name):
    with _lock:
        if dev_id in CFG["devices"]:
            CFG["devices"][dev_id]["name"] = _clean_name(name)
            save()


def revoke(dev_id):
    with _lock:
        dev = CFG["devices"].pop(dev_id, None)
        save()
    if dev:
        audit("device_removed", device=dev.get("name"))
    return bool(dev)


def revoke_all():
    with _lock:
        n = len(CFG["devices"])
        CFG["devices"].clear()
        save()
    audit("all_devices_signed_out", count=n)


def create_automation_key(name="Automation"):
    """A limited key (it can only run your buttons) for iPhone Shortcuts, Tasker etc."""
    dev_id = "auto-" + secrets.token_hex(4)
    token = _issue(dev_id, _clean_name(name), "", scope="automation")
    audit("automation_key_created", name=name)
    return dev_id, token


def devices():
    now = time.time()
    with _lock:
        return [{"id": i, "name": d.get("name", "?"), "scope": d.get("scope", "full"), "ip": d.get("last_ip", ""),
                 "created": d.get("created", 0), "last_seen": d.get("last_seen", 0),
                 "online": now - _seen.get(i, 0) < 15} for i, d in CFG["devices"].items()]


def set_session_days(days):
    if days not in SESSION_CHOICES:
        raise ValueError("Pick 1, 7, 30 or 90 days.")
    put("session_days", days)
    audit("stay_signed_in_changed", days=days)
