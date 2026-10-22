"""本地性能基准：用合成活动页测量「准备页面 → 定位商品 → 下单」链路耗时。

不访问外网，也不需要登录：页面在本地用 data: URL 构造，尽量贴近真实活动页的
几个关键特征（懒渲染的秒杀模块、异步出现的配置弹窗、延迟启用的提交按钮），
用来对比优化前后的耗时。

前提：本机已装 Edge 或 Chrome（Playwright 直接驱动它，不下载内核）。

用法：
    python scripts/bench_flow.py                    # 跑一轮，打印各阶段耗时
    python scripts/bench_flow.py --repeat 3         # 跑三轮取中位数
    python scripts/bench_flow.py --cards 200        # 模拟 200 张卡片的大活动页
"""
import statistics
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from product_finder import ProductFinder  # noqa: E402
from purchase_flow import PurchaseFlow  # noqa: E402

# ---------------------------------------------------------------- 合成活动页

CARD_109 = (
    "轻量应用服务器 4核4G3M\n"
    "建站、Web应用、电商独立站等高性价比选择\n"
    "规格\n4核4G3M\n地域\n上海\n时长\n1年\n1.4折\n限1个\n"
    "活动价：\n109\n元\n日常价：780元"
)
CARD_38 = (
    "轻量应用服务器\n建站、Web应用、电商独立站等高性价比选\n4核4G3M\n"
    "300GB 月流量\n地域\n广州/上海/北京\n0.5折\n个人专享\n限1个\n"
    "活动价：\n38\n元\n日常价：780元\n0%\n已抢0%"
)
CARD_OTHER = (
    "轻量应用服务器\n4核8G5M\n活动价：\n199\n元\n日常价：1200元\n立即购买"
)

