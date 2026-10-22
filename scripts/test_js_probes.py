"""真实浏览器里验证 utils 的批量 DOM 探测（JS 快路径）。

这些 JS 片段决定了「快路径」是否可信：如果它们判断错了，抢购会选错商品
或点错按钮。因此除了单元测试，还要在真浏览器里跑一遍。

没有可用浏览器时整体跳过（返回 0），方便在无桌面的机器上跑全量测试。

用法：
    python scripts/test_js_probes.py
"""
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils import (  # noqa: E402
    UNSUPPORTED,
    js_any_visible_fast,
    js_collect_cards,
    js_find_buy_button,
    js_find_option,
    js_scroll_until_ready,
    js_visible_any,
    js_wait_enabled_button,
)

FAILED = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {name}: got={got!r} want={want!r}")
    if not ok:
        FAILED.append(name)


FIXTURE = """<!doctype html><meta charset="utf-8"><body>
<div id="grid">
  <div class="qc-base-grid__col" id="c1">
    <div>轻量应用服务器 4核4G3M 活动价：109元 日常价：780元</div>
    <button>立即购买</button>
  </div>
  <div class="qc-base-grid__col" id="c2">
    <div>轻量应用服务器 4核4G3M 活动价：38元 已抢0%</div>
    <button>添加提醒</button>
  </div>
  <div class="qc-base-grid__col" id="c3" style="display:none">
    <div>隐藏卡片</div><button>立即购买</button>
  </div>
</div>
<div style="height:3000px"></div>
<div id="lazy-slot"></div>
<div style="height:500px"></div>
<script>
  // 模拟懒渲染：滚到 600px 之后才把秒杀模块插进 DOM
  window.addEventListener('scroll', () => {
    if (window.scrollY > 600 && !document.querySelector('.uno3-buy-card-seckill')) {
      const d = document.createElement('div');
      d.className = 'uno3-buy-card-seckill';
      d.textContent = '秒杀模块';
      document.getElementById('lazy-slot').appendChild(d);
    }
  });
  window.openDialog = function (delay) {
    const d = document.createElement('div');
    d.className = 'uno3-dialog';
    d.innerHTML = '<div>选择镜像</div>'
      + '<span class="img-option">OpenCloudOS</span>'
      + '<span class="img-option">TencentOS</span>'
      + '<div class="uno3-dialog-footer"><button id="go" disabled>立即购买</button></div>';
    document.body.appendChild(d);
    setTimeout(() => { document.getElementById('go').disabled = false; }, delay);
  };
</script>
</body>"""


def run_checks(page):
    print("\n--- 可见性探测 ---")
    check("存在的选择器", js_visible_any(page, ["#grid"]), "#grid")
    check("不存在的选择器", js_visible_any(page, ["#nope"]), None)
    check("隐藏元素不算可见", js_visible_any(page, ["#c3"]), None)
    check("text= 伪选择器", js_visible_any(page, ["text=添加提醒"]), "text=添加提醒")
    check("text= 不存在", js_visible_any(page, ["text=不存在的文案"]), None)
    check("多条里取第一个命中", js_visible_any(page, ["#nope", "#c1"]), "#c1")
    check("单选择器快捷接口", js_any_visible_fast(page, "#c1"), True)
    check("单选择器快捷接口（无）", js_any_visible_fast(page, "#nope"), False)

    print("\n--- 卡片批量扫描 ---")
    cards = js_collect_cards(page, ".qc-base-grid__col")
    check("卡片数量", len(cards), 3)
    check("隐藏卡片被标记", cards[2]["visible"], False)
    check("卡片文本可取", "109" in cards[0]["text"], True)

    print("\n--- 购买按钮 ---")
    goods = ["立即抢购", "抢购", "立即购买", "马上抢", "去购买", "购买"]
    bad = ["添加提醒", "取消提醒", "已抢光", "售罄", "抢光", "查看", "详情", "续费"]
    c1 = page.locator("#c1")
    check("命中立即购买", js_find_buy_button(c1, goods, bad), "立即购买")
    c2 = page.locator("#c2")
    check("添加提醒不算购买按钮", js_find_buy_button(c2, goods, bad), None)

    print("\n--- 懒渲染：滚动等待 ---")
    # 只认选择器：卡片里本来就有「添加提醒」文案，带文本判据会在首屏就命中
    check("滚动前模块未渲染", page.locator(".uno3-buy-card-seckill").count(), 0)
    result = js_scroll_until_ready(
        page, ".uno3-buy-card-seckill", [], max_steps=20, step_ms=50
    )
    check("滚动后等到秒杀模块", result["found"], True)
    check("确实发生了滚动", result["steps"] >= 1, True)

    print("\n--- 配置弹窗：镜像与提交按钮 ---")
    page.evaluate("() => window.openDialog(600)")
    page.wait_for_timeout(150)
    check("弹窗内找到镜像", js_find_option(page, ["Clawdbot", "OpenCloudOS"], ".uno3-dialog"), "OpenCloudOS")
    marked = page.locator('[data-lk-opt="1"]').first
    check("镜像已打标记", marked.inner_text().strip(), "OpenCloudOS")

    started = __import__("time").perf_counter()
    text = js_wait_enabled_button(
        page, ["立即购买", "确认购买"], scope="dialog", wait_ms=5000, interval_ms=100
    )
    elapsed = __import__("time").perf_counter() - started
    check("等到可用提交按钮", text, "立即购买")
    check(f"等待在浏览器内完成且不超时（{elapsed:.1f}s）", 0.2 <= elapsed < 4, True)
    check("提交按钮已打标记", page.locator('[data-lk-submit="1"]').count(), 1)

    print("\n--- 不支持 evaluate 的对象：必须回退 ---")

    class Stub:
        def locator(self, sel):
            raise AssertionError("不该走到这里")

    check("无 evaluate -> UNSUPPORTED", js_visible_any(Stub(), ["#x"]), UNSUPPORTED)
    check("evaluate 报错 -> UNSUPPORTED", js_visible_any(page, ["!!bad-selector[[["]), None)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        print(f"SKIP 未安装 playwright: {exc}")
        return 0

    launched = False
    with sync_playwright() as p:
        for channel in ("msedge", "chrome", None):
            try:
                browser = p.chromium.launch(channel=channel, headless=True)
                launched = True
                break
            except Exception:
                continue
        if not launched:
            print("SKIP 本机没有可用的 Edge/Chrome")
            return 0

        page = browser.new_page()
        page.set_content(FIXTURE, wait_until="domcontentloaded")
        try:
            run_checks(page)
        finally:
            browser.close()

    if FAILED:
        print(f"\n失败 {len(FAILED)} 项: {FAILED}")
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
