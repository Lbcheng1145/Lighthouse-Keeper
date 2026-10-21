"""选品判据回归测试（不依赖浏览器，可在 CI 直接运行）。

背景：早期选品只看「卡片含 4核4G」+ 裸数字价格正则，实测会在抢购时刻
选到错误商品。本测试锁定修复后的判据：

  1. 价格必须是真正的价格文案（¥38 / 38元 / 活动价：38），
     不能是「已抢38%」「38.35元」「398元」「3.8折」这类页面噪声
  2. 购买按钮只认明确的购买动作，不认「添加提醒」「已抢X%」
  3. find_and_click 任何分支都返回二元组，绝不返回 None

用法：
    python scripts/test_product_matcher.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from product_finder import ProductFinder  # noqa: E402
from utils import match_price_in_card  # noqa: E402

FAILED = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {name}: got={got!r} want={want!r}")
    if not ok:
        FAILED.append(name)


class FakeEl:
    def __init__(self, text):
        self._text = text

    def is_visible(self, timeout=None):
        return True

    def inner_text(self):
        return self._text


class FakeLocator:
    def __init__(self, els):
        self._els = els

    def all(self):
        return self._els


class FakeCard:
    """按选择器返回预置元素，模拟 card.locator(sel).all()。"""

    def __init__(self, mapping):
        self._mapping = mapping

    def locator(self, sel):
        return FakeLocator(self._mapping.get(sel, []))


class StubFinder(ProductFinder):
    """跳过真实页面，直接喂一张命中卡片。"""

    def __init__(self, card):
        super().__init__(None, {"text": "4核4G"}, {})
        self._card = card

    def _collect_candidates(self, prices):
        return [
            {
                "card": self._card,
                "text": "4核4G3M 活动价：38元 添加提醒",
                "order": 0,
                "score": 20,
                "price": "38",
            }
        ]
def test_price():
    print("\n--- 价格文案判据 ---")
    check("¥38", match_price_in_card("活动价：¥38", "38"), True)
    check("￥38.00", match_price_in_card("￥38.00", "38"), True)
    check("38元", match_price_in_card("活动价：38元", "38"), True)
    check("38 元（换行分隔）", match_price_in_card("活动价：\n38\n元", "38"), True)
    check("38元/年", match_price_in_card("活动价：38元/年", "38"), True)
    check("活动价：38（无单位）", match_price_in_card("活动价：38", "38"), True)
    check("抢购价 38", match_price_in_card("抢购价\n38", "38"), True)
    check("39.00元/月 命中39", match_price_in_card("日常价：39.00元/月", "39"), True)

    check("已抢38%（进度条）", match_price_in_card("已抢38%", "38"), False)
    check("38.35元（小数）", match_price_in_card("活动价：38.35元", "38"), False)
    check("398元（不同价）", match_price_in_card("活动价：398元", "38"), False)
    check("3.8折（折扣）", match_price_in_card("3.8折", "38"), False)
    check("38GB 流量", match_price_in_card("38GB", "38"), False)
    check("原价：38（非活动价）", match_price_in_card("原价：38", "38"), False)


def test_button():
    print("\n--- 购买按钮判据 ---")
    finder = ProductFinder(page=None, target_config={"text": "4核4G"}, timeout_config={})

    check("只有添加提醒 -> 无按钮", finder._find_buy_button(FakeCard({"button": [FakeEl("添加提醒")]})), None)
    check(
        "进度条+提醒 -> 无按钮",
        finder._find_buy_button(FakeCard({"button": [FakeEl("0%\n已抢0%\n添加提醒")]})),
        None,
    )

    got = finder._find_buy_button(FakeCard({"button": [FakeEl("立即抢购"), FakeEl("查看详情")]}))
    check("立即抢购 -> 命中", got.inner_text() if got else None, "立即抢购")

    container = FakeCard(
        {"a": [FakeEl("轻量应用服务器 4核4G3M 建站、Web应用、电商独立站等高性价比选择 立即购买")]}
    )
    check("祖先容器（文案过长）-> 不选", finder._find_buy_button(container), None)


def test_return_contract():
    print("\n--- find_and_click 返回契约 ---")
    finder = StubFinder(FakeCard({"button": [FakeEl("添加提醒")]}))
    result = finder.find_and_click(["38"], click=True)
    check("卡片命中但按钮不合格 -> 二元组", isinstance(result, tuple), True)
    check("且 success=False", result[0] if isinstance(result, tuple) else "NOT_TUPLE", False)
    check("且元素为 None", result[1] if isinstance(result, tuple) else "NOT_TUPLE", None)

    # 赛前预定位：按钮还没开放（「添加提醒」）也应成功返回卡片位置，
    # 否则主循环会一直刷新页面、永远进不到倒计时。
    pre = StubFinder(FakeCard({"button": [FakeEl("添加提醒")]}))
    pre_result = pre.find_and_click(["38"], click=False, require_buyable=False)
    check("赛前预定位 -> 成功", pre_result[0], True)
    check("赛前预定位 -> 返回卡片本身", pre_result[1] is pre._card, True)


class FakeMouse:
    def __init__(self, page=None):
        self.wheels = 0
        self._page = page

    def wheel(self, x, y):
        self.wheels += 1
        if self._page is not None:
            self._page._wheels += 1


class FakeKeyboard:
    def press(self, key):
        return None


class FakePage:
    """最小页面桩：记录滚动次数，可设定「第几屏之后秒杀模块才出现」。"""

    def __init__(self, ready_after_wheels=None):
        self._wheels = 0
        self.mouse = FakeMouse(self)
        self.keyboard = FakeKeyboard()
        self.ready_after_wheels = ready_after_wheels

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, script):
        return False

    def locator(self, sel):
        return FakeLocator([])

    def get_by_text(self, text, exact=False):
        return FakeCount(
            1 if (self.ready_after_wheels is not None and self._wheels >= self.ready_after_wheels) else 0
        )


class FakeCount:
    def __init__(self, n):
        self._n = n

    def count(self):
        return self._n


class LazyFinder(StubFinder):
    """前 N 次扫描为空，之后才命中 —— 模拟秒杀模块懒渲染。"""

    def __init__(self, card, empty_rounds=3, ready_after_wheels=None):
        super().__init__(card)
        self.page = FakePage(ready_after_wheels)
        self._empty_rounds = empty_rounds
        self.scans = 0

    def _collect_candidates(self, prices):
        self.scans += 1
        if self.scans <= self._empty_rounds:
            return []
        return super()._collect_candidates(prices)


def test_lazy_render():
    print("\n--- 秒杀模块懒渲染：扫描要带轮询 ---")
    finder = LazyFinder(FakeCard({"button": [FakeEl("立即抢购")]}), empty_rounds=3)
    result = finder.find_and_click(["38"], click=False, wait_seconds=5)
    check("空扫 3 轮后仍能命中", result[0], True)
    check("确实重扫了多次", finder.scans >= 4, True)

    # 不轮询（wait_seconds=0）时行为保持原样：只扫一次就放弃
    once = LazyFinder(FakeCard({"button": [FakeEl("立即抢购")]}), empty_rounds=3)
    check("wait_seconds=0 只扫一次", once.find_and_click(["38"], click=False)[0], False)
    check("扫描次数为 1", once.scans, 1)

    sweep = LazyFinder(FakeCard({"button": [FakeEl("立即抢购")]}), ready_after_wheels=3)
    ok = sweep.ensure_full_render(step_ms=0)
    check("整页滚动可触发秒杀模块", ok, True)
    check("滚动过程中持续检测", sweep.page.mouse.wheels >= 3, True)


CARD_109 = (
    "轻量应用服务器 4核4G3M\n建站、Web应用、电商独立站等高性价比选择\n规格\n4核4G3M\n"
    "地域\n上海\n时长\n1年\n1.4折\n数量\n-\n+\n1.4折\n限1个\n活动价：\n109\n元\n"
    "日常价：780元\n立即购买"
)
CARD_38 = (
    "轻量应用服务器\n建站、Web应用、电商独立站等高性价比选\n4核4G3M\n300GB 月流量\n"
    "地域\n广州/上海/北京\n有效期\n自购买一年内有效\n0.5折\n个人专享\n限1个\n"
    "活动价：\n38\n元\n日常价：780元\n0%\n已抢0%\n添加提醒"
)


def test_test_tier_109():
    print("\n--- 测试档（4核4G3M 109 元）判据 ---")
    from product_finder import evaluate_card_text

    target = {
        "text": "4核4G",
        "bandwidth": "3M",
        "duration": ["1年", "一年"],
        "exclude_specs": ["4核8G", "4核16G", "2核2G", "2核4G", "2核8G"],
    }

    ok, score, price = evaluate_card_text(CARD_109, target, ["109"])
    check("109 常售卡命中测试档", ok, True)
    check("命中价是 109", price, "109")
    check("命中得分 > 0", score > 0, True)

    check("38 秒杀卡不命中 109", evaluate_card_text(CARD_38, target, ["109"])[0], False)
    check("109 常售卡不命中正式档 38/39", evaluate_card_text(CARD_109, target, ["38", "39"])[0], False)
    check("38 秒杀卡仍命中正式档", evaluate_card_text(CARD_38, target, ["38", "39"])[0], True)


def main():
    test_price()
    test_button()
    test_return_contract()
    test_lazy_render()
    test_test_tier_109()

    if FAILED:
        print(f"\n失败 {len(FAILED)} 项: {FAILED}")
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
