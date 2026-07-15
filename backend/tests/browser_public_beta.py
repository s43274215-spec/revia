from playwright.sync_api import sync_playwright


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    anonymous_responses: list[int] = []
    page.on(
        "response",
        lambda response: anonymous_responses.append(response.status)
        if response.url.endswith("/api/v1/auth/anonymous")
        else None,
    )
    page.goto("http://localhost:3000", wait_until="networkidle")
    page.get_by_role("button", name="开始使用 Revia").wait_for()
    page.get_by_text("自己的 DeepSeek API Key").wait_for()
    page.get_by_role("button", name="开始使用 Revia").click()
    page.get_by_role("heading", name="复习项目").wait_for()
    token_before = page.evaluate("localStorage.getItem('revia-workspace-token-v1')")
    assert token_before and token_before.count(".") == 1
    page.reload(wait_until="networkidle")
    page.get_by_role("heading", name="复习项目").wait_for()
    token_after = page.evaluate("localStorage.getItem('revia-workspace-token-v1')")
    assert token_after == token_before
    assert anonymous_responses == [200]
    print("PUBLIC_BROWSER_RESULT=entry:ok anonymous_http:200 token_persisted:ok refresh_session:ok")
    browser.close()
