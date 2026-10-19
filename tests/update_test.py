"""The update check: compares versions correctly and only links to GitHub. One real request to GitHub at the end."""
import os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="kovrel-update-")
os.environ["KOVREL_FILES_DIR"] = os.path.join(os.environ["APPDATA"], "files")
sys.path.insert(0, ROOT)
import server

bad = []
def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond: bad.append(name)

page = "https://github.com/Nyxaliths/kovrel/releases/tag/v2.1.0"
gh = lambda tag, **kw: (lambda: {"tag_name": tag, "html_url": kw.get("url", page), **kw})
check("newer release found", server.check_for_update("2.0.1", gh("v2.1.0")) == ("2.1.0", page))
check("same version: nothing", server.check_for_update("2.0.1", gh("v2.0.1")) is None)
check("older release: nothing", server.check_for_update("2.0.1", gh("v1.9.9")) is None)
check("2.0.10 is newer than 2.0.9 (numbers, not text)", server.check_for_update("2.0.9", gh("v2.0.10"))[0] == "2.0.10")
check("pre-releases are ignored", server.check_for_update("2.0.1", gh("v3.0.0", prerelease=True)) is None)
check("a link that isn't GitHub is replaced by the release page",
      server.check_for_update("2.0.1", gh("v2.1.0", url="https://evil.example/x.exe"))[1] == server.RELEASES_PAGE)
try:
    live = server.check_for_update("0.0.1")
    check("real request to GitHub works", live is not None and live[1].startswith("https://github.com/Nyxaliths/kovrel/"), str(live))
    print("     latest on GitHub:", live[0])
except OSError as e:
    print("SKIP real request (no internet):", e)
print("\nRESULT:", f"{len(bad)} FAILED: {bad}" if bad else "all passed")
sys.exit(1 if bad else 0)
