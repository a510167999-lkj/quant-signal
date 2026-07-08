import logging

from app.logging_setup import configure_logging


def test_configure_logging_is_idempotent(monkeypatch):
    """重复调用不能堆积 handler——uvicorn reload 和 jobs 每次运行都会重复触发。"""
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    root = configure_logging()
    first_count = len(root.handlers)
    assert root.level == logging.DEBUG

    configure_logging()
    configure_logging()

    assert len(root.handlers) == first_count


def test_configure_logging_writes_file_when_path_set(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "logs" / "app.log"))
    root = configure_logging()
    assert any(isinstance(h, logging.FileHandler) for h in root.handlers)

    # 还原全局状态，避免 file handler 残留到其他测试。
    monkeypatch.delenv("LOG_PATH", raising=False)
    configure_logging()
