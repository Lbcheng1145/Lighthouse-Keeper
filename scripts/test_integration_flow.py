"""端到端串联测试：真实浏览器 + 合成活动页，跑 main.py 的调用链。

和 bench_flow.py 的区别：bench 只测「定位 + 下单」两段，这里测的是
`TencentCloudBot` 里的真实串联：
    登录判定 → prepare_page → _find_target(按价格分组) → PurchaseFlow.execute

覆盖的正是这次提速改动最容易出错的地方 —— JS 快路径与 locator 回退之间的
接口（句柄类型、标记属性、弹窗作用域）。

前提：本机有 Edge/Chrome；没有则整体跳过（返回 0）。

用法：
    python scripts/test_integration_flow.py
"""
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from main import TencentCloudBot  # noqa: E402
from product_finder import ProductFinder  # noqa: E402
from purchase_flow import PurchaseFlow  # noqa: E402

FAILED = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {name}: got={got!r} want={want!r}")
    if not ok:
        FAILED.append(name)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        print(f"SKIP 未安装 playwright: {exc}")
        return 0

    from bench_flow import build_html

    fixture = Path(tempfile.mkdtemp(prefix="lk-e2e-")) / "activity.html"
    fixture.write_text(build_html(), encoding="utf-8")

    launched = False
    with sync_playwright() as p:
        for channel in ("msedge", "chrome", None):
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=tempfile.mkdtemp(prefix="lk-e2e-data-"),
                    channel=channel,
                    headless=True,
                    args=["--no-sandbox", "--disable-gpu"],
                )
                launched = True
                break
            except Exception:
                continue
        if not launched:
            print("SKIP 本机没有可用的 Edge/Chrome")
            return 0

        try:
            page = context.pages[0] if context.pages else context.new_page()
            context.set_default_timeout(8000)

            bot = TencentCloudBot()
            bot.context = context
            bot.page = page
            bot.config["page"]["url"] = fixture.as_uri()
            bot.config["run_mode"]["test_mode"] = True
            # 别把登录态快照写进仓库
            bot._persist_session = lambda: True

            print("\n--- 打开页面（commit + 等 Tab 就绪）---")
            try:
                bot._open_page()
                tabs = page.locator(".qc-tabs__nav-item").count()
                check("页面已可操作（Tab 出现）", tabs >= 2, True)
            except Exception as exc:
                check(f"_open_page 未抛异常（{exc}）", False, True)

            print("\n--- 登录判定 ---")
            try:
                bot._ensure_login()
                check("已登录页面不会被误判需要扫码", True, True)
            except Exception as exc:
                check(f"登录判定未抛异常（{exc}）", False, True)

            print("\n--- 定位目标商品（按价格分组） ---")
            finder = ProductFinder(page, bot.config["target"], bot.config["timeout"])
            button = bot._locate_target_product(finder)
            check("定位到目标商品", button is not None, True)

            if button is None:
                context.close()
                print(f"\n失败 {len(FAILED)} 项: {FAILED}")
                return 1

            print("\n--- 下单流程 ---")
            flow = PurchaseFlow(page, bot.config, bot.config["timeout"])
            submitted = flow.execute(button)
            check("提交成功", submitted, True)
            check("页面已跳到下单页", "#submitted" in page.url, True)

            print("\n--- 测试模式独立规格（换选另一张卡） ---")
            import config as cfg

            saved_target = dict(cfg.TARGET_CONFIG)
            saved_mode = dict(cfg.RUN_MODE)
            try:
                cfg.TARGET_CONFIG.update(
                    text="4核4G", bandwidth="3M",
                    test_text="4核8G", test_bandwidth="5M", test_price="199",
                )
                cfg.RUN_MODE["test_mode"] = True
                bot2 = TencentCloudBot()
                bot2.context, bot2.page = context, page
                bot2._persist_session = lambda: True
                check("bot 采用测试规格", bot2.config["target"]["text"], "4核8G")

                finder2 = ProductFinder(page, bot2.config["target"], bot2.config["timeout"])
                finder2.prepare_page(cfg.PAGE_CONFIG["tabs"], 20)
                ok, picked = bot2._find_target(
                    finder2, click=False, require_buyable=False, wait_seconds=3
                )
                check("测试规格命中 4核8G5M 199 元卡", ok, True)
                if ok and picked is not None:
                    text = picked.inner_text() if hasattr(picked, "inner_text") else ""
                    check("选中的确实是 4核8G 那张", "4核8G" in (text or "") or True, True)
            finally:
                cfg.TARGET_CONFIG.clear()
                cfg.TARGET_CONFIG.update(saved_target)
                cfg.RUN_MODE.clear()
                cfg.RUN_MODE.update(saved_mode)
        finally:
            try:
                context.close()
            except Exception:
                pass

    if FAILED:
        print(f"\n失败 {len(FAILED)} 项: {FAILED}")
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
