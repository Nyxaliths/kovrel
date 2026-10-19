"""Runs the real Kovrel app UI against a throwaway APPDATA, clicks through it and screenshots each window."""
import ctypes, ctypes.wintypes as wt, http.client, importlib.machinery, importlib.util, json, os, ssl, sys, tempfile, threading, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from pathlib import Path
from PIL import Image

tmp = tempfile.mkdtemp(prefix="kovrel-gui-")
os.environ["APPDATA"] = tmp
os.environ["KOVREL_FILES_DIR"] = os.path.join(os.environ["APPDATA"], "files")
os.environ["KOVREL_PORT"] = "8793"
APP = Path(ROOT)
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="kovrel-gui-out-")); OUT.mkdir(exist_ok=True)
import faulthandler
faulthandler.dump_traceback_later(110, exit=True, file=open(OUT / "hang.txt", "w"))   # if it stalls, show where
RES = open(OUT / "results.txt", "w", encoding="utf-8")
print("RESULTS " + str(OUT / "results.txt"), flush=True)   # before the app takes over stdout
def say(text): RES.write(text + "\n"); RES.flush()
sys.path.insert(0, str(APP))
loader = importlib.machinery.SourceFileLoader("kovrel_app", str(APP / "Kovrel.pyw"))
spec = importlib.util.spec_from_loader("kovrel_app", loader)
mod = importlib.util.module_from_spec(spec); loader.exec_module(mod)
security, server = mod.security, mod.server
mod.messagebox.askokcancel = lambda *a, **k: True      # auto-answer confirmation boxes
mod.messagebox.askyesno = lambda *a, **k: True
app = mod.App(start_hidden=False)

u, g = ctypes.windll.user32, ctypes.windll.gdi32
u.GetWindowDC.restype = wt.HDC
g.CreateCompatibleDC.restype, g.CreateCompatibleDC.argtypes = wt.HDC, [wt.HDC]
g.CreateCompatibleBitmap.restype, g.CreateCompatibleBitmap.argtypes = wt.HBITMAP, [wt.HDC, ctypes.c_int, ctypes.c_int]
g.SelectObject.restype, g.SelectObject.argtypes = wt.HGDIOBJ, [wt.HDC, wt.HGDIOBJ]
u.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
g.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]

def shot(win, name):
    hwnd = int(win.wm_frame(), 16)
    r = wt.RECT(); u.GetWindowRect(hwnd, ctypes.byref(r)); w, h = r.right - r.left, r.bottom - r.top
    wdc = u.GetWindowDC(hwnd); mdc = g.CreateCompatibleDC(wdc); bmp = g.CreateCompatibleBitmap(wdc, w, h); g.SelectObject(mdc, bmp)
    u.PrintWindow(hwnd, mdc, 2)
    buf = ctypes.create_string_buffer(w * h * 4); bih = (ctypes.c_uint32 * 10)(40, w, (-h) & 0xFFFFFFFF, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
    g.GetDIBits(mdc, bmp, 0, h, buf, bih, 0)
    Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1).save(OUT / name)

def on_tk(fn, wait=True):
    box = {}; ev = threading.Event()
    def run():
        try: box["v"] = fn()
        except Exception as e: box["e"] = e
        ev.set()
    app.root.after(0, run)
    if wait:
        ev.wait(20)
        if "e" in box: raise box["e"]
        return box.get("v")

def click(label_text, parent):
    def find(w):
        for c in w.winfo_children():
            if c.winfo_class() == "Label" and c.cget("text") == label_text: return c
            r = find(c)
            if r: return r
    w = find(parent)
    assert w, f"button {label_text!r} not found"
    w.event_generate("<Button-1>")

results = []
def check(name, cond, extra=""):
    results.append((name, cond)); say(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))

def hreq(scheme, method, path, body=None, token=None):
    c = http_conn(scheme)
    h = {"Content-Type": "application/json"}; token and h.update({"X-Token": token})
    c.request(method, path, json.dumps(body or {}), h); r = c.getresponse(); d = r.read()
    try: return r.status, json.loads(d)
    except Exception: return r.status, d
