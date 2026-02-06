"""
腾讯云轻量服务器自动抢购脚本 - 配置文件
"""

# ==================== 基础配置 ====================

# 目标商品配置
TARGET_CONFIG = {
    "text": "4核4G",              # 商品特征文字
    "bandwidth": "3M",            # 带宽要求（生产模式）
    "test_price": "79",           # 测试模式价格 (主要匹配)
    "test_prices_fallback": ["79.00", "38", "39", "180"], # 备选价格（防止价格变动）
    "prod_prices": ["38", "39"],  # 生产模式价格
}

# 页面配置
PAGE_CONFIG = {
    "url": "https://cloud.tencent.com/act/pro/double12-2025#MS",
    "tabs": [
        "限时秒杀",  # 优先点击且只关注这个
        "全线产品特惠" # 备选
    ],
    "scroll_times": 20,           # 滚动次数 (增加到20以确保加载所有商品)
    "scroll_interval": 1.0,       # 滚动间隔（秒）
}

# 运行模式
RUN_MODE = {
    "test_mode": False,           # True=测试79元, False=抢购38/39元
    "monitor_mode": False,        # True=持续监控按钮变化
    "buy_times": ["10:00:00", "15:00:00"],  # 秒杀时间点
}

# 超时配置
TIMEOUT_CONFIG = {
    "login_detect": 3000,         # 登录检测超时（毫秒）
    "login_wait": 300,            # 登录等待超时（秒）
    "page_load": 3000,            # 页面加载等待（毫秒）
    "tab_load": 2500,             # Tab切换等待（毫秒）
    "button_check": 500,          # 按钮检测超时（毫秒）
}

# 镜像配置
IMAGE_OPTIONS = ["Clawdbot", "OpenClaw", "clawdbot", "OpenCloudOS"]

# 调试配置
DEBUG = {
    "verbose": True,              # 详细日志
    "show_detection": True,       # 显示检测细节
}
