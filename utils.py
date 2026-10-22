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


# ==================== 批量 DOM 探测（JS 快路径） ====================
#
# 为什么需要这一层：
#   Playwright 的每一次 locator 查询都是一次「Python ↔ 浏览器」进程间往返，
#   而 `is_visible()` 在元素不存在时还要把 timeout 等满。抢购链路里有成百
#   上千次这样的查询（扫卡片、找按钮、等弹窗），累积起来就是几十秒的纯等待
#   —— 而抢购恰恰是「差 1 秒就丢单」的场景。
#
#   这里把「扫一遍 DOM、判断可见性、按文案筛选」压成一次 page.evaluate，
#   结果带回 Python 再判断（判据仍然是 Python 里的纯函数，便于回归测试）。
#   需要真正点击时，JS 只负责在目标元素上打一个标记属性，Python 再用
#   `locator('[data-lk-xxx="1"]')` 拿到句柄去点 —— 保持真实用户点击语义。
#
#   对不支持 evaluate 的对象（单元测试的桩、老接口）一律返回 UNSUPPORTED，
#   调用方自动回退到原来的 locator 写法，行为与优化前完全一致。

UNSUPPORTED = object()


def js_probe(target, script, arg=None):
    """在页面/元素上下文里执行一段 JS，失败或不支持时返回 UNSUPPORTED。

    始终带参数调用（arg 缺省为 {}）：这样「只接受一个参数」的桩对象会直接
    抛 TypeError 走回退路径，而不会把返回的假值误当成探测结果。
    """
    if target is None:
        return UNSUPPORTED
    runner = getattr(target, "evaluate", None)
    if runner is None:
        return UNSUPPORTED
    try:
        return runner(script, arg if arg is not None else {})
    except Exception:
        return UNSUPPORTED


# 可见性判据与 Playwright 的 is_visible 对齐：有非空盒子，且没有被
# display/visibility/opacity 隐藏。注意「在视口外」不算不可见 ——
# 商品卡片本来就可能还在屏幕下方。
_JS_PRELUDE = """
  const lkVisible = (el) => {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  };
  const lkText = (el) => (el.innerText || el.textContent || '');
"""

# 返回第一个「可见」的选择器原文；都不可见则返回 null。
# 支持 Playwright 的 `text=xxx` 伪选择器（按文本匹配最小元素）。
_JS_VISIBLE_ANY = (
    """
(arg) => {
"""
    + _JS_PRELUDE
    + """
  const sels = arg.sels || [];
  let all = null;
  for (const sel of sels) {
    try {
      if (sel.startsWith('text=')) {
        const needle = sel.slice(5);
        if (!all) all = document.querySelectorAll('body *');
        for (const el of all) {
          if (el.children.length === 0 && lkText(el).includes(needle) && lkVisible(el)) {
            return sel;
          }
        }
        continue;
      }
      const list = document.querySelectorAll(sel);
      for (const el of list) {
        if (lkVisible(el)) return sel;
      }
    } catch (e) { /* 选择器写法不合法就跳过，与原逐条 try/except 一致 */ }
  }
  return null;
}
"""
)


def js_visible_any(page, selectors):
    """一次往返判断「这些选择器里有没有可见的」，返回命中的选择器或 None。"""
    return js_probe(page, _JS_VISIBLE_ANY, {"sels": list(selectors)})


# 扫一个卡片选择器下的所有卡片，一次性取回 [可见?, 文本]。
_JS_COLLECT_CARDS = (
    """
(arg) => {
"""
    + _JS_PRELUDE
    + """
  const out = [];
  const feature = arg.feature || '';
  let list = [];
  try { list = document.querySelectorAll(arg.sel); } catch (e) { return null; }
  for (const el of list) {
    if (!lkVisible(el)) { out.push({visible: false, text: ''}); continue; }
    // 粗筛：先用不触发布局的 textContent 过一遍，只有含目标文字的卡才取
    // innerText。innerText 会强制同步布局，几十张卡逐个取实测要 300ms+。
    if (feature) {
      const raw = el.textContent || '';
      if (!raw.includes(feature)) { out.push({visible: true, text: '', skip: true}); continue; }
    }
    out.push({visible: true, text: lkText(el)});
  }
  return out;
}
"""
)


