"""
购买流程模块 - 处理点击购买后的流程

性能约定（v1.2.0）：
    抢购时刻这条链路是「差 1 秒就丢单」的地方，因此所有「等某个东西出现」
    都改成事件驱动或浏览器内轮询：
      * 点完购买不再固定等满 3 秒新标签页，弹窗一出现就往下走；
      * 等配置弹窗用 wait_for_selector（出现即返回），不再 300ms 一跳；
      * 选镜像从「4 个文案 × 2 个范围 × 800ms 超时」的串行探测，
        改成一次 evaluate 扫完并打标记；
      * 等提交按钮可用（异步校验可能要几秒）整段压进浏览器里等，
        一次往返替代「每 500ms × 6 组选择器」的轮询。
    每次点击都显式带 timeout：默认 30 秒的点击超时在抢购时等于直接放弃。
"""
from logger import log
from utils import (
    UNSUPPORTED,
    js_any_visible_fast,
    js_find_option,
    js_visible_any,
    js_wait_enabled_button,
)
from login_handler import LoginHandler
from product_finder import ProductFinder


class PurchaseFlow:
    """购买流程处理器"""

    # 关键点击的超时上限。Playwright 默认 30 秒 —— 抢购时刻卡 30 秒等于丢单，
    # 这里压到 3 秒：失败就记日志、交给上层决定，不在这里死等。
    CLICK_TIMEOUT_MS = 3000

    # 点完购买后，最多花多久同时观察「新标签页 / 页内配置弹窗」。
    POPUP_OR_DIALOG_MS = 1000

    def __init__(self, page, config, timeout_config):
        self.page = page
        self.config = config
        self.timeout = timeout_config
        self.login_handler = LoginHandler(page, timeout_config)

    def execute(self, buy_button):
        """
        执行购买流程

        Args:
            buy_button: 购买按钮元素

        Returns:
            bool: 成功返回True
        """
        old_url = self.page.url

        # 1. 点击购买按钮（这会触发弹窗或登录）。只会点一次。
        opened_page = self._click_buy_button(buy_button)

        # 购买页可能在新标签页里打开：必须真正切换过去。
        # 早期版本拿到 new_page 却没用，后续弹窗检测全作用在活动页上，
        # 看不到弹窗 → 误判「没点成功」→ 重试 → 又下一单。
        if opened_page is not None:
            self.page = opened_page
            self.login_handler.page = opened_page
            log("后续流程切换到新标签页", "INFO")

        # 2. 先看有没有配置弹窗 —— 有就说明登录态本来就是好的
        #
        # 能展开「选择地域/时长/镜像」的配置弹窗，本身就已经证明登录有效，
        # 这时候再去跑一遍登录检测纯属浪费：实测日志里出现过
        # 「点击购买按钮后: 可见元素=['配置弹窗']」紧接着
        # 「检测登录状态... → 忽略弱登录提示 → 3 秒后才发现弹窗」的情况。
        # 抢购时刻这几秒就是丢单，所以顺序必须反过来：先认弹窗，再谈登录。
        if self._wait_for_dialog(3000):
            log("配置弹窗已出现（登录态可用），直接进入配置流程", "SUCCESS")
            self._warn_if_dialog_asks_login()
            return self._select_image()

        # 3. 没有弹窗，这时才需要判断是不是被要求登录
        # 点完之后先把现场记下来：以后再出现「点了要登录」时，日志里能看出
        # 到底停在哪个页面、有没有弹窗/登录框，不用再靠猜。
        self._log_page_state("点击购买按钮后（未出现配置弹窗）")
        login_success = self.login_handler.handle_login_flow(old_url)

        if not login_success:
            log("登录处理可能未完成，尝试继续操作...", "WARNING")
        self._log_page_state("登录处理后")

        # 4. 登录处理完（或本来就不需要），再看弹窗是否出现
        if self._wait_for_dialog(5000):
            log("检测到配置弹窗 (.uno3-dialog)，准备选择镜像", "INFO")
            self._warn_if_dialog_asks_login()
            return self._select_image()

        # 如果没有弹窗，再检查是否还在活动页
        current_url = self.page.url
        log(f"当前URL: {current_url}", "DEBUG")

        if self._is_activity_page(current_url):
            # 二次检测：有些页面弹窗出现得慢
            try:
                mirror = js_any_visible_fast(self.page, "text=镜像")
                if mirror is UNSUPPORTED:
                    mirror = self.page.get_by_text("镜像", exact=False).first.is_visible(timeout=1000)
                if mirror:
                    log("二次检测发现弹窗，继续流程", "INFO")
                    return self._select_image()
            except Exception:
                pass

            # 重复下单的代价远高于漏单：默认不再自动重试点击购买。
            if not self.config.get("allow_retry_purchase", False):
                log(
                    "未检测到配置弹窗且仍在活动页。为避免重复下单，默认不自动重试；"
                    "如确认点击确实无效，可把 allow_retry_purchase 设为 True",
                    "ERROR",
                )
                return False

            log("未检测到配置弹窗且仍在活动页，按配置重新定位商品", "WARNING")
            return self._retry_purchase()

        # 如果URL变了，也继续
        return self._select_image()

    def _warn_if_dialog_asks_login(self):
        """配置弹窗里若有「请登录」字样，明确报错而不是静默失败。"""
        try:
            text = self.page.locator(".uno3-dialog").first.inner_text()
        except Exception:
            return
        for keyword in ("登录后", "请登录", "扫码登录", "登录购买", "登录继续"):
            if keyword in text:
                log(
                    f"配置页要求登录（出现「{keyword}」）："
                    "请在浏览器窗口里完成登录，然后点「停止」再「开始抢购」重试",
                    "ERROR",
                )
                return

    def _log_page_state(self, label):
        """记录当前页面状态，便于排查「点了没反应 / 提示登录」这类问题。

        性能：4 个选择器一次 evaluate 判断完，不再逐个等 200ms 超时。
        """
        checks = (
            (".uno3-dialog", "配置弹窗"),
            (".accsys-tp-modal", "登录弹窗"),
            ("#accsys-tp-overlay-root", "登录遮罩"),
            ('img[src*="qrcode"]', "二维码"),
        )
        found = []

        probe = js_visible_any(self.page, [sel for sel, _ in checks])
        if probe is not UNSUPPORTED:
            if probe:
                found = [name for sel, name in checks if sel == probe]
        else:
            for selector, name in checks:
                try:
                    if self.page.locator(selector).first.is_visible(timeout=200):
                        found.append(name)
                except Exception:
                    continue
        try:
            url = self.page.url
        except Exception:
            url = "(读不到)"
        log(f"{label}: URL={url} | 可见元素={found or '无'}", "DEBUG")

    def _dialog_visible(self):
        """配置弹窗是否已经可见（一次往返）。"""
        probe = js_any_visible_fast(self.page, ".uno3-dialog")
        if probe is not UNSUPPORTED:
            return bool(probe)
        try:
            return bool(self.page.locator(".uno3-dialog").first.is_visible(timeout=100))
        except Exception:
            return False

    def _wait_for_dialog(self, timeout_ms):
        """等待配置弹窗出现。

        事件驱动：弹窗一出现就返回，不再「睡 300ms 查一次」。
        没有 wait_for_selector 的页面对象（测试桩）退化为轮询。
        """
        waiter = getattr(self.page, "wait_for_selector", None)
        if waiter is not None:
            try:
                waiter(".uno3-dialog", state="visible", timeout=max(1, int(timeout_ms)))
                return True
            except Exception:
                # 超时、或页面正在导航都走这里：再用一次可见性判断兜底
                return self._dialog_visible()

        waited = 0
        step = 150
        while waited < timeout_ms:
            if self._dialog_visible():
                return True
            try:
                self.page.wait_for_timeout(step)
            except Exception:
                return False
            waited += step
        return False

    def _click_buy_button(self, button):
        """点击购买按钮，且只点一次。

        早期实现把点击包在 expect_popup 里：页内弹窗不会产生新标签页，
        expect_popup 超时抛异常后代码又点了一次 —— 同一个按钮被点两次，
        于是下两单。现在把「点击」与「是否开了新标签」解耦：先点一次，
        再轮询 context.pages 判断有没有新标签。

        性能：原来固定轮询 15 × 200ms = 3 秒等新标签，而活动页的配置弹窗
        根本不开新标签 —— 这 3 秒是纯白等。现在「新标签 / 配置弹窗」同时
        观察，哪个先出现就往哪走。

        Returns:
            新标签页 page，或 None（没有新标签页）
        """
        log("点击购买按钮...", "INFO")
        try:
            context = self.page.context
        except Exception:
            context = None
        pages_before = len(context.pages) if context is not None else 0

        try:
            # 使用 force=True 强制点击，防止被登录弹窗遮挡导致的点击超时
            button.click(force=True, timeout=self.CLICK_TIMEOUT_MS)
        except Exception as exc:
            text = str(exc).lower()
            if "intercepts pointer events" in text:
                log("点击被遮挡（可能是登录弹窗），继续处理登录流程", "WARNING")
            else:
                log(f"点击购买按钮异常: {exc}", "WARNING")
            # 这里不重试：点击可能已经生效，重试会把「可能一单」变成「确定两单」。
            return None

        if context is None:
            return None

        steps = max(1, int(self.POPUP_OR_DIALOG_MS / 100))
        for _ in range(steps):
            if len(context.pages) > pages_before:
                new_page = context.pages[-1]
                try:
                    new_page.wait_for_load_state('domcontentloaded', timeout=5000)
                except Exception:
                    pass
                log(f"检测到新标签页: {new_page.url}", "INFO")
                return new_page
            if self._dialog_visible():
                log("未开新标签页，配置弹窗已出现", "DEBUG")
                return None
            try:
                self.page.wait_for_timeout(100)
            except Exception:
                break

        log("未检测到新标签页，按页内弹窗继续", "DEBUG")
        return None
    
    def _is_activity_page(self, url):
        """判断是否是活动页"""
        return 'act/pro' in url or 'double12' in url
    
    def _retry_purchase(self):
        """重新定位商品并购买

        性能：以前是「固定等 3 秒 + Home + 20 次 PageDown 各 0.5 秒」，
        光滚动就 10 秒。现在复用 ProductFinder 的滚动等待（一次往返、
        见到秒杀模块就停）。
        """
        log("重新定位商品...", "INFO")

        scroll_times = self.config["page"].get("scroll_times", 20)
        finder = ProductFinder(self.page, self.config["target"], self.timeout)
        finder.ensure_full_render(max_steps=max(10, int(scroll_times)))

        ok, button = finder.find_and_click(
            self._get_target_prices(), click=False, require_buyable=True, wait_seconds=3
        )

        if not ok or button is None:
            log("重新定位失败", "ERROR")
            return False

        log("重新找到商品，点击购买", "SUCCESS")
        try:
            button.click(force=True, timeout=self.CLICK_TIMEOUT_MS)
        except Exception as e:
            log(f"重新点击异常，尝试常规点击: {e}", "WARNING")
            button.click(timeout=self.CLICK_TIMEOUT_MS)

        # 极速模式：不再死等3秒，而是等待DOM加载（必须有上限，
        # 否则页面一直在加载时这里会卡满默认 30 秒）
        self._ensure_page_ready(3000)
        return self._select_image()

    def _ensure_page_ready(self, timeout_ms=3000):
        """等 DOM 就绪，但不许按默认 30 秒死等。"""
        try:
            self.page.wait_for_load_state('domcontentloaded', timeout=timeout_ms)
        except Exception:
            log("页面仍在加载中，先继续处理配置弹窗", "DEBUG")

    def _select_image(self):
        """在配置弹窗里选择镜像并提交订单。

        坑 1：`wait_for_load_state('domcontentloaded')` 不给超时会按默认 30 秒等，
        点完购买后如果页面还在加载，表现就是「卡在配置页不动」。
        坑 2：镜像是弹窗里的选项，早期在整页范围里找，容易命中页面其它同名文字。

        性能：原来是「4 个镜像名 × 2 个范围 × 800ms」的串行探测，最坏 6.4 秒，
        而多数页面根本没有配置里的镜像名 —— 等于每次抢购白等几秒。现在一次
        evaluate 扫完（弹窗优先、退到整页），命中就打标记再点。
        """
        log("在配置弹窗里选择镜像...", "INFO")
        self._ensure_page_ready(3000)

        if not self._pick_image():
            log("未找到指定镜像，按默认选择继续", "WARNING")
        return self._submit_order()

    def _pick_image(self):
        """选镜像。命中返回 True；没有可用镜像返回 False（调用方继续提交）。"""
        names = [n for n in (self.config.get("images") or []) if n]
        if not names:
            return False

        picked = js_find_option(self.page, names, root_selector=".uno3-dialog")
        if picked is not UNSUPPORTED:
            if not picked:
                return False
            log(f"找到镜像: {picked}", "SUCCESS")
            try:
                self.page.locator('[data-lk-opt="1"]').first.click(
                    timeout=self.CLICK_TIMEOUT_MS
                )
                return True
            except Exception as exc:
                log(f"点击镜像失败: {exc}", "WARNING")
                return False

        # ---- 回退路径：逐名逐范围探测 ----
        dialog = self._find_dialog_scope()
        scope = dialog if dialog is not None else self.page
        for image_name in names:
            for candidate_scope, label in ((scope, "弹窗内"), (self.page, "整页")):
                try:
                    img_el = candidate_scope.locator(f'text={image_name}').first
                    if img_el.is_visible(timeout=800):
                        log(f"找到镜像（{label}）: {image_name}", "SUCCESS")
                        img_el.click(timeout=self.CLICK_TIMEOUT_MS)
                        return True
                except Exception:
                    continue
        return False

    # 提交按钮文案：只认明确的提交动作
    SUBMIT_TEXTS = ("立即购买", "确认购买", "立即抢购", "提交订单", "确认下单", "确定")

    # 配置弹窗里等「提交按钮可用」的上限。
    # 有些商品的提交按钮要等异步校验（库存、地域、镜像）结束才解除禁用，
    # 早期实现只找一次、找不到就放弃 —— 表现就是「卡在配置页」。
    SUBMIT_WAIT_SECONDS = 10

    def _submit_order(self):
        """提交订单。

        只允许在「配置弹窗」范围内点提交按钮。
        早期实现把页面级 `button:has-text("立即购买")` 也放进候选选择器，
        而活动页上每张商品卡都有这个按钮 —— 于是会点到别的商品；外层还有
        10 次重试，实测表现为「同时下单两个商品」。
        """
        log("提交订单...", "INFO")
        start_url = self.page.url

        for attempt in range(2):
            scope = self._find_dialog_scope()
            if scope is None:
                if self._is_activity_page(self.page.url):
                    log(
                        "仍在活动页且找不到配置弹窗：拒绝点击页面级购买按钮"
                        "（那会买到页面上的其它商品），请人工确认",
                        "ERROR",
                    )
                    return False
                log("未找到配置弹窗，改为在非活动页查找提交按钮", "WARNING")
                scope = self.page

            btn = self._wait_submit_button(scope)
            if btn is None:
                log(f"第 {attempt + 1} 次尝试：等不到可用的提交按钮", "WARNING")
                self._dump_dialog_state(scope)
                continue

            try:
                btn.highlight()
            except Exception:
                pass

            try:
                btn_text = (btn.inner_text() or "").strip().replace("\n", " ")
            except Exception:
                btn_text = "(未知)"

            try:
                btn.click(force=True, timeout=self.CLICK_TIMEOUT_MS)
                log(f"已点击提交按钮: {btn_text}", "INFO")
            except Exception as exc:
                log(f"点击提交按钮失败: {exc}", "WARNING")
                continue

            if self._wait_submitted(start_url):
                return True

        log("未确认提交成功，请人工点击（不再自动重复点击，避免重复下单）", "WARNING")
        return False

    def _wait_submit_button(self, scope):
        """轮询等待提交按钮变为可点击。

        按钮一开始可能是禁用态（要等库存/地域/镜像校验），
        这里给它一点时间，而不是看了一眼就判定失败。

        性能：整段等待压进浏览器里做（一次往返），不再是
        「每 500ms × 6 组文案选择器 × 每个候选一次 is_visible」。
        """
        wait_ms = int(float(self.SUBMIT_WAIT_SECONDS) * 1000)
        scope_kind = "page" if scope is self.page else "dialog"
        probe = js_wait_enabled_button(
            self.page,
            self.SUBMIT_TEXTS,
            scope=scope_kind,
            wait_ms=wait_ms,
            interval_ms=120,
        )
        if probe is not UNSUPPORTED:
            if not probe:
                return None
            try:
                return self.page.locator('[data-lk-submit="1"]').first
            except Exception:
                return None

        # ---- 回退路径 ----
        deadline_attempts = max(1, int(self.SUBMIT_WAIT_SECONDS / 0.5))
        for i in range(deadline_attempts):
            btn = self._find_submit_button(scope)
            if btn is not None:
                return btn
            if i == 4:
                log("提交按钮暂不可用，继续等待校验完成...", "DEBUG")
            try:
                self.page.wait_for_timeout(500)
            except Exception:
                return None
        return None

    def _dump_dialog_state(self, scope):
        """把配置弹窗的现场写进日志，卡住时能看出到底缺什么。"""
        try:
            text = scope.inner_text()
        except Exception:
            text = ""
        flat = " | ".join(x.strip() for x in (text or "").splitlines() if x.strip())
        if flat:
            log(f"配置弹窗内容: {flat[:400]}", "WARNING")

        buttons = []
        try:
            for b in scope.locator("button").all():
                try:
                    if b.is_visible(timeout=200):
                        label = (b.inner_text() or "").strip().replace("\n", " ")
                        disabled = "禁用" if self._looks_disabled(b) else "可用"
                        buttons.append(f"{label or '(无文字)'}[{disabled}]")
                except Exception:
                    continue
        except Exception:
            pass
        if buttons:
            log(f"配置弹窗按钮: {buttons[:8]}", "WARNING")
        log(
            "提示：若按钮一直是禁用态，通常是还有配置项没选（镜像/地域/时长）。"
            "请在浏览器窗口里选好后，点「停止」再「开始抢购」重试。",
            "WARNING",
        )

    def _find_dialog_scope(self):
        """返回可见的配置弹窗；没有则返回 None。"""
        probe = js_any_visible_fast(self.page, ".uno3-dialog")
        if probe is not UNSUPPORTED:
            if not probe:
                return None
            try:
                return self.page.locator('.uno3-dialog').first
            except Exception:
                return None

        try:
            dialog = self.page.locator('.uno3-dialog').first
            if dialog.is_visible(timeout=1500):
                return dialog
        except Exception:
            pass
        return None

    def _find_submit_button(self, scope):
        """在给定范围内查找可点击的提交按钮（跳过禁用态）。"""
        for text in self.SUBMIT_TEXTS:
            try:
                candidates = scope.locator(f'button:has-text("{text}")').all()
            except Exception:
                continue
            for cand in candidates:
                try:
                    if not cand.is_visible(timeout=200):
                        continue
                    if self._looks_disabled(cand):
                        continue
                    return cand
                except Exception:
                    continue

        # 兜底：弹窗底部按钮（只限弹窗内，绝不退回整页）
        try:
            for cand in scope.locator('.uno3-dialog-footer button').all():
                if cand.is_visible(timeout=200) and not self._looks_disabled(cand):
                    return cand
        except Exception:
            pass
        return None

    @staticmethod
    def _looks_disabled(el):
        """按钮是否处于禁用态（禁用态再点也无效，还会让重试逻辑误判）。"""
        try:
            if el.is_disabled():
                return True
            cls = (el.get_attribute('class') or '').lower()
            return any(k in cls for k in ("disabled", "is-disabled"))
        except Exception:
            return False

    def _wait_submitted(self, start_url, timeout_ms=3000):
        """确认订单是否已提交：URL 变化，或配置弹窗关闭。

        轮询间隔 150ms（原来 300ms）：既能更快确认提交，也不会把页面查垮。
        """
        waited = 0
        step = 150
        while waited < timeout_ms:
            try:
                self.page.wait_for_timeout(step)
            except Exception:
                return True
            waited += step
            try:
                if self.page.url != start_url:
                    log(f"检测到页面跳转，提交成功: {self.page.url}", "SUCCESS")
                    return True
            except Exception:
                return True
            if not self._dialog_visible():
                log("配置弹窗已关闭，判定提交成功", "SUCCESS")
                return True
        return False

    def _get_target_prices(self):
        """获取目标价格列表"""
        if self.config["run_mode"]["test_mode"]:
            prices = [self.config["target"]["test_price"]]
            if "test_prices_fallback" in self.config["target"]:
                prices.extend(self.config["target"]["test_prices_fallback"])
            return prices
        else:
            return self.config["target"]["prod_prices"]
