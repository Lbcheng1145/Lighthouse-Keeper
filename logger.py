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
    
    def _setup_logger(self):
        """配置日志系统"""
        # 创建logs目录
        log_dir = _app_dir() / "logs"
        log_dir.mkdir(exist_ok=True)
        
        # 日志文件名：logs/purchase_20260204_163045.log
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        log_file = log_dir / f"purchase_{timestamp}.log"
        
        # 配置日志格式
        log_format = '%(asctime)s [%(levelname)-7s] %(message)s'
        date_format = '%H:%M:%S'
        
        # 创建logger
        self.logger = logging.getLogger('TencentCloudBot')
        self.logger.setLevel(logging.DEBUG)
        
        # 清除已有的处理器
        self.logger.handlers.clear()
        
        # 文件处理器（记录所有级别）
        file_handler = logging.FileHandler(
            log_file, 
            encoding='utf-8'
        )
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
        
        # 记录日志文件位置
        self.log_file = log_file
        self.logger.info(f"日志文件: {log_file}")
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
