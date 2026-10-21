"""
商品查找模块 - 定位和点击商品

选品判据是「规格 + 价格 + 规格细节」三重校验：
    1) 规格：卡片文本必须含 TARGET_CONFIG["text"]（如「4核4G」）
    2) 价格：必须命中目标价，且必须是真正的价格文案（¥38 / 38元），
       不接受「已抢38%」「38.35元」「398元」这类页面噪声
    3) 规格细节：配置了 bandwidth / duration 时必须同时命中（如 3M、1年）
多张卡同时命中时按得分择优，而不是取 DOM 里第一张 —— 活动页同时存在
「4核4G3M 38元」和「4核4G3M 109元」两张同规格卡，只看规格必然选错。
"""
import re

from logger import log
from utils import match_price_in_card


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

    def __init__(self, page, target_config, timeout_config):
        self.page = page
        self.target = target_config
        self.timeout = timeout_config

    def prepare_page(self, tabs, scroll_times):
        """
        准备页面：点击Tab，滚动加载

        Args:
            tabs: 要点击的Tab列表
            scroll_times: 滚动次数
        """
        log("展开所有商品Tab...", "INFO")
        self._click_tabs(tabs)

        log("滚动页面加载商品...", "INFO")
        self._scroll_page(scroll_times)

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

    def find_and_click(self, target_prices, click=True, require_buyable=True):
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

        Returns:
            (success, element_or_None) —— 任何分支都返回二元组，
            调用方可以直接解包；早期版本在「卡片命中但按钮不合格」时
            隐式返回 None，会让调用方抛 TypeError 并中断整轮扫描。
        """
        log(f"查找目标商品（价格: {target_prices}）...", "INFO")

        candidates = self._collect_candidates(target_prices)
        if not candidates:
            log("没有卡片同时满足「规格 + 价格 + 规格细节」校验，本轮放弃", "WARNING")
            return False, None

        candidates.sort(key=lambda item: (-item["score"], item["order"]))
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
        for selector in self.CARD_SELECTORS:
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
                ok, score, price = evaluate_card_text(text, self.target, target_prices)
                if ok:
                    found.append(
                        {
                            "card": card,
                            "text": text,
                            "order": order,
                            "score": score,
                            "price": price,
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
        """
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
        """点击Tab（专注模式：只点限时秒杀）"""
        target_tab = "限时秒杀"

        if any(target_tab in t for t in tabs):
            log(f"专注模式: 锁定 '{target_tab}' Tab", "INFO")
            tabs_to_click = [target_tab]
        else:
            tabs_to_click = tabs

        for tab_name in tabs_to_click:
            try:
                initial_count = self.page.locator('.qc-base-grid__col').count()

                tab_selectors = [
                    f'.qc-tabs__nav-item:has-text("{tab_name}")',
                    f'div[role="tab"]:has-text("{tab_name}")',
                    f'text={tab_name}'
                ]

                clicked = False
                for selector in tab_selectors:
                    els = self.page.locator(selector).all()
                    for el in els:
                        if el.is_visible():
                            log(f"点击Tab: {tab_name}", "DEBUG")
                            el.scroll_into_view_if_needed()
                            el.click(force=True)
                            self.page.wait_for_timeout(1000)

                            new_count = self.page.locator('.qc-base-grid__col').count()
                            if new_count > initial_count or new_count > 10:
                                log(f"Tab加载成功，当前卡片数: {new_count}", "SUCCESS")
                                clicked = True
                                break
                            else:
                                log(f"点击后未检测到内容增加 (当前: {new_count})，尝试再次点击...", "WARNING")
                                el.click(force=True)
                                self.page.wait_for_timeout(1000)
                                clicked = True
                                break
                    if clicked:
                        break

                if not clicked:
                    log(f"未找到Tab: {tab_name}", "WARNING")

            except Exception as e:
                log(f"点击Tab异常: {e}", "DEBUG")

    def _scroll_page(self, times):
        """滚动页面"""
        self.page.keyboard.press("Home")
        self.page.wait_for_timeout(1000)

        for i in range(times):
            self.page.keyboard.press("PageDown")
            self.page.wait_for_timeout(1000)

    def _show_debug_info(self):
        """显示调试信息"""
        log("调试信息:", "DEBUG")
        debug_els = self.page.get_by_text(self.target["text"]).all()
        for i, el in enumerate(debug_els[:3]):
            try:
                log(f"  [{i}] {el.inner_text()[:60]}", "DEBUG")
            except Exception:
                pass
