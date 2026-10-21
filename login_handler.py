"""
登录模块 - 处理登录相关逻辑
"""
import time
from logger import log
from utils import wait_with_progress


class LoginHandler:
    """登录处理器"""
    
    # 强证据：出现任意一个就基本可以确定需要登录
    LOGIN_STRONG_INDICATORS = [
        '.accsys-tp-modal',              # 登录弹窗容器
        '#accsys-tp-overlay-root',       # 登录遮罩层
        'iframe[src*="login"]',
        'iframe[src*="passport"]',
        '[class*="login-modal"]',
        '[class*="login-dialog"]',
        '[class*="login-qrcode"]',
        'text=扫码登录',
        'text=微信登录',
    ]

    # 弱证据：页面上到处都有「登录」字样（顶栏、配置页提示、页脚…），
    # 单独命中不足以判定需要登录，必须配合二维码/登录弹窗才算数。
    LOGIN_WEAK_INDICATORS = [
        'text=登录',
        '[class*="login"]',
    ]

    # 兼容旧代码引用：合并视图（检测逻辑不再直接用它）
    LOGIN_INDICATORS = LOGIN_STRONG_INDICATORS + LOGIN_WEAK_INDICATORS

    # 判定「疑似要登录」但没有二维码时，最多等这么久。
    # 抢购时刻死等 5 分钟代价太大：等不到就按已登录继续，日志说清楚。
    NO_QR_GRACE_SECONDS = 30

    # 二维码选择器
    QR_SELECTORS = [
        'img[alt*="二维码"]',
        'img[src*="qrcode"]',
        '.qrcode-img',
        'canvas[class*="qrcode"]',
        '[class*="login-qrcode"]'
    ]
    
    # 用户登录状态选择器
    USER_INFO_SELECTORS = [
        '[class*="user-info"]',
        '[class*="user-name"]',
        '[class*="avatar"]',
        'text=退出登录'
    ]
    
    def __init__(self, page, timeout_config):
        self.page = page
        self.timeout = timeout_config

    def _first_visible(self, selectors, timeout):
        """返回第一个可见的选择器，没有则返回 None。"""
        for indicator in selectors:
            try:
                if self.page.locator(indicator).first.is_visible(timeout=timeout):
                    return indicator
            except Exception:
                continue
        return None

    def _is_login_url(self, url=None):
        """当前页面是否停在登录/认证页。"""
        url = (self.page.url if url is None else url) or ""
        return any(kw in url.lower() for kw in ['login', 'passport', 'oauth'])

    def detect_login_required(self, max_attempts=3):
        """检测是否需要登录。

        判定顺序（宁放过、不误判）：
          1. 停在登录/认证页 → 需要登录；
          2. 出现登录弹窗容器 / 扫码入口（强证据）→ 需要登录；
          3. 只有「登录」字样（弱证据）→ 再看有没有二维码，都没有就按
             已登录处理。

        早期版本把 `text=登录` 和 `[class*="login"]` 与强证据一视同仁，
        点「立即购买」后配置页顶栏的「登录」字样就会触发误判，接着进入
        5 分钟扫码等待，把抢购机会彻底耗掉（实测日志 15:23:17 起）。
        """
        log("检测登录状态...", "DEBUG")
        detect_ms = self.timeout.get("login_detect", 800) if self.timeout else 800

        for attempt in range(max_attempts):
            log(f"第 {attempt + 1}/{max_attempts} 次检测", "DEBUG")

            if self._is_login_url():
                log(f"页面停在登录页: {self.page.url}", "WARNING")
                return True

            strong = self._first_visible(self.LOGIN_STRONG_INDICATORS, detect_ms)
            if strong:
                log(f"检测到登录弹窗: {strong}", "WARNING")
                return True

            weak = self._first_visible(self.LOGIN_WEAK_INDICATORS, detect_ms)
            if weak:
                if self._is_qr_visible():
                    log(f"检测到二维码（弱提示 {weak}），判定需要登录", "WARNING")
                    return True
                log(
                    f"忽略弱登录提示「{weak}」：没有登录弹窗也没有二维码，按已登录处理",
                    "WARNING",
                )
                return False

            if attempt < max_attempts - 1:
                log("未检测到登录弹窗，1秒后重试...", "DEBUG")
                self.page.wait_for_timeout(1000)

        log("确认：无需登录（已登录）", "SUCCESS")
        return False
    
    def wait_for_qr_code(self, timeout=10):
        """
        等待二维码出现
        
        Returns:
            bool: True表示检测到二维码
        """
        log("等待二维码显示...", "PROGRESS")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            for selector in self.QR_SELECTORS:
                try:
                    if self.page.locator(selector).first.is_visible(timeout=500):
                        log(f"二维码已显示: {selector}", "SUCCESS")
                        return True
                except:
                    pass
            time.sleep(0.5)
        
        log("未检测到二维码元素", "WARNING")
        return False
    
    def wait_for_login_complete(self, qr_appeared, old_url):
        """
        等待登录完成
        
        Args:
            qr_appeared: 是否检测到过二维码
            old_url: 登录前的URL
            
        Returns:
            bool: True表示登录成功
        """
        log("="*60, "INFO")
        log("请扫码登录（最多等待5分钟）", "INFO")
        log("="*60, "INFO")
        
        timeout = self.timeout["login_wait"]
        start_time = time.time()
        check_count = 0
        
        while True:
            check_count += 1
            elapsed = int(time.time() - start_time)
            
            # 超时检查
            if elapsed > timeout:
                log(f"登录超时（{timeout}秒）", "ERROR")
                return False
            
            # 每5秒显示一次进度
            if check_count % 5 == 1:
                log(f"等待登录... [{elapsed}秒]", "PROGRESS")
            
            try:
                # 检测1：二维码消失
                qr_gone = False
                if qr_appeared:
                    qr_gone = not self._is_qr_visible()
                
                # 检测2：URL变化
                current_url = self.page.url
                url_changed = (
                    current_url != old_url and 
                    'login' not in current_url.lower()
                )
                
                # 检测3：用户信息出现
                user_logged_in = self._check_user_logged_in()
                
                # 检测4：登录弹窗是否还存在
                login_modal_exists = self.page.locator('.accsys-tp-modal').first.is_visible(timeout=500)
                
                # 判断登录完成
                # 条件1：明确检测到用户信息
                if user_logged_in:
                    log("检测到用户信息，登录成功！", "SUCCESS")
                    self.page.wait_for_timeout(2000)
                    return True
                
                # 条件2：URL发生变化（非登录页）
                if url_changed and not self._is_login_url(current_url):
                    log("URL发生有效跳转，登录成功！", "SUCCESS")
                    self.page.wait_for_timeout(2000)
                    return True
                    
                # 条件3：弹窗消失且二维码消失（放宽判定，防止一直死等）
                # 必须确保 qr_appeared 为 True（即之前确实出现过二维码），防止误判
                if qr_appeared and qr_gone and not login_modal_exists:
                    # 双重检查：等待2秒再次确认，防止只是闪烁
                    self.page.wait_for_timeout(2000)
                    if not self._is_qr_visible() and not self.page.locator('.accsys-tp-modal').first.is_visible():
                        log("登录弹窗已消失，判定为登录成功", "WARNING")
                        return True
                    
            except Exception as e:
                if check_count == 1:
                    log(f"检测异常: {e}", "DEBUG")
            
            time.sleep(1)
    
    def _is_qr_visible(self):
        """检查二维码是否可见"""
        for selector in self.QR_SELECTORS[:3]:  # 只检查前3个常用选择器
            try:
                if self.page.locator(selector).first.is_visible(timeout=300):
                    return True
            except:
                pass
        return False
    
    def _check_user_logged_in(self):
        """检查用户是否已登录"""
        for selector in self.USER_INFO_SELECTORS:
            try:
                if self.page.locator(selector).first.is_visible(timeout=300):
                    return True
            except:
                pass
        return False
    
    def handle_login_flow(self, old_url):
        """
        完整的登录流程处理
        
        Args:
            old_url: 点击购买前的URL
            
        Returns:
            bool: True表示登录成功（或无需登录）
        """
        # 检测是否需要登录
        need_login = self.detect_login_required()
        
        if not need_login:
            log("无需登录，等待页面跳转...", "INFO")
            self.page.wait_for_load_state('domcontentloaded')
            wait_with_progress(self.page, 3, "页面稳定等待")
            return True

        # 给了强证据也不代表真能扫码：某些页面只有「请登录」提示而没有二维码。
        # 这种情况下绝不死等到 5 分钟超时（抢购机会会被彻底耗掉），
        # 而是给一个短窗口观察：出现二维码就扫码，出现配置弹窗就直接继续。
        log("疑似需要登录，先确认是否真有二维码...", "WARNING")
        started = time.time()
        while time.time() - started < self.NO_QR_GRACE_SECONDS:
            if self._is_qr_visible():
                log("二维码已出现，进入扫码登录", "WARNING")
                return self.wait_for_login_complete(True, old_url)
            try:
                if self.page.locator('.uno3-dialog').first.is_visible(timeout=300):
                    log("配置弹窗已出现，说明登录态没问题，继续购买流程", "SUCCESS")
                    return True
            except Exception:
                pass
            time.sleep(1)

        log(
            f"等了 {self.NO_QR_GRACE_SECONDS} 秒仍没有二维码，"
            "按「登录态可用」继续流程（如需扫码请点停止后重新开始）",
            "WARNING",
        )
        return True

    def check_login_success_simple(self):
        """
        简单检查登录是否成功（用于主循环等待）
        
        Returns:
            bool: True表示已登录
        """
        try:
            # 检测用户信息
            if self._check_user_logged_in():
                return True
                
            # 检测登录弹窗是否消失
            # 如果没有登录弹窗，也没有登录按钮，且不是登录页，可能就是登录了
            # detect_login_required 返回 False 表示无需登录（已登录）
            if not self.detect_login_required(max_attempts=1):
                return True
                
            return False
        except:
            return False
