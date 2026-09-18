from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
STYLES = (ROOT / "app" / "static" / "styles.css").read_text(encoding="utf-8")


def test_blur_settings_are_outside_the_primary_form_flow():
    assert 'id="settings-toggle"' in TEMPLATE
    assert 'id="settings-drawer"' in TEMPLATE
    assert 'aria-controls="settings-drawer"' in TEMPLATE
    assert 'id="settings-drawer" hidden' in TEMPLATE
    assert 'id="job-form"' in TEMPLATE
    assert TEMPLATE.index('id="settings-drawer"') < TEMPLATE.index('<form id="job-form">')


def test_settings_drawer_has_open_close_behavior():
    assert "settings-toggle" in SCRIPT
    assert "settings-drawer" in SCRIPT
    assert "settings-close" in SCRIPT
    assert "drawer-open" in SCRIPT
    assert 'id="mask-scale"' in TEMPLATE
    assert 'value="1"' in TEMPLATE


def test_two_stage_video_flow_has_preview_and_generation_step():
    assert 'id="preview-step"' in TEMPLATE
    assert 'id="defaced-video"' in TEMPLATE
    assert 'id="next-step"' in TEMPLATE
    assert 'id="generate-step"' in TEMPLATE
    assert 'id="generate-form"' in TEMPLATE
    assert 'defaced_url' in SCRIPT
    assert '/generate' in SCRIPT
    assert 'generationStepActive' in SCRIPT
    assert 'function formatError' in SCRIPT


def test_two_stage_api_routes_are_declared():
    from app.main import app

    routes = {(route.path, method) for route in app.routes for method in (getattr(route, "methods", None) or set())}
    assert ("/api/jobs/{job_id}/defaced", "GET") in routes
    assert ("/api/jobs/{job_id}/generate", "POST") in routes


def test_settings_drawer_is_fixed_to_the_right_and_mobile_safe():
    assert ".settings-drawer" in STYLES
    assert "position: fixed" in STYLES
    assert "right: 0" in STYLES
    assert ".settings-drawer.drawer-open" in STYLES
    assert "@media (max-width: 680px)" in STYLES
