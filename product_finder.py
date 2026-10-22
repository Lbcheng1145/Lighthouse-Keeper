"""
商品查找模块 - 定位和点击商品

选品判据是「规格 + 价格 + 规格细节」三重校验：
    1) 规格：卡片文本必须含 TARGET_CONFIG["text"]（如「4核4G」）
    2) 价格：必须命中目标价，且必须是真正的价格文案（¥38 / 38元），
       不接受「已抢38%」「38.35元」「398元」这类页面噪声
    3) 规格细节：配置了 bandwidth / duration 时必须同时命中（如 3M、1年）
多张卡同时命中时按得分择优，而不是取 DOM 里第一张 —— 活动页同时存在
「4核4G3M 38元」和「4核4G3M 109元」两张同规格卡，只看规格必然选错。

性能约定（v1.2.0）：
    判据仍是本文件里的纯函数（便于回归测试），但「取文本」这一步改由
    utils 的批量 JS 探测完成：一次 page.evaluate 扫完整个卡片列表，而不是
    「每张卡两次跨进程往返（is_visible + inner_text）」。滚动加载也改成
    「一次往返、在浏览器里滚到底并检测」，不再由 Python 逐屏 sleep。
    探测不支持时自动回退到原来的 locator 写法。
"""
import re
import time

from logger import log
from utils import (
    UNSUPPORTED,
    js_collect_cards,
    js_find_buy_button,
    js_scroll_until_ready,
    js_visible_any,
    match_price_in_card,
)


# 抢购时刻允许点击的按钮文案（必须是明确的购买动作）
BUY_BUTTON_TEXTS = ("立即抢购", "抢购", "立即购买", "马上抢", "去购买", "购买")

# 绝不可点击的文案：提醒、售罄、详情、续费、进度条
NOT_BUY_BUTTON_TEXTS = (
    "添加提醒", "取消提醒", "已设置", "已抢光", "售罄", "抢光",
    "查看", "了解", "详情", "续费", "用量", "预约", "即将",
)


def evaluate_card_text(text, target, target_prices):
    """判定一张卡片的文本是否是目标商品（纯函数，便于用页面快照做回归测试）。

    Args:
        text: 卡片的 inner_text()
        target: TARGET_CONFIG
        target_prices: 目标价格列表

    Returns:
        (是否命中, 得分, 命中的价格)
    """
    if not text or len(text) > 800:
        return False, 0, None
    if not _is_single_product_card(text):
        return False, 0, None

    feature = target.get("text")
    if feature and feature not in text:
        return False, 0, None

    for bad in target.get("exclude_specs") or []:
        if bad and bad in text:
            return False, 0, None

    # 价格是硬条件：必须是真正的价格文案
    hit_price = None
    for price in target_prices:
        if match_price_in_card(text, price):
            hit_price = price
            break
    if hit_price is None:
        return False, 0, None

    score = 10

    bandwidth = target.get("bandwidth")
    if bandwidth:
        if not _spec_matches(text, feature, bandwidth):
            return False, 0, None
        score += 5

    duration = target.get("duration")
    if duration:
        options = [duration] if isinstance(duration, str) else list(duration)
        if any(opt and opt in text for opt in options):
            score += 3

    if "活动价" in text:
        score += 2
    if "秒杀" in text or "限时" in text:
        score += 1

    return True, score, hit_price


def _match_tiers(text, target, tiers):
    """按价格分组依次判定，返回 (分组序号, 得分, 命中价)；都不命中返回 None。

    分组序号用于排序：靠前的分组（例如测试档 109）优先于 fallback 分组。
    """
    for tier, prices in enumerate(tiers):
        ok, score, price = evaluate_card_text(text, target, prices)
        if ok:
            return tier, score, price
    return None


