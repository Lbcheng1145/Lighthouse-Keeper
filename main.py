"""
腾讯云轻量服务器自动抢购脚本 - 主程序
版本号见 config.VERSION（启动横幅与 --version 都读它）
"""
import sys
import io
import os
import time
import threading
import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

# 解决Windows中文输出问题
# 注意：PyInstaller windowed(无控制台) 模式下 stdout/stderr 为 None，
# 直接访问 .buffer 会抛 AttributeError 导致启动即崩溃，故需判空。
if sys.stdout is not None:
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True
    )
if sys.stderr is not None:
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True
    )

# 导入模块
from config import *
from logger import log, get_log_file
from product_finder import ProductFinder, BUY_BUTTON_TEXTS, NOT_BUY_BUTTON_TEXTS
from purchase_flow import PurchaseFlow


def _app_dir():
    """EXE 所在目录；源码运行时为脚本目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _short_err(err):
    """把 Playwright 冗长的多行报错压成一行，便于日志阅读。"""
    if err is None:
        return "(无)"
    text = str(err).strip().splitlines()
    return text[0][:200] if text else "(空)"


def _resolve_state_file(browser_cfg):
    """返回登录态快照文件路径（storage_state.json）。"""
    base = _resolve_profile_dir(browser_cfg)
    return base.parent / "session-state.json"


def _load_session_state(context, state_file):
    """把上次保存的 cookie 回灌到当前浏览器上下文。

    为什么需要这一步：
    腾讯云的登录 cookie 是"会话型"（无 expires），Chromium 不会把它们
    写进 profile 的 Cookies 数据库，关闭浏览器即丢失。
    因此单靠 user_data_dir 无法保住登录态，必须额外做快照回灌。
    """
    if not state_file.is_file():
        return False
    try:
        import json

        data = json.loads(state_file.read_text(encoding="utf-8"))
        cookies = data.get("cookies") or []
        if not cookies:
            return False
        context.add_cookies(cookies)
        log(f"已加载上次的登录态快照（{len(cookies)} 条 cookie）", "INFO")
        return True
    except Exception as e:
        log(f"登录态快照加载失败（将按未登录处理）: {_short_err(e)}", "WARNING")
        return False


def _save_session_state(context, state_file):
    """保存当前登录态，供下次启动回灌。"""
    try:
        context.storage_state(path=str(state_file))
        log(f"登录态已保存: {state_file.name}", "INFO")
        return True
    except Exception as e:
        log(f"登录态保存失败: {_short_err(e)}", "WARNING")
        return False


def _resolve_profile_dir(browser_cfg):
    """返回持久化用户数据目录。

    放在 EXE 同级 browser-data/ 下，保证：
    - 程序更新/重新打包后登录态仍在
    - 不与用户日常使用的 Edge/Chrome 主配置冲突

    若目录无法创建（只读介质、被杀软锁定等），退回到系统临时目录，
    避免因为一个目录问题导致整个程序起不来。
    """
    import tempfile

    configured = browser_cfg.get("profile_dir")
    if configured:
        base = Path(configured)
        if not base.is_absolute():
            base = _app_dir() / base
    else:
        base = _app_dir() / "browser-data"

    try:
        base.mkdir(parents=True, exist_ok=True)
        return base
    except OSError as exc:
        fallback = Path(tempfile.gettempdir()) / "LighthouseKeeper-data"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise RuntimeError(
                f"无法创建用户数据目录，且回退目录也失败:\n"
                f"  首选: {base} ({exc})\n  回退: {fallback}"
            )
        print(f"[警告] 无法创建 {base}（{exc}），改用 {fallback}")
        print("[警告] 注意：该目录是临时的，登录态可能无法长期保留")
        return fallback


class TencentCloudBot:
    """腾讯云抢购机器人"""
    
    def __init__(self):
        self.config = {
            "target": TARGET_CONFIG,
            "page": PAGE_CONFIG,
            "run_mode": RUN_MODE,
            "timeout": TIMEOUT_CONFIG,
            "images": IMAGE_OPTIONS,
            "browser": BROWSER_CONFIG,
            "payment": PAYMENT_CONFIG,
        }
        self.browser = None
        self.context = None
        self.page = None
        # 停止信号：图形界面的「停止」按钮会置位，各等待循环据此退出。
        # 纯命令行模式下没人置位，行为与以前完全一致。
        self._stop_event = threading.Event()

    def stop(self):
        """请求停止（可从其他线程调用）。"""
        self._stop_event.set()

    @property
    def stopped(self):
        return self._stop_event.is_set()

    def run(self):
        """主运行流程"""
        self._print_banner()
        
        with sync_playwright() as p:
            # 初始化浏览器
            self._init_browser(p)
            
            # 打开页面
            self._open_page()
            
            # 初始化工具
            finder = ProductFinder(self.page, self._target_config_for_mode(), self.config["timeout"])
            self.purchase_flow = PurchaseFlow(self.page, self.config, self.config["timeout"])
            
            # 1. 确保登录
            self._ensure_login()
            
            # 2. 提前定位商品 (找不到就一直找，直到找到为止)
            buy_button = None
            while not buy_button:
                if self.stopped:
                    log("已取消：停止定位", "WARNING")
                    self._keep_browser_open()
                    return
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

                # 抢购时刻只「定位」不点击：点击统一交给 purchase_flow.execute，
                # 否则同一个按钮会被点两次（可能开两个标签页、下两个单）。
                pre_button = buy_button  # 赛前锁定的句柄，刷新后可能已失效
                success, buy_button = self._find_target(finder, click=False, wait_seconds=8)

                if not success or buy_button is None:
                    log("抢购时刻未定位到可购买按钮，稍后重试一次...", "WARNING")
                    self.page.wait_for_timeout(300)
                    success, buy_button = self._find_target(finder, click=False, wait_seconds=5)

                if not success or buy_button is None:
                    # 兜底只能用「真正的购买控件」。赛前锁定的往往是卡片本身
                    # （那时按钮还叫「添加提醒」），点卡片不会下单，反而会点到
                    # 卡片里的提醒按钮、在你账号上挂一条提醒 —— 实测日志就出现过
                    # 这种情况（卡片文案变成「取消提醒」）。
                    if self._is_buy_control(pre_button):
                        log("重试后仍未定位到可购买按钮，复用赛前锁定的购买按钮句柄", "WARNING")
                        buy_button = pre_button
                    else:
                        log("赛前锁定的是卡片而非购买按钮，不复用（点卡片不会下单）", "WARNING")
                        buy_button = None

                if buy_button is None:
                    # 秒杀按钮可能在开抢瞬间才出现，这里再轮询等一段，而不是直接放弃
                    log("未拿到购买按钮，继续轮询等待按钮出现（最多 15 秒）...", "WARNING")
                    deadline = time.time() + 15
                    while time.time() < deadline:
                        if self.stopped:
                            log("已取消：停止等待购买按钮", "WARNING")
                            break
                        self.page.wait_for_timeout(400)
                        ok, cand = self._find_target(finder, click=False)
                        if ok and cand is not None:
                            buy_button = cand
                            log("购买按钮已出现，继续购买流程", "SUCCESS")
                            break

                if buy_button is None:
                    log("没有任何可用按钮，放弃本次抢购", "ERROR")
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
                    # 登录成功立即落盘，避免用户直接关窗口导致登录态丢失
                    self._persist_session()
                    break
                time.sleep(1)
        else:
            log("检测到已登录，继续下一步", "SUCCESS")
            # 顺手刷新一次快照（延长会话型 cookie 的有效窗口）
            self._persist_session()

    def _locate_target_product(self, finder):
        """提前定位商品并滚动到可见区域"""
        log("正在提前定位目标商品...", "INFO")
        
        # 准备页面（展开Tab，滚动）
        finder.prepare_page(
            self.config["page"]["tabs"], 
            self.config["page"]["scroll_times"]
        )
        
        target_prices = self._get_target_prices()
        # 查找但不点击 (click=False)；开抢前按钮可能还是「添加提醒」，
        # 此时只要求锁定卡片位置，不要求此刻可购买。
        # wait_seconds：秒杀模块异步渲染，给它一点时间再判定失败。
        success, button = self._find_target(
            finder, click=False, require_buyable=False, wait_seconds=15
        )
        
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
        print(f"🚀 腾讯云轻量服务器自动抢购脚本 v{VERSION}")
        
        if self.config["run_mode"]["test_mode"]:
            price = self.config["target"]["test_price"]
            log(f"测试模式: 将抢购 {price}元 套餐", "INFO")
        else:
            prices = self.config["target"]["prod_prices"]
            times = self.config["run_mode"]["buy_times"]
            log(f"生产模式: 将在 {times} 抢购 {prices}元 套餐", "INFO")
        
        print("="*60)
    
    def _init_browser(self, playwright):
        """初始化浏览器：调用本机自带的浏览器 + 持久化用户目录

        两层保活缺一不可：
        1. user_data_dir 持久化目录 —— 保存 localStorage、IndexedDB 等
        2. session-state.json 快照   —— 保存 cookie，包括"会话型"cookie
           （腾讯云登录 cookie 无 expires，Chromium 不会写盘，必须靠快照）
        """
        browser_cfg = self.config.get("browser", {}) or {}
        channels = browser_cfg.get(
            "channels", ["msedge", "chrome", None]
        ) or [None]
        profile_dir = _resolve_profile_dir(browser_cfg)
        state_file = _resolve_state_file(browser_cfg)

        hide_window = bool(browser_cfg.get("hide_window", False))
        headless = bool(browser_cfg.get("headless", False))
        window_position = browser_cfg.get("window_position", "-3200,-3200")
        window_size = browser_cfg.get("window_size", "1400,1000")

        launch_args = [
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--disable-gpu',
            '--disable-extensions',
            '--disable-session-crashed-bubble',
        ]
        if hide_window:
            # 关键：不是 headless，而是把窗口挪到屏幕外。活动页对无头浏览器
            # 有反爬（实测只返回 1 KB 挑战页），离屏窗口则和正常浏览器一致。
            launch_args += [
                f'--window-position={window_position}',
                f'--window-size={window_size}',
                '--disable-features=CalculateNativeWinOcclusion',
            ]
            log("离屏模式：浏览器窗口移到屏幕外（看不到，但真实渲染）", "INFO")
        else:
            launch_args.append('--start-maximized')

        if headless:
            log("警告：已启用 headless，活动页可能返回反爬挑战页", "WARNING")

        last_error = None
        for channel in channels:
            label = channel or "Playwright 自带内核"
            log(f"尝试使用浏览器: {label}", "INFO")
            try:
                self.context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    channel=channel,
                    headless=headless,
                    no_viewport=not hide_window,
                    args=launch_args,
                )
                self.page = (
                    self.context.pages[0]
                    if self.context.pages
                    else self.context.new_page()
                )
                log(f"浏览器已启动: {label}", "SUCCESS")
                log(f"用户数据目录: {profile_dir}", "INFO")
                # 回灌上次的登录态（关键：会话型 cookie 只能靠快照保住）
                _load_session_state(self.context, state_file)
                return
            except Exception as e:
                last_error = e
                log(f"使用 {label} 启动失败: {_short_err(e)}", "WARNING")

        log(f"所有浏览器均启动失败: {last_error}", "ERROR")
        raise RuntimeError(
            "无法启动浏览器。请确认本机已安装 Microsoft Edge 或 Google Chrome。\n"
            f"最后一次错误: {_short_err(last_error)}"
        )

    def _persist_session(self):
        """立即把当前登录态落盘。"""
        browser_cfg = self.config.get("browser", {}) or {}
        if self.context is None:
            return False
        return _save_session_state(self.context, _resolve_state_file(browser_cfg))
    
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
        # 已执行赛前刷新的时间点。多个 buy_times 时每个时间点都要各刷新一次，
        # 早期版本用单个布尔量，第二个时间点不会刷新，页面可能已过期/改版。
        refreshed_times = set()
        
        while True:
            # 停止请求（GUI「停止」按钮）
            if self.stopped:
                log("已取消等待抢购时间", "WARNING")
                return False

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
                if 60 < diff < 120 and buy_time not in refreshed_times:
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
                        while not self.stopped:
                            if handler.check_login_success_simple():
                                log("登录成功！", "SUCCESS")
                                break
                            time.sleep(1)
                        if self.stopped:
                            return False
                    else:
                        log("刷新后登录态正常", "SUCCESS")
                        
                    # 重新定位商品 (非常重要！刷新后所有ElementHandle都失效了)
                    log("刷新后重新定位商品...", "INFO")
                    finder = ProductFinder(self.page, self._target_config_for_mode(), self.config["timeout"])
                    
                    # 使用 _locate_target_product (它是TencentCloudBot的方法，需要self调用)
                    # 但在这里我们直接调用finder逻辑即可，简单点
                    finder.prepare_page(self.config["page"]["tabs"], self.config["page"]["scroll_times"])
                    target_prices = self._get_target_prices()
                    success, button = self._find_target(
                        finder, click=False, require_buyable=False, wait_seconds=15
                    )
                    
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

                    refreshed_times.add(buy_time)
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
            self._target_config_for_mode(),
            self.config["timeout"]
        )
        finder.prepare_page(
            self.config["page"]["tabs"],
            self.config["page"]["scroll_times"]
        )
        
        # 2. 查找商品（只定位，点击交给 execute，避免点两次）
        success, buy_button = self._find_target(finder, click=False)
        
        if not success:
            return False
        
        # 3. 执行购买流程
        flow = PurchaseFlow(
            self.page,
            self.config,
            self.config["timeout"]
        )
        return flow.execute(buy_button)
    
    def _target_config_for_mode(self):
        """当前模式下的选品条件。

        测试档的规格可能和正式档完全不同（正式 4核4G3M 38 元、测试 2核2G3M 68 元），
        所以测试模式下要覆盖 text/bandwidth；同时清空 exclude_specs ——
        那份排除表是按正式档写的（例如 2核2G 正在被排除），照用会把测试目标误杀掉。
        """
        target = dict(self.config["target"])
        if not self.config["run_mode"]["test_mode"]:
            return target

        text = target.get("test_text")
        if text:
            target["text"] = text
        bandwidth = target.get("test_bandwidth")
        if bandwidth is not None:
            target["bandwidth"] = bandwidth
        target["exclude_specs"] = []
        log(
            f"测试模式选品条件: text={target.get('text')!r} "
            f"bandwidth={target.get('bandwidth')!r} 价格={self._get_target_prices()}",
            "INFO",
        )
        return target

    def _get_target_prices(self):
        """获取目标价格（扁平列表，购买流程侧使用）"""
        target = self.config["target"]
        if self.config["run_mode"]["test_mode"]:
            prices = []
            if target.get("test_price"):
                prices.append(target["test_price"])
            prices.extend(target.get("test_prices_fallback") or [])
            return prices or list(target["prod_prices"])
        return target["prod_prices"]

    def _get_target_price_tiers(self):
        """按优先级分组返回目标价格。

        测试模式的价格原本与 fallback 混在同一个列表里，页面上没有测试档
        时就会去抢最便宜的那档（可能是正式秒杀套餐）。改为分层：先用
        `test_price` 找，确实找不到才降级到 fallback；fallback 为空时
        直接报错，不再自动换档（换档意味着买成别的东西）。
        """
        target = self.config["target"]
        if self.config["run_mode"]["test_mode"]:
            tiers = []
            test_price = target.get("test_price")
            if test_price:
                tiers.append([test_price])
            fallback = target.get("test_prices_fallback") or []
            if fallback:
                tiers.append(list(fallback))
            # 都没配就退回正式档，避免 test_mode 开着却完全找不到商品
            return tiers or [list(target["prod_prices"])]
        return [list(target["prod_prices"])]

    def _find_target(self, finder, click=False, require_buyable=True, wait_seconds=0):
        """按价格分组依次尝试定位，返回 (success, element)。"""
        result = (False, None)
        for tier in self._get_target_price_tiers():
            log(f"按价格分组定位: {tier}", "DEBUG")
            result = finder.find_and_click(
                tier,
                click=click,
                require_buyable=require_buyable,
                wait_seconds=wait_seconds,
            )
            if result[0]:
                return result
        return result

    @staticmethod
    def _is_buy_control(element):
        """元素是否是可以直接下单的购买控件（而不是整张卡片或提醒按钮）。"""
        if element is None:
            return False
        try:
            tag = element.evaluate("el => el.tagName")
            text = (element.inner_text() or "").strip()
        except Exception:
            return False
        if tag not in ("BUTTON", "A"):
            return False
        if any(bad in text for bad in NOT_BUY_BUTTON_TEXTS):
            return False
        return any(good in text for good in BUY_BUTTON_TEXTS)
    
    def _keep_browser_open(self):
        """保持浏览器打开（收到停止请求或窗口被关闭时退出）"""
        log("浏览器保持打开，按 Ctrl+C 或点「停止」退出", "INFO")
        try:
            # 用户直接关掉浏览器窗口时，context 会失效，需及时退出并落盘
            while not self.stopped:
                if self.context is not None:
                    try:
                        if not self.context.pages:
                            log("浏览器窗口已被关闭，准备保存登录态并退出", "WARNING")
                            break
                    except Exception:
                        break
                time.sleep(1)
        except KeyboardInterrupt:
            log("收到 Ctrl+C", "INFO")

        # 无论哪种方式退出，都先尝试保存登录态
        try:
            self._persist_session()
        except Exception:
            pass

        log("正在关闭浏览器...", "INFO")
        # 持久化上下文下没有独立的 browser 对象，需关 context
        if self.context:
            try:
                self.context.close()
            except Exception:
                pass
        elif self.browser:
            self.browser.close()
        log("已退出", "SUCCESS")


def main():
    """主函数"""
    # --version / -V：只打印版本号，不启动浏览器（便于确认手上的包是哪个版本）
    if any(arg in ("--version", "-V", "-v") for arg in sys.argv[1:]):
        print(f"LighthouseKeeper v{VERSION}")
        return

    try:
        bot = TencentCloudBot()
        bot.run()
    except Exception as e:
        log(f"程序异常: {e}", "ERROR")
        import traceback
        error_details = traceback.format_exc()
        log(f"错误详情:\n{error_details}", "ERROR")
        print("\n程序崩溃! 详见上方输出")
        log_path = get_log_file()
        if log_path:
            print(f"日志文件: {log_path}")
        else:
            print("（本次未能创建日志文件，错误信息仅见上方输出）")
        try:
            input("按回车键退出...")
        except:
            pass


if __name__ == "__main__":
    main()