PAGE_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>bench</title>
<style>
  body {{ margin: 0; font-family: sans-serif; }}
  .qc-tabs__nav-item {{ display:inline-block; padding:8px 16px; border:1px solid #ddd; }}
  .qc-base-grid {{ display:flex; flex-wrap:wrap; gap:8px; }}
  .qc-base-grid__col {{ width:240px; border:1px solid #ccc; padding:8px; }}
  .uno3-dialog {{ position:absolute; top:20px; left:20px; width:400px; background:#fff;
                  border:1px solid #333; padding:12px; }}
  .img-option {{ display:inline-block; padding:4px 8px; border:1px solid #999; margin:2px; }}
</style></head><body>
<div class="qc-tabs">
  <div class="qc-tabs__nav-item" role="tab">限时秒杀</div>
  <div class="qc-tabs__nav-item" role="tab">全线产品特惠</div>
</div>
<div style="height:900px">顶部横幅占位</div>
<div id="grid" class="qc-base-grid"></div>
<div style="height:3000px">页脚占位</div>
<script>
const CARDS = {cards};
let injected = false;
function inject() {{
  if (injected) return;
  injected = true;
  const grid = document.getElementById('grid');
  for (const c of CARDS) {{
    const col = document.createElement('div');
    col.className = 'qc-base-grid__col';
    const body = document.createElement('div');
    body.innerText = c.text;
    col.appendChild(body);
    const btn = document.createElement('button');
    btn.textContent = c.kind === 'seckill' ? '添加提醒' : '立即购买';
    btn.className = 'uno3-buy-card-seckill-btn';
    if (c.kind !== 'seckill') {{
      btn.addEventListener('click', () => openDialog());
    }}
    col.appendChild(btn);
    grid.appendChild(col);
  }}
}}
// 模拟懒渲染：滚到第一屏之后才把商品注入 DOM
window.addEventListener('scroll', () => {{ if (window.scrollY > 300) inject(); }});
// 点 Tab 会让内容换一批（真实页面就是异步渲染的），延迟 300ms
document.querySelectorAll('.qc-tabs__nav-item').forEach((tab) => {{
  tab.addEventListener('click', () => setTimeout(inject, 300));
}});

function openDialog() {{
  if (document.querySelector('.uno3-dialog')) return;
  // 真实页面点购买后会跳到配置区，这里也回到顶部，保证弹窗在视口内
  window.scrollTo(0, 0);
  setTimeout(() => {{
    const d = document.createElement('div');
    d.className = 'uno3-dialog';
    d.innerHTML = '<div>选择镜像</div>'
      + '<div class="img-option">OpenCloudOS</div>'
      + '<div class="img-option">TencentOS</div>'
      + '<div>地域 上海 · 时长 1年</div>'
      + '<div class="uno3-dialog-footer"><button id="submit" disabled>立即购买</button></div>';
    document.body.appendChild(d);
    // 模拟异步校验：2 秒后提交按钮才可用
    setTimeout(() => {{ const b = document.getElementById('submit'); if (b) b.disabled = false; }}, 2000);
    document.getElementById('submit').addEventListener('click', () => {{
      location.href = '#submitted';
      d.remove();
    }});
  }}, 400);
}}
</script>
</body></html>
"""


def build_html(filler=0):
    """构造合成活动页。

    filler: 额外插入多少张「不相干的卡片」，用来模拟真实活动页的规模
    （腾讯云活动页通常有上百张商品卡）。卡片越多，扫描成本越能暴露出来。
    """
    import json

    side = max(0, filler) // 2
    # 至少一张「不相干」的卡：用来验证规格判据不会误选相邻档
    cards = [{"text": CARD_OTHER, "kind": "normal"} for _ in range(max(1, side))]
    cards.append({"text": CARD_109, "kind": "normal"})      # 目标（109 常售）
    cards.extend({"text": CARD_OTHER, "kind": "normal"} for _ in range(side))
    cards.append({"text": CARD_38, "kind": "seckill"})      # 秒杀档（不应被选中）
    return PAGE_HTML.format(cards=json.dumps(cards, ensure_ascii=False))


TARGET = {
    "text": "4核4G",
    "bandwidth": "3M",
    "duration": ["1年", "一年"],
    "exclude_specs": ["4核8G", "4核16G", "2核2G", "2核4G", "2核8G"],
    "test_price": "109",
    "test_prices_fallback": [],
    "prod_prices": ["38", "39"],
}

CONFIG = {
    "target": TARGET,
    "page": {"tabs": ["限时秒杀", "全线产品特惠"], "scroll_times": 20, "scroll_interval": 1.0},
    "run_mode": {"test_mode": True, "allow_retry_purchase": False},
    "images": ["Clawdbot", "OpenClaw", "clawdbot", "OpenCloudOS"],
    "timeout": {"login_detect": 3000, "login_wait": 300, "page_load": 3000,
                "tab_load": 2500, "button_check": 500},
    "browser": {},
}


def run_once(p, page_url):
    """跑一次完整链路，返回各阶段耗时（秒）。"""
    import tempfile

    marks = {}
    t0 = time.perf_counter()
    profile = tempfile.mkdtemp(prefix="lk-bench-")
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=profile, channel="msedge", headless=True,
        args=["--no-sandbox", "--disable-gpu"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    # 用 file:// 打开，reload() 才会重新加载同一份页面（set_content 的 data: 页 reload 会丢内容）
    page.goto(page_url, wait_until="domcontentloaded")
    marks["open_page"] = time.perf_counter() - t0

    finder = ProductFinder(page, TARGET, CONFIG["timeout"])

    t1 = time.perf_counter()
    finder.prepare_page(CONFIG["page"]["tabs"], CONFIG["page"]["scroll_times"])
    marks["prepare_page"] = time.perf_counter() - t1

    t2 = time.perf_counter()
    ok, btn = finder.find_and_click(["109"], click=False, require_buyable=True)
    marks["locate"] = time.perf_counter() - t2
    if not ok or btn is None:
        ctx.close()
        raise RuntimeError("基准页面里没定位到目标商品")

    # 模拟抢购时刻：维护性刷新后「立刻重新定位」，这是真正决定成败的一段。
    t2b = time.perf_counter()
    page.reload(wait_until="domcontentloaded")
    ok, btn = finder.find_and_click(
        ["109"], click=False, require_buyable=True, wait_seconds=8
    )
    marks["locate_after_reload"] = time.perf_counter() - t2b
    if not ok or btn is None:
        ctx.close()
        raise RuntimeError("刷新后没定位到目标商品")

    flow = PurchaseFlow(page, CONFIG, CONFIG["timeout"])
    t3 = time.perf_counter()
    submitted = flow.execute(btn)
    marks["purchase"] = time.perf_counter() - t3
    marks["total"] = time.perf_counter() - t0
    marks["submitted"] = bool(submitted)
    ctx.close()
    return marks


def main():
    repeat = 1
    if "--repeat" in sys.argv:
        repeat = int(sys.argv[sys.argv.index("--repeat") + 1])
    filler = 0
    if "--cards" in sys.argv:
        filler = int(sys.argv[sys.argv.index("--cards") + 1])

    import tempfile

    html = build_html(filler)
    fixture = Path(tempfile.mkdtemp(prefix="lk-fixture-")) / "activity.html"
    fixture.write_text(html, encoding="utf-8")
    page_url = fixture.as_uri()
    runs = []
    with sync_playwright() as p:
        for i in range(repeat):
            marks = run_once(p, page_url)
            runs.append(marks)
            print(
                f"[第 {i + 1} 轮] 开页 {marks['open_page']:.2f}s | "
                f"准备页面 {marks['prepare_page']:.2f}s | "
                f"定位 {marks['locate']:.2f}s | "
                f"刷新后定位 {marks['locate_after_reload']:.2f}s | "
                f"下单 {marks['purchase']:.2f}s | "
                f"总计 {marks['total']:.2f}s | 提交={marks['submitted']}"
            )

    print("\n=== 中位数 ===")
    for key, label in (
        ("open_page", "打开页面"),
        ("prepare_page", "准备页面（Tab + 滚动 + 等秒杀渲染）"),
        ("locate", "定位商品"),
        ("locate_after_reload", "刷新后重新定位（抢购时刻）"),
        ("purchase", "下单流程（点击→弹窗→选镜像→提交）"),
        ("total", "合计"),
    ):
        value = statistics.median(r[key] for r in runs)
        print(f"{label:<40}{value:6.2f}s")

    if not all(r["submitted"] for r in runs):
        print("\n[警告] 有轮次未确认提交成功")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
