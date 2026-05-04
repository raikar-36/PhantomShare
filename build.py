"""
Build PhantomShare using PyInstaller.

Run:  python build.py

Automatically selects the correct .spec for the current OS:
  - Windows → PhantomShare.spec  (produces PhantomShare.exe)
  - Linux   → PhantomShare-linux.spec  (produces PhantomShare)
  - macOS   → PhantomShare-macos.spec  (produces PhantomShare.app)
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"


def main():
    if IS_WINDOWS:
        spec_file = ROOT / "PhantomShare.spec"
        output_name = "PhantomShare.exe"
        output_path = ROOT / "dist" / output_name
    elif IS_MACOS:
        spec_file = ROOT / "PhantomShare-macos.spec"
        output_name = "PhantomShare.app"
        output_path = ROOT / "dist" / output_name
    else:
        spec_file = ROOT / "PhantomShare-linux.spec"
        output_name = "PhantomShare"
        output_path = ROOT / "dist" / output_name

    if not spec_file.exists():
        print(f"[ERROR] Spec file not found: {spec_file}")
        sys.exit(1)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        str(spec_file),
    ]
    platform_name = "Windows" if IS_WINDOWS else ("macOS" if IS_MACOS else "Linux")
    print(f"Platform: {platform_name}")
    print("Running:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))

    if output_path.exists():
        if output_path.is_dir():
            # For .app bundles, get folder size
            size_bytes = sum(f.stat().st_size for f in output_path.rglob('*') if f.is_file())
        else:
            size_bytes = output_path.stat().st_size
        size_mb = size_bytes / (1024 * 1024)
        print(f"\n[OK] Build complete! {output_path}  ({size_mb:.1f} MB)")
    else:
        print(f"\n[ERROR] Build failed — {output_name} not found")
        sys.exit(1)


if __name__ == "__main__":
    main()
