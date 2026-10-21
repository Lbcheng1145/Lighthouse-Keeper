"""CI 冒烟测试：验证打包产物里的运行时是否完整。

检查顺序：
1. exe 存在（文件夹版还要求 `_internal` 存在）
2. `--version` 能输出可识别的版本行，退出码 0
3. `--self-check` 通过：
   * `SELF-CHECK OK` —— 打包内的 Playwright 驱动能启动本机浏览器
   * `GUI-CHECK OK`  —— tkinter 能建窗口（tcl/tk 数据打进去了）

为什么不直接启动程序本体：无参数启动等于打开图形界面，CI 里会一直等着
用户关窗口；而 `--cli` 会真的走抢购流程。`--self-check` 两者都不碰，
既能验证打包完整性，又不会产生任何订单。

用法：
    python scripts/ci_smoke_test.py dist/LighthouseKeeper/LighthouseKeeper.exe
"""
import subprocess
import sys
import tempfile
from pathlib import Path

# Windows CI 的 stdout 默认是 cp1252，打印中文会 UnicodeEncodeError。
# 这个脚本一直被 continue-on-error 兜着，之前静默失败没人发现。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TIMEOUT = 180


def run(exe: Path, *args):
    """跑一次 exe，返回 (exit_code, 合并输出)。"""
    proc = subprocess.run(
        [str(exe), *args],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=TIMEOUT,
        cwd=str(exe.parent),
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python scripts/ci_smoke_test.py <exe 路径>")
        return 2

    exe = Path(sys.argv[1]).resolve()
    folder_edition = exe.parent.name == "LighthouseKeeper"

    print(f"[INFO] 被测程序: {exe}")
    if not exe.is_file():
        print("[FAIL] exe 不存在")
        return 1
    print(f"[INFO] 体积: {exe.stat().st_size / 1048576:.1f} MB")

    if folder_edition:
        internal = exe.parent / "_internal"
        if not internal.is_dir():
            print("[FAIL] 缺少 _internal 目录（文件夹版不完整）")
            return 1
        print("[INFO] _internal 存在")

    # 1) 版本号
    code, out = run(exe, "--version")
    line = next((ln.strip() for ln in out.splitlines() if "LighthouseKeeper v" in ln), "")
    print(f"[INFO] --version -> rc={code} {line!r}")
    if code != 0 or not line:
        print("[FAIL] --version 没有输出可识别的版本行")
        print(out[:800])
        return 1

    # 2) 打包自检
    code, out = run(exe, "--self-check")
    print(f"[INFO] --self-check -> rc={code}")
    report = ""
    for candidate in (exe.parent / "self-check.log",
                      Path(tempfile.gettempdir()) / "lighthouse-self-check.log"):
        if candidate.is_file():
            report = candidate.read_text(encoding="utf-8", errors="replace")
            candidate.unlink(missing_ok=True)
            break

    combined = out + report
    if code != 0 or "SELF-CHECK OK" not in combined:
        print("[FAIL] 浏览器驱动自检未通过")
        print(combined[:1200])
        return 1
    if "GUI-CHECK OK" not in combined:
        print("[FAIL] tkinter 自检未通过（图形接口没打进包里？）")
        print(combined[:1200])
        return 1

    print("[OK] 冒烟测试通过：版本可读、浏览器驱动可用、图形接口可用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
