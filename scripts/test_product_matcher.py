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


def main():
    test_price()
    test_button()
    test_return_contract()

    if FAILED:
        print(f"\n失败 {len(FAILED)} 项: {FAILED}")
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
