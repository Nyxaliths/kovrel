"""The rename from Vexari: settings, trusted phones and received files must come along. Throwaway folders only."""
import json, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp(prefix="kovrel-migrate-")
os.environ["APPDATA"] = tmp
os.environ["KOVREL_FILES_DIR"] = os.path.join(tmp, "Downloads", "Kovrel")
sys.path.insert(0, ROOT)

bad = []
def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond: bad.append(name)

# ---- a Vexari 1.2 install as it would be on disk
old = os.path.join(tmp, "Vexari"); os.makedirs(old)
import importlib.util
spec = importlib.util.spec_from_file_location("dpapi_only", os.path.join(ROOT, "security.py"))
# protect() needs no settings: borrow it by loading security once against a scratch APPDATA
os.environ["APPDATA"] = os.path.join(tmp, "scratch"); sec0 = importlib.util.module_from_spec(spec); spec.loader.exec_module(sec0)
os.environ["APPDATA"] = tmp
import hashlib
token = "old-session-token-abcdefghijklmnopqrstuvwxyz"
cfg = {"secret": sec0.protect("314159"), "secret_kind": "pin", "https": True, "session_days": 30,
       "devices": {"phone-old-0001": {"name": "Old iPhone", "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                                      "scope": "full", "created": 1, "last_seen": 9e12, "last_ip": "10.0.0.2"}},
       "shortcuts": [{"id": "x1", "name": "Mine", "type": "keys", "value": "win+d", "icon": "star"}],
       "profiles": [], "shared_folders": [], "wake_targets": []}
open(os.path.join(old, "config.json"), "w").write(json.dumps(cfg))
open(os.path.join(old, "activity.log"), "w").write("2026-10-19 10:00:00 | device_approved\n")
open(os.path.join(old, "vexari-cert.pem"), "w").write("CERT")
open(os.path.join(old, "vexari-key.pem"), "w").write("KEY")
old_files = os.path.join(tmp, "Downloads", "Vexari"); os.makedirs(old_files)
open(os.path.join(old_files, "IMG_0001.jpg"), "wb").write(b"\xff\xd8photo")

# ---- first start of Kovrel
import security, server   # importing server moves the received files
new = os.path.join(tmp, "Kovrel")
check("settings moved to the Kovrel folder", os.path.isfile(os.path.join(new, "config.json")))
check("old settings folder removed (no second copy of the PIN)", not os.path.exists(old))
check("same PIN after the move", security.current_pin() == "314159")
check("approved phone still signed in", (security.authenticate(token) or {}).get("name") == "Old iPhone")
check("buttons kept", [s["name"] for s in security.CFG["shortcuts"]] == ["Mine"])
check("HTTPS setting kept", security.get("https") is True)
check("certificate renamed", os.path.isfile(os.path.join(new, "kovrel-cert.pem")) and os.path.isfile(os.path.join(new, "kovrel-key.pem")))
check("activity log kept", "device_approved" in open(os.path.join(new, "activity.log")).read())
check("received files moved to Downloads\\Kovrel", (server.SHARED_DIR / "IMG_0001.jpg").is_file() and not os.path.exists(old_files))

# ---- a second start doesn't redo anything
os.makedirs(old); open(os.path.join(old, "config.json"), "w").write("{}")
security._move_old_settings()
check("an existing Kovrel config is never overwritten", security.current_pin() == "314159" and os.path.exists(old))
print("\nRESULT:", f"{len(bad)} FAILED: {bad}" if bad else "all passed")
sys.exit(1 if bad else 0)
