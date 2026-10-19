"""Checks the phone page: its script must parse (needs Node.js), every id the script looks up must
exist, and the page must not load anything from the internet."""
import os, re, subprocess, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

html = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
bad = []
def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond: bad.append(name)

script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
    f.write(script)
try:
    r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True)
    check("page script parses", r.returncode == 0, r.stderr.strip()[:300])
except FileNotFoundError:
    print("SKIP page script parse (Node.js not installed)")
finally:
    os.unlink(f.name)

ids = set(re.findall(r'id="([^"]+)"', html))
used = set(re.findall(r"\$\('#([A-Za-z][\w-]*)", script))
check("every #id the script uses exists in the page", used <= ids, ", ".join(sorted(used - ids)))
dupes = sorted({i for i in re.findall(r'id="([^"]+)"', html) if re.findall(rf'id="{re.escape(i)}"', html).__len__() > 1})
check("no duplicate ids", not dupes, ", ".join(dupes))
remote = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
check("nothing is loaded from the internet (links people tap are fine)", all("github.com" in u for u in remote), ", ".join(remote))
print("\nRESULT:", f"{len(bad)} FAILED: {bad}" if bad else "all passed")
sys.exit(1 if bad else 0)
