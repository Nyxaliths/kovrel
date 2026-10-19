"""Installs Kovrel-Setup.exe silently into a scratch folder and tests the INSTALLED Kovrel.exe over HTTP/HTTPS.
Uses a throwaway APPDATA (a trusted device is pre-seeded so no approval click is needed except where tested)."""
import ctypes, ctypes.wintypes as wt, http.client, json, os, shutil, ssl, struct, subprocess, sys, tempfile, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from pathlib import Path

SETUP = os.path.join(ROOT, "build", "Kovrel-Setup.exe")
WORK = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="kovrel-exe-")); shutil.rmtree(WORK, ignore_errors=True); WORK.mkdir(parents=True)
INSTALL, LINKS, APPDATA = WORK / "install", WORK / "links", WORK / "appdata"
APPDATA.mkdir()
os.environ["APPDATA"] = str(APPDATA)
os.environ["KOVREL_FILES_DIR"] = os.path.join(os.environ["APPDATA"], "files")
sys.path.insert(0, ROOT)
import security

bad = []
def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""), flush=True)
    if not cond: bad.append(name)

# ---- install
t = time.time()
subprocess.run([SETUP, "--silent", "--dir", str(INSTALL), "--shortcut-dir", str(LINKS), "--autostart", "--no-register", "--no-launch"], check=True, timeout=180)
print(f"     installed in {time.time() - t:.1f}s")
check("Kovrel.exe + uninstaller installed", (INSTALL / "Kovrel.exe").exists() and (INSTALL / "uninstall.ps1").exists())
check("controller helper DLL bundled", any(INSTALL.rglob("ViGEmClient.dll")))
check("licence notices bundled", any(INSTALL.rglob("NOTICE.txt")))
check("web page bundled", any(INSTALL.rglob("index.html")))
check("shortcuts created (Start menu, Desktop, Startup)", sorted(p.parent.name for p in LINKS.rglob("Kovrel.lnk")) == ["Desktop", "Programs", "Startup"])
check("no old-name files in the install", not any(INSTALL.rglob("Vexari*")))

# ---- seed a trusted device, then run the installed exe
security.revoke_all()
token = security._issue("exe-device-0001", "Exe test phone", "127.0.0.1")
PIN = security.current_pin()
def launch(https):
    security.put("https", https)
    env = dict(os.environ, KOVREL_PORT="8792", APPDATA=str(APPDATA))
    p = subprocess.Popen([str(INSTALL / "Kovrel.exe")], env=env, cwd=str(INSTALL))
    for _ in range(40):
        time.sleep(0.5)
        try:
            c = conn(https); c.request("POST", "/api/info", "{}", {"Content-Type": "application/json"}); c.getresponse().read(); return p
        except Exception: pass
    return p
def conn(https):
    return (http.client.HTTPSConnection("127.0.0.1", 8792, timeout=15, context=ssl._create_unverified_context()) if https
            else http.client.HTTPConnection("127.0.0.1", 8792, timeout=15))
def call(https, cmd, body=None, tok=token):
    c = conn(https); c.request("POST", "/api/" + cmd, json.dumps(body or {}), {"Content-Type": "application/json", "X-Token": tok})
    r = c.getresponse(); d = r.read()
    return r.status, (json.loads(d) if r.headers.get_content_type() == "application/json" else d), r

