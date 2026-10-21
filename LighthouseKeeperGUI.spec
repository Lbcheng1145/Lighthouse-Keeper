# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— 图形界面 + 单文件

构建（在项目根目录执行）:
    pyinstaller LighthouseKeeperGUI.spec --noconfirm

产物:
    dist-gui/LighthouseKeeper.exe   ← 单个文件，双击即开窗口

与 LighthouseKeeper.spec（命令行 + 文件夹版）的区别：
  * onefile：所有依赖塞进一个 exe，运行时解压到临时目录，启动会慢几秒；
  * console=False：不弹黑窗口，双击直接出图形界面；
  * 仍然是「只打包 Playwright 驱动、跑本机 Edge/Chrome」，不附带浏览器本体。
    默认开图形界面，加 --cli 仍可无界面运行，--version / --self-check 用于排查。
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

_ver_tuple = ", ".join(VERSION.split(".") + ["0"])
_version_file = PROJECT / "build" / "version_info_gui.txt"
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
        StringStruct('FileDescription', '腾讯云轻量服务器自动抢购脚本（图形界面）'),
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

datas = collect_data_files("playwright")

hiddenimports = [
    "playwright",
    "playwright.sync_api",
    "playwright._impl._driver",
    "greenlet",
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
    # tkinter 要留着（图形界面用），只排掉真正没用的
    excludes=["playwright.async_api", "pydoc", "doctest", "unittest"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="LighthouseKeeper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
    version=str(_version_file),
)
