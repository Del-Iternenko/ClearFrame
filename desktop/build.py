"""Builds dist/ClearFrame/ClearFrame.exe: the desktop app with its own Python, no install needed.

    .venv/Scripts/python desktop/build.py

Needs the project .venv with desktop/requirements.txt and mpv in vendor/ (python bench/bench.py setup).
Output is a folder (exe + _internal/ + mpv/) rather than a single file: it starts instantly
instead of unpacking ~150 MB to %TEMP% on every launch. Zip the folder to share it.
"""
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
APP = DIST / "ClearFrame"
MPV = ROOT / "vendor" / "mpv"
PYINSTALLER = "pyinstaller==6.22.3"

sys.path.insert(0, str(HERE))
from main import VERSION, tray_image  # noqa: E402


def make_icon(path):
    img = tray_image(True, "#76b900")
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])


def version_file(path):
    nums = (VERSION.split(".") + ["0"] * 4)[:4]
    tup = ", ".join(nums)
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tup}), prodvers=({tup})),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Del_Iter'),
    StringStruct('FileDescription', 'ClearFrame - real-time video upscaling'),
    StringStruct('FileVersion', '{VERSION}'),
    StringStruct('ProductName', 'ClearFrame'),
    StringStruct('ProductVersion', '{VERSION}'),
    StringStruct('LegalCopyright', 'MIT License'),
    StringStruct('OriginalFilename', 'ClearFrame.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
""", encoding="utf-8")


def main():
    if not (MPV / "mpv.exe").exists():
        sys.exit("mpv is missing: run  python bench/bench.py setup  first")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", PYINSTALLER], check=True)
    BUILD.mkdir(exist_ok=True)
    icon, ver = BUILD / "clearframe.ico", BUILD / "version.txt"
    make_icon(icon)
    version_file(ver)
    # windows_capture imports OpenCV only for save_as_image(), which we never call: a stub saves ~110 MB
    stub = BUILD / "no_cv2.py"
    stub.write_text("import sys, types\nsys.modules.setdefault('cv2', types.ModuleType('cv2'))\n", encoding="utf-8")
    shutil.rmtree(APP, ignore_errors=True)
    subprocess.run([
        sys.executable, "-m", "PyInstaller", str(HERE / "main.py"),
        "--name", "ClearFrame", "--onedir", "--windowed", "--noconfirm", "--clean",
        "--icon", str(icon), "--version-file", str(ver),
        "--paths", str(HERE), "--paths", str(ROOT / "live"),
        "--hidden-import", "live_upscale", "--hidden-import", "host_window", "--hidden-import", "native_res",
        "--add-data", f"{HERE / 'ui'};ui",
        "--exclude-module", "cv2", "--runtime-hook", str(stub),
        "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"), "--specpath", str(BUILD),
    ], check=True)
    # mpv next to the exe (live_upscale looks for mpv/mpv.exe there); skip its installer/updater scripts
    shutil.copytree(MPV, APP / "mpv", ignore=shutil.ignore_patterns("installer", "doc", "updater.bat", "mpv-*register.bat"))
    shutil.copy(ROOT / "LICENSE", APP / "LICENSE.txt")
    size = sum(f.stat().st_size for f in APP.rglob("*") if f.is_file()) / 2**20
    print(f"\nbuilt {APP / 'ClearFrame.exe'}  ({size:.0f} MB folder)")


if __name__ == "__main__":
    main()