def _is_single_product_card(text):
    """排除「列容器 / 整块 grid」被当成一张卡的情况。

    一张商品卡只应有一个活动价、一个日常价、最多一个购买类按钮文案；
    若文本里出现多个价格区或大量按钮文案，说明拿到的是外层容器，
    在其中点第一个「抢购」必然点错商品。
    """
    if text.count("活动价") > 1 or text.count("日常价") > 1:
        return False
    buy_marks = sum(text.count(k) for k in ("抢购", "立即购买", "添加提醒"))
    return buy_marks <= 2


def _spec_matches(text, feature, bandwidth):
    """规格串必须形如「4核4G3M」，即特征文字后紧跟带宽。

    只判断 `4核4G in text` 无法区分 4核4G3M / 4核4G5M，
    而页面上的 4核4G 与 4核4G3M 是同价不同档的两个商品。
    """
    if not feature:
        return True
    pattern = re.escape(feature) + r"\s*" + re.escape(bandwidth)
    return bool(re.search(pattern, text.replace("\n", "")))


class ProductFinder:
    """商品查找器"""

    # 卡片容器候选：按精度从高到低尝试
    CARD_SELECTORS = (
        ".qc-base-grid__col",
        ".c-card",
        ".goods-item",
        '[class*="goods-card"]',
    )

    # 秒杀模块的判定依据。实测该模块异步渲染，页面 domcontentloaded 之后
    # 还要 8-16 秒才进 DOM；且不同活动页/改版后 class 名会变，因此同时接受
    # 「秒杀卡片容器」与「秒杀专属文案（添加提醒 / 已抢）」两种信号。
    SECKILL_SELECTOR = ".uno3-buy-card-seckill"
    SECKILL_TEXTS = ("添加提醒", "已抢")

    def __init__(self, page, target_config, timeout_config):
        self.page = page
        self.target = target_config
        self.timeout = timeout_config

    def prepare_page(self, tabs, scroll_times, wait_seckill=True):
        """
        准备页面：点击Tab，滚动加载

        Args:
            tabs: 要点击的Tab列表
            scroll_times: 滚动次数（同时作为「最多滚多少屏」的上限）
            wait_seckill: 是否等待秒杀模块渲染完成

        性能：以前这里是「先 PageDown 20 次各睡 1 秒」+「再从顶部逐屏滚
        60 次各睡 300ms」，同一段页面被滚了两遍，实测光准备页面就要 23 秒。
        现在只滚一遍：滚动与检测合并成一次浏览器内操作（见 ensure_full_render），
        一旦秒杀模块出现就立即返回。
        """
        log("展开所有商品Tab...", "INFO")
        self._click_tabs(tabs)

        if wait_seckill:
            log("滚动页面并等待秒杀模块渲染...", "INFO")
            self.ensure_full_render(max_steps=max(20, int(scroll_times or 20)))
        else:
            log("滚动页面加载商品...", "INFO")
            self._scroll_page(scroll_times, getattr(self, "scroll_interval", 0.25))

    def ensure_full_render(self, step_ms=300, max_steps=60):
        """从头到底逐屏滚动，触发所有懒加载模块（尤其是秒杀模块）。

        实测：秒杀模块（含目标商品）只有滚动到它附近才会进 DOM。早期版本
        只做固定次数 PageDown 再扫一次，碰上「没滚到 / 没渲染」的会话就
        直接报「找不到商品」——同一个页面刷新几次结果都不一样，正是这个
        原因。这里逐屏滚到底，确保模块真的渲染出来。

        性能：整段「滚动 + 每屏检测」压进浏览器里做（一次往返）。
        以前每滚一屏就要从 Python 发 3 次查询（容器 + 2 个文案），
        光是往返开销就比滚动本身还贵。
        """
        probe = js_scroll_until_ready(
            self.page,
            self.SECKILL_SELECTOR,
            self.SECKILL_TEXTS,
            max_steps=max_steps,
            step_ms=150 if not step_ms else min(max(int(step_ms), 60), 400),
        )
        if probe is not UNSUPPORTED and isinstance(probe, dict):
            steps = probe.get("steps") or 0
            if probe.get("found"):
                if steps:
                    log(f"秒杀模块已渲染（下滚 {steps} 屏）", "INFO")
                else:
                    log("秒杀模块已就绪（首屏）", "INFO")
                return True
            log("整页滚动完成仍未检测到秒杀模块，按当前 DOM 继续", "WARNING")
            return False

        # ---- 回退路径：不支持 evaluate 的页面对象（单元测试的桩等） ----
        try:
            self.page.keyboard.press("Home")
            self.page.wait_for_timeout(step_ms)
        except Exception:
            pass

        if self._seckill_ready():
            log("秒杀模块已就绪（首屏）", "INFO")
            return True

        for i in range(max_steps):
            try:
                self.page.mouse.wheel(0, 600)
            except Exception:
                break
            self.page.wait_for_timeout(step_ms)
            if self._seckill_ready():
                log(f"秒杀模块已渲染（下滚 {i + 1} 屏）", "INFO")
                return True
            try:
                at_bottom = self.page.evaluate(
                    "() => window.innerHeight + window.scrollY "
                    ">= document.documentElement.scrollHeight - 80"
                )
            except Exception:
                at_bottom = False
            if at_bottom:
                break

        log("整页滚动完成仍未检测到秒杀模块，按当前 DOM 继续", "WARNING")
        return False

    def _seckill_ready(self):
        """秒杀模块是否已渲染（容器出现，或已出现秒杀专属文案）。"""
        probes = [self.SECKILL_SELECTOR] + [f"text={t}" for t in self.SECKILL_TEXTS]
        probe = js_visible_any(self.page, probes)
        if probe is not UNSUPPORTED:
            return probe is not None

        try:
            if self.page.locator(self.SECKILL_SELECTOR).count() > 0:
                return True
        except Exception:
            pass
        for text in self.SECKILL_TEXTS:
            try:
                if self.page.get_by_text(text, exact=False).count() > 0:
                    return True
            except Exception:
                continue
        return False

    def wait_for_seckill_module(self, timeout_ms=15000):
        """兼容旧接口：确保秒杀模块已渲染（实现见 ensure_full_render）。"""
        if self._seckill_ready():
            return True
        return self.ensure_full_render(step_ms=300)

    def _debug_scan_page(self):
        """调试：全页面扫描关键词"""
        log("开始全页面结构扫描...", "DEBUG")
        try:
            elements = self.page.get_by_text(self.target["text"], exact=False).all()
            log(f"页面上共找到 {len(elements)} 个包含 '{self.target['text']}' 的元素", "DEBUG")

            for i, el in enumerate(elements[:10]):
                try:
                    txt = el.inner_text()
                    txt_clean = txt.replace('\n', ' ')
                    parent = el.locator('xpath=./ancestor::div[3]').first
                    html = parent.inner_html()
                    html_clean = html.replace('\n', ' ')

                    log(f"--- 元素 [{i}] ---", "DEBUG")
                    log(f"文本: {txt_clean[:100]}", "DEBUG")
                    log(f"HTML片段: {html_clean[:200]}...", "DEBUG")
                except Exception:
                    pass
        except Exception as e:
            log(f"扫描异常: {e}", "ERROR")

    def find_and_click(
        self, target_prices, click=True, require_buyable=True, wait_seconds=0, tiers=None
    ):
        """查找目标商品，可选地点击它的购买按钮。

        Args:
            target_prices: 目标价格列表
            click: 是否点击购买按钮
            require_buyable: 是否要求「此刻就能买」。
                True  —— 抢购时刻用：卡片里必须有明确的购买按钮，
                         否则视为不可用，交给调用方重试。
                False —— 赛前预定位用：目标卡在开抢前显示「添加提醒」，
                         此时没有购买按钮是正常的，仍应返回卡片位置用于
                         滚动/高亮，否则预定位会永远失败、主循环空转刷新。
            wait_seconds: 扫不到候选时最多再等多少秒（秒杀模块异步渲染，
                          一次扫描很可能什么都看不到）。0 表示只扫一次。
            tiers: 价格分组（优先级从高到低）。给了就一次扫完、按分组排序，
                   不再「每组各扫一遍」—— 测试模式的 109 档和 fallback 档
                   以前要串行扫两次，抢购时刻等于白等一轮。

        Returns:
            (success, element_or_None) —— 任何分支都返回二元组，
            调用方可以直接解包；早期版本在「卡片命中但按钮不合格」时
            隐式返回 None，会让调用方抛 TypeError 并中断整轮扫描。
        """
        log(f"查找目标商品（价格: {target_prices}）...", "INFO")

        if tiers:
            def scan():
                return self._scan_tiers([list(t) for t in tiers])
        else:
            def scan():
                return self._collect_candidates(target_prices)

        deadline = time.time() + max(0, wait_seconds)
        candidates = scan()
        sweep = 0
        while not candidates and time.time() < deadline:
            # 秒杀模块是懒渲染的：只等待不滚动可能一直等不到，
            # 这里「小幅滚动 + 重扫」交替，边滚边找。
            try:
                self.page.mouse.wheel(0, 700 if sweep % 4 != 3 else -2100)
            except Exception:
                pass
            sweep += 1
            self.page.wait_for_timeout(180)
            candidates = scan()
        if not candidates:
            log("没有卡片同时满足「规格 + 价格 + 规格细节」校验，本轮放弃", "WARNING")
            return False, None

        # 价格分组优先级 > 得分 > DOM 顺序
        candidates.sort(
            key=lambda item: (item.get("tier", 0), -item["score"], item["order"])
        )
        for item in candidates[:5]:
            log(
                f"候选卡[#{item['order']}] 得分={item['score']} 价格={item['price']} "
                f":: {self._flatten(item['text'])[:120]}",
                "DEBUG",
            )

        best = candidates[0]
        log(
            f"✅ 锁定商品（得分 {best['score']}，命中价 {best['price']}）: "
            f"{self._flatten(best['text'])[:160]}",
            "SUCCESS",
        )
        return self._process_found_card(
            best["card"], best["text"], best["order"], click, require_buyable
        )

    def _collect_candidates(self, target_prices):
        """扫描所有卡片容器，返回命中目标的候选（含得分）。"""
        return self._scan_tiers([list(target_prices)])

    def _scan_tiers(self, tiers):
        """按卡片选择器扫描，并在多个价格分组里择优。

        性能要点：卡片文本一次性取回（一次 evaluate），命中哪张卡再单独
        拿那张卡的句柄，而不是「每张卡都做两次跨进程往返」。
        """
        for selector in self.CARD_SELECTORS:
            probe = js_collect_cards(
                self.page, selector, feature=(self.target.get("text") or "")
            )

            if probe is not UNSUPPORTED:
                if not isinstance(probe, list) or not probe:
                    continue
                log(f"扫描卡片容器 {selector}，共 {len(probe)} 张", "DEBUG")
                found = []
                for order, item in enumerate(probe):
                    if not isinstance(item, dict) or not item.get("visible"):
                        continue
                    text = item.get("text") or ""
                    hit = _match_tiers(text, self.target, tiers)
                    if hit is None:
                        continue
                    tier, score, price = hit
                    found.append(
                        {
                            "text": text,
                            "order": order,
                            "score": score,
                            "price": price,
                            "tier": tier,
                            "_selector": selector,
                        }
                    )
                if found:
                    # 只为命中的卡片解析句柄（通常 1~2 张），并复核一遍：
                    # JS 扫的是 textContent，句柄拿到的是 innerText；两者之间
                    # 页面还可能因为懒渲染插入/替换了节点。复核不过就丢弃，
                    # 宁可多扫一轮，也不能拿着偏移的索引去点别的商品。
                    verified = []
                    for item in found:
                        try:
                            handle = self.page.locator(selector).nth(item["order"])
                            actual = handle.inner_text()
                        except Exception:
                            continue
                        if not actual:
                            continue
                        hit = _match_tiers(actual, self.target, tiers)
                        if hit is None:
                            log("卡片内容在扫描后发生变化，已丢弃该候选", "DEBUG")
                            continue
                        tier, score, price = hit
                        item.update(
                            card=handle, text=actual, score=score, price=price, tier=tier
                        )
                        verified.append(item)
                    if verified:
                        return verified
                    continue

            # ---- 回退路径：逐张卡片查可见性 + 取文本 ----
            try:
                cards = self.page.locator(selector).all()
            except Exception as exc:
                log(f"选择器 {selector} 查询失败: {exc}", "DEBUG")
                continue
            if not cards:
                continue

            log(f"扫描卡片容器 {selector}，共 {len(cards)} 张", "DEBUG")
            found = []
            for order, card in enumerate(cards):
                try:
                    if not card.is_visible():
                        continue
                    text = card.inner_text()
                except Exception:
                    continue
                hit = _match_tiers(text, self.target, tiers)
                if hit is None:
                    continue
                tier, score, price = hit
                found.append(
                    {
                        "card": card,
                        "text": text,
                        "order": order,
                        "score": score,
                        "price": price,
                        "tier": tier,
                    }
                )
            if found:
                return found
        return []

    def _process_found_card(self, card, text, idx, click, require_buyable=True):
        """在已确认命中的卡片里找可点击的购买按钮。"""
        btn = self._find_buy_button(card)
        if btn is None:
            if require_buyable:
                log(f"卡片[{idx}] 内没有可点击的购买按钮（可能是「添加提醒」或已售罄）", "WARNING")
                return False, None
            # 赛前预定位：卡片已确认是目标，只是按钮还没开放，返回卡片本身
            # 供调用方滚动/高亮，避免预定位死循环刷新页面。
            log(f"卡片[{idx}] 已锁定位置（购买按钮尚未开放）", "SUCCESS")
            return True, card

        try:
            btn_text = (btn.inner_text() or "").strip().replace("\n", " ")
        except Exception:
            btn_text = "(未知)"

        log(f"卡片[{idx}] 购买按钮: {btn_text}", "INFO")
        if not click:
            return True, btn

        log(f"正在点击购买按钮: {btn_text}...", "INFO")
        try:
            btn.click(force=True)
        except Exception as exc:
            log(f"点击购买按钮失败: {exc}", "WARNING")
            return False, None
        return True, btn

    def _find_buy_button(self, card):
        """在卡片内定位「真正的购买按钮」。

        判据比早期版本严格：
          * 只接受明确的购买动作文案（抢购 / 立即购买 …）
          * 明确排除「添加提醒」「已抢X%」「售罄」「查看」「续费」等
          * 元素自身文案长度受限，避免命中整张卡片的祖先容器

        性能：卡片内的 5 组选择器 × 每个元素一次 is_visible 往返，改由一次
        evaluate 完成；JS 只负责在命中的按钮上打 data-lk-btn 标记，句柄仍从
        locator 拿，点击语义不变。
        """
        marked = js_find_buy_button(card, BUY_BUTTON_TEXTS, NOT_BUY_BUTTON_TEXTS)
        if marked is not UNSUPPORTED:
            if not marked:
                return None
            try:
                return card.locator('[data-lk-btn="1"]').first
            except Exception:
                return None

        # ---- 回退路径 ----
        selectors = (
            "button",
            'a[role="button"]',
            "a",
            '[class*="btn"]',
            '[class*="button"]',
        )
        for selector in selectors:
            try:
                candidates = card.locator(selector).all()
            except Exception:
                continue
            for cand in candidates:
                try:
                    if not cand.is_visible():
                        continue
                    cand_text = (cand.inner_text() or "").strip()
                except Exception:
                    continue
                if not cand_text or len(cand_text) > 20:
                    continue
                if any(bad in cand_text for bad in NOT_BUY_BUTTON_TEXTS):
                    continue
                if any(good in cand_text for good in BUY_BUTTON_TEXTS):
                    return cand
        return None

    @staticmethod
    def _flatten(text):
        return " | ".join(x.strip() for x in text.splitlines() if x.strip())

    def _click_tabs(self, tabs):
        """点击Tab（专注模式：只点限时秒杀）

        性能：以前点完固定睡 1 秒，再查一次数量，没变就「再点一次 + 再睡
        1 秒」—— 光切个 Tab 就要 2 秒，而且重复点击同一 Tab 有可能把它切回去。
        现在改成「点完轮询等卡片数变化」，一变就往下走；第一个候选点不动就
        换下一个候选，而不是重复点同一个。
        """
        target_tab = "限时秒杀"

        if any(target_tab in t for t in tabs):
            log(f"专注模式: 锁定 '{target_tab}' Tab", "INFO")
            tabs_to_click = [target_tab]
        else:
            tabs_to_click = tabs

        tab_load_ms = int((self.timeout or {}).get("tab_load", 2500))

        for tab_name in tabs_to_click:
            try:
                initial_count = self._card_count()
                tab_selectors = [
                    f'.qc-tabs__nav-item:has-text("{tab_name}")',
                    f'div[role="tab"]:has-text("{tab_name}")',
                    f'text={tab_name}',
                ]

                clicked = False
                for selector in tab_selectors:
                    try:
                        els = self.page.locator(selector).all()
                    except Exception:
                        continue
                    for el in els:
                        try:
                            if not el.is_visible():
                                continue
                            log(f"点击Tab: {tab_name}", "DEBUG")
                            el.scroll_into_view_if_needed()
                            el.click(force=True)
                        except Exception as exc:
                            log(f"点击Tab异常: {exc}", "DEBUG")
                            continue

                        # 点中就不再换候选：数量没变化未必是失败（Tab 可能本来
                        # 就是激活态），后面 ensure_full_render + 扫描会兜底。
                        # 只有「点不动」（异常/被遮挡）才去试下一个候选。
                        clicked = True
                        new_count = self._wait_card_count_change(
                            initial_count, min(tab_load_ms, 1500)
                        )
                        if new_count > initial_count or new_count > 10:
                            log(f"Tab加载成功，当前卡片数: {new_count}", "SUCCESS")
                        else:
                            log(
                                f"Tab点击后卡片数未变化 (当前: {new_count})，继续后续流程",
                                "DEBUG",
                            )
                        break
                    if clicked:
                        break

                if not clicked:
                    log(f"未找到Tab: {tab_name}", "WARNING")

            except Exception as e:
                log(f"点击Tab异常: {e}", "DEBUG")

    def _card_count(self):
        """当前卡片数（一次往返）。"""
        try:
            return self.page.locator(".qc-base-grid__col").count()
        except Exception:
            return 0

    def _wait_card_count_change(self, initial_count, timeout_ms):
        """等卡片数量相对 initial_count 发生变化，最多等 timeout_ms。

        数量一变立刻返回，避免原来的「固定睡 1 秒」。
        """
        deadline = time.time() + max(0, timeout_ms) / 1000.0
        count = initial_count
        while time.time() < deadline:
            count = self._card_count()
            if count > initial_count:
                return count
            try:
                self.page.wait_for_timeout(120)
            except Exception:
                break
        return count

    def _scroll_page(self, times, interval=0.25):
        """滚动页面（仅在 wait_seckill=False 的少数路径上使用）。"""
        try:
            self.page.keyboard.press("Home")
            self.page.wait_for_timeout(300)
        except Exception:
            return

        for _ in range(int(times or 0)):
            try:
                self.page.mouse.wheel(0, 600)
            except Exception:
                break
            self.page.wait_for_timeout(int(max(0.05, interval) * 1000))

    def _show_debug_info(self):
        """显示调试信息"""
        log("调试信息:", "DEBUG")
        debug_els = self.page.get_by_text(self.target["text"]).all()
        for i, el in enumerate(debug_els[:3]):
            try:
                log(f"  [{i}] {el.inner_text()[:60]}", "DEBUG")
            except Exception:
                pass
