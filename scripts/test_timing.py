"""倒计时与取消逻辑的回归测试（不依赖浏览器）。

锁定两件事：
  1. 「准点触发」是真的准点：误差在 100ms 内，而不是原来那种提前近 2 秒；
  2. 不会白等：今天的时间点全过去时立即报错返回，而不是空转 2 小时；
  3. 「停止」能在倒计时/登录等待中被及时打断。

用法：
    python scripts/test_timing.py
"""
import datetime
import sys
import threading
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from main import TencentCloudBot  # noqa: E402

FAILED = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {name}: got={got!r} want={want!r}")
    if not ok:
        FAILED.append(name)


def make_bot(buy_times, test_mode=False):
    bot = TencentCloudBot()
    bot.config["run_mode"]["test_mode"] = test_mode
    bot.config["run_mode"]["buy_times"] = buy_times
    return bot


def test_fires_on_time():
    print("\n--- 准点触发精度 ---")
    target = datetime.datetime.now().replace(microsecond=0) + datetime.timedelta(seconds=2)
    bot = make_bot([target.strftime("%H:%M:%S")])

    started = time.time()
    ok = bot._wait_for_buy_time()
    elapsed = time.time() - started

    check("返回 True", ok, True)
    # 相对目标时刻的误差（正数=晚于目标）
    error = (started + elapsed) - target.timestamp()
    print(f"      目标 {target.strftime('%H:%M:%S')}，实际触发误差 {error * 1000:+.0f}ms")
    check("不提前（误差 > -50ms）", error > -0.05, True)
    check("不迟到（误差 < 150ms）", error < 0.15, True)


def test_all_times_passed():
    print("\n--- 时间点已过：不空等 ---")
    past = (datetime.datetime.now() - datetime.timedelta(minutes=5)).strftime("%H:%M:%S")
    bot = make_bot([past])
    started = time.time()
    ok = bot._wait_for_buy_time()
    elapsed = time.time() - started
    check("返回 False", ok, False)
    check(f"立即返回（{elapsed:.2f}s ≤ 1s）", elapsed <= 1.0, True)


def test_stop_interrupts_countdown():
    print("\n--- 「停止」能打断倒计时 ---")
    later = (datetime.datetime.now() + datetime.timedelta(hours=1)).strftime("%H:%M:%S")
    bot = make_bot([later])
    result = {}

    def run():
        started = time.time()
        result["ok"] = bot._wait_for_buy_time()
        result["elapsed"] = time.time() - started

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    time.sleep(0.4)
    bot.stop()
    thread.join(timeout=3)

    check("线程已退出", thread.is_alive(), False)
    check("返回 False", result.get("ok"), False)
    check(f"及时退出（{result.get('elapsed', 9):.2f}s ≤ 1.5s）", result.get("elapsed", 9) <= 1.5, True)


def test_test_mode_immediate():
    print("\n--- 测试模式立即开始 ---")
    bot = make_bot(["10:00:00"], test_mode=True)
    started = time.time()
    check("返回 True", bot._wait_for_buy_time(), True)
    check(f"立即返回（{time.time() - started:.2f}s）", time.time() - started < 0.5, True)


def test_bad_time_format():
    print("\n--- 时间格式错误不崩溃 ---")
    bot = make_bot(["25:99:99", "abc"])
    check("返回 False 而不是抛异常", bot._wait_for_buy_time(), False)


def test_cancellable_sleep():
    print("\n--- 可取消等待 ---")
    bot = make_bot(["10:00:00"])
    started = time.time()
    bot._sleep_cancellable(5)
    check(f"未停止时睡满（{time.time() - started:.1f}s ≥ 4.5s）", time.time() - started >= 4.5, True)

    bot.stop()
    started = time.time()
    bot._sleep_cancellable(5)
    check(f"已停止时立即返回（{time.time() - started:.2f}s < 0.3s）", time.time() - started < 0.3, True)


def main():
    test_fires_on_time()
    test_all_times_passed()
    test_stop_interrupts_countdown()
    test_test_mode_immediate()
    test_bad_time_format()
    test_cancellable_sleep()

    if FAILED:
        print(f"\n失败 {len(FAILED)} 项: {FAILED}")
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
