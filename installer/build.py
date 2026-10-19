"""Builds Kovrel-Setup.exe.

    python installer/build.py

1. Bundles the app (Kovrel.pyw + server + web page) into Kovrel.exe with PyInstaller.
2. Zips it with the uninstaller into payload.zip.
3. Bundles the setup wizard + payload into a single Kovrel-Setup.exe.
Output: build/Kovrel-Setup.exe
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
WORK, DIST = BUILD / "work", BUILD / "dist"
ICON = ROOT / "assets" / "Kovrel.ico"
SEP = ";"  # PyInstaller --add-data separator on Windows


def pyinstaller(*args):
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
                    "--workpath", str(WORK), "--specpath", str(WORK), *args], check=True)


def main():
    shutil.rmtree(BUILD, ignore_errors=True)

    print("[1/3] Building Kovrel.exe…")
    pyinstaller(
        "--name", "Kovrel", "--windowed", "--onedir", "--icon", str(ICON),
        "--distpath", str(DIST),
        "--paths", str(ROOT),
        "--add-data", f"{ROOT / 'web'}{SEP}web",
        "--add-data", f"{ROOT / 'assets'}{SEP}assets",
        "--add-binary", f"{ROOT / 'vendor' / 'ViGEmClient.dll'}{SEP}.",
        "--add-data", f"{ROOT / 'vendor' / 'NOTICE.txt'}{SEP}.",
        "--add-data", f"{ROOT / 'vendor' / 'LICENSE-vgamepad.txt'}{SEP}.",
        "--hidden-import", "pystray._win32",
        "--hidden-import", "security", "--hidden-import", "server", "--hidden-import", "vigem",
        "--collect-submodules", "winrt",
        "--collect-submodules", "pycaw",
        "--collect-submodules", "comtypes",
        "--collect-submodules", "cryptography",
        str(ROOT / "Kovrel.pyw"),
    )

    print("[2/3] Packing payload…")
    app = DIST / "Kovrel"
    payload = WORK / "payload.zip"
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in app.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(app))
        z.write(ROOT / "installer" / "uninstall.ps1", "uninstall.ps1")

    print("[3/3] Building Kovrel-Setup.exe…")
    pyinstaller(
        "--name", "Kovrel-Setup", "--windowed", "--onefile", "--icon", str(ICON),
        "--distpath", str(BUILD),
        "--add-data", f"{payload}{SEP}.",
        "--add-data", f"{ROOT / 'assets' / 'logo.png'}{SEP}.",
        "--add-data", f"{ICON}{SEP}.",
        str(ROOT / "installer" / "setup.pyw"),
    )
    out = BUILD / "Kovrel-Setup.exe"
    print(f"Done: {out}  ({out.stat().st_size / 2**20:.1f} MB)")


if __name__ == "__main__":
    main()
