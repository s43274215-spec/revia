import json
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Route, sync_playwright


PROJECT_ID = "11111111-1111-4111-8111-111111111111"


def response_for(path: str) -> object:
    project = {
        "id": PROJECT_ID,
        "name": "人力资源",
        "description": "部分生成结果验收",
        "status": "completed",
        "created_at": "2026-07-18T16:00:00Z",
        "updated_at": "2026-07-18T17:00:00Z",
    }
    if path == "/api/v1/auth/mode":
        return {"public_access_enabled": True}
    if path == "/api/v1/auth/session":
        return {"workspace_id": "22222222-2222-4222-8222-222222222222", "role": "public"}
    if path == "/api/v1/projects":
        return [project]
    if path == f"/api/v1/projects/{PROJECT_ID}":
        return project
    if path == f"/api/v1/projects/{PROJECT_ID}/learning-material":
        return {
            "project_id": PROJECT_ID,
            "chapters": [{
                "id": "33333333-3333-4333-8333-333333333333",
                "title": "人力资源管理基础",
                "position": 0,
                "knowledge_points": [{
                    "id": "44444444-4444-4444-8444-444444444444",
                    "title": "人力资源的概念",
                    "position": 0,
                    "bullet_points": [{
                        "id": "55555555-5555-4555-8555-555555555555",
                        "position": 0,
                        "versions": [
                            {"id": "61111111-1111-4111-8111-111111111111", "kind": "original", "title": "基本概念", "content": "人力资源是能够推动社会和经济发展的劳动能力总和。"},
                            {"id": "62222222-2222-4222-8222-222222222222", "kind": "recitation", "title": "基本概念", "content": "人力资源是劳动能力总和。"},
                            {"id": "63333333-3333-4333-8333-333333333333", "kind": "keywords", "title": "基本概念", "content": "劳动能力、资源总和、组织发展"},
                        ],
                        "sources": [],
                    }],
                }],
            }],
        }
    if path == f"/api/v1/projects/{PROJECT_ID}/generation-jobs/latest":
        return {
            "id": "77777777-7777-4777-8777-777777777777",
            "project_id": PROJECT_ID,
            "status": "partial_failed",
            "provider": "deepseek",
            "progress": 100,
            "processed_items": 3,
            "total_items": 3,
            "item_failures": [
                {"syllabus_item": "X-Y 理论", "reason": "unmatched: no TextChunk met the configured relevance threshold"},
                {"syllabus_item": "战略性人力资源规划", "reason": "AI output failed schema validation: String should have at most 800 characters"},
            ],
            "status_history": ["pending", "partial_failed"],
            "error_message": "generation_partial_failed",
            "successful_items": 1,
            "failed_items": 2,
            "created_at": "2026-07-18T16:00:00Z",
            "started_at": "2026-07-18T16:00:00Z",
            "completed_at": "2026-07-18T16:10:00Z",
            "last_activity_at": "2026-07-18T16:10:00Z",
        }
    raise AssertionError(f"Unexpected API request: {path}")


def main() -> None:
    console_errors: list[str] = []
    requests: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.add_init_script("localStorage.setItem('revia-workspace-token-v1', 'test.workspace-token')")

        def handle(route: Route) -> None:
            payload = response_for(urlparse(route.request.url).path)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

        page.route("http://127.0.0.1:8000/api/v1/**", handle)
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: console_errors.append(f"PAGEERROR: {error}"))
        page.on("request", lambda request: requests.append(request.url) if "/api/v1/" in request.url else None)
        page.goto(f"http://127.0.0.1:3000/projects/{PROJECT_ID}/learn", wait_until="networkidle")

        try:
            page.get_by_text("1 / 3 个考点可阅读").wait_for(timeout=10_000)
        except Exception:
            print(json.dumps({"body": page.locator("body").inner_text(), "requests": requests, "console_errors": console_errors}, ensure_ascii=False, indent=2))
            raise
        page.get_by_text("未生成考点").wait_for()
        assert page.locator(".outline-missing li").count() == 2
        page.get_by_role("button", name="X-Y 理论").click()
        page.get_by_text("资料依据不足", exact=True).wait_for()
        page.get_by_text("课程资料中没有找到达到相关性要求的内容。").wait_for()
        assert not console_errors, console_errors

        screenshot = Path(tempfile.gettempdir()) / "revia-partial-generation-visibility.png"
        page.screenshot(path=str(screenshot), full_page=True)
        print(json.dumps({"summary": "1 / 3", "missing_placeholders": 2, "reason_visible": True, "screenshot": str(screenshot)}, ensure_ascii=False))
        browser.close()


if __name__ == "__main__":
    main()
