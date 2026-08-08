from __future__ import annotations

from pathlib import Path

from app import config


def test_formal_runtime_can_disable_dotenv_loading(
    monkeypatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text(
        "SYNTHETIC_FORMAL_DOTENV_MARKER=must-not-load\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOAD_ENV_IN_TESTS", "1")
    monkeypatch.setenv("DISABLE_ENV_FILE", "1")
    monkeypatch.delenv("SYNTHETIC_FORMAL_DOTENV_MARKER", raising=False)

    config._load_env_file()

    assert "SYNTHETIC_FORMAL_DOTENV_MARKER" not in config.os.environ
