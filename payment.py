"""支付页识别与二维码提取。

为什么不用真无头：实测（2026-10）活动页对 headless 有反爬，返回的是约 1 KB
的挑战页、0 张商品卡；改成「有头但窗口移到屏幕外」则完全正常。所以
「不弹浏览器」靠离屏窗口实现，渲染仍是真实浏览器。

二维码提取走多策略，按可靠性依次尝试：
  1. <canvas> 直接 toDataURL —— 最干净，拿到的是原始像素
  2. <img> 元素截图 —— 覆盖后端返回图片的情况
  3. 整页截图 —— 兜底，人工也能从里面找到二维码

不引入 opencv/pyzbar 这类重依赖：只用 PNG 头解析尺寸，做「像不像二维码」
的基本校验（近似正方形、够大、不是纯白）。
"""
import base64
import struct
import time
from pathlib import Path

from logger import log

# 支付页特征词（命中任一即认为进了收银台）
PAYMENT_HINTS = (
    "收银台", "确认支付", "立即支付", "扫码支付", "微信支付", "支付宝",
    "订单支付", "支付订单", "支付金额",
)

# 二维码元素候选选择器：从「明确是二维码」到「可能承载二维码」
QR_SELECTORS = (
    'canvas[class*="qr"]',
    'canvas[id*="qr"]',
    'canvas[class*="code"]',
    '[class*="qrcode"] canvas',
    '[class*="qr-code"] canvas',
    '[class*="qrCode"] canvas',
    'img[src*="qrcode"]',
    'img[src*="qr_code"]',
    'img[alt*="二维码"]',
    '[class*="qrcode"] img',
    '[class*="qr-code"]',
    '[class*="qrcode"]',
)


def resolve_dir(path_value, default="qrcodes") -> Path:
    """把配置里的目录解析成绝对路径（相对路径按 EXE 所在目录算）。"""
    p = Path(path_value or default)
    if p.is_absolute():
        return p
    try:
        from config import _app_dir

        return _app_dir() / p
    except Exception:
        return Path.cwd() / p


def _png_size(data: bytes):
    """从 PNG 头里读宽高，非法则返回 None。"""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    if data[12:16] != b"IHDR":
        return None
    try:
        width, height = struct.unpack(">II", data[16:24])
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def looks_like_qr(data: bytes, min_side: int = 80) -> bool:
    """粗略判断这段 PNG 是否像二维码：够大的近似正方形。"""
    size = _png_size(data)
    if not size:
        return False
    width, height = size
    if width < min_side or height < min_side:
        return False
    ratio = width / height
    return 0.75 <= ratio <= 1.33


def is_payment_page(page) -> bool:
    """当前页面是不是支付/收银台。"""
    try:
        url = (page.url or "").lower()
    except Exception:
        return False
    if any(k in url for k in ("pay.", "/pay", "cashier", "checkout")):
        return True
    try:
        body = page.inner_text("body", timeout=2000) or ""
    except Exception:
        body = ""
    return any(hint in body for hint in PAYMENT_HINTS)


def find_payment_page(context, timeout_ms=15000):
    """在所有标签页里找支付页。

    提交订单后腾讯云可能**新开标签页**打开收银台，也可能原地跳转，
    两种都要覆盖，所以遍历 context.pages 而不是只看当前页。
    返回 (page_or_None, waited_ms)。
    """
    started = time.time()
    while (time.time() - started) * 1000 < timeout_ms:
        for page in getattr(context, "pages", []) or []:
            try:
                if is_payment_page(page):
                    return page, int((time.time() - started) * 1000)
            except Exception:
                continue
        time.sleep(0.5)
    return None, int((time.time() - started) * 1000)


def _canvas_to_png(element) -> bytes | None:
    """把 canvas 的像素取出来。"""
    try:
        data_url = element.evaluate(
            "c => { try { return c.toDataURL('image/png'); } catch (e) { return ''; } }"
        )
    except Exception:
        return None
    if not data_url or "," not in data_url:
        return None
    try:
        return base64.b64decode(data_url.split(",", 1)[1])
    except Exception:
        return None


def _element_png(element) -> bytes | None:
    try:
        return element.screenshot(type="png")
    except Exception:
        return None


def _prune(folder: Path, keep: int = 10) -> None:
    """只保留最近的几个二维码文件，避免目录越积越多。"""
    try:
        files = sorted(folder.glob("*.png"), key=lambda f: f.stat().st_mtime, reverse=True)
        for old in files[max(0, keep):]:
            old.unlink(missing_ok=True)
    except Exception:
        pass


