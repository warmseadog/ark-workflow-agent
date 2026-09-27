from pathlib import Path


SCRIPT = (Path(__file__).resolve().parents[1] / "app" / "static" / "production.js").read_text(encoding="utf-8")


def test_submission_error_clears_progress_instead_of_showing_zero_percent():
    assert "document.getElementById('generation-percent').textContent = '—'" in SCRIPT
    assert "document.getElementById('generation-bar').style.width = '0%'" in SCRIPT