def js_collect_cards(page, selector, feature=""):
    """一次往返取回某选择器下所有卡片的可见性与文本。

    feature: 给了就先按它做粗筛 —— 不含该文字的卡片不取 innerText，
    只回一个 skip 标记。真实活动页几十上百张卡时，这一步能省掉
    大量强制布局（实测 78 张卡从 ~350ms 降到几十毫秒）。
    """
    return js_probe(page, _JS_COLLECT_CARDS, {"sel": selector, "feature": feature})


# 在某个元素内找「真正的购买按钮」：返回文案并打标记，找不到返回 null。
# 用 card.evaluate(fn, arg) 调用，fn 的第一个参数就是卡片元素本身。
_JS_FIND_BUY_BUTTON = (
    """
(el, arg) => {
"""
    + _JS_PRELUDE
    + """
  const root = el;
  const goods = arg.goods || [];
  const bad = arg.bad || [];
  const maxLen = arg.maxLen || 20;
  const sels = ['button', 'a[role="button"]', 'a', '[class*="btn"]', '[class*="button"]'];

  // 清掉上一轮留下的标记，避免同名属性越积越多
  try {
    root.querySelectorAll('[data-lk-btn]').forEach((b) => b.removeAttribute('data-lk-btn'));
  } catch (e) {}

  const seen = new Set();
  for (const sel of sels) {
    let list = [];
    try { list = root.querySelectorAll(sel); } catch (e) { continue; }
    for (const b of list) {
      if (seen.has(b)) continue;
      seen.add(b);
      if (!lkVisible(b)) continue;
      const text = (b.textContent || '').trim().replace(/\\s+/g, ' ');
      if (!text || text.length > maxLen) continue;
      if (bad.some((x) => text.includes(x))) continue;
      if (!goods.some((x) => text.includes(x))) continue;
      b.setAttribute('data-lk-btn', '1');
      return text;
    }
  }
  return null;
}
"""
)


def js_find_buy_button(card, goods, bad, max_len=20):
    """在卡片元素内找购买按钮：命中则给它打上 data-lk-btn 标记并返回文案。"""
    return js_probe(
        card,
        _JS_FIND_BUY_BUTTON,
        {"goods": list(goods), "bad": list(bad), "maxLen": max_len},
    )


# 找镜像选项：返回命中文案并打标记。rootSel 为空表示整页。
_JS_FIND_OPTION = (
    """
(arg) => {
"""
    + _JS_PRELUDE
    + """
  const root = (arg.rootSel && document.querySelector(arg.rootSel)) || document.body;
  const names = arg.names || [];
  const attr = arg.attr || 'data-lk-opt';
  try {
    document.querySelectorAll('[' + attr + ']').forEach((el) => el.removeAttribute(attr));
  } catch (e) {}

  let all = [];
  try { all = root.querySelectorAll('*'); } catch (e) { return null; }

  for (const name of names) {
    for (const el of all) {
      if (!lkVisible(el)) continue;
      const text = lkText(el).trim();
      if (!text || text.length > 40) continue;
      if (!text.includes(name)) continue;
      // 命中最贴近文本的元素；再往上找一层可点的 单选/标签容器
      let target = el;
      for (let i = 0; i < 3 && target.parentElement && target.parentElement !== root; i++) {
        const tag = target.tagName;
        const role = target.getAttribute ? target.getAttribute('role') : null;
        if (tag === 'LABEL' || tag === 'BUTTON' || tag === 'A' || role === 'radio') break;
        target = target.parentElement;
      }
      target.setAttribute(attr, '1');
      return name;
    }
  }
  return null;
}
"""
)


def js_find_option(page, names, root_selector="", attr="data-lk-opt"):
    """按文案找一个选项（弹窗优先，退到整页），命中则打标记并返回名称。"""
    return js_probe(
        page,
        _JS_FIND_OPTION,
        {"rootSel": root_selector, "names": list(names), "attr": attr},
    )