for https in (False, True):
    label = "HTTPS" if https else "HTTP"
    p = launch(https)
    try:
        s, d, r = call(https, "ping"); check(f"[{label}] installed exe answers, v{d.get('version')}", s == 200 and d["version"] == "2.0.1")
        s, d, _ = call(https, "info", tok=""); check(f"[{label}] info reports the mode", d["https"] is https)
        s, d, _ = call(https, "media"); check(f"[{label}] volume works from the exe (COM bundled)", s == 200 and d["volume"] is not None)
        s, d, _ = call(https, "mixer"); check(f"[{label}] mixer works", s == 200)
        s, d, _ = call(https, "status"); check(f"[{label}] status (psutil, GPU)", s == 200 and "ram" in d)
        s, d, _ = call(https, "processes", {"q": "Kovrel"}); check(f"[{label}] programs list hides Kovrel itself", s == 200 and all(x["name"].lower() != "kovrel.exe" for x in d["items"]))
        s, d, _ = call(https, "pad_status")
        check(f"[{label}] bundled controller DLL loads (reports driver, not 'DLL missing')", s == 200 and "ViGEmBus" in d["reason"] and "missing from this install" not in d["reason"], d.get("reason", ""))
        s, d, _ = call(https, "screenshot"); check(f"[{label}] screenshot PNG", s == 200 and d[:4] == b"\x89PNG")
        # video
        c = conn(https); c.request("POST", "/api/video", json.dumps({"width": 1280, "monitor": 0, "quality": 65, "fps": 30}), {"X-Token": token, "Content-Type": "application/json"})
        vr = c.getresponse(); n_frames = 0; t0 = time.time()
        while time.time() - t0 < 2.5:
            n = struct.unpack(">I", vr.read(4))[0]; blob = vr.read(n); assert blob[:2] == b"\xff\xd8"; n_frames += 1
        check(f"[{label}] video frames flow ({n_frames / 2.5:.0f}/s, includes the idle heartbeat)", n_frames >= 2)
        c.close()
        # sign in as a NEW device -> the exe must show an approval window
        s, d, _ = call(https, "login", {"secret": PIN, "device": "exe-newdev-0002", "name": "Brand new phone"}, tok="")
        check(f"[{label}] new device -> pending", s == 200 and d["status"] == "pending", str(d))
        time.sleep(1.5)
        titles = []
        EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def cb(h, _):
            n = ctypes.windll.user32.GetWindowTextLengthW(h); b = ctypes.create_unicode_buffer(n + 1)
            ctypes.windll.user32.GetWindowTextW(h, b, n + 1)
            if b.value == "Allow this device?": titles.append(h)
            return True
        ctypes.windll.user32.EnumWindows(EnumProc(cb), 0)
        check(f"[{label}] the exe shows an 'Allow this device?' window", len(titles) == 1, str(titles))
        for h in titles: ctypes.windll.user32.PostMessageW(h, 0x0010, 0, 0)   # close = Deny
        time.sleep(1.0)
        s, st, _ = call(https, "login_status", {"req": d["req"], "device": "exe-newdev-0002"}, tok="")
        check(f"[{label}] closing that window denies the phone", st["status"] == "denied", str(st))
    finally:
        p.terminate()
        time.sleep(1.0)
        subprocess.run(["taskkill", "/F", "/IM", "Kovrel.exe"], capture_output=True)
        time.sleep(0.8)

check("HTTPS certificate was created by the exe", (APPDATA / "Kovrel" / "kovrel-cert.pem").exists())
log = (APPDATA / "Kovrel" / "activity.log").read_text()
check("activity log written by the exe", "device_waiting_for_approval" in log and "device_denied" in log)
check("exe log has no crash", "Traceback" not in (APPDATA / "Kovrel" / "log.txt").read_text() if (APPDATA / "Kovrel" / "log.txt").exists() else True)

# ---- uninstall (scripted, quiet) must only remove its own things
dummy_note = INSTALL / "keep-me.txt"; dummy_note.write_text("not part of Kovrel")
r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(INSTALL / "uninstall.ps1"), "-Quiet"], capture_output=True, timeout=60)
check("uninstaller removed exe + libraries", not (INSTALL / "Kovrel.exe").exists() and not (INSTALL / "_internal").exists())
check("uninstaller kept a file it didn't install", dummy_note.exists())
print("\nRESULT:", f"{len(bad)} FAILED: {bad}" if bad else "all passed")
sys.exit(1 if bad else 0)
