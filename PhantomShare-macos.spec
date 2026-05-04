# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for macOS builds (.app bundle).
from PyInstaller.utils.hooks import collect_all

# macOS uses .icns format for icons
datas = [('assets/icon_32.png', 'assets')]
binaries = []
hiddenimports = ['customtkinter', 'certifi', 'websocket', 'tkinterdnd2']

# Collect all customtkinter resources
tmp_ret = collect_all('customtkinter')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# Collect certifi CA bundle
tmp_ret = collect_all('certifi')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# Try to collect tkinterdnd2 if available
try:
    tmp_ret = collect_all('tkinterdnd2')
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
except Exception:
    pass


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PhantomShare',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=False,  # UPX not commonly used on macOS
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,  # Important for macOS app bundles
    target_arch=None,  # Build for native architecture
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=True,
    upx=False,
    upx_exclude=[],
    name='PhantomShare',
)

# Create macOS .app bundle
app = BUNDLE(
    coll,
    name='PhantomShare.app',
    icon='assets/PhantomShare.icns',  # Convert from .ico or use .icns
    bundle_identifier='org.phantomshare.app',
    info_plist={
        'CFBundleName': 'PhantomShare',
        'CFBundleDisplayName': 'PhantomShare',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSHighResolutionCapable': True,
        'NSRequiresAquaSystemAppearance': False,  # Support dark mode
        'LSMinimumSystemVersion': '10.13.0',
        'NSPrincipalClass': 'NSApplication',
        'CFBundleDocumentTypes': [],
    },
)
