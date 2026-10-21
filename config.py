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


# ==================== 版本号 ====================

# 唯一的版本号来源：启动横幅、`--version`、EXE 文件属性都由它派生。
# 发版流程：改这里 → 提交 → 打同名 tag（v1.1.0）→ CI 自动构建并发布。
VERSION = "1.2.0"


# ==================== 基础配置 ====================

# 目标商品配置
#
# 选品判据是「规格 + 价格 + 规格细节」三重校验，缺一不可：
#   text          —— 卡片必须含这段文字（如「4核4G」）
#   bandwidth     —— 卡片必须含该带宽，且紧跟在 text 之后（如「4核4G3M」）
#   duration      —— 可选，命中则加分；支持多个写法（「1年」/「一年」）
#   exclude_specs —— 命中任一项即排除该卡片，防止选到相邻规格
#   prod_prices   —— 必须命中其中任一价格，且必须是「¥38 / 38元」这种
#                    真正的价格文案，「已抢38%」「38.35元」「398元」不算
#
# 为什么需要 bandwidth：活动页同时存在「4核4G3M 38元」与「4核4G3M 109元」
# 两张同规格卡，只判断 text 无法区分，必须靠价格；而带宽能挡住
# 「4核4G5M」这类同核数不同带宽的相邻档位。
TARGET_CONFIG = {
    "text": "4核4G",              # 商品特征文字
    "bandwidth": "3M",            # 带宽要求（生产模式）
    "duration": ["1年", "一年"],  # 时长（可选，命中加分）
    "exclude_specs": ["4核8G", "4核16G", "2核2G", "2核4G", "2核8G"],
    # 测试模式：买最便宜的常售套餐做流程验证。
    # 注意测试档的**规格与正式档不同**，所以要单独给 test_text / test_bandwidth；
    # 109 元那档已因「首单条件不满足」买不了（账号买过），改用 2核2G3M 68 元。
    "test_text": "2核2G",         # 测试档规格
    "test_bandwidth": "3M",       # 测试档带宽
    "test_price": "68",
    # 备选价格故意留空：109 是常售档，页面上没有它时宁可报错，
    # 也不要降级去抢 38 元秒杀档（那是正式套餐，会真的下一单）。
    "test_prices_fallback": [],
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
    "test_mode": False,           # True=测试109元(4核4G3M常售), False=抢购38/39元
    "monitor_mode": False,        # True=持续监控按钮变化
    "buy_times": ["10:00:00", "15:00:00"],  # 秒杀时间点
    # 点击购买后如果没看到配置弹窗，是否允许「重新定位商品再点一次」。
    # 默认 False：重复下单的代价远高于漏单，且实测该路径会买到页面上的
    # 其它商品（表现为「同时下单两个商品」）。确认点击确实无效再打开。
    "allow_retry_purchase": False,
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
    # 不显示浏览器窗口：把窗口挪到屏幕外（肉眼看不见，但仍是真实渲染）。
    #
    # 为什么不用 headless：实测活动页对无头浏览器有反爬，headless=True 时
    # 返回的是约 1 KB 的挑战页、0 张商品卡；离屏窗口则完全正常。
    "hide_window": True,
    "window_position": "-3200,-3200",  # 屏幕外坐标（多显示器时改更负的值）
    "window_size": "1400,1000",
}

# 支付页与二维码提取
PAYMENT_CONFIG = {
    "qr_dir": "qrcodes",        # 二维码图片保存目录（相对 EXE 目录）
    "wait_seconds": 20,         # 提交后等支付页出现的最长时间
    "keep_last": 10,            # 目录里最多保留多少个二维码文件
    # 收银台首页没有二维码：要先勾协议、点「下一步」，站点才跳到第三方支付页。
    # 这几步由 advance_to_qr() 自动完成；关掉的话只会存收银台整页截图。
    "auto_next_step": True,
    "agreement_labels": ["label.tea-form-check", ".tea-checkbox"],
    "next_button_texts": ["下一步", "确认支付", "去支付"],
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

