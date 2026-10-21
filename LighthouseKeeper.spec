# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置

构建（在项目根目录执行）:
    pyinstaller LighthouseKeeper.spec --noconfirm

产物:
    dist/LighthouseKeeper/LighthouseKeeper.exe  (onedir，启动快)

说明：不打包 Chromium，运行时调用本机 Edge/Chrome。
"""
from pathlib import Path
from re import MULTILINE, search

from PyInstaller.utils.hooks import collect_data_files

PROJECT = Path(SPECPATH)


def _read_version() -> str:
    """从 config.py 读取 VERSION —— 版本号只维护这一处。"""
    text = (PROJECT / "config.py").read_text(encoding="utf-8")
    matched = search(r'^VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', text, MULTILINE)
    if not matched:
        raise SystemExit("config.py 里找不到 VERSION 定义")
    return matched.group(1)


VERSION = _read_version()

# 生成 Windows 文件版本资源，让 EXE 属性里显示的版本与实际发版一致
_ver_tuple = ", ".join(VERSION.split(".") + ["0"])
_version_file = PROJECT / "build" / "version_info.txt"
_version_file.parent.mkdir(parents=True, exist_ok=True)
_version_file.write_text(
    f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({_ver_tuple}),
    prodvers=({_ver_tuple}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404b0', [
        StringStruct('CompanyName', 'Lighthouse-Keeper'),
        StringStruct('FileDescription', '腾讯云轻量服务器自动抢购脚本'),
        StringStruct('FileVersion', '{VERSION}'),
        StringStruct('InternalName', 'LighthouseKeeper'),
        StringStruct('OriginalFilename', 'LighthouseKeeper.exe'),
        StringStruct('ProductName', 'LighthouseKeeper'),
        StringStruct('ProductVersion', '{VERSION}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""",
    encoding="utf-8",
)

# Playwright 的驱动（node + package）仍需打包，否则无法与浏览器通信。
datas = collect_data_files("playwright")

hiddenimports = [
    "playwright",
    "playwright.sync_api",
    "playwright._impl._driver",
    "greenlet",
    # 文件夹版默认也是图形界面入口，tkinter 必须打进去。
    # 早先这里把 tkinter 放进了 excludes，结果双击 exe 直接报
    # "No module named tkinter" —— 用 --self-check 的 GUI-CHECK 发现的。
    "gui",
    "tkinter",
    "tkinter.ttk",
    "tkinter.scrolledtext",
    "tkinter.messagebox",
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
        "unittest", "pydoc", "doctest",
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
    version=str(_version_file),
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
