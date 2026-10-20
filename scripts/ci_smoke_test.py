"""CI 冒烟测试：验证打包产物能真正启动。

检查顺序：
1. exe 与 _internal 存在
2. 启动 exe，能生成日志文件
3. 日志中出现启动横幅与配置加载
4. 进程在限定时间内没有崩溃退出

注意：不强制要求浏览器成功启动——GitHub Runner 的图形环境
不一定可靠，浏览器失败属于环境限制，不作为构建失败依据，
但会在输出里明确提示。

用法：
    python scripts/ci_smoke_test.py dist/LighthouseKeeper/LighthouseKeeper.exe

注意：若在当前会话的文件沙箱内运行，且被测试程序位于系统临时目录
（如 %TEMP%），子进程可能无法在该目录创建 logs/browser-data，
从而表现为"未生成日志"。请把产物放到普通目录下再测。
"""
import subprocess
import sys
import time
from pathlib import Path

TIMEOUT = 40          # 最多等多少秒
# 必须出现在日志里的标记。
# 注意：启动横幅是 print() 输出到 stdout 的，不会进日志，不能用作标记。
# "用户数据目录" 依赖浏览器启动成功，在无图形环境的 Runner 上可能不出现，
# 因此只作提示，不作为失败依据。
REQUIRED_MARKERS = [
    "日志文件",          # logger 初始化成功
]
OPTIONAL_MARKERS = [
    "用户数据目录",      # 浏览器启动成功
]


def _mode_ok(content: str) -> bool:
    """启动横幅里会打印模式，二者其一即可。"""
    return ("生产模式" in content) or ("测试模式" in content)


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}")
    sys.exit(1)


def main() -> None:
    if len(sys.argv) < 2:
        fail("用法: ci_smoke_test.py <exe路径>")
    exe = Path(sys.argv[1]).resolve()
    if not exe.is_file():
        fail(f"找不到 exe: {exe}")

    internal = exe.parent / "_internal"
    if not internal.is_dir():
        fail(f"找不到 _internal 目录: {internal}")

    logs_dir = exe.parent / "logs"
    before = set(logs_dir.glob("*.log")) if logs_dir.is_dir() else set()

    print(f"[INFO] 启动 {exe.name} ...")
    proc = subprocess.Popen(
        [str(exe)],
        cwd=str(exe.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )

    log_file = None
    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        if logs_dir.is_dir():
            new = set(logs_dir.glob("*.log")) - before
            if new:
                log_file = max(new, key=lambda p: p.stat().st_mtime)
                break
        if proc.poll() is not None:
            # 进程退出了，先检查是否其实写了日志
            if logs_dir.is_dir():
                new = set(logs_dir.glob("*.log")) - before
                if new:
                    log_file = max(new, key=lambda p: p.stat().st_mtime)
                    break
            fail(f"进程提前退出，exit={proc.returncode}（未生成日志）")
        time.sleep(0.5)

    if log_file is None:
        proc.kill()
        fail(f"{TIMEOUT}s 内没有生成日志文件")

    # 等日志写够内容
    content = ""
    for _ in range(40):          # 最多 20 秒
        try:
            content = log_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = ""
        if all(m in content for m in REQUIRED_MARKERS) and _mode_ok(content):
            break
        if proc.poll() is not None:
            break                # 进程已退出，不再空等
        time.sleep(0.5)

    alive = proc.poll() is None
    proc.kill()

    # 进程退出后日志可能才刷完，重读一次
    try:
        content = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass

    print(f"[INFO] 日志文件: {log_file.name}")
    print("-------- 日志摘要 --------")
    for line in content.splitlines()[:15]:
        print("  " + line)
    print("--------------------------")

    missing = [m for m in REQUIRED_MARKERS if m not in content]
    if missing:
        fail(f"日志缺少关键标记: {missing}")
    if not _mode_ok(content):
        fail("日志中未检测到运行模式，程序可能未正常初始化")

    browser_ok = all(m in content for m in OPTIONAL_MARKERS)
    print(
        "[INFO] 浏览器启动: "
        + ("成功" if browser_ok else "未确认（无图形环境的 Runner 上属正常）")
    )

    if not alive:
        fail("启动过程中进程异常退出")

    print("[OK] 冒烟测试通过")


if __name__ == "__main__":
    main()
