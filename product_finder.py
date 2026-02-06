"""
商品查找模块 - 定位和点击商品
"""
from logger import log
from utils import match_price_exact


class ProductFinder:
    """商品查找器"""
    
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
            # 查找所有包含关键词的元素
            elements = self.page.get_by_text(self.target["text"], exact=False).all()
            log(f"页面上共找到 {len(elements)} 个包含 '{self.target['text']}' 的元素", "DEBUG")
            
            for i, el in enumerate(elements[:10]): # 只看前10个
                try:
                    txt = el.inner_text()
                    txt_clean = txt.replace('\n', ' ')
                    # 获取父级HTML片段
                    parent = el.locator('xpath=./ancestor::div[3]').first
                    html = parent.inner_html()
                    html_clean = html.replace('\n', ' ')
                    
                    log(f"--- 元素 [{i}] ---", "DEBUG")
                    log(f"文本: {txt_clean[:100]}", "DEBUG")
                    log(f"HTML片段: {html_clean[:200]}...", "DEBUG")
                except:
                    pass
        except Exception as e:
            log(f"扫描异常: {e}", "ERROR")

    def find_and_click(self, target_prices, click=True):
        """
        查找目标商品
        """
        log(f"查找目标商品（价格: {target_prices}）...", "INFO")
        
        # 策略1：基于容器查找（最稳健）
        card_selectors = [
            '.qc-base-grid__col',
            '.c-card', 
            '.goods-item',
            '[class*="goods-card"]'
        ]
        
        for selector in card_selectors:
            cards = self.page.locator(selector).all()
            if not cards:
                continue
                
            log(f"策略1: 找到 {len(cards)} 个卡片容器 ({selector})", "DEBUG")
            
            # --- 第一轮：严格价格匹配 ---
            log("执行第一轮筛选：严格价格匹配...", "DEBUG")
            for idx, card in enumerate(cards):
                try:
                    if not card.is_visible(): continue
                    text = card.inner_text()
                    if self.target["text"] not in text: continue
                    
                    normalized_text = text.replace('\n', '')
                    
                    # 严格匹配
                    if self._has_target_price(text, target_prices) or \
                       self._has_target_price(normalized_text, target_prices):
                        
                        log(f"✅ [严格匹配] 锁定目标卡片 [索引: {idx}]", "SUCCESS")
                        return self._process_found_card(card, text, idx, click)
                except:
                    continue

            # --- 第二轮：模糊特征匹配 (仅当严格匹配失败时) ---
            log("执行第二轮筛选：模糊特征匹配...", "DEBUG")
            for idx, card in enumerate(cards):
                try:
                    if not card.is_visible(): continue
                    text = card.inner_text()
                    if self.target["text"] not in text: continue
                    
                    # 排除明确包含其他高价的商品（防止误判 79 为目标）
                    # 比如：虽然是1折，但价格是79
                    current_price_seems_high = "79" in text or "159" in text or "99" in text
                    
                    # 如果目标就是79，那就不排除。如果目标是38，就要排除79
                    is_targeting_high_price = any(p in ["79", "99"] for p in target_prices)
                    
                    if not is_targeting_high_price and current_price_seems_high:
                        # 再次确认：如果文本里同时也包含 38，那可能是原价79现价38，这种情况不该排除
                        # 但如果只有79没有38，那就必须排除
                        has_target = any(p in text for p in target_prices)
                        if not has_target:
                            continue

                    # 特征匹配
                    if "1折" in text or "0.5折" in text or "38元" in text:
                        log(f"✅ [模糊匹配] 锁定目标卡片 [索引: {idx}]", "SUCCESS")
                        return self._process_found_card(card, text, idx, click)
                except:
                    continue

        # 策略2：原有的基于文本查找（兜底）
        # ... (保持不变，但通常不会走到这里了)
        return False, None

    def _process_found_card(self, card, text, idx, click):
        """处理找到的卡片：查找按钮并点击"""
        # 尝试查找卡片内的按钮
        # 优先找 button 标签，其次找 role=button 的链接，最后找看起来像按钮的 div
        btn = None
        btn_selectors = [
            'button', 
            'a[role="button"]', 
            '.buy-btn', 
            '[class*="btn"]',
            # 新增：针对可能变成按钮的div区域（包含抢购/购买文字）
            'div:has-text("抢购")',
            'div:has-text("购买")',
            'div:has-text("立即")',
            # 新增：针对进度条区域作为点击入口的情况
            'div:has-text("已抢")'
        ]
        
        # 如果是抢购模式，我们更倾向于找到包含特定文字的元素
        if click:
            pass

        for btn_sel in btn_selectors:
            candidates = card.locator(btn_sel).all()
            for c in candidates:
                if c.is_visible() and c.inner_text().strip():
                    c_text = c.inner_text()
                    
                    # 排除"取消提醒" (在严格模式下)
                    if click and "取消提醒" in c_text:
                        continue
                        
                    # 排除纯进度条描述文本（如果是纯文本不可点）
                    # 但 Playwright 很难判断 div 是否可点，我们假设如果不包含“取消提醒”，它就是那个购买区域
                    # 且如果是 "已抢xx%"，通常它也是进度条容器的一部分，点击它也有效
                    
                    btn = c
                    # 如果找到了强力候选，直接选中
                    if any(k in c_text for k in ["抢购", "购买", "立即", "已抢"]):
                        break
            if btn and any(k in btn.inner_text() for k in ["抢购", "购买", "立即", "已抢"]): 
                break
        
        if btn:
            # 检查按钮状态
            # 如果是点击模式(click=True)，开启严格模式(strict_buy_mode=True)
            # 这样可以防止在抢购时刻误点 "取消提醒"
            result = self._check_button(btn, text, idx, strict_buy_mode=click)
            if result:
                if click:
                    log(f"正在点击购买按钮: {btn.inner_text()}...", "INFO")
                    result.click(force=True)
                return True, result
        else:
            log("卡片内未找到按钮", "WARNING")
            return False, None
    
    def _click_tabs(self, tabs):
        """点击Tab（专注模式：只点限时秒杀）"""
        target_tab = "限时秒杀"
        
        # 如果列表中包含限时秒杀，就只针对它进行操作
        if any(target_tab in t for t in tabs):
            log(f"专注模式: 锁定 '{target_tab}' Tab", "INFO")
            tabs_to_click = [target_tab]
        else:
            tabs_to_click = tabs

        for tab_name in tabs_to_click:
            try:
                # 记录点击前的卡片数量，用于判断是否加载成功
                initial_count = self.page.locator('.qc-base-grid__col').count()
                
                # 尝试点击
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
                            
                            # 检查加载效果
                            new_count = self.page.locator('.qc-base-grid__col').count()
                            if new_count > initial_count or new_count > 10:
                                log(f"Tab加载成功，当前卡片数: {new_count}", "SUCCESS")
                                clicked = True
                                break
                            else:
                                log(f"点击后未检测到内容增加 (当前: {new_count})，尝试再次点击...", "WARNING")
                                el.click(force=True)
                                self.page.wait_for_timeout(1000)
                                clicked = True # 算作点击了，继续后续流程
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
    
    def _check_element(self, element, target_prices, idx):
        """
        检查单个元素是否是目标商品
        
        Returns:
            button element if found, None otherwise
        """
        # 尝试不同层级查找容器
        for level in range(4, 10):
            try:
                container = element.locator(f'xpath=./ancestor::div[{level}]')
                if not container.count():
                    continue
                
                text = container.inner_text()
                # 预处理文本：去除换行，方便匹配
                normalized_text = text.replace('\n', '')
                
                # 检查价格
                # 先尝试原始文本
                has_price = self._has_target_price(text, target_prices)
                # 如果失败，尝试归一化后的文本
                if not has_price:
                    has_price = self._has_target_price(normalized_text, target_prices)
                
                if not has_price:
                    # 只有在非常接近目标（比如包含核心关键词）时才打印调试信息，避免刷屏
                    if self.target["text"] in text and idx < 3:
                        log(f"元素 [{idx}] 价格不匹配。期望: {target_prices}", "DEBUG")
                        # 打印前50个字符看看结构
                        clean_text = text.replace('\n', '[BR]').strip()[:100]
                        log(f"  实际文本: {clean_text}...", "DEBUG")
                    continue
                
                # 查找按钮
                buttons = container.locator('button, a[role="button"]').all()
                for btn in buttons:
                    result = self._check_button(btn, text, idx)
                    if result:
                        return result
                        
            except Exception as e:
                # 记录调试信息但不中断扫描
                # log(f"元素检查异常 [{idx}]: {e}", "DEBUG")
                continue
        
        return None
    
    def _has_target_price(self, text, prices):
        """检查文本中是否包含目标价格"""
        for price in prices:
            if match_price_exact(text, price):
                return True
        return False
    
    def _check_button(self, btn, product_text, idx, strict_buy_mode=False):
        """
        检查按钮状态
        
        Args:
            strict_buy_mode: 是否为严格购买模式（抢购时刻）。
                             如果是True，则只接受"抢购/购买"按钮，忽略"提醒/等待"按钮。
                             防止在抢购时刻误点"取消提醒"。
        """
        try:
            if not btn.is_visible(timeout=self.timeout["button_check"]):
                return None
            
            btn_text = btn.inner_text().strip()
            if not btn_text:
                return None
            
            # 已售罄
            if any(kw in btn_text for kw in ["已抢光", "售罄", "抢光"]):
                log(f"商品 [{idx}] 已售罄: {btn_text}", "WARNING")
                log(f"  {product_text[:80]}...", "DEBUG")
                return None
            
            # 可购买 (优先级最高)
            # 只要包含抢购/购买/已抢(非光)，无论处于什么模式，都是我们要点的
            # "已抢" 是针对进度条区域可点的情况
            if any(kw in btn_text for kw in ["抢购", "购买", "立即", "已抢"]):
                log(f"找到可购买商品 [{idx}]: {btn_text}", "SUCCESS")
                log(f"  {product_text[:80]}...", "DEBUG")
                return btn

            # 如果是严格购买模式（抢购时刻），除了明确的购买按钮，其他一律忽略
            if strict_buy_mode:
                # log(f"严格模式跳过非购买按钮: {btn_text}", "DEBUG")
                return None

            # 等待中/提醒状态 (仅在预定位模式下有效)
            pending_keywords = ["预约", "即将", "等待", "添加提醒", "取消提醒", "已设置"]
            if any(kw in btn_text for kw in pending_keywords):
                log(f"商品 [{idx}] 状态: {btn_text} (已锁定)", "SUCCESS")
                return btn
            
            # 未知状态
            log(f"商品 [{idx}] 未知状态: {btn_text}", "WARNING")
            return None
            
        except Exception as e:
            return None
    
    def _show_debug_info(self):
        """显示调试信息"""
        log("调试信息:", "DEBUG")
        debug_els = self.page.get_by_text(self.target["text"]).all()
        for i, el in enumerate(debug_els[:3]):
            try:
                log(f"  [{i}] {el.inner_text()[:60]}", "DEBUG")
            except:
                pass