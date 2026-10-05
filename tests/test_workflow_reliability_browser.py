"""Exercise recovery controls in the shipped legacy workflow page without a server."""
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from tests.test_account_frontend import browser


@pytest.mark.parametrize("uncertain,status", [(False, "failed"), (True, "failed"), (True, "restore_held")])
def test_restored_failed_generation_offers_only_safe_next_action(browser, uncertain, status):
    root = Path(__file__).resolve().parents[1] / "app"
    task = {"id": "generation-1", "stage": "generation", "status": status, "output": {},
            "error": {"message": "提交结果无法确认" if uncertain else "下载中断"},
            "provider_state": {"phase": "submitting" if uncertain else "downloading"},
            "can_resume": not uncertain, "requires_reconciliation": uncertain}
    project = {"id": "project-1", "name": "recovery", "stage": "READY_FOR_EXECUTION", "tasks": [task],
               "source_assets": [], "reference_assets": []}
    context = browser.new_context()
    page = context.new_page()
    calls, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script("localStorage.setItem('workflow-project-id','project-1')")
    def handle(route):
        path = urlparse(route.request.url).path
        calls.append((route.request.method, path))
        if path == "/":
            route.fulfill(body=(root / "templates/workflow.html").read_text(encoding="utf-8"), content_type="text/html")
        elif path.startswith("/static/"):
            route.fulfill(body=(root / path.lstrip("/")).read_text(encoding="utf-8"),
                          content_type="application/javascript" if path.endswith(".js") else "text/css")
        elif path.endswith("/resume"):
            task["status"] = "succeeded"
            task["can_resume"] = False
            route.fulfill(json={"task": task, "project": project})
        elif path.endswith("/generation-1"):
            route.fulfill(json=task)
        elif path.endswith("/artifact"):
            route.fulfill(body=b"", content_type="video/mp4")
        else:
            route.fulfill(json=project)
    page.route("**/*", handle)
    try:
        page.goto("http://workflow.test/")
        expect(page.locator("#execute-status")).to_contain_text(task["error"]["message"])
        expect(page.locator("#start-execution")).to_be_disabled()
        if uncertain:
            expect(page.locator("#resume-execution")).to_be_hidden()
            expect(page.locator("#execute-status")).to_contain_text("核对")
        else:
            page.locator("#resume-execution").click()
            expect(page.locator("#execute-status")).to_contain_text("生成完成")
            assert ("POST", "/api/workflow/projects/project-1/tasks/generation-1/resume") in calls
        assert not any(method == "POST" and path.endswith("/execute") for method, path in calls)
        assert not errors
    finally:
        context.close()


@pytest.mark.parametrize("lost_at", ["submit", "poll"])
@pytest.mark.parametrize("stage", ["generation", "redaction"])
def test_network_retry_keeps_the_original_submission(browser, lost_at, stage):
    root = Path(__file__).resolve().parents[1] / "app"
    project = {"id": "project-1", "name": "recovery", "stage": "READY_FOR_EXECUTION", "tasks": [],
               "source_assets": [], "reference_assets": []}
    task = {"id": "task-1", "stage": "generation" if stage == "generation" else "redaction_render", "status": "succeeded", "output": {}, "error": {}}
    endpoint = "/execute" if stage == "generation" else "/redaction/render"
    prefix = "execute" if stage == "generation" else "redaction"
    button = "#start-execution" if stage == "generation" else "#start-redaction"
    if stage == "redaction":
        project["stage"] = "REDACTION_REVIEW"
    context = browser.new_context()
    page = context.new_page()
    page.add_init_script("localStorage.setItem('workflow-project-id','project-1')")
    submissions, queries = [], []
    def handle(route):
        path = urlparse(route.request.url).path
        if path == "/":
            route.fulfill(body=(root / "templates/workflow.html").read_text(encoding="utf-8"), content_type="text/html")
        elif path.startswith("/static/"):
            route.fulfill(body=(root / path.lstrip("/")).read_text(encoding="utf-8"),
                          content_type="application/javascript" if path.endswith(".js") else "text/css")
        elif path.endswith(endpoint):
            submissions.append(route.request.post_data_json)
            if lost_at == "submit" and len(submissions) == 1:
                route.abort("failed")
            else:
                route.fulfill(json={"task": task, "project": project})
        elif path.endswith("/task-1"):
            queries.append(path)
            if lost_at == "poll" and len(queries) == 1:
                route.abort("failed")
            else:
                route.fulfill(json=task)
        elif path.endswith("/artifact"):
            route.fulfill(body=b"", content_type="video/mp4")
        else:
            route.fulfill(json=project)
    page.route("**/*", handle)
    try:
        page.goto("http://workflow.test/", wait_until="domcontentloaded")
        page.locator(button).click()
        expect(page.locator("#" + prefix + "-status")).to_contain_text("fetch")
        page.locator(button).click()
        expect(page.locator("#" + prefix + "-status")).to_contain_text("生成完成" if stage == "generation" else "打码完成")
        if lost_at == "submit":
            assert len(submissions) == 2
            assert submissions[0]["idempotency_key"] == submissions[1]["idempotency_key"]
        else:
            assert len(submissions) == 1
            assert len(queries) == 2
    finally:
        context.close()


@pytest.mark.parametrize("lost_at", ["project", "task"])
def test_restore_lookup_failure_does_not_enable_a_new_generation(browser, lost_at):
    root = Path(__file__).resolve().parents[1] / "app"
    task = {"id": "task-1", "stage": "generation", "status": "failed", "output": {}, "error": {},
            "provider_state": {"task_id": "accepted"}, "can_resume": True, "requires_reconciliation": False}
    project = {"id": "project-1", "name": "recovery", "stage": "READY_FOR_EXECUTION", "tasks": [task],
               "source_assets": [], "reference_assets": []}
    context = browser.new_context()
    page = context.new_page()
    page.add_init_script("localStorage.setItem('workflow-project-id','project-1')")
    def handle(route):
        path = urlparse(route.request.url).path
        if path == "/":
            route.fulfill(body=(root / "templates/workflow.html").read_text(encoding="utf-8"), content_type="text/html")
        elif path.startswith("/static/"):
            route.fulfill(body=(root / path.lstrip("/")).read_text(encoding="utf-8"),
                          content_type="application/javascript" if path.endswith(".js") else "text/css")
        elif path.endswith("/task-1") or lost_at == "project":
            route.abort("failed")
        else:
            route.fulfill(json=project)
    page.route("**/*", handle)
    try:
        page.goto("http://workflow.test/", wait_until="domcontentloaded")
        expect(page.locator("#execute-status")).to_contain_text("暂时无法")
        expect(page.locator("#start-execution")).to_be_disabled()
        if lost_at == "task":
            expect(page.locator("#resume-execution")).to_be_visible()
    finally:
        context.close()
