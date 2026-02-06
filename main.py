"""
腾讯云轻量服务器自动抢购脚本 - 主程序
重构版本 v2.0
"""
import sys
import io
import time
import datetime
from playwright.sync_api import sync_playwright

# 解决Windows中文输出问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 导入模块
from config import *
from logger import log, get_log_file
from product_finder import ProductFinder
from purchase_flow import PurchaseFlow


class TencentCloudBot:
    """腾讯云抢购机器人"""
    
    def __init__(self):
        self.config = {
            "target": TARGET_CONFIG,
            "page": PAGE_CONFIG,
            "run_mode": RUN_MODE,
            "timeout": TIMEOUT_CONFIG,
            "images": IMAGE_OPTIONS,
        }
        self.browser = None
        self.page = None
    
    def run(self):
        """主运行流程"""
        self._print_banner()
        
        with sync_playwright() as p:
            # 初始化浏览器
            self._init_browser(p)
            
            # 打开页面
            self._open_page()
            
            # 初始化工具
            finder = ProductFinder(self.page, self.config["target"], self.config["timeout"])
            self.purchase_flow = PurchaseFlow(self.page, self.config, self.config["timeout"])
            
            # 1. 确保登录
            self._ensure_login()
            
            # 2. 提前定位商品 (找不到就一直找，直到找到为止)
            buy_button = None
            while not buy_button:
                buy_button = self._locate_target_product(finder)
                if not buy_button:
                    log("未找到目标商品，5秒后刷新重试...", "WARNING")
                    time.sleep(5)
                    self.page.reload()
                    self.page.wait_for_load_state('domcontentloaded')
                    self.page.wait_for_timeout(3000)
            
            # 3. 等待抢购时间
            if self._wait_for_buy_time():
                # 4. 执行购买
                log("时间到！立即重新定位并点击...", "INFO")
                
                # 直接调用 finder 重新查找并点击 (click=True)
                # 因为页面元素可能已经刷新，使用预定位的按钮句柄可能失效
                # 现在的 finder 足够快，且页面已经滚动到位，能瞬间找到
                target_prices = self._get_target_prices()
                success, buy_button = finder.find_and_click(target_prices, click=True)
                
                if not success:
                    log("抢购时刻无法定位商品，尝试使用旧句柄兜底...", "WARNING")
                    # 如果重新找失败了（极其罕见），才尝试用旧的
                    try:
                        buy_button.click(force=True)
                        success = True
                    except:
                        log("旧句柄也失效", "ERROR")
                        return

                success = self.purchase_flow.execute(buy_button)
                
                if success:
                    log("购买流程完成！请在浏览器中完成支付", "SUCCESS")
                else:
                    log("购买流程未完成", "ERROR")
            
            # 保持浏览器打开
            self._keep_browser_open()

    def _ensure_login(self):
        """确保用户已登录"""
        from login_handler import LoginHandler
        handler = LoginHandler(self.page, self.config["timeout"])
        
        log("检查登录状态...", "INFO")
        if handler.detect_login_required(max_attempts=1):
            log("请先扫码登录（你有充分的时间进行操作）...", "WARNING")
            log("脚本将暂停等待，直到检测到登录成功...", "INFO")
            
            # 触发登录弹窗（如果没弹出来的话，随便点个按钮触发一下）
            # 这里简单点，直接让用户操作
            
            # 循环检测登录状态
            while True:
                if handler.check_login_success_simple():
                    log("登录成功！准备进行下一步...", "SUCCESS")
                    break
                time.sleep(1)
        else:
            log("检测到已登录，继续下一步", "SUCCESS")

    def _locate_target_product(self, finder):
        """提前定位商品并滚动到可见区域"""
        log("正在提前定位目标商品...", "INFO")
        
        # 准备页面（展开Tab，滚动）
        finder.prepare_page(
            self.config["page"]["tabs"], 
            self.config["page"]["scroll_times"]
        )
        
        target_prices = self._get_target_prices()
        # 查找但不点击 (click=False)
        success, button = finder.find_and_click(target_prices, click=False)
        
        if success and button:
            log("已锁定目标商品！", "SUCCESS")
            # 强制滚动到屏幕中央 (使用JS)
            try:
                button.evaluate("element => element.scrollIntoView({behavior: 'smooth', block: 'center'})")
                # 稍微等待滚动完成
                self.page.wait_for_timeout(500)
                # 再用 Playwright 的方法确保一下
                button.scroll_into_view_if_needed()
                # 高亮
                button.highlight()
            except Exception as e:
                log(f"滚动定位异常: {e}", "WARNING")
                
            return button
        else:
            log("未找到目标商品，请检查配置或库存", "ERROR")
            return None
    
    def _print_banner(self):
        """打印启动信息"""
        print("="*60)
        print("🚀 腾讯云轻量服务器自动抢购脚本 v2.0")
        
        if self.config["run_mode"]["test_mode"]:
            price = self.config["target"]["test_price"]
            log(f"测试模式: 将抢购 {price}元 套餐", "INFO")
        else:
            prices = self.config["target"]["prod_prices"]
            times = self.config["run_mode"]["buy_times"]
            log(f"生产模式: 将在 {times} 抢购 {prices}元 套餐", "INFO")
        
        print("="*60)
    
    def _init_browser(self, playwright):
        """初始化浏览器"""
        log("启动浏览器...", "INFO")
        try:
            self.browser = playwright.chromium.launch(
                headless=False,
                args=[
                    '--start-maximized',
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',  # 禁用GPU加速，减少显存/内存占用
                    '--disable-extensions'
                ]
            )
            context = self.browser.new_context(no_viewport=True)
            self.page = context.new_page()
        except Exception as e:
            log(f"浏览器启动失败: {e}", "ERROR")
            raise e
    
    def _open_page(self):
        """打开活动页面"""
        log("打开活动页面...", "INFO")
        url = self.config["page"]["url"]
        self.page.goto(url, wait_until='domcontentloaded')
        self.page.wait_for_timeout(3000)
        log("页面加载完成", "SUCCESS")
    
    def _wait_for_buy_time(self):
        """
        等待抢购时间
        """
        # 测试模式：立即执行
        if self.config["run_mode"]["test_mode"]:
            log("测试模式：立即开始", "INFO")
            return True
        
        # 生产模式：等待指定时间
        buy_times = self.config["run_mode"]["buy_times"]
        max_wait = 2 * 3600  # 最多等待2小时
        start_time = time.time()
        
        last_refresh_time = time.time()
        refreshed_before_snipe = False  # 是否已执行赛前刷新
        
        while True:
            # 超时检查
            if time.time() - start_time > max_wait:
                log("等待超时（2小时）", "ERROR")
                return False
            
            # 检查是否到达抢购时间
            now = datetime.datetime.now()
            current_time = now.strftime("%H:%M:%S")
            
            for buy_time in buy_times:
                target = datetime.datetime.strptime(
                    buy_time, "%H:%M:%S"
                ).replace(year=now.year, month=now.month, day=now.day)
                
                diff = (target - now).total_seconds()
                
                # --- 策略：赛前 2 分钟刷新保活 ---
                if 60 < diff < 120 and not refreshed_before_snipe:
                    log(f"距离抢购还有 {int(diff)} 秒，执行赛前刷新保活...", "INFO")
                    self.page.reload()
                    self.page.wait_for_load_state('domcontentloaded')
                    self.page.wait_for_timeout(2000)
                    
                    # 刷新后必须重新确保登录
                    from login_handler import LoginHandler
                    handler = LoginHandler(self.page, self.config["timeout"])
                    
                    if handler.detect_login_required(max_attempts=1):
                        log("刷新后发现登录态失效！请立即扫码！", "WARNING")
                        # 循环检测直到登录
                        while True:
                            if handler.check_login_success_simple():
                                log("登录成功！", "SUCCESS")
                                break
                            time.sleep(1)
                    else:
                        log("刷新后登录态正常", "SUCCESS")
                        
                    # 重新定位商品 (非常重要！刷新后所有ElementHandle都失效了)
                    log("刷新后重新定位商品...", "INFO")
                    finder = ProductFinder(self.page, self.config["target"], self.config["timeout"])
                    
                    # 使用 _locate_target_product (它是TencentCloudBot的方法，需要self调用)
                    # 但在这里我们直接调用finder逻辑即可，简单点
                    finder.prepare_page(self.config["page"]["tabs"], self.config["page"]["scroll_times"])
                    target_prices = self._get_target_prices()
                    success, button = finder.find_and_click(target_prices, click=False)
                    
                    if success and button:
                        log("赛前重新定位成功，滚动并锁定...", "SUCCESS")
                        try:
                            button.evaluate("element => element.scrollIntoView({behavior: 'smooth', block: 'center'})")
                            self.page.wait_for_timeout(500)
                            button.highlight()
                        except:
                            pass
                    else:
                        log("赛前重新定位失败！将在抢购时尝试现场抓取", "WARNING")

                    refreshed_before_snipe = True
                    log("赛前准备完成，继续倒计时...", "INFO")

                # --- 策略：准点触发 ---
                # 准点触发（0秒误差）
                if 0 <= diff < 2:
                    log(f"到达抢购时间: {buy_time}", "SUCCESS")
                    return True
            
            # 显示等待状态
            print(f"\r⏳ 当前时间: {current_time} | 等待: {buy_times}", end="")
            time.sleep(0.5)
    
    def _execute_purchase(self):
        """
        执行购买流程
        
        Returns:
            bool: 成功返回True
        """
        log("开始抢购流程", "INFO")
        print("="*60)
        
        # 1. 准备页面
        finder = ProductFinder(
            self.page,
            self.config["target"],
            self.config["timeout"]
        )
        finder.prepare_page(
            self.config["page"]["tabs"],
            self.config["page"]["scroll_times"]
        )
        
        # 2. 查找商品
        target_prices = self._get_target_prices()
        success, buy_button = finder.find_and_click(target_prices)
        
        if not success:
            return False
        
        # 3. 执行购买流程
        flow = PurchaseFlow(
            self.page,
            self.config,
            self.config["timeout"]
        )
        return flow.execute(buy_button)
    
    def _get_target_prices(self):
        """获取目标价格"""
        if self.config["run_mode"]["test_mode"]:
            prices = [self.config["target"]["test_price"]]
            if "test_prices_fallback" in self.config["target"]:
                prices.extend(self.config["target"]["test_prices_fallback"])
            return prices
        else:
            return self.config["target"]["prod_prices"]
    
    def _keep_browser_open(self):
        """保持浏览器打开"""
        log("浏览器保持打开，按 Ctrl+C 退出", "INFO")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log("正在关闭浏览器...", "INFO")
            if self.browser:
                self.browser.close()
            log("已退出", "SUCCESS")


def main():
    """主函数"""
    try:
        bot = TencentCloudBot()
        bot.run()
    except Exception as e:
        log(f"程序异常: {e}", "ERROR")
        import traceback
        error_details = traceback.format_exc()
        log(f"错误详情:\n{error_details}", "ERROR")
        print(f"\n❌ 程序崩溃! 错误详情已记录到日志文件")
        print(f"请查看日志: {get_log_file()}")
        try:
            input("按回车键退出...")
        except:
            pass


if __name__ == "__main__":
    main()
