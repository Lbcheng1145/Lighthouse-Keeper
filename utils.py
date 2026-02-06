"""
工具模块 - 通用工具函数
"""
import re
import time
from logger import log


def match_price_exact(text, price):
    """
    精确匹配价格，避免误匹配
    例如: "79" 不会匹配 "780"
    """
    price_num = price.replace("元", "").strip()
    pattern = r'(?<!\d)' + re.escape(price_num) + r'(?!\d)'
    return bool(re.search(pattern, text.replace("\n", " ")))


def wait_with_progress(page, seconds, message="等待中"):
    """带进度提示的等待"""
    log(f"{message} ({seconds}秒)...", "PROGRESS")
    page.wait_for_timeout(seconds * 1000)


def retry_operation(func, max_attempts=3, wait_between=1.0, operation_name="操作"):
    """
    重试装饰器
    
    Args:
        func: 要执行的函数
        max_attempts: 最大尝试次数
        wait_between: 重试间隔（秒）
        operation_name: 操作名称（用于日志）
    """
    for attempt in range(1, max_attempts + 1):
        try:
            log(f"{operation_name} (尝试 {attempt}/{max_attempts})", "DEBUG")
            result = func()
            if result:
                log(f"{operation_name} 成功", "SUCCESS")
                return result
        except Exception as e:
            log(f"{operation_name} 失败: {e}", "ERROR")
        
        if attempt < max_attempts:
            log(f"等待 {wait_between}秒后重试...", "DEBUG")
            time.sleep(wait_between)
    
    log(f"{operation_name} 所有尝试均失败", "ERROR")
    return None
