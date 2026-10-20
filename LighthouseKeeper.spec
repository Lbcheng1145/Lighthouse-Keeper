# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置

构建（在项目根目录执行）:
    pyinstaller LighthouseKeeper.spec --noconfirm

产物:
    dist/LighthouseKeeper/LighthouseKeeper.exe  (onedir，启动快)

说明：不打包 Chromium，运行时调用本机 Edge/Chrome。
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

PROJECT = Path(SPECPATH)

# Playwright 的驱动（node + package）仍需打包，否则无法与浏览器通信。
datas = collect_data_files("playwright")

hiddenimports = [
    "playwright",
    "playwright.sync_api",
    "playwright._impl._driver",
    "greenlet",
]

a = Analysis(
    [str(PROJECT / "launcher.py")],
    pathex=[str(PROJECT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "unittest", "pydoc", "doctest",
        "playwright.async_api",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LighthouseKeeper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LighthouseKeeper",
)