def capture_qr(page, out_dir, tag: str = "pay", keep: int = 10) -> list[str]:
    """从页面里提取二维码，保存成 PNG，返回成功保存的路径列表。

    参数:
        page: 支付页
        out_dir: 保存目录（不存在会自动创建）
        tag: 文件名前缀，便于区分支付/登录等场景
    """
    folder = resolve_dir(out_dir)
    folder.mkdir(parents=True, exist_ok=True)

    saved: list[str] = []
    stamp = time.strftime("%Y%m%d_%H%M%S")

    _prune(folder, keep)

    def save(data: bytes, kind: str) -> None:
        path = folder / f"{tag}_{stamp}_{kind}.png"
        path.write_bytes(data)
        saved.append(str(path))
        log(f"二维码已保存（{kind}）: {path}", "SUCCESS")

    # 策略 1+2：逐个候选元素尝试 canvas / 元素截图
    # 遍历所有 frame：收银台/第三方支付页可能把二维码放在 iframe 里
    scopes = [page]
    try:
        scopes += [f for f in page.frames if f != page.main_frame]
    except Exception:
        pass

    for scope in scopes:
        for selector in QR_SELECTORS:
            try:
                elements = scope.locator(selector).all()
            except Exception:
                continue
            for element in elements[:3]:
                try:
                    if not element.is_visible(timeout=300):
                        continue
                except Exception:
                    continue

                tag_name = ""
                try:
                    tag_name = element.evaluate("el => el.tagName.toLowerCase()")
                except Exception:
                    pass

                if tag_name == "canvas":
                    data = _canvas_to_png(element)
                    if data and looks_like_qr(data):
                        save(data, "canvas")
                        return saved

                data = _element_png(element)
                if data and looks_like_qr(data):
                    save(data, "element")
                    return saved

    # 策略 3：整页截图兜底（人工也能从截图里扫）
    try:
        shot = page.screenshot(type="png", full_page=True)
        if shot and len(shot) > 5000:
            save(shot, "fullpage")
            log("未定位到二维码元素，已保存整页截图供手动扫码", "WARNING")
    except Exception as exc:
        log(f"整页截图失败: {str(exc)[:120]}", "WARNING")

    return saved


def advance_to_qr(page, context, cfg=None, wait_seconds: int = 20):
    """在收银台里推进到「出二维码」的那一步。

    实测（2026-10 腾讯云收银台）首页只有「余额支付 / 在线支付」两个选项，
    没有二维码；必须先勾选协议（否则「下一步」是禁用态），再点「下一步」，
    站点才会跳到第三方支付页（可能新开标签页），二维码在那里。

    返回：候选页面列表（当前页 + 期间新开的标签页）。
    """
    cfg = cfg or {}
    agreement_labels = cfg.get(
        "agreement_labels", ["label.tea-form-check", "[class*=checkbox]"]
    )
    next_texts = cfg.get("next_button_texts", ["下一步", "确认支付", "去支付"])
    timeout_ms = int(cfg.get("wait_seconds", wait_seconds)) * 1000

    pages_before = list(getattr(context, "pages", []) or [])

    # 1) 勾选协议（点 label 而不是 input：Vue 接管了受控 input，点 input 无效）
    for selector in agreement_labels:
        try:
            for element in page.locator(selector).all():
                try:
                    if element.is_visible(timeout=300):
                        element.click(force=True)
                except Exception:
                    continue
        except Exception:
            continue
    page.wait_for_timeout(800)

    # 2) 点「下一步」——用 JS 直接 click，避免被禁用态/遮罩挡住
    clicked = None
    for text in next_texts:
        try:
            done = page.evaluate(
                """(label) => {
                    const b = [...document.querySelectorAll('button')]
                        .find(x => (x.innerText || '').trim() === label);
                    if (!b) return 'missing';
                    const disabled = b.disabled === true
                        || String(b.className).includes('is-disabled');
                    b.click();
                    return disabled ? 'disabled' : 'clicked';
                }""",
                text,
            )
        except Exception:
            done = "error"
        log(f"收银台「{text}」: {done}", "DEBUG" if done == "missing" else "INFO")
        if done == "clicked":
            clicked = text
            break

    if clicked is None:
        log("收银台里没能点到「下一步」，二维码可能不会出现", "WARNING")
        return pages_before

    # 3) 等新标签页/新内容出现
    started = time.time()
    while (time.time() - started) * 1000 < timeout_ms:
        page.wait_for_timeout(1000)
        pages_now = list(getattr(context, "pages", []) or [])
        if len(pages_now) > len(pages_before):
            log(f"点「下一步」后新开了 {len(pages_now) - len(pages_before)} 个标签页", "INFO")
            break

    pages_now = list(getattr(context, "pages", []) or [])
    # 新页面排前面（二维码通常在新页面里）
    ordered = pages_now[len(pages_before):] + pages_now[: len(pages_before)]
    return ordered


def capture_from_all_pages(context, out_dir, tag: str = "pay") -> list[str]:
    """支付页可能在新标签页：在所有页面上各试一遍，取第一个成功的。"""
    pages = list(getattr(context, "pages", []) or [])
    # 支付页优先，其次是其它页面（第三方支付页的域名/文案往往不含关键词）
    ordered = [p for p in pages if _safe(is_payment_page, p)] + [
        p for p in pages if not _safe(is_payment_page, p)
    ]
    for i, page in enumerate(reversed(ordered)):
        saved = capture_qr(page, out_dir, tag=tag)
        if saved:
            return saved
        if i >= 3:
            break
    return []


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return False
