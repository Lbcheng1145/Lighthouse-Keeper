"""
日志系统 - 支持控制台和文件双输出
"""
import logging
import sys
import time
from pathlib import Path


def _app_dir():
    """返回可写的工作目录。

    - 源码运行: 脚本所在目录
    - PyInstaller 单文件/单目录运行: EXE 所在目录（而非临时解包目录 _MEIPASS）
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


class Logger:
    """统一日志管理器"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self._setup_logger()
    
    def _open_log_file(self):
        """获取可用的日志文件，尽量不因 IO 问题中断程序。

        常见冲突场景：多个实例同时启动、上次进程未完全退出、
        杀软短暂锁定目录。宁可换名或退化为仅控制台输出，
        也不要让日志系统把整个抢购流程带崩。
        """
        log_dir = _app_dir() / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"[日志] 无法创建日志目录 {log_dir}: {exc}")
            return None, None

        stamp = time.strftime("%Y%m%d_%H%M%S")
        # 主文件名若被占用，依次尝试带序号的文件名
        for suffix in [""] + [f"_{i}" for i in range(1, 6)]:
            candidate = log_dir / f"purchase_{stamp}{suffix}.log"
            try:
                handler = logging.FileHandler(candidate, encoding="utf-8")
            except OSError:
                continue
            return candidate, handler

        print(f"[日志] 无法创建日志文件（目录可能被占用）: {log_dir}")
        return None, None

    def _setup_logger(self):
        """配置日志系统"""
        # 创建logs目录
        log_dir = _app_dir() / "logs"

        # 日志文件名：logs/purchase_20260204_163045.log
        log_file, file_handler = self._open_log_file()
        
        # 配置日志格式
        log_format = '%(asctime)s [%(levelname)-7s] %(message)s'
        date_format = '%H:%M:%S'
        
        # 创建logger
        self.logger = logging.getLogger('TencentCloudBot')
        self.logger.setLevel(logging.DEBUG)
        
        # 清除已有的处理器
        self.logger.handlers.clear()
        
        # 文件处理器（记录所有级别）—— 打不开就退化为仅控制台
        if file_handler is not None:
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(
                logging.Formatter(log_format, date_format)
            )
            self.logger.addHandler(file_handler)
        
        # 控制台处理器（只显示INFO及以上）
        # 显式写到 stdout：main.py 已把 stdout 包装成 UTF-8，
        # 而 Windows 控制台默认 stderr 是 GBK，会让 ✓ / ⏳ 等符号抛
        # UnicodeEncodeError 并被 logging 静默吞掉。
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(
            logging.Formatter(log_format, date_format)
        )
        self.logger.addHandler(console_handler)
        
        # 记录日志文件位置（可能为 None：仅控制台模式）
        self.log_file = log_file
        if log_file is not None:
            self.logger.info(f"日志文件: {log_file}")
        else:
            self.logger.warning("未能创建日志文件，本次仅输出到控制台")
        self.logger.info("="*60)
    
    def debug(self, msg):
        """调试信息（仅记录到文件）"""
        self.logger.debug(msg)
    
    def info(self, msg):
        """一般信息"""
        self.logger.info(msg)
    
    def success(self, msg):
        """成功信息"""
        self.logger.info(f"✓ {msg}")
    
    def warning(self, msg):
        """警告信息"""
        self.logger.warning(msg)
    
    def error(self, msg):
        """错误信息"""
        self.logger.error(msg)
    
    def progress(self, msg):
        """进度信息"""
        self.logger.info(f"⏳ {msg}")


# 全局logger实例
_logger = Logger()


def log(msg, level="INFO"):
    """
    统一日志接口
    
    Args:
        msg: 日志消息
        level: 日志级别 (DEBUG/INFO/SUCCESS/WARNING/ERROR/PROGRESS)
    """
    level = level.upper()
    
    if level == "DEBUG":
        _logger.debug(msg)
    elif level == "SUCCESS":
        _logger.success(msg)
    elif level == "WARNING":
        _logger.warning(msg)
    elif level == "ERROR":
        _logger.error(msg)
    elif level == "PROGRESS":
        _logger.progress(msg)
    else:  # INFO
        _logger.info(msg)


def get_log_file():
    """获取当前日志文件路径"""
    return _logger.log_file
