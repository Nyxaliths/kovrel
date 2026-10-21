"""End-to-end test of the Kovrel server (HTTP + HTTPS) using a throwaway APPDATA.
Sends no real keyboard/mouse/power actions."""
import http.client, json, os, shutil, socket, ssl, struct, subprocess, sys, tempfile, threading, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

tmp = tempfile.mkdtemp(prefix="kovrel-e2e-")
os.environ["APPDATA"] = tmp
os.environ["KOVREL_FILES_DIR"] = os.path.join(os.environ["APPDATA"], "files")
sys.path.insert(0, ROOT)
import security, server

bad = []
def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond: bad.append(name)

# ---- start servers
srv = server.make_server(8798, local=True, https=False)
threading.Thread(target=srv.serve_forever, daemon=True).start()
srv_s = server.make_server(8797, local=True, https=True)
threading.Thread(target=srv_s.serve_forever, daemon=True).start()

popups = []
security.approval_callback = lambda r: popups.append(r)
PIN = security.current_pin()
DEV = "e2e-device-0001"

def conn(https=False):
    if https:
        return http.client.HTTPSConnection("127.0.0.1", 8797, timeout=15, context=ssl._create_unverified_context())
    return http.client.HTTPConnection("127.0.0.1", 8798, timeout=15)

def call(c, cmd, body=None, token=None, raw=None, headers=None):
    h = {"Content-Type": "application/json"}
    if token: h["X-Token"] = token
    h.update(headers or {})
    c.request("POST", "/api/" + cmd, raw if raw is not None else json.dumps(body or {}), h)
    r = c.getresponse(); data = r.read()
    ct = r.headers.get_content_type()
    return r.status, (json.loads(data) if ct == "application/json" else data), r

