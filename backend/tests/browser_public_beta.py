from playwright.sync_api import expect, sync_playwright


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
    page.goto("http://127.0.0.1:3000", wait_until="domcontentloaded")
    page.get_by_role("button", name="开始使用 Revia").wait_for()
    page.get_by_text("自己的 DeepSeek API Key").wait_for()
    page.get_by_role("button", name="开始使用 Revia").click()
    page.get_by_role("heading", name="复习项目").wait_for()
    token_before = page.evaluate("localStorage.getItem('revia-workspace-token-v1')")
    assert token_before and token_before.count(".") == 1
    page.reload(wait_until="domcontentloaded")
    page.get_by_role("heading", name="复习项目").wait_for()
    token_after = page.evaluate("localStorage.getItem('revia-workspace-token-v1')")
    assert token_after == token_before
    assert anonymous_responses == [200]

    page.evaluate("localStorage.removeItem('revia-workspace-token-v1')")
    page.reload(wait_until="domcontentloaded")
    page.get_by_role("button", name="站长入口").click()
    page.get_by_label("访问码").fill("revia-local")
    page.get_by_role("button", name="进入 Owner Workspace").click()
    page.get_by_role("heading", name="复习项目").wait_for()
    owner_session = page.evaluate("""async () => {
      const token = localStorage.getItem('revia-workspace-token-v1');
      const response = await fetch('http://127.0.0.1:8000/api/v1/auth/session', {
        headers: { Authorization: `Bearer ${token}` },
      });
      return { status: response.status, body: await response.json() };
    }""")
    assert owner_session["status"] == 200
    assert owner_session["body"]["role"] == "owner"
    page.get_by_role("button", name="设置").click()
    try:
        page.get_by_text("Revia 公开访问").wait_for()
    except Exception:
        print(f"OWNER_SESSION={owner_session}")
        print(f"SETTINGS_BODY={page.locator('body').inner_text()}")
        raise
    site_switch = page.get_by_role("switch", name="切换 Revia 公开访问")
    expect(site_switch).to_have_attribute("aria-checked", "true")
    site_switch.click()
    page.get_by_text("Revia 公开访问已关闭").wait_for()
    expect(site_switch).to_have_attribute("aria-checked", "false")
    page.reload(wait_until="domcontentloaded")
    page.get_by_role("heading", name="复习项目").wait_for()

    public_page = browser.new_page()
    public_page.goto("http://127.0.0.1:3000", wait_until="domcontentloaded")
    public_page.get_by_role("heading", name="站长登录").wait_for()
    assert public_page.get_by_role("button", name="开始使用 Revia").count() == 0
    public_page.close()
    print("PUBLIC_BROWSER_RESULT=anonymous:ok owner_login:ok owner_settings:visible runtime_close:ok owner_refresh:ok closed_entry:ok")
    browser.close()
