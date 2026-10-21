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

    注意：本函数只保证「数字边界」，仍会被页面噪声命中，
    例如进度条「已抢38%」、小数「38.35元」、折扣「3.8折」。
    选品判据请使用 match_price_in_card()。
    """
    price_num = price.replace("元", "").strip()
    pattern = r'(?<!\d)' + re.escape(price_num) + r'(?!\d)'
    return bool(re.search(pattern, text.replace("\n", " ")))


# 活动页里与价格无关、却含数字的片段。匹配价格前先剔除，
# 否则「已抢38%」「3.8折」「398元」都会被当成命中目标价。
_PRICE_NOISE_PATTERNS = (
    r"已抢\s*\d+(?:\.\d+)?\s*%",          # 进度条百分比
    r"\d+(?:\.\d+)?\s*折",                 # 折扣标签
    r"\d+(?:\.\d+)?\s*(?:GB|TB|MB|G|M)\b",  # 流量 / 带宽 / 容量
    r"限\s*\d+\s*个",                      # 限购数量
    r"\d+\s*天",                           # 天数
    r"\d+\s*积分",                         # 积分
    r"约\s*\d+\s*轮",                      # 轮次说明
    r"\d+\s*个?月",                        # 时长
    r"\d+\s*年",                           # 时长
)


def match_price_in_card(text, price):
    """在卡片文本里匹配「真正的价格文案」。

    与 match_price_exact 的区别：价格必须带货币上下文，
    即「¥38」「￥38」「38元」「38.00元」，而不是任意位置的 38。

    这样可排除实测中真实出现的误命中：
      * 「已抢38%」        —— 进度条百分比
      * 「活动价：38.35元」 —— 小数（38.35 不是 38）
      * 「398元」           —— 不同价格
      * 「3.8折」           —— 折扣
    """
    price_num = price.replace("元", "").strip()
    if not price_num:
        return False

    cleaned = text.replace("\n", " ")
    for noise in _PRICE_NOISE_PATTERNS:
        cleaned = re.sub(noise, " ", cleaned)

    patterns = (
        # ¥38 / ￥38 / ¥38.00（后接非数字，且小数部分只能是 .0 / .00）
        r"[¥￥]\s*" + re.escape(price_num) + r"(?:\.0+)?(?![\d.])",
        # 38元 / 38.00元 / 38 元（价格与单位可能分处不同节点，中间只允许空白或竖线）
        r"(?<![\d.])" + re.escape(price_num) + r"(?:\.0+)?[\s|｜]*元",
        # 抢购瞬间价格可能只渲染成「活动价：38」，没有货币符号也没有「元」。
        # 只认明确的正向价格标签，避免「日常价/原价」被当成活动价。
        r"(?:活动价|抢购价|秒杀价|到手价|特惠价)\s*[:：]?\s*[¥￥]?\s*"
        + re.escape(price_num)
        + r"(?:\.0+)?(?![\d.])",
    )
    return any(re.search(pat, cleaned) for pat in patterns)


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
