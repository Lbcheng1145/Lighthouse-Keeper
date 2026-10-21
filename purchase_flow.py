"""
购买流程模块 - 处理点击购买后的流程
"""
from logger import log
from utils import wait_with_progress
from login_handler import LoginHandler
from product_finder import ProductFinder


class PurchaseFlow:
    """购买流程处理器"""
    
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
        
        # 1. 点击购买按钮（这会触发弹窗或登录）
        page = self._click_buy_button(buy_button)
        
        # 2. 处理登录
        # 注意：如果是配置弹窗模式，登录也会在弹窗之上覆盖一层，处理完登录后，配置弹窗应该还在下面
        login_success = self.login_handler.handle_login_flow(old_url)
        
        if not login_success:
            log("登录处理可能未完成，尝试继续操作...", "WARNING")
            
        # 3. 检查状态
        # 优先检查弹窗是否存在（使用更通用的选择器）
        # 只要有 uno3-dialog，就说明弹窗成功了，不需要重试
        has_dialog = False
        try:
            if self.page.locator('.uno3-dialog').first.is_visible(timeout=3000):
                has_dialog = True
                log("检测到配置弹窗 (.uno3-dialog)，准备选择镜像", "INFO")
        except:
            pass
            
        if has_dialog:
            return self._select_image()
            
        # 如果没有弹窗，再检查是否还在活动页
        current_url = self.page.url
        log(f"当前URL: {current_url}", "DEBUG")
        
        # 只有在明确没有弹窗，且还在活动页的情况下，才考虑重试
        # 这里的判断要非常谨慎，防止误判导致重复下单
        if self._is_activity_page(current_url):
            # 双重检查：再次确认真的没有弹窗吗？
            try:
                if self.page.get_by_text("镜像", exact=False).first.is_visible(timeout=1000):
                    log("二次检测发现弹窗，继续流程", "INFO")
                    return self._select_image()
            except:
                pass
                
            log("未检测到配置弹窗且仍在活动页，尝试重新定位商品", "WARNING")
            return self._retry_purchase()
        
        # 如果URL变了，也继续
        return self._select_image()
    
    def _click_buy_button(self, button):
        """
        点击购买按钮，处理可能的新标签页
        
        Returns:
            page object
        """
        log("点击购买按钮...", "INFO")
        
        try:
            # 尝试检测新标签页
            with self.page.expect_popup(timeout=3000) as popup_info:
                # 使用 force=True 强制点击，防止被登录弹窗遮挡导致的点击超时
                button.click(force=True)
            new_page = popup_info.value
            log("检测到新标签页，已切换", "DEBUG")
            return new_page
        except Exception as e:
            # 检查是否是因为被遮挡（通常是登录弹窗）
            error_str = str(e).lower()
            if "intercepts pointer events" in error_str:
                log("点击被遮挡（可能是登录弹窗），继续处理登录流程", "WARNING")
                return self.page
                
            # 如果没有新标签页，但点击可能已被触发
            try:
                # 如果按钮还可见，再次尝试轻触
                if button.is_visible(timeout=500):
                    button.click(force=True, timeout=1000)
            except:
                pass
                
            self.page.wait_for_timeout(2000)
            log("在当前页面继续", "DEBUG")
            return self.page
    
    def _is_activity_page(self, url):
        """判断是否是活动页"""
        return 'act/pro' in url or 'double12' in url
    
    def _retry_purchase(self):
        """重新定位商品并购买"""
        log("重新定位商品...", "INFO")
        
        # 等待页面稳定
        wait_with_progress(self.page, 3, "页面加载等待")
        
        # 重新初始化定位：先回到顶部，然后按配置滚动
        log("重置页面位置...", "DEBUG")
        self.page.keyboard.press("Home")
        self.page.wait_for_timeout(1000)
        
        # 获取配置的滚动次数（默认15次）
        scroll_times = self.config["page"].get("scroll_times", 15)
        log(f"执行滚动加载 ({scroll_times}次)...", "INFO")
        
        for i in range(scroll_times):
            self.page.keyboard.press("PageDown")
            self.page.wait_for_timeout(500)
        
        # 获取价格配置
        target_prices = self._get_target_prices()

        # 查找商品：复用 ProductFinder 的严格判据（规格 + 价格 + 规格细节），
        # 不再用 get_by_text 遍历祖先 div —— 那套判据与主流程不一致，
        # 且只查前 20 个元素，重试路径更容易点到相邻规格。
        finder = ProductFinder(self.page, self.config["target"], self.timeout)
        ok, button = finder.find_and_click(target_prices, click=False)

        if not ok or button is None:
            log("重新定位失败", "ERROR")
            return False

        log("重新找到商品，点击购买", "SUCCESS")
        try:
            button.click(force=True)
        except Exception as e:
            log(f"重新点击异常，尝试常规点击: {e}", "WARNING")
            button.click()

        # 极速模式：不再死等3秒，而是等待DOM加载
        self.page.wait_for_load_state('domcontentloaded')
        return self._select_image()
    
    def _select_image(self):
        """选择镜像并提交订单"""
        log("选择镜像: OpenClaw/Clawdbot...", "INFO")
        
        # 极速等待：配置页通常加载较快，缩短等待时间
        self.page.wait_for_load_state('domcontentloaded')
        
        # 1. 选择镜像
        image_found = False
        for image_name in self.config["images"]:
            try:
                # 缩短超时时间，快速尝试
                img_el = self.page.locator(f'text={image_name}').first
                if img_el.is_visible(timeout=500):
                    log(f"找到镜像: {image_name}", "SUCCESS")
                    img_el.click()
                    image_found = True
                    break
            except:
                continue
        
        if not image_found:
            log("未找到指定镜像，默认使用第一个或手动选择", "WARNING")
        
        # 2. 同意协议并提交
        return self._submit_order()

    def _submit_order(self):
        """提交订单"""
        log("提交订单...", "INFO")
        
        try:
            # 针对 Uno Design 弹窗的专门处理
            # 结构通常是: .uno3-dialog >> .uno3-dialog-footer >> button
            
            # 1. 尝试在弹窗Footer中查找按钮 (这是最准确的)
            footer_btn_selectors = [
                '.uno3-dialog-footer button:has-text("立即购买")',
                '.uno3-dialog-footer button:has-text("确认购买")',
                '.uno3-dialog-footer button:has-text("立即抢购")',  # 新增
                '.uno3-dialog-footer button:has-text("确定")',
                '.uno3-dialog-footer .buy-button'
            ]
            
            # 2. 常规按钮选择器 (作为备选)
            fallback_selectors = [
                'button:has-text("立即购买")',
                'button:has-text("确认购买")',
                'button:has-text("立即抢购")',  # 新增
                'button:has-text("提交订单")',
                '[data-action="buy"]'
            ]
            
            # 合并选择器，优先查弹窗里的
            all_selectors = footer_btn_selectors + fallback_selectors
            
            # 获取当前URL用于检测跳转
            start_url = self.page.url
            
            for attempt in range(10):  # 增加尝试次数到10次
                log(f"尝试点击提交按钮 (第{attempt+1}次)...", "INFO")
                
                # 遍历查找按钮
                btn_clicked = False
                for btn_selector in all_selectors:
                    try:
                        # 查找所有匹配的按钮
                        # 注意：可能有多个弹窗（隐藏的），我们要找可见的那个
                        btns = self.page.locator(btn_selector).all()
                        for btn in btns:
                            if btn.is_visible(timeout=200):
                                # 再次确认它是否在弹窗里（可选）
                                # log(f"找到可见按钮: {btn_selector}", "DEBUG")
                                
                                # 高亮按钮方便观察
                                try:
                                    btn.highlight()
                                except:
                                    pass
                                    
                                btn.click(force=True)
                                log(f"点击了按钮: {btn_selector}", "DEBUG")
                                btn_clicked = True
                                break # 找到一个点完就退出内层循环
                        if btn_clicked:
                            break
                    except:
                        pass
                
                if not btn_clicked:
                    # 如果常规方法找不到，尝试在当前活动的 dialog 中找所有 button
                    try:
                        dialog = self.page.locator('.uno3-dialog').filter(has_text="立即购买").first
                        if dialog.is_visible():
                            btn = dialog.locator('button').filter(has_text="立即购买").first
                            if btn.is_visible():
                                btn.click(force=True)
                                log("通过Dialog上下文点击了按钮", "DEBUG")
                                btn_clicked = True
                    except:
                        pass
                
                if not btn_clicked:
                     log("未找到提交按钮，尝试按 Enter 键...", "WARNING")
                     self.page.keyboard.press("Enter")
                
                # 等待跳转
                try:
                    # 等待URL变化或出现支付相关元素
                    self.page.wait_for_timeout(500) 
                    if self.page.url != start_url or "pay" in self.page.url:
                        log("检测到页面跳转，提交成功！", "SUCCESS")
                        return True
                except:
                    pass
            
            log("多次点击后页面未跳转，请手动点击！", "WARNING")
            return True
            
        except Exception as e:
            log(f"提交订单异常: {e}", "ERROR")
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
