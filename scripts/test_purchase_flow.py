"""购买流程回归测试：防止「一次下两单 / 买到别的商品」。

覆盖三处曾导致重复下单的缺陷：
  1. _click_buy_button 在 expect_popup 超时后二次点击
  2. 新标签页拿到却没切换，导致误判「没点成功」→ 重试 → 又下一单
  3. _submit_order 用页面级 `button:has-text("立即购买")`，在活动页上会点到
     任意商品卡片；外层还有 10 次重试
另加测试模式的「价格分层」：先用 test_price，不与正式档混用。

用法：
    python scripts/test_purchase_flow.py
"""
import sys
from pathlib import Path

# Windows CI 的 stdout 默认是 cp1252，直接打印中文会 UnicodeEncodeError。
# 这里显式切到 UTF-8，不依赖 logger 的副作用。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from purchase_flow import PurchaseFlow  # noqa: E402

FAILED = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {name}: got={got!r} want={want!r}")
    if not ok:
        FAILED.append(name)


class FakeButton:
    def __init__(self, text="立即抢购", on_click=None):
        self.text = text
        self.clicks = 0
        self._on_click = on_click

    def click(self, **kwargs):
        self.clicks += 1
        if self._on_click:
            self._on_click()

    def is_visible(self, timeout=None):
        return True

    def inner_text(self):
        return self.text

    def highlight(self):
        pass

    def is_disabled(self):
        return False

    def get_attribute(self, name):
        return ""


class FakeContext:
    def __init__(self, pages=None):
        self.pages = list(pages or [])


class FakeEl:
    def __init__(self, visible=False, text=""):
        self._visible = visible
        self._text = text

    def is_visible(self, timeout=None):
        return self._visible

    def inner_text(self):
        return self._text


class FakeLocator:
    def __init__(self, els):
        self._els = list(els)

    @property
    def first(self):
        return self._els[0] if self._els else FakeEl(False)

    def all(self):
        return self._els


class FakePage:
    """最小页面桩：可配置是否有配置弹窗、页面级购买按钮。"""

    def __init__(self, url, context=None, dialog_visible=False, page_level_buttons=None):
        self.url = url
        self.context = context or FakeContext([self])
        self.dialog_visible = dialog_visible
        self.page_level_buttons = page_level_buttons or []

    def wait_for_timeout(self, ms):
        pass

    def wait_for_load_state(self, state):
        pass

    def locator(self, selector):
        if ".uno3-dialog-footer" in selector:
            return FakeLocator([])
        if ".uno3-dialog" in selector:
            return FakeLocator([FakeEl(self.dialog_visible, "配置")])
        if "button" in selector:
            return FakeLocator(self.page_level_buttons)
        return FakeLocator([])

    def get_by_text(self, text, exact=False):
        return FakeLocator([])


def make_flow(page):
    config = {
        "target": {"text": "4核4G", "prod_prices": ["38", "39"]},
        "page": {"tabs": [], "scroll_times": 0},
        "run_mode": {"test_mode": False, "allow_retry_purchase": False},
        "images": [],
    }
    return PurchaseFlow(page, config, {})


def test_click_once():
    print("\n--- 点击只发生一次 ---")
    page = FakePage("https://cloud.tencent.com/act/pro/double12-2025")
    flow = make_flow(page)
    btn = FakeButton()
    result = flow._click_buy_button(btn)
    check("无新标签页时返回 None", result, None)
    check("按钮只被点击 1 次", btn.clicks, 1)

    # 模拟点击后开新标签页
    page2 = FakePage("https://cloud.tencent.com/act/pro/double12-2025")
    new_tab = FakePage("https://buy.cloud.tencent.com/lighthouse")

    def open_tab():
        page2.context.pages.append(new_tab)

    btn2 = FakeButton(on_click=open_tab)
    flow2 = make_flow(page2)
    got = flow2._click_buy_button(btn2)
    check("有弹窗时返回新标签页", got is new_tab, True)
    check("有弹窗时也只点 1 次", btn2.clicks, 1)


def test_submit_scope():
    print("\n--- 提交按钮只在弹窗内找 ---")
    # 活动页 + 无配置弹窗：必须拒绝，且不能点页面级「立即购买」
    danger = FakeButton("立即购买")
    page = FakePage(
        "https://cloud.tencent.com/act/pro/double12-2025",
        dialog_visible=False,
        page_level_buttons=[danger],
    )
    flow = make_flow(page)
    check("活动页无弹窗 -> 拒绝提交", flow._submit_order(), False)
    check("页面级购买按钮未被点击", danger.clicks, 0)

    # 非活动页（已进入购买页）才允许用页面级按钮
    ok_btn = FakeButton("立即购买")
    page2 = FakePage(
        "https://buy.cloud.tencent.com/lighthouse",
        dialog_visible=False,
        page_level_buttons=[ok_btn],
    )
    flow2 = make_flow(page2)
    flow2._submit_order()
    check("非活动页允许点页面级按钮", ok_btn.clicks >= 1, True)


