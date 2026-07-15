import json
import time
from io import BytesIO

from playwright.sync_api import sync_playwright
from reportlab.pdfgen import canvas


def pdf_bytes(text: str) -> bytes:
    output = BytesIO()
    document = canvas.Canvas(output)
    y = 780
    for line in text.splitlines():
        document.drawString(72, y, line)
        y -= 18
    document.save()
    return output.getvalue()


def create_project(page, name: str, console_errors: list[str]) -> tuple[str, list[dict[str, object]]]:
    requests: list[dict[str, object]] = []

    def record(response) -> None:
        if "/api/v1/" in response.url:
            requests.append({"method": response.request.method, "url": response.url, "status": response.status})

    page.on("response", record)
    page.goto("http://127.0.0.1:3000", wait_until="networkidle")
    page.wait_for_timeout(3_000)
    page.locator(".new-project-button").click()
    page.wait_for_timeout(500)
    if page.locator("#course-name").count() == 0:
        raise RuntimeError("新建项目对话框未打开。控制台：" + repr(console_errors))
    page.locator("#course-name").fill(name)
    page.locator("#course-description").fill("浏览器真实链路验收")
    page.get_by_role("button", name="确认创建").click()
    page.wait_for_url("**/projects/*/upload")
    project_id = page.url.split("/projects/")[1].split("/")[0]
    return project_id, requests


def submit_project(page, syllabus: str, pdf_text: str) -> list[str]:
    file_inputs = page.locator('input[type="file"]')
    file_inputs.nth(0).set_input_files({
        "name": "browser-flow.pdf",
        "mimeType": "application/pdf",
        "buffer": pdf_bytes(pdf_text),
    })
    page.locator("#syllabus-text").fill(syllabus)
    page.get_by_role("button", name="开始生成").click()
    observed: list[str] = []
    deadline = time.time() + 20
    while time.time() < deadline and "/upload" in page.url:
        overlay = page.locator(".generation-overlay")
        if overlay.count() and overlay.is_visible():
            text = overlay.inner_text()
            if text not in observed:
                observed.append(text)
            if "生成失败" in text:
                break
        page.wait_for_timeout(80)
    return observed


def main() -> None:
    result: dict[str, object] = {"console_errors": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on("console", lambda message: result["console_errors"].append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: result["console_errors"].append("PAGEERROR: " + str(error)))

        project_name = f"浏览器真实链路 {int(time.time())}"
        project_id, requests = create_project(page, project_name, result["console_errors"])
        result["completed_project_id"] = project_id
        observed = submit_project(
            page,
            "第一章 浏览器链路\n1. BrowserFlowConcept",
            "Chapter Browser Flow\n1.1 BrowserFlowConcept\nBrowserFlowConcept is unique evidence from the uploaded PDF.",
        )
        page.wait_for_url(f"**/projects/{project_id}/learn", timeout=20_000)
        page.get_by_text("BrowserFlowConcept is unique evidence from the uploaded PDF.").wait_for(timeout=10_000)
        body = page.locator("body").inner_text()
        result["completed_url"] = page.url
        result["completed_status_texts"] = observed
        result["shows_uploaded_content"] = "BrowserFlowConcept is unique evidence from the uploaded PDF." in body
        result["shows_legacy_mock"] = "西方经济学" in body or "市场失灵" in body
        result["completed_requests"] = requests

        failed_id, failed_requests = create_project(page, f"浏览器失败链路 {int(time.time())}", result["console_errors"])
        failed_observed = submit_project(
            page,
            "1. ZqvxNorpAlpha",
            "Astronomy describes planets orbiting a distant star.",
        )
        page.get_by_role("button", name="重新生成").wait_for(timeout=20_000)
        result["failed_project_id"] = failed_id
        result["failed_url"] = page.url
        result["failed_status_texts"] = failed_observed
        result["failed_stayed_on_upload"] = page.url.endswith(f"/projects/{failed_id}/upload")
        result["retry_visible"] = page.get_by_role("button", name="重新生成").is_visible()
        result["failed_requests"] = failed_requests

        browser.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
