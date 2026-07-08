from pathlib import Path


def test_frontend_does_not_render_untrusted_html():
    script = Path("app/static/app.js").read_text(encoding="utf-8")

    assert "innerHTML" not in script
    assert "textContent" in script
