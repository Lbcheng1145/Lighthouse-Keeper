"""
打包入口（PyInstaller 专用）

与 main.py 的区别：
1. 先把 console 编码/异常保护做好，再导入主程序；
2. 打包后（frozen）自动指向随包分发的 Chromium，
   避免要求用户另外执行 `playwright install`；
3. 未捕获异常时暂停，防止双击运行的窗口一闪而过。
"""
import os
import sys
import traceback
from pathlib import Path


def _base_dir() -> Path:
    """EXE 所在目录；源码运行时为脚本目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _configure_browser_path() -> None:
    """让 Playwright 使用随包附带的浏览器目录。

    目录约定: <发布目录>/ms-playwright
    若该目录不存在（用户自己装了），则回退到 Playwright 默认缓存。
    """
    bundled = _base_dir() / "ms-playwright"
    if bundled.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled)


def _ensure_console() -> None:
    """无控制台构建下 stdout 可能为 None，统一兜底为 UTF-8。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is None:
            # 窗口模式（console=False）下没有控制台：给个黑洞流，
            # 免得 logging 的 StreamHandler 和 print 抛 AttributeError。
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except Exception:
                pass
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _acquire_console() -> None:
    """窗口化构建（console=False）下临时要一个控制台。

    只有 --version / --self-check 这类「输出到屏幕」的场景才需要，
    图形界面模式下不能调，否则会多弹一个黑窗口。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        if kernel32.GetConsoleWindow() == 0:
            if not kernel32.AllocConsole():
                return
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
    except Exception:
        pass


def _self_check() -> int:
    """打包自检：浏览器驱动能否在单文件包里正常启动。

    单文件（onefile）启动时会把内容解压到临时目录，Playwright 的 node
    驱动路径最容易出问题，所以留一个不依赖账号、不产生订单的自检。

    窗口化构建没有控制台，结果同时写入 exe 同目录的 self-check.log，
    便于用户/CI 在不看屏幕的情况下确认结果。
    """
    lines = []
    ok = True
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page()
            page.set_content("<h1 id='t'>ok</h1>")
            text = page.inner_text("#t")
            browser.close()
        lines.append(f"SELF-CHECK OK playwright=ok render={text}")
    except Exception:
        ok = False
        lines.append("SELF-CHECK FAIL")
        lines.append(traceback.format_exc())

    report = "\n".join(lines) + "\n"
    for line in lines:
        try:
            print(line)
        except Exception:
            pass
    if not ok:
        try:
            print(report, file=sys.stderr)
        except Exception:
            pass
    try:
        (_base_dir() / "self-check.log").write_text(report, encoding="utf-8")
    except Exception:
        pass
    return 0 if ok else 1


def _run_gui() -> int:
    """打开图形界面；失败时尽量给出可读提示（窗口模式下没有控制台）。"""
    try:
        from gui import run_gui
    except Exception:
        detail = traceback.format_exc()
        try:
            print(detail, file=sys.stderr)
        except Exception:
            pass
        try:
            import tkinter.messagebox as mb

            mb.showerror("无法启动图形界面", detail[-1500:])
        except Exception:
            pass
        return 1
    return run_gui()


def _run_cli() -> int:
    try:
        from main import main as run_bot
        run_bot()
        return 0
    except KeyboardInterrupt:
        return 0
    except SystemExit:
        raise
    except Exception:
        detail = traceback.format_exc()
        try:
            print(detail)
        except Exception:
            pass
        # 日志尽量落盘，便于用户反馈
        try:
            from logger import get_log_file
            print(f"\n日志文件: {get_log_file()}")
        except Exception:
            pass
        try:
            input("\n程序异常结束，按回车键退出...")
        except Exception:
            pass
        return 1


def main() -> int:
    _ensure_console()
    _configure_browser_path()

    args = sys.argv[1:]
    if any(a in ("--version", "-V") for a in args):
        _acquire_console()
        from config import VERSION

        print(f"LighthouseKeeper v{VERSION}")
        return 0
    if "--self-check" in args:
        _acquire_console()
        return _self_check()

    # 默认开图形界面；加 --cli 走原来的纯命令行流程（计划任务/远程用）
    if "--cli" in args:
        sys.argv = [a for a in sys.argv if a != "--cli"]
        return _run_cli()

    return _run_gui()


if __name__ == "__main__":
    sys.exit(main())
