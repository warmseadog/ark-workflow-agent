from pathlib import Path
from html.parser import HTMLParser


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
    assert 'value="1.4"' in TEMPLATE


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


def test_hair_aware_mask_controls_are_exposed():
    assert 'name="mask_mode"' in TEMPLATE
    assert 'value="face_hair_primary"' in TEMPLATE
    assert 'value="hair_primary"' in TEMPLATE
    assert 'value="face_hair_all"' in TEMPLATE
    assert 'id="hair-mode-note"' in TEMPLATE
    assert 'name="robust_tracking"' in TEMPLATE
    assert 'maskMode' in SCRIPT
    assert 'hair-mode-note' in SCRIPT


def test_jobs_route_accepts_hair_aware_options():
    import inspect
    from app.main import create_job

    parameters = inspect.signature(create_job).parameters
    assert 'mask_mode' in parameters
    assert 'robust_tracking' in parameters


def test_v2_installation_documents_hair_models():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "mediapipe" in requirements
    assert "opencv-contrib-python" in requirements
    assert "头发" in readme
    assert "face_hair_primary" in readme
    assert "selfie_multiclass_256x256.tflite" in readme


def test_task_settings_are_persisted_in_browser_storage():
    assert 'face-mosaic-settings-v2' in SCRIPT
    assert 'localStorage' in SCRIPT
    assert 'saveSettings' in SCRIPT
    assert 'loadSettings' in SCRIPT


def test_standalone_production_exposes_core_creation_flow():
    from fastapi.testclient import TestClient
    from app.main import app
    page = TestClient(app).get('/v1').text
    assert 'id="create-view"' in page
    assert 'id="studio-job-form"' in page
    assert 'id="studio-generate-form"' in page
    assert 'id="studio-defaced-video"' in page
    assert 'data-view=' not in page
    assert 'id="discover-view"' not in page
    assert 'id="cases-view"' not in page
    assert '/static/studio.js' not in page
    assert '/static/studio.css' not in page


def test_all_entrypoints_open_standalone_production():
    from fastapi.testclient import TestClient
    from app.main import app

    class BodyParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.classes = set()

        def handle_starttag(self, tag, attrs):
            if tag == 'body':
                self.classes.update((dict(attrs).get('class') or '').split())

    client = TestClient(app)
    for path in ('/', '/v1', '/studio'):
        response = client.get(path)
        assert response.status_code == 200
        assert '<title>方舟 · 制作流程</title>' in response.text
        body = BodyParser()
        body.feed(response.text)
        assert {'standalone-production', 'production-view'} <= body.classes
        assert '素材发现' not in response.text
        assert '成功案例' not in response.text


def test_production_flow_has_three_material_inputs_and_optional_prompt():
    from fastapi.testclient import TestClient
    from app.main import app
    studio = TestClient(app).get('/v1').text
    assert studio.count('class="asset-entry"') == 3
    assert 'name="video" form="studio-job-form"' in studio
    assert 'name="clothing_image"' in studio
    assert 'name="face_image"' in studio
    assert '<details class="compact-prompt"' in studio
    assert 'id="studio-job-submit"' not in studio
    assert 'id="studio-reference-submit"' not in studio


def test_production_flow_has_inline_generation_and_source_preview():
    from fastapi.testclient import TestClient
    from app.main import app
    studio = TestClient(app).get('/v1').text
    assert 'id="redaction-settings"' not in studio
    assert 'id="model-settings-toggle"' not in studio
    assert studio.index('class="generation-action"') < studio.index('id="model-settings"') < studio.index('id="studio-generate-submit"')
    assert studio.index('id="flow-stage-source"') < studio.index('id="studio-preview-submit"') < studio.index('id="flow-stage-references"')
    assert 'id="studio-redaction-confirm"' not in studio