# 在页面里「边滚边等」某个模块出现：一次往返完成整段等待。
_JS_SCROLL_UNTIL_READY = """
async (arg) => {
""" + _JS_PRELUDE + """
  const sel = arg.sel || null;
  const texts = arg.texts || [];
  const maxSteps = arg.maxSteps || 40;
  const stepMs = arg.stepMs || 150;
  const ratio = arg.ratio || 0.85;

  const ready = () => {
    try { if (sel && document.querySelector(sel)) return true; } catch (e) {}
    if (!texts.length) return false;
    // 先用整页纯文本做一次字符串快筛：不命中就直接返回，
    // 省掉「遍历上千个元素 + 逐个 getBoundingClientRect」的强制布局开销。
    const bodyText = document.body ? (document.body.textContent || '') : '';
    if (!texts.some((t) => bodyText.includes(t))) return false;
    let all = [];
    try { all = document.querySelectorAll('body *'); } catch (e) { return false; }
    for (const el of all) {
      if (el.children.length > 3) continue;
      const text = (el.textContent || '');
      if (!text || text.length > 60) continue;
      for (const needle of texts) {
        if (text.includes(needle) && lkVisible(el)) return true;
      }
    }
    return false;
  };

  try { window.scrollTo(0, 0); } catch (e) {}
  await new Promise((r) => setTimeout(r, 30));
  if (ready()) return {found: true, steps: 0};

  for (let i = 1; i <= maxSteps; i++) {
    const before = window.scrollY;
    window.scrollBy(0, Math.max(240, window.innerHeight * ratio));
    await new Promise((r) => setTimeout(r, stepMs));
    if (ready()) return {found: true, steps: i};
    const atBottom =
      window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 80;
    if (atBottom && window.scrollY === before) return {found: false, steps: i};
  }
  return {found: false, steps: maxSteps};
}
"""


def js_scroll_until_ready(page, selector, texts, max_steps=40, step_ms=150, ratio=0.85):
    """一次往返完成「回到顶部 → 逐屏下滚 → 检测目标模块是否渲染」。"""
    return js_probe(
        page,
        _JS_SCROLL_UNTIL_READY,
        {
            "sel": selector,
            "texts": list(texts),
            "maxSteps": max_steps,
            "stepMs": step_ms,
            "ratio": ratio,
        },
    )


# 在 scope 内等一个「可点击的提交按钮」，等到就标记并返回文案。
# 轮询放在浏览器里做：整段 10 秒等待只需要一次往返。
_JS_WAIT_ENABLED_BUTTON = """
async (arg) => {
""" + _JS_PRELUDE + """
  const scope = (arg.scope === 'dialog')
    ? (document.querySelector('.uno3-dialog') || document.body)
    : document.body;
  const texts = arg.texts || [];
  const waitMs = arg.waitMs || 0;
  const intervalMs = arg.intervalMs || 120;
  const deadline = Date.now() + waitMs;

  const disabled = (el) => {
    if (el.disabled) return true;
    if (el.getAttribute && el.getAttribute('aria-disabled') === 'true') return true;
    const cls = (el.className || '').toString().toLowerCase();
    return /(^|\\s|_|-)(is-)?disabled($|\\s|_|-)/.test(cls);
  };

  const pick = () => {
    const sels = ['button', '.uno3-dialog-footer button', 'a[role="button"]', '[class*="btn"]'];
    // 外层按文案优先级遍历：先认「立即购买」，最后才是「确定」，
    // 与 Python 版 _find_submit_button 的优先级一致，避免误点「确定」。
    for (const text of texts) {
      const seen = new Set();
      for (const sel of sels) {
        let list = [];
        try { list = scope.querySelectorAll(sel); } catch (e) { continue; }
        for (const el of list) {
          if (seen.has(el)) continue;
          seen.add(el);
          if (!lkVisible(el)) continue;
          if (disabled(el)) continue;
          const label = (el.textContent || '').trim().replace(/\\s+/g, ' ');
          if (!label || label.length > 20) continue;
          if (!label.includes(text)) continue;
          return el;
        }
      }
    }
    return null;
  };

  try {
    document.querySelectorAll('[data-lk-submit]')
      .forEach((el) => el.removeAttribute('data-lk-submit'));
  } catch (e) {}

  for (;;) {
    const el = pick();
    if (el) {
      el.setAttribute('data-lk-submit', '1');
      return (el.textContent || '').trim().replace(/\\s+/g, ' ');
    }
    if (Date.now() >= deadline) return null;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
"""


def js_wait_enabled_button(page, texts, scope="dialog", wait_ms=0, interval_ms=120):
    """等一个可用的提交按钮（等待在浏览器内完成），返回文案或 None。"""
    return js_probe(
        page,
        _JS_WAIT_ENABLED_BUTTON,
        {
            "scope": scope,
            "texts": list(texts),
            "waitMs": max(0, int(wait_ms)),
            "intervalMs": interval_ms,
        },
    )


def js_any_visible_fast(page, selector):
    """单个选择器是否可见（一次往返）。"""
    result = js_visible_any(page, [selector])
    if result is UNSUPPORTED:
        return UNSUPPORTED
    return result is not None


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
