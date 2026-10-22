"""图形界面（tkinter）。

把命令行流程包成一个窗口：改配置 → 点「开始抢购」→ 浏览器自动跑流程，
日志实时显示在窗口里。配置会同时写进 exe 同目录的 config.local.py，
下次启动自动生效（和命令行模式共用同一份配置）。

设计上刻意保持"薄"：所有抢购逻辑仍由 main.TencentCloudBot 负责，
这里只做三件事 —— 传配置、起/停线程、把日志搬到窗口上。
"""
import logging
import os
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

import config
from config import VERSION
from logger import get_log_file


def _bot_class():
    """延迟导入抢购主流程。

    主流程要 import playwright（打包后还要从 PYZ 里解出来），而窗口本身
    只需要 tkinter。放到点「开始抢购」时再导入，窗口能早一点显示出来。
    """
    from main import TencentCloudBot

    return TencentCloudBot

# 日志级别 -> 窗口里文字标签的颜色
LEVEL_TAGS = {
    "DEBUG": "debug",
    "INFO": "info",
    "WARNING": "warn",
    "ERROR": "error",
    "CRITICAL": "error",
}


class QueueHandler(logging.Handler):
    """把 logging 记录塞进队列，由主线程轮询搬到界面上。

    tkinter 不是线程安全的，抢购线程只能间接通过队列更新界面。
    """

    def __init__(self, sink):
        super().__init__()
        self.sink = sink

    def emit(self, record):
        try:
            self.sink.put((record.levelname, record.getMessage()))
        except Exception:
            pass


