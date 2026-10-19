"""Builds Kovrel-Setup.exe.

    python installer/build.py            # everything
    python installer/build.py app        # only step 1 (build/dist/Kovrel)
    python installer/build.py setup      # only steps 2-3, from an existing build/dist/Kovrel

1. Bundles the app (Kovrel.pyw + server + web page) into Kovrel.exe with PyInstaller.
2. Zips it with the uninstaller into payload.zip.
3. Bundles the setup wizard + payload into a single Kovrel-Setup.exe.
Output: build/Kovrel-Setup.exe

The release workflow runs "app" and "setup" separately so Kovrel.exe can be code-signed in between.
Both exes get Windows version info (product "Kovrel", version from server.py).
"""
import re
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


def read_version():
    """VERSION from server.py, which must match installer/setup.pyw. (Read as text: importing server
    would touch the real settings folder.)"""
    app = re.search(r'^VERSION = "([\d.]+)"', (ROOT / "server.py").read_text("utf-8-sig"), re.M).group(1)
    setup = re.search(r'^APP, VERSION, PUBLISHER = "Kovrel", "([\d.]+)"',
                      (ROOT / "installer" / "setup.pyw").read_text("utf-8-sig"), re.M).group(1)
    if app != setup:
        sys.exit(f"Version mismatch: server.py says {app}, installer/setup.pyw says {setup}")
    return app


def version_file(name, description):
    """Writes a PyInstaller version resource for <name>.exe and returns its path."""
    v = read_version()
    nums = tuple(int(n) for n in (v.split(".") + ["0"] * 4)[:4])
    strings = {
        "CompanyName": "The Kovrel authors", "FileDescription": description, "FileVersion": v,
        "InternalName": name, "LegalCopyright": "Copyright (c) 2026 The Kovrel authors. MIT License.",
        "OriginalFilename": f"{name}.exe", "ProductName": "Kovrel", "ProductVersion": v,
    }
    table = ", ".join(f"StringStruct({k!r}, {s!r})" for k, s in strings.items())
    WORK.mkdir(parents=True, exist_ok=True)
    path = WORK / f"{name}-version.txt"
    path.write_text(
        f"VSVersionInfo(ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, "
        f"OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)), kids=["
        f"StringFileInfo([StringTable('040904B0', [{table}])]), "
        f"VarFileInfo([VarStruct('Translation', [1033, 1200])])])\n", "utf-8")
    return path


def pyinstaller(*args):
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
                    "--workpath", str(WORK), "--specpath", str(WORK), *args], check=True)


def build_app():
    shutil.rmtree(BUILD, ignore_errors=True)
    print("[1/3] Building Kovrel.exe…")
    pyinstaller(
        "--name", "Kovrel", "--windowed", "--onedir", "--icon", str(ICON),
        "--version-file", str(version_file("Kovrel", "Kovrel")),
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


def build_setup():
    app = DIST / "Kovrel"
    if not (app / "Kovrel.exe").exists():
        sys.exit(f"{app / 'Kovrel.exe'} is missing: run 'python installer/build.py app' first")

    print("[2/3] Packing payload…")
    WORK.mkdir(parents=True, exist_ok=True)
    payload = WORK / "payload.zip"
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in app.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(app))
        z.write(ROOT / "installer" / "uninstall.ps1", "uninstall.ps1")

    print("[3/3] Building Kovrel-Setup.exe…")
    pyinstaller(
        "--name", "Kovrel-Setup", "--windowed", "--onefile", "--icon", str(ICON),
        "--version-file", str(version_file("Kovrel-Setup", "Kovrel Setup")),
        "--distpath", str(BUILD),
        "--add-data", f"{payload}{SEP}.",
        "--add-data", f"{ROOT / 'assets' / 'logo.png'}{SEP}.",
        "--add-data", f"{ICON}{SEP}.",
        str(ROOT / "installer" / "setup.pyw"),
    )
    out = BUILD / "Kovrel-Setup.exe"
    print(f"Done: {out}  ({out.stat().st_size / 2**20:.1f} MB)")


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    if stage not in ("all", "app", "setup"):
        sys.exit("usage: python installer/build.py [app|setup]")
    if stage in ("all", "app"):
        build_app()
    if stage in ("all", "setup"):
        build_setup()


if __name__ == "__main__":
    main()
