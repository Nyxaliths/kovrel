"""Tests security.py against a throwaway APPDATA. Never touches real settings."""
import json, os, sys, tempfile, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp(prefix="kovrel-sec-")
os.environ["APPDATA"] = tmp
os.environ["KOVREL_FILES_DIR"] = os.path.join(os.environ["APPDATA"], "files")
sys.path.insert(0, ROOT)
import security as s

def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        global bad; bad = True
bad = False

# --- secret storage
pin = s.current_pin()
check("PIN is 6 digits", pin and len(pin) == 6 and pin.isdigit())
raw = json.loads(s.CONFIG_FILE.read_text())
check("PIN not stored in plain text", pin not in s.CONFIG_FILE.read_text() and raw["secret"].startswith("dpapi:"))
check("verify right/wrong", s.verify_secret(pin) and not s.verify_secret("000000" if pin != "000000" else "111111"))
try: s.set_secret("12345", "pin"); check("short PIN rejected", False)
except ValueError: check("short PIN rejected", True)
try: s.set_secret("short", "password"); check("short password rejected", False)
except ValueError: check("short password rejected", True)
s.set_secret("correct horse battery", "password")
check("password works, PIN display hidden", s.verify_secret("correct horse battery") and s.current_pin() is None)
newpin = s.reset_pin(); check("reset_pin gives PIN again", s.current_pin() == newpin)

# --- new device flow
ev = []
s.approval_callback = lambda r: ev.append(r)
r = s.login(newpin, "phone-aaaa1111", "Jo's iPhone <script>", "10.0.0.5")
check("new device -> pending + popup raised", r["status"] == "pending" and len(ev) == 1)
check("name cleaned", ev[0]["name"] == "Jo's iPhone script"[:40] or "<" not in ev[0]["name"])
check("status pending before approval", s.login_status(r["req"], "phone-aaaa1111")["status"] == "pending")
check("other device can't read my request", s.login_status(r["req"], "phone-bbbb2222")["status"] == "expired")
check("no token yet -> not authenticated", s.authenticate("anything") is None)
s.approve(r["req"])
st = s.login_status(r["req"], "phone-aaaa1111")
check("approved -> token delivered", st["status"] == "ok" and len(st["token"]) > 30)
check("token delivered only once", s.login_status(r["req"], "phone-aaaa1111")["status"] == "expired")
tok = st["token"]
dev = s.authenticate(tok, "10.0.0.5")
check("token authenticates", dev and dev["name"].startswith("Jo"))
check("token not stored in plain text", tok not in s.CONFIG_FILE.read_text())
check("wrong token rejected", s.authenticate(tok + "x") is None)
r2 = s.login(newpin, "phone-aaaa1111", "Jo", "10.0.0.5")
check("known device logs in without a popup", r2["status"] == "ok" and len(ev) == 1)
check("old token invalid after re-login", s.authenticate(tok) is None and s.authenticate(r2["token"]))

# --- deny + block
r3 = s.login(newpin, "phone-cccc3333", "Stranger", "10.0.0.9")
s.deny(r3["req"])
check("denied", s.login_status(r3["req"], "phone-cccc3333")["status"] == "denied")
try: s.login(newpin, "phone-cccc3333", "Stranger", "10.0.0.9"); check("denied device blocked for a while", False)
except s.AuthError as e: check("denied device blocked for a while", e.status == 403)

# --- no approver (console server) auto-denies
s.approval_callback = None
r4 = s.login(newpin, "phone-dddd4444", "X", "10.0.0.8")
check("no approver -> denied", s.login_status(r4["req"], "phone-dddd4444")["status"] == "denied")
s.approval_callback = lambda r: None

# --- brute force limits
for i in range(5):
    try: s.login("bad", "phone-eeee5555", "Atk", "6.6.6.6")
    except s.AuthError as e: last = e
check("5th wrong try -> 401, 6th -> locked (429)", last.status == 401)
try: s.login(newpin, "phone-eeee5555", "Atk", "6.6.6.6"); check("locked even with right PIN", False)
except s.AuthError as e: check("locked even with right PIN", e.status == 429)
# many addresses -> global pause
for i in range(30):
    try: s.login("bad", "phone-ffff6666", "Bot", f"7.7.{i}.1")
    except s.AuthError: pass
try: s.login(newpin, "phone-ffff6666", "Owner", "10.0.0.5"); check("global pause stops everyone", False)
except s.AuthError as e: check("global pause stops everyone", e.status == 429)
s._state["global_until"] = 0; s._ips.clear()

# --- sessions, revoke, automation, expiry
dev_id, key = s.create_automation_key("Shortcuts")
check("automation key authenticates with limited scope", s.authenticate(key)["scope"] == "automation")
check("device list", {d["name"] for d in s.devices()} >= {"Jo", "Shortcuts"})
s.revoke(dev_id); check("revoked key dead", s.authenticate(key) is None)
s.CFG["devices"]["phone-aaaa1111"]["last_seen"] = time.time() - 40 * 86400
check("session idle >30 days expires", s.authenticate(r2["token"]) is None)
s.revoke_all(); check("sign out everyone", s.devices() == [])
try: s.login(newpin, "bad id!", "x", "1.1.1.1"); check("bad device id rejected", False)
except s.AuthError as e: check("bad device id rejected", e.status == 400)

check("activity log written", "device_approved" in s.AUDIT_FILE.read_text())
before = len(s.AUDIT_FILE.read_text().splitlines())
s.audit("file_uploaded", file="evil.txt\n2026-01-01 00:00:00 | login_ok | device=fake", device="x\r\ny")
after = s.AUDIT_FILE.read_text().splitlines()
check("a name with line breaks can't add fake lines to the activity log",
      len(after) == before + 1 and "fake" in after[-1] and "\r" not in after[-1])
check("config persisted", "shortcuts" in json.loads(s.CONFIG_FILE.read_text()))
print("\nRESULT:", "FAILED" if bad else "all passed")
sys.exit(1 if bad else 0)
