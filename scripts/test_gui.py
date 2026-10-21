"""图形界面的无窗口冒烟测试。

CI 里没有桌面会话，直接开 tk.Tk() 可能失败，所以这里只测「与窗口无关」
的部分：配置收集/落盘/回读、日志队列桥接、停止信号。

用法：
    python scripts/test_gui.py
"""
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import gui  # noqa: E402
from logger import log  # noqa: E402

FAILED = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {name}: got={got!r} want={want!r}")
    if not ok:
        FAILED.append(name)


def test_split():
    print("\n--- 输入解析 ---")
    check("逗号分隔", gui._split("38, 39"), ["38", "39"])
    check("顿号分隔", gui._split("38、39"), ["38", "39"])
    check("空格分隔", gui._split("10:00:00 15:00:00"), ["10:00:00", "15:00:00"])
    check("空串", gui._split("   "), [])


def test_config_roundtrip(tmp_path=None):
    print("\n--- 配置写入与回读 ---")
    values = {
        "test_mode": True,
        "spec": "4核4G",
        "bandwidth": "3M",
        "test_price": "109",
        "prod_prices": ["38", "39"],
        "buy_times": ["10:00:00", "15:00:00"],
        "url": "https://cloud.tencent.com/act/pro/double12-2025#MS",
        "allow_retry_purchase": False,
    }
    # 用假 app 对象，绕开窗口构造
    app = gui.BotApp.__new__(gui.BotApp)
    path = app._write_local_config(values)
    text = Path(path).read_text(encoding="utf-8")
    check("文件已生成", Path(path).is_file(), True)
    check("写入 test_mode", "'test_mode': True" in text, True)
    check("写入测试价 109", "'test_price': '109'" in text, True)
    check("无重复下单开关", "'allow_retry_purchase': False" in text, True)

    # 回读：用 runpy 模拟程序的加载方式
    import runpy

    data = runpy.run_path(str(path))
    check("回读 TARGET_CONFIG 版本", data["TARGET_CONFIG"]["test_price"], "109")
    check("回读 RUN_MODE 模式", data["RUN_MODE"]["test_mode"], True)
    check("回读页面地址", data["PAGE_CONFIG"]["url"], values["url"])

    # 清理，避免污染仓库
    Path(path).unlink()
    return True


def test_log_bridge():
    print("\n--- 日志桥接 ---")
    import logging
    import queue

    q = queue.Queue()
    handler = gui.QueueHandler(q)
    logger = logging.getLogger("TencentCloudBot")
    logger.addHandler(handler)
    try:
        log("界面测试消息", "INFO")
    finally:
        logger.removeHandler(handler)
    level, msg = q.get(timeout=2)
    check("日志进入队列", "界面测试消息" in msg, True)
    check("级别正确", level, "INFO")


def test_stop_flag():
    print("\n--- 停止信号 ---")
    from main import TencentCloudBot

    bot = TencentCloudBot()
    check("默认未停止", bot.stopped, False)
    bot.stop()
    check("stop() 后为真", bot.stopped, True)
    check("重复 stop 无副作用", (bot.stop(), bot.stopped)[1], True)


def main():
    test_split()
    test_config_roundtrip()
    test_log_bridge()
    test_stop_flag()

    if FAILED:
        print(f"\n失败 {len(FAILED)} 项: {FAILED}")
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
