from pathlib import Path


def test_frontend_does_not_render_untrusted_html():
    script = Path("app/static/app.js").read_text(encoding="utf-8")

    assert "innerHTML" not in script
    assert "textContent" in script


def test_frontend_shows_selection_funnel_explanation_for_empty_results():
    script = Path("app/static/app.js").read_text(encoding="utf-8")

    assert "selection_funnel?.explanation" in script
