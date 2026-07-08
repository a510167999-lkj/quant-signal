"""统一日志配置。

入口（app.main / app.jobs）启动时调用 ``configure_logging()``；各业务模块用
``logging.getLogger(__name__)`` 取自己的 logger 即可，无需关心 handler。

默认输出到 stderr，被 systemd journald 自动收集；设置 ``LOG_PATH`` 可额外落
文件，便于 VPS 上事后排查。``LOG_LEVEL`` 控制级别（默认 INFO）。
"""
import logging
import os
import sys
from pathlib import Path


_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging() -> logging.Logger:
    """配置 root logger。幂等——重复调用不会堆积 handler。"""
    level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(level)

    formatter = logging.Formatter(_LOG_FORMAT)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    root.addHandler(stderr_handler)

    log_path = os.getenv("LOG_PATH", "").strip()
    if log_path:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    return root
