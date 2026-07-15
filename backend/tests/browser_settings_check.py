import json
import tempfile
from pathlib import Path

from playwright.sync_api import Route, sync_playwright


SCREENSHOT = Path(tempfile.gettempdir()) / "revia-settings-drawer.png"
SECRET = "sk-browser-plaintext-must-not-leak-123456"


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    console_errors: list[str] = []
    settings_payloads: list[str] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on(
        "request",
        lambda request: settings_payloads.append(request.post_data or "")
        if "/settings/deepseek" in request.url and request.method in {"PUT", "POST"}
        else None,
    )

    page.goto("http://localhost:3000/", wait_until="networkidle")
    header_trigger = page.get_by_role("button", name="设置")
    assert header_trigger.is_visible(), "首页未找到设置入口"
    header_trigger.click()
    drawer = page.get_by_role("dialog", name="DeepSeek API")
    drawer.wait_for(state="visible")
    drawer.get_by_text("未配置", exact=True).wait_for()

    key_input = page.get_by_label("API Key")
    assert key_input.get_attribute("type") == "password"
    key_input.fill(SECRET)
    page.get_by_role("button", name="显示 API Key").click()
    assert key_input.get_attribute("type") == "text"
    page.get_by_role("button", name="隐藏 API Key").click()
    assert key_input.get_attribute("type") == "password"
    page.screenshot(path=str(SCREENSHOT), full_page=True)

    page.get_by_role("button", name="保存", exact=True).click()
    page.get_by_text("DeepSeek API Key 已安全保存", exact=True).wait_for()
    assert drawer.get_by_text("已配置", exact=True).is_visible()
    assert all(SECRET not in payload for payload in settings_payloads), "设置请求体出现明文 API Key"

    page.get_by_role("button", name="关闭设置").last.click()
    page.reload(wait_until="networkidle")
    page.get_by_role("button", name="设置").click()
    drawer.get_by_text("已配置", exact=True).wait_for()

    test_results = iter([
        {"success": False, "message": "连接失败：API Key 无效"},
        {"success": True, "message": "连接成功，DeepSeek API 可用"},
    ])

    def respond_to_test(route: Route) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps(next(test_results), ensure_ascii=False))

    page.route("**/api/v1/settings/deepseek/test", respond_to_test)
    page.get_by_role("button", name="测试连接").click()
    page.get_by_text("连接失败：API Key 无效", exact=True).wait_for()
    page.get_by_role("button", name="测试连接").click()
    page.get_by_text("连接成功，DeepSeek API 可用", exact=True).wait_for()
    page.unroute("**/api/v1/settings/deepseek/test", respond_to_test)

    page.get_by_role("button", name="关闭设置").last.click()
    page.goto("http://localhost:3000/projects/economics/learn", wait_until="networkidle")
    sidebar_trigger = page.locator(".settings-trigger.sidebar")
    assert sidebar_trigger.is_visible(), "学习页左侧栏底部未找到设置入口"
    sidebar_trigger.click()
    page.get_by_role("dialog", name="DeepSeek API").wait_for(state="visible")

    assert not console_errors, f"浏览器控制台错误: {console_errors}"
    print("BROWSER_SETTINGS_OK=true")
    print(f"SETTINGS_REQUESTS_WITH_PLAINTEXT={sum(SECRET in payload for payload in settings_payloads)}")
    print("HOME_TRIGGER=true")
    print("LEARNING_SIDEBAR_TRIGGER=true")
    print("PASSWORD_VISIBILITY_TOGGLE=true")
    print("REFRESH_CONFIGURED_STATUS=true")
    print("SUCCESS_FAILURE_FEEDBACK=true")
    browser.close()