def _app_dir() -> Path:
    """EXE 所在目录；源码运行时为脚本目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _split(text: str):
    """把 "38, 39" / "38 39" / "38、39" 拆成列表。"""
    for sep in ("、", "，", ";", "；"):
        text = text.replace(sep, ",")
    return [part.strip() for part in text.replace(" ", ",").split(",") if part.strip()]


class BotApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.bot = None
        self.worker = None
        self.queue = queue.Queue()

        root.title(f"腾讯云轻量服务器抢购 v{VERSION}")
        root.geometry("860x620")
        root.minsize(760, 520)

        self._build_ui()
        self._load_config_into_ui()

        # 日志 -> 队列
        logging.raiseExceptions = False
        self.handler = QueueHandler(self.queue)
        self.handler.setLevel(logging.INFO)
        logging.getLogger("TencentCloudBot").addHandler(self.handler)

        self.root.after(120, self._pump_logs)
        # 窗口画出来之后再预热主流程：打包后 import playwright 要几百毫秒，
        # 放到后台线程做，用户点「开始抢购」时就不用等了。
        self.root.after(300, self._preheat)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _preheat(self):
        """后台导入抢购主流程（只 import，不碰浏览器、不碰账号）。"""
        def work():
            try:
                _bot_class()
            except Exception:
                pass

        threading.Thread(target=work, daemon=True, name="preheat").start()

    # ---------------- 界面 ----------------
    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except Exception:
            pass

        head = ttk.Frame(self.root, padding=(12, 10, 12, 0))
        head.pack(fill="x")
        ttk.Label(
            head,
            text=f"腾讯云轻量服务器自动抢购  v{VERSION}",
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(side="left")
        self.status_var = tk.StringVar(value="空闲")
        ttk.Label(head, textvariable=self.status_var, foreground="#666").pack(side="right")

        cfg = ttk.LabelFrame(self.root, text=" 抢购配置 ", padding=10)
        cfg.pack(fill="x", padx=12, pady=(8, 6))
        cfg.columnconfigure(1, weight=1)
        cfg.columnconfigure(3, weight=1)

        self.mode_var = tk.StringVar(value="test")
        ttk.Label(cfg, text="模式").grid(row=0, column=0, sticky="w", pady=3)
        mode_box = ttk.Frame(cfg)
        mode_box.grid(row=0, column=1, columnspan=3, sticky="w")
        ttk.Radiobutton(
            mode_box, text="测试模式（立即抢，不等时间点）",
            variable=self.mode_var, value="test", command=self._on_mode_change,
        ).pack(side="left")
        ttk.Radiobutton(
            mode_box, text="正式模式（到点抢）",
            variable=self.mode_var, value="prod", command=self._on_mode_change,
        ).pack(side="left", padx=(16, 0))

        ttk.Label(cfg, text="目标规格").grid(row=1, column=0, sticky="w", pady=3)
        self.spec_var = tk.StringVar()
        ttk.Entry(cfg, textvariable=self.spec_var).grid(row=1, column=1, sticky="ew", padx=(0, 8))

        ttk.Label(cfg, text="带宽").grid(row=1, column=2, sticky="w")
        self.bandwidth_var = tk.StringVar()
        ttk.Entry(cfg, textvariable=self.bandwidth_var, width=8).grid(row=1, column=3, sticky="w")

        ttk.Label(cfg, text="测试价").grid(row=2, column=0, sticky="w", pady=3)
        self.test_price_var = tk.StringVar()
        ttk.Entry(cfg, textvariable=self.test_price_var, width=12).grid(row=2, column=1, sticky="w")

        ttk.Label(cfg, text="正式价").grid(row=2, column=2, sticky="w")
        self.prod_price_var = tk.StringVar()
        ttk.Entry(cfg, textvariable=self.prod_price_var, width=16).grid(row=2, column=3, sticky="w")

        ttk.Label(cfg, text="抢购时间").grid(row=3, column=0, sticky="w", pady=3)
        self.buy_times_var = tk.StringVar()
        ttk.Entry(cfg, textvariable=self.buy_times_var).grid(
            row=3, column=1, columnspan=3, sticky="ew"
        )

        ttk.Label(cfg, text="活动页地址").grid(row=4, column=0, sticky="w", pady=3)
        self.url_var = tk.StringVar()
        ttk.Entry(cfg, textvariable=self.url_var).grid(
            row=4, column=1, columnspan=3, sticky="ew"
        )

        self.retry_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            cfg,
            text="点击后看不到配置弹窗时允许重新定位再点一次（有重复下单风险，默认关闭）",
            variable=self.retry_var,
        ).grid(row=5, column=0, columnspan=4, sticky="w", pady=(6, 0))

        bar = ttk.Frame(self.root, padding=(12, 0))
        bar.pack(fill="x")
        self.start_btn = ttk.Button(bar, text="开始抢购", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(bar, text="停止", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(bar, text="保存配置", command=self.save_config).pack(side="left")
        ttk.Button(bar, text="打开活动页", command=self.open_page).pack(side="left", padx=6)
        ttk.Button(bar, text="打开日志目录", command=self.open_logs).pack(side="left")

        logf = ttk.LabelFrame(self.root, text=" 运行日志 ", padding=6)
        logf.pack(fill="both", expand=True, padx=12, pady=(8, 12))
        self.log_widget = scrolledtext.ScrolledText(
            logf, height=16, wrap="word", font=("Consolas", 9), state="disabled"
        )
        self.log_widget.pack(fill="both", expand=True)
        self.log_widget.tag_configure("info", foreground="#222")
        self.log_widget.tag_configure("debug", foreground="#888")
        self.log_widget.tag_configure("warn", foreground="#b26b00")
        self.log_widget.tag_configure("error", foreground="#c0392b")
        self.log_widget.tag_configure("success", foreground="#1e7d32")

    # ---------------- 配置读写 ----------------
    def _load_config_into_ui(self):
        target = config.TARGET_CONFIG
        run_mode = config.RUN_MODE
        self.mode_var.set("test" if run_mode.get("test_mode") else "prod")
        self.spec_var.set(target.get("text", "4核4G"))
        self.bandwidth_var.set(target.get("bandwidth", "3M"))
        self.test_price_var.set(target.get("test_price", "109"))
        self.prod_price_var.set(", ".join(target.get("prod_prices", ["38", "39"])))
        self.buy_times_var.set(", ".join(run_mode.get("buy_times", ["10:00:00", "15:00:00"])))
        self.url_var.set(config.PAGE_CONFIG.get("url", ""))
        self.retry_var.set(bool(run_mode.get("allow_retry_purchase", False)))
        self._on_mode_change()

    def _on_mode_change(self):
        """切换模式时补一个默认测试价（其余输入项两种模式共用）。"""
        if not self.test_price_var.get().strip():
            self.test_price_var.set("109")

    def _collect_config(self) -> dict:
        times = _split(self.buy_times_var.get()) or ["10:00:00"]
        for t in times:
            if len(t.split(":")) != 3:
                raise ValueError(f"时间格式应为 HH:MM:SS，收到：{t}")
        prices = _split(self.prod_price_var.get()) or ["38", "39"]
        return {
            "test_mode": self.mode_var.get() == "test",
            "spec": self.spec_var.get().strip(),
            "bandwidth": self.bandwidth_var.get().strip(),
            "test_price": self.test_price_var.get().strip(),
            "prod_prices": prices,
            "buy_times": times,
            "url": self.url_var.get().strip(),
            "allow_retry_purchase": bool(self.retry_var.get()),
        }

    def _apply_to_runtime(self, values: dict):
        """就地改 config 模块里的字典，正在用的 bot 下次运行即生效。"""
        config.TARGET_CONFIG["text"] = values["spec"]
        config.TARGET_CONFIG["bandwidth"] = values["bandwidth"]
        config.TARGET_CONFIG["test_price"] = values["test_price"]
        config.TARGET_CONFIG["prod_prices"] = values["prod_prices"]
        config.RUN_MODE["test_mode"] = values["test_mode"]
        config.RUN_MODE["buy_times"] = values["buy_times"]
        config.RUN_MODE["allow_retry_purchase"] = values["allow_retry_purchase"]
        if values["url"]:
            config.PAGE_CONFIG["url"] = values["url"]

    def _write_local_config(self, values: dict):
        """写 config.local.py（程序启动时会自动加载它）。"""
        target = dict(config.TARGET_CONFIG)
        target.update(
            text=values["spec"],
            bandwidth=values["bandwidth"],
            test_price=values["test_price"],
            prod_prices=values["prod_prices"],
        )
        run_mode = dict(config.RUN_MODE)
        run_mode.update(
            test_mode=values["test_mode"],
            buy_times=values["buy_times"],
            allow_retry_purchase=values["allow_retry_purchase"],
        )
        page = dict(config.PAGE_CONFIG)
        if values["url"]:
            page["url"] = values["url"]

        path = _app_dir() / "config.local.py"
        path.write_text(
            "# 由图形界面自动生成，每次点「保存配置」都会覆盖本文件。\n"
            "# 只改了这里列出的项，其余仍用程序内置默认值。\n"
            f"TARGET_CONFIG = {target!r}\n\n"
            f"RUN_MODE = {run_mode!r}\n\n"
            f"PAGE_CONFIG = {page!r}\n",
            encoding="utf-8",
        )
        return path

    def save_config(self):
        try:
            values = self._collect_config()
        except ValueError as exc:
            messagebox.showerror("配置有误", str(exc))
            return
        self._apply_to_runtime(values)
        path = self._write_local_config(values)
        self._append(f"配置已保存: {path}", "success")

    # ---------------- 运行控制 ----------------
    def start(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            values = self._collect_config()
        except ValueError as exc:
            messagebox.showerror("配置有误", str(exc))
            return

        self._apply_to_runtime(values)
        self._write_local_config(values)

        self.bot = _bot_class()()
        self.bot.config["run_mode"]["test_mode"] = values["test_mode"]
        self.bot._stop_event.clear()

        mode = "测试模式（109 元档）" if values["test_mode"] else "正式模式"
        self.status_var.set(f"运行中 · {mode}")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self._append(f"=== 开始运行 · {mode} ===", "success")

        def work():
            try:
                self.bot.run()
            except Exception as exc:  # 线程里的异常必须带回界面
                self.queue.put(("ERROR", f"运行异常: {exc}"))
            finally:
                self.bot.stop()  # 让 _keep_browser_open 退出
                self.queue.put(("__DONE__", ""))

        self.worker = threading.Thread(target=work, daemon=True, name="bot")
        self.worker.start()

    def stop(self):
        if not (self.worker and self.worker.is_alive()):
            self.status_var.set("空闲")
            return
        self.status_var.set("停止中…")
        self.stop_btn.config(state="disabled")
        self._append("已请求停止，等待当前步骤收尾…", "warn")
        if self.bot is not None:
            self.bot.stop()

    def open_page(self):
        url = self.url_var.get().strip() or config.PAGE_CONFIG["url"]
        webbrowser.open(url)

    def open_logs(self):
        log_path = get_log_file()
        folder = Path(log_path).parent if log_path else _app_dir() / "logs"
        if not folder.exists():
            folder.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(folder)  # noqa: S606
            else:
                webbrowser.open(folder.as_uri())
        except Exception as exc:
            messagebox.showinfo("日志目录", f"{folder}\n\n无法自动打开：{exc}")

    # ---------------- 日志搬砖 ----------------
    def _pump_logs(self):
        while True:
            try:
                level, msg = self.queue.get_nowait()
            except queue.Empty:
                break
            if level == "__DONE__":
                self.status_var.set("已停止")
                self.start_btn.config(state="normal")
                self.stop_btn.config(state="disabled")
                self._append("=== 运行结束 ===", "warn")
                continue
            if msg.startswith("✓"):
                tag = "success"
            else:
                tag = LEVEL_TAGS.get(level, "info")
            self._append(msg, tag)
        self.root.after(120, self._pump_logs)

    def _append(self, text: str, tag: str = "info"):
        self.log_widget.config(state="normal")
        self.log_widget.insert("end", text + "\n", tag)
        self.log_widget.see("end")
        self.log_widget.config(state="disabled")

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askokcancel("正在运行", "抢购仍在运行，确定要退出吗？"):
                return
            if self.bot is not None:
                self.bot.stop()
            self.worker.join(timeout=5)
        try:
            logging.getLogger("TencentCloudBot").removeHandler(self.handler)
        except Exception:
            pass
        self.root.destroy()


def run_gui() -> int:
    """启动窗口（失败时把原因打到 stderr，便于排查）。"""
    try:
        root = tk.Tk()
    except Exception as exc:
        print(f"无法创建窗口: {exc}", file=sys.stderr)
        return 1
    BotApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(run_gui())
