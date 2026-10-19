"""Measures the push video stream while a small window animates on screen (so frames really change)."""
import http.client, os, struct, subprocess, sys, tempfile, threading, time, json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="kovrel-vid-")
os.environ["KOVREL_FILES_DIR"] = os.path.join(os.environ["APPDATA"], "files")
sys.path.insert(0, ROOT)
import security, server

security.approval_callback = lambda r: None
srv = server.make_server(8796, local=True, https=False)
threading.Thread(target=srv.serve_forever, daemon=True).start()
T = security._issue("vid-device-001", "vid", "127.0.0.1")

anim = subprocess.Popen([sys.executable, "-c", """
import tkinter as tk, time
r = tk.Tk(); r.overrideredirect(True); r.geometry('360x120+40+40'); r.attributes('-topmost', True)
c = tk.Canvas(r, width=360, height=120, bg='#222'); c.pack()
box = c.create_rectangle(0, 20, 60, 100, fill='#6366F1', width=0)
x, d = 0, 9
def tick():
    global x, d
    x += d
    if x > 300 or x < 0: d = -d
    c.coords(box, x, 20, x + 60, 100); r.after(8, tick)
tick(); r.after(16000, r.destroy); r.mainloop()
"""])
time.sleep(1.2)

def measure(width, label, seconds=4):
    c = http.client.HTTPConnection("127.0.0.1", 8796, timeout=15)
    c.request("POST", "/api/video", json.dumps({"width": width, "monitor": 0, "quality": 65, "fps": 30}), {"X-Token": T, "Content-Type": "application/json"})
    r = c.getresponse(); n_frames, total, t0, first = 0, 0, time.time(), None
    while time.time() - t0 < seconds:
        n = struct.unpack(">I", r.read(4))[0]; blob = r.read(n)
        assert blob[:2] == b"\xff\xd8"
        n_frames += 1; total += n
        first = first or time.time() - t0
    c.close()
    print(f"{label:28} {n_frames / seconds:5.1f} fps   {total / n_frames / 1024:5.0f} KB/frame   {total / seconds / 1048576:4.1f} MB/s   first frame {first * 1000:.0f} ms")
    return n_frames / seconds

fps = {w: measure(w, f"{w}px") for w in (960, 1280, 1920)}
anim.terminate()
time.sleep(0.5)
static = measure(1280, "idle screen (heartbeat)", 3)
print("RESULT:", "ok" if fps[1280] >= 12 and 0.8 <= static <= 2.5 else "CHECK")
