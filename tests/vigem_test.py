import ctypes, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import vigem as v

bad = []
def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond: bad.append(name)

check("XUSB_REPORT is 12 bytes", ctypes.sizeof(v.XUSB_REPORT) == 12)
check("DS4_REPORT is 9 bytes", ctypes.sizeof(v.DS4_REPORT) == 9)
st = {"buttons": ["a", "rb", "up", "rt"], "lt": 0.0, "rt": 0.0, "lx": 1.0, "ly": -1.0, "rx": 0.0, "ry": 0.5}
x = v._x360_report(st)
check("x360 buttons A+RB+Up", x.wButtons == (v.X_A | v.X_RB | v.X_UP))
check("x360 digital RT -> 255, LT 0", (x.bRightTrigger, x.bLeftTrigger) == (255, 0))
check("x360 stick: right=+32767, up=+32767 (y flipped), down=-16383", (x.sThumbLX, x.sThumbLY, x.sThumbRY) == (32767, 32767, -16383))
d = v._ds4_report(st)
check("ds4 cross+R1 + hat north", (d.wButtons & 0xF) == 0 and d.wButtons & v.D_CROSS and d.wButtons & v.D_R1)
check("ds4 R2 digital bit and trigger", d.bTriggerR == 255 and d.wButtons & v.D_R2)
check("ds4 sticks: right=255, up=1, down=191", (d.bThumbLX, d.bThumbLY, d.bThumbRY) == (255, 1, 191))
none = v._ds4_report(v.NEUTRAL)
check("ds4 neutral: centre sticks, hat released (8)", (none.bThumbLX, none.bThumbLY) == (128, 128) and (none.wButtons & 0xF) == 8)
ok, why = v.available()
print("driver available:", ok, "|", why)
try:
    v.update("x360", v.NEUTRAL); check("update without driver raises", ok)
except RuntimeError as e:
    print("OK   update without driver raises a clear message:", e)
try:
    v.update("wii", v.NEUTRAL); check("unknown type rejected", False)
except ValueError as e:
    print("OK   unknown type rejected:", e)
v.neutral(); v.close(); print("OK   neutral()/close() safe with no controller")
print("\nRESULT:", f"FAILED: {bad}" if bad else "all passed")
sys.exit(1 if bad else 0)
