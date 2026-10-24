# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

datas = []
datas += collect_data_files('certifi')


a = Analysis(
    ['DataGrabber.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
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
    name='DataGrabber',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['DataGrabber.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='DataGrabber',
)
app = BUNDLE(
    coll,
    name='DataGrabber.app',
    icon='DataGrabber.icns',
    bundle_identifier='com.datagrabber.DataGrabber',
    info_plist={
        'NSHighResolutionCapable': True,
        'NSSupportsAutomaticGraphicsSwitching': True,
        'NSSupportsSuddenTermination': False,
        'NSSupportsAutomaticTermination': False,
        'LSMinimumSystemVersion': '15.0',
        'LSMultipleInstancesProhibited': True,
        'CFBundleSupportedPlatforms': ['MacOSX'],
    },
)
