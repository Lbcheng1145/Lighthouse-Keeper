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
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> int:
    _ensure_console()
    _configure_browser_path()

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


if __name__ == "__main__":
    sys.exit(main())
