"""
登录模块 - 处理登录相关逻辑
"""
import time
from logger import log
from utils import wait_with_progress


class LoginHandler:
    """登录处理器"""
    
    # 登录检测选择器
    LOGIN_INDICATORS = [
        'text=登录',
        'text=扫码登录',
        'text=微信登录',
        '[class*="login"]',
        'iframe[src*="login"]',
        '.accsys-tp-modal',  # 登录弹窗容器
        '#accsys-tp-overlay-root'  # 遮罩层
    ]
    
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
        
    def detect_login_required(self, max_attempts=3):
        """
        检测是否需要登录
        
        Returns:
            bool: True表示需要登录，False表示已登录
        """
        log("检测登录状态...", "DEBUG")
        
        for attempt in range(max_attempts):
            log(f"第 {attempt + 1}/{max_attempts} 次检测", "DEBUG")
            
            for indicator in self.LOGIN_INDICATORS:
                try:
                    if self.page.locator(indicator).first.is_visible(
                        timeout=self.timeout["login_detect"]
                    ):
                        log(f"检测到登录需求: {indicator}", "WARNING")
                        return True
                except:
                    pass
            
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
    
    def _is_login_url(self, url):
        """检查是否是登录相关URL"""
        return any(kw in url.lower() for kw in ['login', 'passport', 'oauth'])

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
        
        # 等待二维码
        qr_appeared = self.wait_for_qr_code()
        
        # 等待登录完成
        return self.wait_for_login_complete(qr_appeared, old_url)

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
