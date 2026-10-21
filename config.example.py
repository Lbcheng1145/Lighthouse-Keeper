# 外部自定义配置模板
#
# 用法：把本文件复制为 config.local.py（与 LighthouseKeeper.exe 同级），
# 只保留你想修改的项即可，未写的项使用程序内置默认值。
#
# 保存后重新启动程序，控制台会打印
#   [配置] 已应用 config.local.py 中的 N 项自定义配置

# 提示：图形界面里点「保存配置」会自动生成本文件（config.local.py）的等价内容，
# 一般不需要手写；手写时只需保留要改的项，其余走内置默认值。
# 运行模式
RUN_MODE = {
    "test_mode": False,           # True = 抢 109 元（4核4G3M 常售）测试；False = 抢正式秒杀套餐
    "monitor_mode": False,
    "buy_times": ["10:00:00", "15:00:00"],  # 秒杀时间点（本地时间）
    # 点击购买后没看到配置弹窗时，是否允许重新定位再点一次。
    # 默认 False（防止重复下单 / 买到别的商品）。
    "allow_retry_purchase": False,
}

# 目标商品
TARGET_CONFIG = {
    "text": "4核4G",              # 商品特征文字，需与活动页描述一致
    "bandwidth": "3M",            # 带宽，必须紧跟在 text 之后（如 4核4G3M）
    "duration": ["1年", "一年"],  # 可选，命中加分
    "exclude_specs": ["4核8G", "4核16G", "2核2G", "2核4G", "2核8G"],
    "test_price": "109",           # 测试档：4核4G3M 109 元常售套餐
    "test_prices_fallback": [],    # 留空：不要降级去抢正式秒杀档
    "prod_prices": ["38", "39"],  # 正式模式目标价格
}

# 活动页与页面操作
PAGE_CONFIG = {
    "url": "https://cloud.tencent.com/act/pro/double12-2025#MS",
    "tabs": ["限时秒杀", "全线产品特惠"],
    "scroll_times": 20,
    "scroll_interval": 1.0,
}

# 镜像选项（按顺序尝试）
IMAGE_OPTIONS = ["Clawdbot", "OpenClaw", "clawdbot", "OpenCloudOS"]

# 浏览器：优先用本机已装的 Edge / Chrome
# 设为 None 表示回退到 Playwright 自带内核
BROWSER_CONFIG = {
    "channels": ["msedge", "chrome", None],
    "profile_dir": "browser-data",   # 登录态目录，可填绝对路径
}

# 超时（毫秒，除注明外）
TIMEOUT_CONFIG = {
    "login_detect": 3000,
    "login_wait": 300,            # 秒
    "page_load": 3000,
    "tab_load": 2500,
    "button_check": 500,
}
