"""支付二维码提取的回归测试（本地假页面，不访问腾讯云、不下单）。

覆盖：
  1. canvas 画的二维码 → 走 canvas 策略，拿到的 PNG 是原始像素
  2. img(data:) 二维码      → 走元素截图策略
  3. 页面上没有二维码        → 兜底整页截图，而不是崩掉
  4. 支付页判定（关键词 / URL）
  5. PNG 尺寸解析与「像不像二维码」的判定

用法：
    python scripts/test_payment_qr.py
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

import payment  # noqa: E402

FAILED = []

# 一个 21x21 的假二维码（棋盘格放大到 210px），用 canvas 画出来
FAKE_QR_PAGE = """
<!doctype html><meta charset="utf-8">
<body style="margin:0;background:#fff">
  <div id="wrap"></div>
  <script>
    const n = 21, cell = 10;
    const c = document.createElement('canvas');
    c.width = c.height = n * cell;
    c.id = 'qrcode-canvas';
    c.className = 'qrcode';
    const ctx = c.getContext('2d');
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.fillStyle = '#000';
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        if ((x * 7 + y * 13) % 3 === 0) {
          ctx.fillRect(x * cell, y * cell, cell, cell);
        }
      }
    }
    document.getElementById('wrap').appendChild(c);
  </script>
</body>
"""

IMG_QR_PAGE = """
<!doctype html><meta charset="utf-8">
<body style="margin:0;background:#fff">
  <img alt="二维码" class="qrcode" width="210" height="210"
       style="display:block;width:210px;height:210px"
       src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==">
</body>
"""

EMPTY_PAGE = """
<!doctype html><meta charset="utf-8">
<body style="margin:0;background:#fff"><h1>收银台</h1><p>这里没有二维码</p></body>
"""


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {name}: got={got!r} want={want!r}")
    if not ok:
        FAILED.append(name)


def test_png_size():
    print("\n--- PNG 尺寸解析 ---")
    tiny = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000d2000000d2080600000001e0d6e8000000"
        "1b49444154789c63fcffff3f0305c30a1416c606c20a0b1b0a000000ffff0300"
        "36d6a5f6"
    )
    size = payment._png_size(tiny)
    check("能读出 210x210", size, (210, 210))
    check("非 PNG 返回 None", payment._png_size(b"not a png"), None)
    check("210x210 算二维码", payment.looks_like_qr(tiny), True)
    check("过小不算", payment.looks_like_qr(tiny, min_side=400), False)


def _browser():
    from playwright.sync_api import sync_playwright

    return sync_playwright()


def test_canvas_and_img():
    print("\n--- 真实浏览器：canvas / img / 无二维码 ---")
    with _browser() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 600, "height": 600})
        try:
            with tempfile.TemporaryDirectory() as tmp:
                page.set_content(FAKE_QR_PAGE)
                saved = payment.capture_qr(page, tmp, tag="canvas")
                check("canvas 提取成功", len(saved) >= 1, True)
                if saved:
                    data = Path(saved[0]).read_bytes()
                    size = payment._png_size(data)
                    check("canvas 尺寸 210x210", size, (210, 210))

                page.set_content(IMG_QR_PAGE)
                saved = payment.capture_qr(page, tmp, tag="img")
                check("img 提取成功", len(saved) >= 1, True)

                page.set_content(EMPTY_PAGE)
                saved = payment.capture_qr(page, tmp, tag="empty")
                check("无二维码时兜底整页截图", len(saved), 1)
                check("兜底文件名含 fullpage", "fullpage" in Path(saved[0]).name, True)
        finally:
            browser.close()


def test_payment_page_detect():
    print("\n--- 支付页判定 ---")
    with _browser() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        try:
            page.set_content(EMPTY_PAGE)
            check("含「收银台」判为支付页", payment.is_payment_page(page), True)

            page.set_content("<body>活动页面</body>")
            check("普通页面不算支付页", payment.is_payment_page(page), False)

            # 不依赖真网络：把 pay 域名的请求就地拦下，只验证 URL 判定
            page.route(
                "https://pay.cloud.tencent.com/**",
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body="<html><body>收银台</body></html>",
                ),
            )
            page.goto("https://pay.cloud.tencent.com/order/123")
            check("pay 域名算支付页", payment.is_payment_page(page), True)
        finally:
            browser.close()


def test_resolve_dir():
    print("\n--- 目录解析 ---")
    abs_dir = payment.resolve_dir(r"D:\tmp\qr")
    check("绝对路径原样返回", str(abs_dir), r"D:\tmp\qr")
    rel_dir = payment.resolve_dir("qrcodes")
    check("相对路径拼到程序目录", rel_dir.name, "qrcodes")
    check("相对路径是绝对路径", rel_dir.is_absolute(), True)



class FakeEl:
    def __init__(self, visible=True):
        self.visible = visible
        self.clicked = 0

    def is_visible(self, timeout=None):
        return self.visible

    def click(self, **kwargs):
        self.clicked += 1


class FakeLocator:
    def __init__(self, els):
        self._els = els

    def all(self):
        return self._els


class FakePage:
    """收银台桩：协议元素 + 「下一步」按钮，点完会「新开」一个标签页。"""

    def __init__(self, context, agree_els=None, has_next=True):
        self.context = context
        self.has_next = has_next
        self.agree_els = agree_els or []
        self.evaluate_calls = []

    def locator(self, selector):
        return FakeLocator(self.agree_els)

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, js, arg=None):
        self.evaluate_calls.append(arg)
        if arg in ("下一步", "确认支付", "去支付"):
            if not self.has_next:
                return "missing"
            self.context.pages.append(FakePage(self.context))
            return "clicked"
        return None


class FakeContext:
    def __init__(self, *pages):
        self.pages = list(pages)


def test_advance_to_qr():
    print("\n--- 收银台推进（勾协议 + 点下一步 + 新标签页）---")
    ctx = FakeContext()
    main_page = FakePage(ctx)
    ctx.pages.append(main_page)
    agree = FakeEl()
    main_page.agree_els = [agree]

    pages = payment.advance_to_qr(main_page, ctx, {"wait_seconds": 2})

    check("协议元素被点击", agree.clicked >= 1, True)
    check("点击了「下一步」", "下一步" in main_page.evaluate_calls, True)
    check("返回包含新页面", len(pages) >= 2, True)
    check("新页面排在前面", pages[0] is not main_page, True)

    # 没有「下一步」按钮时，不应崩，且返回原有页面
    ctx2 = FakeContext()
    page2 = FakePage(ctx2, has_next=False)
    ctx2.pages.append(page2)
    pages2 = payment.advance_to_qr(page2, ctx2, {"wait_seconds": 1})
    check("无按钮时返回原页面", len(pages2), 1)


def main():
    test_png_size()
    test_canvas_and_img()
    test_payment_page_detect()
    test_resolve_dir()
    test_advance_to_qr()

    if FAILED:
        print(f"\n失败 {len(FAILED)} 项: {FAILED}")
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