def test_no_retry_by_default():
    print("\n--- 默认不自动重试购买 ---")
    page = FakePage("https://cloud.tencent.com/act/pro/double12-2025", dialog_visible=False)
    flow = make_flow(page)
    btn = FakeButton()
    called = {"retry": False}
    flow._retry_purchase = lambda: called.__setitem__("retry", True) or True
    result = flow.execute(btn)
    check("execute 返回 False", result, False)
    check("未触发重试下单", called["retry"], False)
    check("购买按钮只点 1 次", btn.clicks, 1)


def test_price_tiers():
    print("\n--- 测试模式价格分层 ---")
    from config import TARGET_CONFIG
    from main import TencentCloudBot

    bot = TencentCloudBot()
    bot.config["run_mode"]["test_mode"] = True
    tiers = bot._get_target_price_tiers()

    test_price = TARGET_CONFIG["test_price"]
    fallback = TARGET_CONFIG.get("test_prices_fallback") or []
    check("第一组只含 test_price", tiers[0], [test_price])
    check("fallback 与 test_price 不混用", [p for p in tiers[0] if p in fallback], [])
    check("测试档不是正式档", test_price in TARGET_CONFIG["prod_prices"], False)
    check("测试模式扁平价格也只含测试档", bot._get_target_prices(), ([test_price] + fallback))

    bot.config["run_mode"]["test_mode"] = False
    check("生产模式只有一组", bot._get_target_price_tiers(), [["38", "39"]])



def test_buy_control_guard():
    print("\n--- 复用旧句柄的守卫 ---")
    from main import TencentCloudBot

    class El:
        def __init__(self, tag, text):
            self.tag = tag
            self.text = text

        def evaluate(self, js):
            return self.tag

        def inner_text(self):
            return self.text

    check("按钮(立即抢购) 可复用", TencentCloudBot._is_buy_control(El("BUTTON", "立即抢购")), True)
    check("按钮(立即购买) 可复用", TencentCloudBot._is_buy_control(El("BUTTON", "立即购买")), True)
    check(
        "卡片容器 不可复用",
        TencentCloudBot._is_buy_control(El("DIV", "4核4G3M 活动价：38元 添加提醒")),
        False,
    )
    check("提醒按钮 不可复用", TencentCloudBot._is_buy_control(El("BUTTON", "添加提醒")), False)
    check("取消提醒 不可复用", TencentCloudBot._is_buy_control(El("BUTTON", "取消提醒")), False)
    check("None 安全", TencentCloudBot._is_buy_control(None), False)



def test_submit_button_wait():
    print("\n--- 配置弹窗：等提交按钮可用 ---")
    from purchase_flow import PurchaseFlow

    class Btn:
        def __init__(self, text="立即购买", disabled=False):
            self._text = text
            self._disabled = disabled
            self.clicked = 0

        def inner_text(self):
            return self._text

        def is_visible(self, timeout=None):
            return True

        def is_disabled(self):
            return self._disabled

        def get_attribute(self, name):
            return "is-disabled" if self._disabled else ""

        def click(self, **kwargs):
            self.clicked += 1

        def highlight(self):
            pass

    class Loc:
        def __init__(self, els):
            self._els = els

        @property
        def first(self):
            return self._els[0]

        def all(self):
            return self._els

    class Scope:
        """第 enable_after 次查询之后按钮才可用。"""

        def __init__(self, enable_after):
            self.queries = 0
            self.enable_after = enable_after

        def locator(self, sel):
            self.queries += 1
            enabled = self.queries > self.enable_after
            return Loc([Btn(disabled=not enabled)])

        def inner_text(self):
            return "配置选择 镜像 地域"

    class Page:
        url = "https://cloud.tencent.com/act/pro/featured-202607"
        context = None

        def wait_for_timeout(self, ms):
            pass

    def make_flow():
        cfg = {"target": {"prod_prices": ["38"]}, "page": {}, "run_mode": {},
               "images": []}
        flow = PurchaseFlow(Page(), cfg, {})
        flow.SUBMIT_WAIT_SECONDS = 5
        return flow

    # 1) 按钮第 1 次就可用 -> 立刻返回
    flow = make_flow()
    btn = flow._wait_submit_button(Scope(enable_after=0))
    check("按钮可用时立即返回", btn is not None, True)

    # 2) 按钮前几次禁用、之后可用 -> 能等到（这就是「卡在配置页」的场景）
    flow = make_flow()
    scope = Scope(enable_after=4)
    btn = flow._wait_submit_button(scope)
    check("等到了后来才启用的按钮", btn is not None, True)
    check("确实重试了多次", scope.queries > 4, True)

    # 3) 一直禁用 -> 超时返回 None，而不是抛异常或死循环
    flow = make_flow()
    btn = flow._wait_submit_button(Scope(enable_after=10 ** 6))
    check("一直禁用时超时返回 None", btn, None)

    # 4) 整条提交路径：按钮延迟启用时最终能点下去
    flow = make_flow()
    scope = Scope(enable_after=3)
    flow._find_dialog_scope = lambda: scope
    flow._wait_submitted = lambda url: True
    ok = flow._submit_order()
    check("延迟启用场景最终提交成功", ok, True)



