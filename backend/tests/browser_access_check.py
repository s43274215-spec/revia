import json

from playwright.sync_api import sync_playwright


def main() -> None:
    result: dict[str, object] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        console_errors: list[str] = []
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)

        page.goto("http://localhost:3000", wait_until="networkidle")
        result["access_page_visible"] = page.get_by_role("heading", name="进入你的学习空间").is_visible()
        page.get_by_label("访问码").fill("wrong-access-code")
        page.get_by_role("button", name="进入 Revia").click()
        access_error = page.locator(".access-error")
        access_error.wait_for(state="visible")
        result["wrong_code_rejected"] = "访问码不正确" in access_error.inner_text()

        page.get_by_label("访问码").fill("revia-local")
        page.get_by_role("button", name="进入 Revia").click()
        page.get_by_role("heading", name="复习项目").wait_for(state="visible")
        result["workspace_entered"] = True
        result["token_saved"] = bool(page.evaluate("window.localStorage.getItem('revia-workspace-token-v1')"))
        console_errors.clear()

        page.reload(wait_until="networkidle")
        result["refresh_keeps_workspace"] = page.get_by_role("heading", name="复习项目").is_visible()

        page.get_by_role("button", name="设置").click()
        page.get_by_role("dialog", name="DeepSeek API").wait_for(state="visible")
        result["settings_available"] = True
        result["console_errors"] = console_errors
        browser.close()

    print(json.dumps(result, ensure_ascii=False))
    required = (
        "access_page_visible",
        "wrong_code_rejected",
        "workspace_entered",
        "token_saved",
        "refresh_keeps_workspace",
        "settings_available",
    )
    if not all(result.get(item) is True for item in required) or console_errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
