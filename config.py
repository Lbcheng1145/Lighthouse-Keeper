"""
腾讯云轻量服务器自动抢购脚本 - 配置文件
"""
import sys
from pathlib import Path


def _app_dir():
    """EXE 所在目录；源码运行时为脚本目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


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

# 浏览器配置
# 使用本机已安装的浏览器（不随包分发 Chromium），并复用同一个
# 用户数据目录保存登录态，避免每次运行都要重新扫码。
# channels 按顺序尝试：msedge = Microsoft Edge，chrome = Google Chrome，
# None = 回退到 Playwright 自带内核（需要单独安装）。
BROWSER_CONFIG = {
    "channels": ["msedge", "chrome", None],
    "profile_dir": "browser-data",   # 相对 EXE 目录，可填绝对路径
}

# 调试配置
DEBUG = {
    "verbose": True,              # 详细日志
    "show_detection": True,       # 显示检测细节
}


# ==================== 外部覆盖 ====================
# 打包成 EXE 后 config.py 被编译进程序，用户无法直接修改。
# 因此额外支持一个与 EXE 同目录的 config.local.py，
# 其中定义的同名变量会覆盖上面的默认值。
#
# 用法：在 EXE 旁边新建 config.local.py，写入例如
#     RUN_MODE = {"test_mode": False, "monitor_mode": False,
#                 "buy_times": ["10:00:00"]}
#     TARGET_CONFIG = {"text": "4核4G", "prod_prices": ["38", "39"]}
# 只写需要改的项即可，其余保持默认。
def _load_external_override():
    path = _app_dir() / "config.local.py"
    if not path.is_file():
        return
    try:
        import runpy

        data = runpy.run_path(str(path), run_name="config_local")
    except Exception as exc:  # 外部配置写错不应导致程序直接退出
        print(f"[配置] 读取 {path.name} 失败，使用内置默认值: {exc}")
        return

    overrides = {k: v for k, v in data.items() if k.isupper()}
    globals().update(overrides)
    print(f"[配置] 已应用 {path.name} 中的 {len(overrides)} 项自定义配置")


_load_external_override()

