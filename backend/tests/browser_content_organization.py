import json
from urllib.parse import urlparse

from playwright.sync_api import Route, sync_playwright


PROJECT_ID = "11111111-1111-4111-8111-111111111111"


def _versions(prefix: str, title: str) -> list[dict[str, object]]:
    return [
        {"id": f"{prefix}1", "kind": "original", "title": title, "content": f"{title}完整原文。"},
        {"id": f"{prefix}2", "kind": "recitation", "title": title, "content": f"{title}背诵内容。"},
        {"id": f"{prefix}3", "kind": "keywords", "title": title, "content": f"{title}、定义、要点"},
    ]


def response_for(path: str) -> object:
    project = {
        "id": PROJECT_ID, "name": "人力资源", "description": "内容组织回归", "status": "completed",
        "created_at": "2026-07-19T00:00:00Z", "updated_at": "2026-07-19T00:00:00Z",
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
        points = [{
            "id": "broad", "title": "人力资源基础概念", "position": 0,
            "bullet_points": [{"id": "meaning", "position": 0, "versions": _versions("11111111-1111-4111-8111-11111111111", "人力资源的含义"), "sources": []}],
        }, {
            "id": "features", "title": "人力资源的特征", "position": 1,
            "bullet_points": [
                {"id": f"feature-{index}", "position": index, "versions": _versions(f"{index}2222222-2222-4222-8222-22222222222", title), "sources": []}
                for index, title in enumerate(("能动性", "可再生性", "增值性", "时效性", "社会性"), start=1)
            ],
        }]
        return {"project_id": PROJECT_ID, "chapters": [{
            "id": "unresolved", "title": None, "position": 0, "chapter_resolved": False,
            "knowledge_points": points,
        }]}
    if path == f"/api/v1/projects/{PROJECT_ID}/generation-jobs/latest":
        return {
            "id": "77777777-7777-4777-8777-777777777777", "project_id": PROJECT_ID,
            "status": "completed", "provider": "mock", "progress": 100,
            "processed_items": 2, "total_items": 2, "item_failures": [],
            "status_history": ["completed"], "error_message": None,
            "successful_items": 2, "failed_items": 0,
            "created_at": "2026-07-19T00:00:00Z", "started_at": "2026-07-19T00:00:00Z",
            "completed_at": "2026-07-19T00:00:01Z", "last_activity_at": "2026-07-19T00:00:01Z",
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
            path = urlparse(route.request.url).path
            api_path = f"/api/v1{path.removeprefix('/api/backend')}"
            route.fulfill(status=200, content_type="application/json", body=json.dumps(response_for(api_path), ensure_ascii=False))

        page.route("http://127.0.0.1:3000/api/backend/**", handle)
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: console_errors.append(f"PAGEERROR: {error}"))
        page.on("request", lambda request: requests.append(request.url) if "/api/backend/" in request.url else None)
        page.goto(f"http://127.0.0.1:3000/projects/{PROJECT_ID}/learn", wait_until="networkidle")

        try:
            page.get_by_role("heading", name="人力资源的特征", exact=True).wait_for(timeout=10_000)
        except Exception:
            print(json.dumps({"body": page.locator("body").inner_text(), "requests": requests, "console_errors": console_errors}, ensure_ascii=False, indent=2))
            raise
        assert page.locator(".chapter-number").count() == 0
        assert page.locator(".reading-document h2").count() == 0
        assert page.locator(".outline-chapter > button").count() == 0
        assert page.locator(".outline-points button", has_text="人力资源的特征").count() == 1
        assert page.get_by_role("heading", name="人力资源的特征", exact=True).count() == 1
        assert page.locator(".knowledge-section#features h4").count() == 5
        assert page.locator(".knowledge-section#broad h4", has_text="人力资源的特征").count() == 0

        page.get_by_label("搜索当前项目").fill("人力资源的特征")
        page.get_by_label("搜索结果").wait_for()
        assert page.locator(".search-results-panel li").count() == 1
        assert "资料章节" not in page.locator(".search-results-panel").inner_text()
        assert not console_errors, console_errors
        print(json.dumps({
            "chapter_heading_count": 0,
            "outline_target_count": 1,
            "feature_bullet_count": 5,
            "search_result_count": 1,
        }, ensure_ascii=False))
        browser.close()


if __name__ == "__main__":
    main()