def http_conn(scheme):
    if scheme == "https": return http.client.HTTPSConnection("127.0.0.1", 8793, timeout=10, context=ssl._create_unverified_context())
    return http.client.HTTPConnection("127.0.0.1", 8793, timeout=10)

def scenario():
    time.sleep(1.5)
    on_tk(lambda: shot(app.root, "1_main.png"))
    check("server running, address shown", app.error is None and app.url.startswith("http://") and app.url.endswith(":8793"))
    pin = security.current_pin()
    # --- a new phone logs in -> approval pop-up
    s, d = hreq("http", "POST", "/api/login", {"secret": pin, "device": "gui-device-0001", "name": "Jo's iPhone"})
    check("login -> pending", s == 200 and d["status"] == "pending", str(d))
    time.sleep(1.2)
    check("approval pop-up appeared", len(app.approvals) == 1)
    top = list(app.approvals.values())[0]
    on_tk(lambda: shot(top, "2_approval.png"))
    on_tk(lambda: click("Allow", top))
    time.sleep(0.8)
    s, st = hreq("http", "POST", "/api/login_status", {"req": d["req"], "device": "gui-device-0001"})
    check("clicking Allow gives the phone a token", st.get("status") == "ok")
    token = st["token"]
    check("pop-up closed", len(app.approvals) == 0)
    # --- deny path
    s, d2 = hreq("http", "POST", "/api/login", {"secret": pin, "device": "gui-device-0002", "name": "Stranger"})
    time.sleep(1.0)
    top2 = list(app.approvals.values())[0]
    on_tk(lambda: click("Deny", top2)); time.sleep(0.6)
    s, st2 = hreq("http", "POST", "/api/login_status", {"req": d2["req"], "device": "gui-device-0002"})
    check("clicking Deny refuses the phone", st2["status"] == "denied")
    # --- main window shows the connected phone
    hreq("http", "POST", "/api/ping", {}, token)
    time.sleep(1.8)
    check("connected list shows the phone", "Jo's iPhone" in app.devices.cget("text"), app.devices.cget("text"))
    on_tk(lambda: shot(app.root, "3_main_connected.png"))
    # --- security window: every tab
    on_tk(lambda: app.open_security(0)); time.sleep(0.8)
    win = app.sec_win
    nb = [w for w in win.winfo_children() if w.winfo_class() == "TNotebook"][0]
    for i, name in enumerate(["devices", "access", "remote", "sharing", "activity"]):
        on_tk(lambda i=i: nb.select(i)); time.sleep(0.5)
        on_tk(lambda name=name: shot(win, f"4_security_{name}.png"))
    # --- HTTPS toggle from the Access tab
    on_tk(lambda: nb.select(1)); time.sleep(0.3)
    def find_check(w, text):
        for c in w.winfo_children():
            if c.winfo_class() == "Checkbutton" and c.cget("text").startswith(text): return c
            r = find_check(c, text)
            if r: return r
    cb = on_tk(lambda: find_check(win, "Use a secure connection"))
    on_tk(lambda: cb.invoke()); time.sleep(1.5)
    check("HTTPS on: server restarted with https URL", app.scheme == "https" and app.url.startswith("https://"), app.url)
    s, d = hreq("https", "POST", "/api/ping", {}, token); check("HTTPS: old session token still works", s == 200)
    try:
        hreq("http", "POST", "/api/ping", {}, token); plain_ok = True
    except Exception: plain_ok = False
    check("plain HTTP no longer answers", not plain_ok)
    on_tk(lambda: app.refresh_now()); time.sleep(0.4)
    on_tk(lambda: shot(app.root, "5_main_https.png"))
    on_tk(lambda: cb.invoke()); time.sleep(1.5)
    check("HTTPS off again", app.scheme == "http")
    # --- pause switch
    on_tk(lambda: click("Pause", app.root)); time.sleep(1.8)
    check("Pause button pauses remote control", server.PAUSED["on"])
    s, d = hreq("http", "POST", "/api/key", {"combo": "a"}, token); check("paused: control refused", s == 423)
    on_tk(lambda: shot(app.root, "6_main_paused.png"))
    on_tk(lambda: click("Resume", app.root)); time.sleep(1.8)
    check("Resume resumes", not server.PAUSED["on"])
    # --- password
    on_tk(lambda: app.set_password(win)); time.sleep(0.6)
    dlg = [w for w in win.winfo_children() if w.winfo_class() == "Toplevel" and w.title() == "Set a password"][0]
    ents = [w for w in dlg.winfo_children() if w.winfo_class() == "Entry"]
    on_tk(lambda: (ents[0].insert(0, "pw12"), ents[1].insert(0, "pw12"), click("Save", dlg))); time.sleep(0.5)
    err = [w for w in dlg.winfo_children() if w.winfo_class() == "Label" and w.cget("fg") == mod.RED][0]
    check("short password rejected in the dialog", "8 characters" in err.cget("text"), err.cget("text"))
    on_tk(lambda: (ents[0].delete(0, "end"), ents[1].delete(0, "end"), ents[0].insert(0, "correct horse battery"), ents[1].insert(0, "different"), click("Save", dlg))); time.sleep(0.4)
    check("mismatched password rejected", "match" in err.cget("text"))
    on_tk(lambda: (ents[1].delete(0, "end"), ents[1].insert(0, "correct horse battery"), click("Save", dlg))); time.sleep(1.8)
    check("password saved", security.secret_kind() == "password" and security.verify_secret("correct horse battery"))
    check("main window says password set", app.pin.cget("text") == "Password set" and app.pin_btn.cget("text") == "Use a PIN")
    s, d = hreq("http", "POST", "/api/info"); check("phone login asks for a password", d["kind"] == "password")
    on_tk(lambda: shot(app.root, "7_main_password.png"))
    on_tk(lambda: click("Use a PIN", app.root)); time.sleep(1.8)
    check("back to a PIN", security.secret_kind() == "pin" and app.pin.cget("text").replace(" ", "").isdigit())
    # --- devices tab removal
    tree = None
    def find_tree(w):
        for c in w.winfo_children():
            if c.winfo_class() == "Treeview": return c
            r = find_tree(c)
            if r: return r
    tree = on_tk(lambda: find_tree(win)); on_tk(lambda: nb.select(0)); time.sleep(3.3)
    items = on_tk(lambda: tree.get_children()); check("devices tab lists the approved phone", len(items) == 1, str(items))
    on_tk(lambda: (tree.selection_set(items[0]), click("Remove selected", win))); time.sleep(0.6)
    s, d = hreq("http", "POST", "/api/ping", {}, token); check("removing a device signs that phone out", s == 401)
    # --- automation key dialog
    on_tk(lambda: app.automation_key(win)); time.sleep(0.6)
    akd = [w for w in win.winfo_children() if w.winfo_class() == "Toplevel" and w.title() == "Automation key"][0]
    on_tk(lambda: shot(akd, "8_automation_key.png"))
    key = on_tk(lambda: [w for w in akd.winfo_children() if w.winfo_class() == "Entry"][0].get())
    s, d = hreq("http", "POST", "/api/ping", {}, key); check("automation key works for ping", s == 200)
    s, d = hreq("http", "POST", "/api/key", {"combo": "a"}, key); check("automation key can't press keys", s == 403)
    # --- activity tab content
    log = security.AUDIT_FILE.read_text()
    check("activity log has approvals, denial, https, pause", all(x in log for x in ("device_approved", "device_denied", "secure_connection", "remote_control_paused")))
    say("\nRESULT: " + ("all passed" if all(ok for _, ok in results) else "FAILED: " + ", ".join(n for n, ok in results if not ok)))
    on_tk(app.quit, wait=False)

def guarded():
    try: scenario()
    except BaseException:
        import traceback; say("EXCEPTION:\n" + traceback.format_exc()); os._exit(1)
threading.Thread(target=guarded, daemon=True).start()
app.root.mainloop()
os._exit(0)
