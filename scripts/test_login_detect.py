"""登录判定回归测试。

背景（实测日志 15:23:17）：点「立即购买」后，配置页顶栏的「登录」字样
命中了 `text=登录` 这个超宽选择器，于是被判为「需要登录」；接着找不到
二维码，却仍然进入 5 分钟扫码等待，抢购机会被彻底耗掉。

本测试锁住修复后的判定：
  1. 只有「登录」字样、没有弹窗/二维码 → 按已登录处理（不误判）
  2. 真实登录弹窗 / 二维码 / 停在登录页 → 判定需要登录
  3. 疑似需要登录但没有二维码时，handle_login_flow 不得长时间阻塞

用法：
    python scripts/test_login_detect.py
"""
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from login_handler import LoginHandler  # noqa: E402

FAILED = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {name}: got={got!r} want={want!r}")
    if not ok:
        FAILED.append(name)


class FakeEl:
    def __init__(self, visible=False):
        self._visible = visible

    def is_visible(self, timeout=None):
        return self._visible


class FakeLocator:
    def __init__(self, el):
        self._el = el

    @property
    def first(self):
        return self._el


class FakePage:
    """按选择器返回「可见/不可见」，模拟登录判定的各种页面状态。"""

    def __init__(self, visible_selectors=(), url="https://cloud.tencent.com/act/pro/x"):
        self.visible = set(visible_selectors)
        self.url = url
        self._dialog_shown_after = None

    def locator(self, selector):
        return FakeLocator(FakeEl(selector in self.visible))

    def wait_for_timeout(self, ms):
        pass

    def wait_for_load_state(self, state):
        pass


def make_handler(page):
    return LoginHandler(page, {"login_detect": 10, "login_wait": 300})


def test_weak_only_is_not_login():
    print("\n--- 只有「登录」字样：不误判 ---")
    page = FakePage({"text=登录", '[class*="login"]'})
    handler = make_handler(page)
    check("弱证据不算登录", handler.detect_login_required(max_attempts=1), False)

    # 配置弹窗同时存在时也一样
    page2 = FakePage({"text=登录", ".uno3-dialog"})
    check("配置页有「登录」字样也不算登录", make_handler(page2).detect_login_required(max_attempts=1), False)


def test_strong_evidence():
    print("\n--- 真实登录弹窗：必须判定 ---")
    for selector in (".accsys-tp-modal", "#accsys-tp-overlay-root", "text=扫码登录"):
        page = FakePage({selector})
        check(f"{selector} -> 需要登录", make_handler(page).detect_login_required(max_attempts=1), True)

    qr_page = FakePage({"text=登录", 'img[src*="qrcode"]'})
    check("弱证据 + 二维码 -> 需要登录", make_handler(qr_page).detect_login_required(max_attempts=1), True)

    login_url = FakePage(set(), url="https://cloud.tencent.com/login?s_url=x")
    check("停在登录页 -> 需要登录", make_handler(login_url).detect_login_required(max_attempts=1), True)


def test_no_qr_does_not_block():
    print("\n--- 疑似登录但没二维码：不得长时间阻塞 ---")
    # 命中强证据（弹窗容器），但始终没有二维码，也没有配置弹窗
    page = FakePage({".accsys-tp-modal"})
    handler = make_handler(page)
    handler.NO_QR_GRACE_SECONDS = 2  # 缩短，测试别真等 30 秒

    started = time.time()
    result = handler.handle_login_flow("https://cloud.tencent.com/act/pro/x")
    elapsed = time.time() - started

    check("返回 True（继续流程）", result, True)
    check(f"耗时可接受（{elapsed:.1f}s ≤ 6s）", elapsed <= 6, True)

    # 二维码在窗口期内出现 -> 应进入扫码流程（这里用 0 秒窗口模拟等不到）
    page2 = FakePage({".accsys-tp-modal", 'img[src*="qrcode"]'})
    handler2 = make_handler(page2)
    handler2.NO_QR_GRACE_SECONDS = 2
    handler2.wait_for_login_complete = lambda qr, url: True
    check("有二维码时走扫码分支", handler2.handle_login_flow("https://x"), True)


def test_dialog_shortcut():
    print("\n--- 出现配置弹窗时立刻继续 ---")
    page = FakePage({".accsys-tp-modal", ".uno3-dialog"})
    handler = make_handler(page)
    handler.NO_QR_GRACE_SECONDS = 30  # 若没有快捷判断，这里会等 30 秒
    started = time.time()
    result = handler.handle_login_flow("https://x")
    elapsed = time.time() - started

    check("返回 True", result, True)
    check(f"立刻返回（{elapsed:.1f}s ≤ 3s）", elapsed <= 3, True)


def main():
    test_weak_only_is_not_login()
    test_strong_evidence()
    test_no_qr_does_not_block()
    test_dialog_shortcut()

    if FAILED:
        print(f"\n失败 {len(FAILED)} 项: {FAILED}")
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