def test_dialog_first_then_login():
    print("\n--- 配置弹窗优先，跳过登录检测 ---")
    from purchase_flow import PurchaseFlow

    class Page:
        url = "https://cloud.tencent.com/act/pro/featured-202607"
        context = None

        def wait_for_timeout(self, ms):
            pass

    class FakeLogin:
        page = None

        def __init__(self, log):
            self.log = log

        def handle_login_flow(self, url):
            self.log["login"] += 1
            return True

    cfg = {"target": {"prod_prices": ["38"]}, "page": {}, "run_mode": {}, "images": []}

    def build(dialog_results):
        flow = PurchaseFlow(Page(), cfg, {})
        log = {"login": 0, "select": 0, "log_state": 0}
        results = list(dialog_results)
        flow._click_buy_button = lambda btn: None
        flow._wait_for_dialog = lambda ms: results.pop(0) if results else False
        flow._warn_if_dialog_asks_login = lambda: None
        flow._log_page_state = lambda label: log.__setitem__("log_state", log["log_state"] + 1)
        flow._select_image = lambda: log.__setitem__("select", log["select"] + 1) or True
        flow.login_handler = FakeLogin(log)
        return flow, log

    # 1) 点完购买配置弹窗立刻出现 -> 完全不该走登录检测
    flow, log = build([True])
    ok = flow.execute(object())
    check("弹窗已出现时流程继续", ok, True)
    check("弹窗已出现时不检测登录", log["login"], 0)
    check("弹窗已出现时进入选镜像", log["select"], 1)

    # 2) 没有弹窗 -> 才去处理登录（这里模拟登录成功后弹窗出现）
    flow2, log2 = build([False, True])
    flow2.execute(object())
    check("无弹窗时才检测登录", log2["login"], 1)
    check("登录处理后继续选镜像", log2["select"], 1)


def test_test_spec_override():
    print("\n--- 测试模式独立规格 ---")
    import config as cfg
    from config import TARGET_CONFIG
    from main import resolve_target
    from product_finder import evaluate_card_text

    base = dict(TARGET_CONFIG)
    prod = resolve_target(base, {"test_mode": False})
    check("正式模式沿用正式规格", prod["text"], "4核4G")

    custom = dict(base, test_text="2核2G", test_bandwidth="3M")
    test = resolve_target(custom, {"test_mode": True})
    check("测试模式换用测试规格", (test["text"], test["bandwidth"]), ("2核2G", "3M"))
    check(
        "与测试规格同名的排除项被移除（否则永远匹配不到）",
        "2核2G" in (test["exclude_specs"] or []),
        False,
    )
    check("其它排除项保留", "4核8G" in test["exclude_specs"], True)
    check("未配测试规格时回退到正式规格", resolve_target(base, {"test_mode": True})["text"], "4核4G")
    check("正式配置不会被测试规格污染", base["text"], TARGET_CONFIG["text"])

    # 选品判据确实按「当前模式的规格」走
    card_2c2g = "轻量应用服务器 2核2G3M\n活动价：\n109\n元\n日常价：780元\n立即购买"
    card_4c4g = "轻量应用服务器 4核4G3M\n活动价：\n109\n元\n日常价：780元\n立即购买"
    check("2核2G 卡命中测试规格", evaluate_card_text(card_2c2g, test, ["109"])[0], True)
    check("4核4G 卡不命中测试规格", evaluate_card_text(card_4c4g, test, ["109"])[0], False)
    check("正式规格仍命中 4核4G 卡", evaluate_card_text(card_4c4g, prod, ["109"])[0], True)

    # bot 初始化后就该拿到「当前模式实际生效」的规格
    from main import TencentCloudBot

    saved = dict(TARGET_CONFIG)
    saved_mode = dict(cfg.RUN_MODE)
    try:
        cfg.TARGET_CONFIG["test_text"] = "2核2G"
        cfg.TARGET_CONFIG["test_bandwidth"] = "3M"
        cfg.RUN_MODE["test_mode"] = True
        bot = TencentCloudBot()
        check("bot 使用测试规格", bot.config["target"]["text"], "2核2G")
        check("bot 的测试价仍在", bot.config["target"]["test_price"], base["test_price"])
    finally:
        cfg.TARGET_CONFIG.clear()
        cfg.TARGET_CONFIG.update(saved)
        cfg.RUN_MODE.clear()
        cfg.RUN_MODE.update(saved_mode)


def main():
    test_click_once()
    test_submit_scope()
    test_no_retry_by_default()
    test_price_tiers()
    test_test_spec_override()
    test_buy_control_guard()
    test_submit_button_wait()
    test_dialog_first_then_login()

    if FAILED:
        print(f"\n失败 {len(FAILED)} 项: {FAILED}")
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
