"""Runs the test scripts and prints one summary line each.

    python tests/run_all.py          fast checks (about a minute, no windows open)
    python tests/run_all.py --all    also the PC window test, video measurement and the installed-exe
                                     test (build first: python installer/build.py)

Every test uses a throwaway settings folder; none of them touch your real Kovrel settings or files.
They do use this PC's screen, sound and process list (read-only), and the window test opens windows.
"""
import os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
FAST = ["sec_test.py", "migrate_test.py", "remote_test.py", "update_test.py", "page_check.py", "vigem_test.py", "h264_test.py", "server_test.py"]
SLOW = ["gui_test.py", "video_test.py", "exe_test.py"]


def run(name):
    t0 = time.time()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, os.path.join(HERE, name)], capture_output=True, text=True, env=env, timeout=600)
    out = r.stdout + r.stderr
    if name == "gui_test.py":   # the window test writes its results to a file next to its screenshots
        res = [l for l in out.splitlines() if l.startswith("RESULTS ")]
        out += open(res[0][8:], encoding="utf-8").read() if res else ""
    fails = [l for l in out.splitlines() if l.startswith("FAIL") or "Traceback" in l]
    oks = sum(1 for l in out.splitlines() if l.startswith("OK"))
    good = not fails and r.returncode == 0
    print(f"{'PASS' if good else 'FAIL'}  {name:18} {oks:4} checks  {time.time() - t0:5.1f}s")
    for l in fails[:10]:
        print("      " + l)
    if not good and not fails:
        print("      " + "\n      ".join(out.strip().splitlines()[-8:]))
    return good


if __name__ == "__main__":
    names = FAST + (SLOW if "--all" in sys.argv else [])
    results = [run(n) for n in names]
    print(f"\n{sum(results)}/{len(results)} test scripts passed")
    sys.exit(0 if all(results) else 1)