c = conn()
# ---- public + login
s, d, r = call(c, "info")
check("info is public and says PIN", s == 200 and d["kind"] == "pin" and d["version"] == server.VERSION)
check("security headers on responses", r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff")
s, d, _ = call(conn(), "ping")
check("no token -> 401", s == 401)
s, d, _ = call(conn(), "login", {"secret": "000000" if PIN != "000000" else "111111", "device": DEV, "name": "E2E"})
check("wrong PIN -> 401", s == 401)
s, d, _ = call(conn(), "login", {"secret": PIN, "device": DEV, "name": "E2E phone"})
check("right PIN, new device -> pending + popup", s == 200 and d["status"] == "pending" and len(popups) == 1)
req = d["req"]
s, d, _ = call(conn(), "login_status", {"req": req, "device": DEV})
check("still pending", d["status"] == "pending")
security.approve(req)
s, d, _ = call(conn(), "login_status", {"req": req, "device": DEV})
check("approved -> token", d["status"] == "ok" and d["token"])
T = d["token"]

# ---- ordinary API with keep-alive
t0 = time.perf_counter()
for _ in range(20): s, d, _ = call(c, "ping", token=T)
check("authenticated ping, version, not paused", s == 200 and d["paused"] is False)
check("ping says whether the PC is locked (it is not during the test)", d.get("locked") is False)
s, d, _ = call(c, "open_url", {"url": "file:///C:/Windows/notepad.exe"}, token=T); check("open_url refuses anything but web links", s != 200 or not d.get("ok"), str(d))
print(f"     20 pings on one connection: {(time.perf_counter()-t0)*50:.1f} ms each")
s, d, _ = call(c, "media", token=T);        check("media (volume via COM)", s == 200 and d["volume"] is not None)
s, d, _ = call(c, "status", token=T);       check("status", s == 200 and "ram" in d)
s, d, _ = call(c, "mixer", token=T);        check("mixer lists apps", s == 200 and isinstance(d["apps"], list), str(d)); print("     apps:", [a["label"] for a in d.get("apps", [])])
s, d, _ = call(c, "brightness", token=T);   check("brightness answers (list, or an explanation when monitors refuse)", s == 200 and (d["displays"] or d["reason"]), str(d)); print("     displays:", d.get("displays") or d.get("reason")[:70])
s, d, _ = call(c, "processes", token=T);    check("processes grouped by name", s == 200 and len(d["items"]) > 3 and "mb" in d["items"][0]); print("     top:", [(p["name"], p["mb"]) for p in d["items"][:3]])
s, d, _ = call(c, "active_window", token=T);check("active_window", s == 200 and "title" in d)
s, d, _ = call(c, "pad_status", token=T);   check("pad_status says driver missing clearly", s == 200 and d["available"] is False and "ViGEmBus" in d["reason"])
s, d, _ = call(c, "pad", {"kind": "x360", "buttons": ["a"]}, token=T)
check("pad without driver -> clean error", s == 400 and "ViGEmBus" in d["error"])
s, d, _ = call(c, "game", {"up": [], "down": [], "held": [], "look": [0, 0]}, token=T); check("game tick (no-op)", s == 200)
s, d, _ = call(c, "game", {"down": ["banana"]}, token=T);  check("game: unknown key rejected", s == 400)
s, d, _ = call(c, "notifications", token=T); check("notifications", s == 200)
s, d, _ = call(c, "monitors", token=T);     check("monitors", s == 200 and len(d["monitors"]) >= 1)

# ---- buttons + macros
s, d, _ = call(c, "shortcut_add", {"name": "Bad", "type": "macro", "value": "keys ctrl+l\nbogus 1"}, token=T)
check("macro with unknown step rejected with line number", s == 400 and "Line 2" in d["error"], str(d))
s, d, _ = call(c, "shortcut_add", {"name": "Bad", "type": "macro", "value": "wait 40s"}, token=T)
check("macro wait too long rejected", s == 400)
s, d, _ = call(c, "shortcut_add", {"name": "Bad", "type": "macro", "value": "keys nope+x"}, token=T)
check("macro bad key rejected", s == 400 and "Line 1" in d["error"])
s, d, _ = call(c, "shortcut_add", {"name": "Good macro", "type": "macro", "value": "# open then search\nopen https://example.com\nwait 1s\nkeys ctrl+l\ntype hello\nclick left"}, token=T)
check("valid macro saved", s == 200 and any(x["name"] == "Good macro" for x in d["items"]))
steps = server.parse_macro("keys ctrl+l\nwait 500\nwait 2s\ntype  spaced text\nclick double")
check("macro parser: units and text", steps == [("keys", "ctrl+l"), ("wait", 0.5), ("wait", 2.0), ("type", "spaced text"), ("click", "double")], str(steps))
sid = next(x["id"] for x in d["items"] if x["name"] == "Good macro")
s, d, _ = call(c, "shortcut_delete", {"id": sid}, token=T); check("button deleted", s == 200)

# ---- profiles
cfg = {"style": "ps", "preset": "custom", "sens": 1.4, "invert": True, "showScreen": True, "lstick": "wasd", "rstick": "mouse",
       "bind": {"a": "space", "rt": "mouse_left", "lt": "mouse_right"}, "evil": "<script>"}
s, d, _ = call(c, "profile_save", {"name": "Brawlhalla", "match": "Brawlhalla", "cfg": cfg}, token=T)
check("profile saved, unknown keys stripped", s == 200 and "evil" not in d["items"][0]["cfg"], str(d))
pid = d["id"]
s, d, _ = call(c, "profile_save", {"name": "X", "cfg": {"bind": {"a": "banana"}}}, token=T); check("profile with bad key rejected", s == 400)
s, d, _ = call(c, "profile_save", {"id": pid, "name": "Brawlhalla 2", "match": "Brawlhalla", "cfg": cfg}, token=T)
check("profile updated in place", len(d["items"]) == 1 and d["items"][0]["name"] == "Brawlhalla 2")
s, d, _ = call(c, "profile_delete", {"id": pid}, token=T); check("profile deleted", d["items"] == [])

# ---- wake targets
s, d, _ = call(c, "wake_add", {"name": "Bad", "mac": "not-a-mac"}, token=T); check("bad MAC rejected", s == 400)
s, d, _ = call(c, "wake_add", {"name": "Den PC", "mac": "aa-bb-cc-dd-ee-ff"}, token=T)
check("MAC normalised", s == 200 and d["items"][0]["mac"] == "AA:BB:CC:DD:EE:FF")
s, d, _ = call(c, "wake", {"id": d["items"][0]["id"]}, token=T); check("magic packet sent (UDP broadcast)", s == 200)
s, d, _ = call(c, "wake_delete", {"id": security.CFG["wake_targets"][0]["id"]}, token=T); check("wake target deleted", d["items"] == [])

# ---- folders: browse / traverse / upload / download
os.makedirs(server.SHARED_DIR, exist_ok=True)
extra = os.path.join(tmp, "Shared Stuff"); os.makedirs(os.path.join(extra, "sub"))
open(os.path.join(extra, "hello.txt"), "w").write("hi"); open(os.path.join(extra, "sub", "deep.txt"), "w").write("deep")
open(os.path.join(tmp, "secret.txt"), "w").write("TOP SECRET")
server.shared_folder_add(extra, "Shared Stuff")
s, d, _ = call(c, "browse", {"root": 1, "path": ""}, token=T)
check("browse extra folder: dirs first", s == 200 and d["roots"] == ["Kovrel folder", "Shared Stuff"] and d["entries"][0]["name"] == "sub" and d["entries"][0]["dir"])
s, d, _ = call(c, "browse", {"root": 1, "path": "sub"}, token=T); check("browse subfolder", d["path"] == "sub" and d["entries"][0]["name"] == "deep.txt")
for evil in ("..", "../secret.txt", "sub/../../secret.txt", "..\\..", "C:\\Windows", "/Windows"):
    s, d, _ = call(c, "browse", {"root": 1, "path": evil}, token=T)
    check(f"traversal blocked: {evil!r}", s == 400 or (s == 200 and d["path"] in ("", ) and all(e["name"] != "secret.txt" for e in d["entries"])), str(d)[:90])
s, d, _ = call(c, "download_link", {"root": 1, "path": "../secret.txt"}, token=T); check("download traversal blocked", s == 400)
s, d, _ = call(c, "browse", {"root": 7, "path": ""}, token=T); check("unshared root refused", s == 400)
s, d, _ = call(c, "download_link", {"root": 1, "path": "sub/deep.txt"}, token=T); check("one-time download link issued", s == 200 and d["url"].startswith("/dl/"))
g = conn(); g.request("GET", d["url"]); gr = g.getresponse(); body = gr.read()
check("download link serves the file (no token needed)", gr.status == 200 and body == b"deep" and "attachment" in gr.headers["Content-Disposition"])
g = conn(); g.request("GET", d["url"]); gr = g.getresponse(); gr.read(); check("download link works only once", gr.status == 404)
s, d, _ = call(c, "download_link", {"root": 1, "path": "../secret.txt"}, token=T); check("download link traversal blocked", s == 400)
bc = conn(); bc.putrequest("POST", "/api/ping"); bc.putheader("Content-Length", "-5"); bc.putheader("X-Token", T); bc.endheaders()
check("negative Content-Length refused quickly", bc.getresponse().status == 400)
payload = os.urandom(2_500_000)
s, d, _ = call(c, "upload", raw=payload, token=T, headers={"X-Filename": "..%2F..%2Fev%3Fil.bin", "X-Root": "1", "X-Dir": "sub", "Content-Type": "application/octet-stream"})
check("upload into shared subfolder, name cleaned", s == 200 and d["name"] == "ev_il.bin", str(d))
check("uploaded bytes identical", open(os.path.join(extra, "sub", "ev_il.bin"), "rb").read() == payload)
s, d, _ = call(c, "upload", raw=b"x", token=T, headers={"X-Filename": "a", "X-Root": "1", "X-Dir": "../.."})
check("upload outside shared folder blocked", s == 400)
c = conn()  # the server closes the connection after a rejected upload (body unread): expected
check("server still fine after the rejected upload", call(c, "ping", token=T)[0] == 200)
s, d, _ = call(c, "game", {"down": ["banana"]}, token=T); check("a handled error (400) keeps the connection open", s == 400)
check("...and the same connection works for the next request", call(c, "ping", token=T)[0] == 200)
s, d, _ = call(c, "key", {"combo": "a"}, token="bogus"); check("bad token -> 401 without dropping the connection", s == 401)
check("...next request on it still works", call(c, "ping", token=T)[0] == 200)

# ---- screen: screenshot + push video
s, d, _ = call(conn(), "screenshot", {"monitor": 0}, token=T); check("screenshot is a PNG", s == 200 and d[:8] == b"\x89PNG\r\n\x1a\n" and len(d) > 20000)
vc = conn(); vc.request("POST", "/api/video", json.dumps({"width": 1280, "monitor": 0, "quality": 65, "fps": 30}), {"X-Token": T, "Content-Type": "application/json"})
vr = vc.getresponse(); check("video stream starts", vr.status == 200 and vr.headers["X-Format"] == "jpeg-frames")
frames, t0, sizes = 0, time.time(), []
while time.time() - t0 < 3:
    n = struct.unpack(">I", vr.read(4))[0]; blob = vr.read(n)
    if blob[:2] != b"\xff\xd8": check("frame is JPEG", False); break
    frames += 1; sizes.append(n)
print(f"     video: {frames/3:.1f} fps average over 3 s, {sum(sizes)/len(sizes)/1024:.0f} KB/frame (screen {'busy' if len(set(sizes))>2 else 'mostly static'})")
check("video delivers at least the 1/second heartbeat even on a still screen", frames >= 2)
vc.close()

# H.264 when asked and possible; otherwise the same request quietly gets JPEG
vc = conn(); vc.request("POST", "/api/video", json.dumps({"width": 960, "monitor": 0, "quality": 65, "fps": 30, "codec": "h264"}), {"X-Token": T, "Content-Type": "application/json"})
vr = vc.getresponse()
if vr.headers["X-Format"] == "h264-frames":
    check("H.264 stream starts and names its codec", vr.status == 200 and vr.headers["X-Codec"].startswith("avc1."))
    n = struct.unpack(">I", vr.read(4))[0]; blob = vr.read(n)
    check("first H.264 frame is a keyframe in Annex B with SPS", blob[0] == 1 and blob[1:5] == b"\x00\x00\x00\x01" and 7 in [k for k, _, _ in server.h264.split_nals(blob[1:])])
    frames, t0 = 0, time.time()
    while time.time() - t0 < 2:
        n = struct.unpack(">I", vr.read(4))[0]; blob = vr.read(n); frames += 1
        if blob[0] not in (0, 1): check("H.264 frame flag is 0 or 1", False); break
    check("H.264 stream keeps delivering frames", frames >= 2)
else:
    check("no H.264 here: the request falls back to JPEG", vr.status == 200 and vr.headers["X-Format"] == "jpeg-frames")
vc.close()
vc = conn(); vc.request("POST", "/api/video", json.dumps({"width": 960, "monitor": 0, "quality": 65, "fps": 30}), {"X-Token": T, "Content-Type": "application/json"})
vr = vc.getresponse(); check("without asking for H.264 the stream stays JPEG", vr.headers["X-Format"] == "jpeg-frames"); vc.close()

# ---- pause switch
auto_id, key = security.create_automation_key("Shortcuts")
s, d, _ = call(conn(), "ping", token=key);                 check("automation key: ping ok", s == 200)
s, d, _ = call(conn(), "key", {"combo": "a"}, token=key);  check("automation key can't press keys", s == 403)
s, d, _ = call(conn(), "power", {"action": "lock"}, token=key); check("automation key can't use power", s == 403)
s, d, _ = call(conn(), "shortcut_run", {"name": "nonexistent"}, token=key); check("automation key may run buttons (404-ish error is fine)", s == 400 and "not found" in d["error"])
server.set_paused(True)
s, d, _ = call(conn(), "key", {"combo": "a"}, token=T);  check("paused: control refused (423)", s == 423)
s, d, _ = call(conn(), "ping", token=T);                 check("paused: ping still works and says paused", s == 200 and d["paused"] is True)
s, d, _ = call(conn(), "status", token=T);               check("paused: read-only status still allowed", s == 200)
s, d, _ = call(conn(), "power", {"action": "lock"}, token=T); check("paused: power refused", s == 423)
s, d, _ = call(conn(), "download_link", {"root": 1, "path": "hello.txt"}, token=T); check("paused: file download refused", s == 423)
s, d, _ = call(conn(), "mixer_set", {"name": "x", "level": 5}, token=T); check("paused: changing volume refused", s == 423)
s, d, _ = call(conn(), "pad", {"buttons": []}, token=T); check("paused: controller input refused", s == 423)
server.set_paused(False)
s, d, _ = call(conn(), "ping", token=T);                 check("resumed", d["paused"] is False)

# ---- devices / sign-out
s, d, _ = call(conn(), "devices", token=T); check("devices lists the phone, not the automation key", [x["name"] for x in d["devices"]] == ["E2E phone"], str(d))
s, d, _ = call(conn(), "rename_device", {"name": "Renamed"}, token=T); check("rename own device", s == 200)
security.revoke(auto_id); check("revoked automation key stops working", call(conn(), "ping", token=key)[0] == 401)

# ---- brute force over HTTP
codes = [call(conn(), "login", {"secret": "bad", "device": "attacker-dev-1", "name": "A"})[0] for _ in range(7)]
check("HTTP: 5 wrong -> 401s then 429", codes[:5] == [401] * 5 and codes[5] == 429, str(codes))

# ---- real processes: list + close a harmless one we start
victim = os.path.join(tmp, "kovrel-victim.exe"); shutil.copy(r"C:\Windows\System32\ping.exe", victim)
p = subprocess.Popen([victim, "-n", "60", "127.0.0.1"], stdout=subprocess.DEVNULL)
time.sleep(1.2)
s, d, _ = call(conn(), "processes", {"q": "KOVREL-VICT"}, token=T); check("search finds the test process", [x["name"] for x in d["items"]] == ["kovrel-victim.exe"], str(d))
s, d, _ = call(conn(), "process_kill", {"name": "kovrel-victim.exe"}, token=T); check("program closed", s == 200 and d["closed"] == 1 and p.wait(timeout=5) is not None)
s, d, _ = call(conn(), "process_kill", {"name": "svchost.exe"}, token=T); check("system process protected", s == 400)
s, d, _ = call(conn(), "process_kill", {"name": "csrss.exe"}, token=T); check("csrss protected", s == 400)

# ---- HTTPS
hc = conn(https=True)
s, d, _ = call(hc, "ping", token=T)
check("HTTPS: ping works", s == 200)
check("HTTPS: TLS 1.2+", hc.sock.version() in ("TLSv1.2", "TLSv1.3"), hc.sock.version())
pem = ssl.DER_cert_to_PEM_cert(hc.sock.getpeercert(binary_form=True))
from cryptography import x509
cert = x509.load_pem_x509_certificate(pem.encode())
san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
check("HTTPS: certificate covers localhost + 127.0.0.1 + this PC's address",
      {"127.0.0.1", server.lan_ip()} <= {str(i) for i in san.get_values_for_type(x509.IPAddress)} and "localhost" in san.get_values_for_type(x509.DNSName))
idle = socket.create_connection(("127.0.0.1", 8797)); time.sleep(0.2)   # a client that never speaks TLS
t0 = time.perf_counter(); s, d, _ = call(conn(https=True), "ping", token=T)
check("HTTPS: an idle client doesn't block others", s == 200 and time.perf_counter() - t0 < 2, f"{time.perf_counter()-t0:.2f}s")
idle.close()
try:
    http.client.HTTPConnection("127.0.0.1", 8797, timeout=5).request("GET", "/"); c2 = http.client.HTTPConnection("127.0.0.1", 8797, timeout=5); c2.request("GET", "/"); c2.getresponse(); check("plain HTTP to HTTPS port is not served", False)
except Exception: check("plain HTTP to HTTPS port is not served", True)
s, d, r = call(conn(https=True), "info"); check("HTTPS info", s == 200 and d["https"] is True)
hc.request("GET", "/"); page = hc.getresponse(); html = page.read().decode()
check("page served with CSP + title", "Content-Security-Policy" in page.headers and "<title>" in html)
check("home-screen icon is inside the page (works with the self-signed certificate)", 'rel="apple-touch-icon" sizes="180x180" href="data:image/png;base64,' in html)
check("page carries the PC's version, so an open copy can tell when the PC was updated",
      f'<meta name="kovrel-version" content="{server.VERSION}">' in html and "__KOVREL_VERSION__" not in html)
check("support link is on the phone page (More and Settings)", html.count("https://buymeacoffee.com/nyxaliths") == 2 and "Hold click" not in html)
for icon in ("/apple-touch-icon.png", "/apple-touch-icon-precomposed.png", "/icon-maskable.png", "/manifest.json"):
    ic = conn(); ic.request("GET", icon); ir = ic.getresponse(); data = ir.read()
    check(f"home-screen file served: {icon}", ir.status == 200 and (data[:4] == b"\x89PNG" or icon.endswith(".json")))
from PIL import Image as _Img
import io as _io
ic = conn(); ic.request("GET", "/apple-touch-icon.png"); im = _Img.open(_io.BytesIO(ic.getresponse().read()))
check("iPhone icon is 180x180 with no transparent corners", im.size == (180, 180) and im.mode == "RGB")
key_f = server.KEY_FILE
check("private key file exists", key_f.exists())

# ---- disconnect one phone (the Disconnect button in the PC window)
T2 = security._issue("e2e-device-0002", "Kick me", "127.0.0.1")   # a 2nd PIN login would hit the wrong-PIN lockout above
call(conn(), "ping", token=T2)
kid = next((x["id"] for x in server.connected_devices() if x["name"] == "Kick me"), None)
check("connected list carries each phone's id", bool(kid))
check("disconnect signs that phone out", server.disconnect_device(kid) and call(conn(), "ping", token=T2)[0] == 401)
check("other phones stay signed in", call(conn(), "ping", token=T)[0] == 200)

# ---- audit log
log = security.AUDIT_FILE.read_text()
for ev in ("device_approved", "login_wrong_secret", "device_waiting_for_approval", "remote_control_paused", "program_closed", "file_uploaded", "screenshot", "https_certificate_created"):
    check(f"activity log has {ev}", ev in log)
check("no secrets in the activity log", PIN not in log and T not in log)

print("\nRESULT:", f"{len(bad)} FAILED: {bad}" if bad else "all passed")
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if bad else 0)
