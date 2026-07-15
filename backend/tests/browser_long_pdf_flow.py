import json
import tempfile
import time
from pathlib import Path

import fitz
from playwright.sync_api import sync_playwright


def build_pdf(path: Path, pages: int = 200) -> None:
    document = fitz.open()
    for page_number in range(1, pages + 1):
        page = document.new_page()
        heading = "1.1 Durable Knowledge Structure\n" if page_number == 1 or page_number % 4 == 0 else ""
        page.insert_text(
            (54, 54),
            f"{heading}Page {page_number} explains durable knowledge structure and connected learning concepts.",
        )
    document.save(path, garbage=4, deflate=True)
    document.close()


def main() -> None:
    result: dict[str, object] = {}
    with tempfile.TemporaryDirectory() as directory:
        pdf_path = Path(directory) / "browser-200-pages.pdf"
        build_pdf(pdf_path)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            console_errors: list[str] = []
            document_requests: list[str] = []
            observed_text: set[str] = set()
            page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
            page.on(
                "request",
                lambda request: document_requests.append(request.url)
                if "/documents" in request.url else None,
            )

            page.goto("http://localhost:3000", wait_until="networkidle")
            if page.get_by_role("heading", name="进入你的学习空间").is_visible():
                page.get_by_label("访问码").fill("revia-local")
                page.get_by_role("button", name="进入 Revia").click()
            page.get_by_role("heading", name="复习项目").wait_for(state="visible")
            page.get_by_role("button", name="新建项目").click()
            page.get_by_label("课程名称 必填").fill("长 PDF 浏览器验收")
            page.get_by_role("button", name="确认创建").click()
            page.wait_for_url("**/upload")
            page.locator('input[type="file"]').first.set_input_files(str(pdf_path))
            page.get_by_label("直接输入考纲").fill("1.1 Durable Knowledge Structure")
            page.get_by_role("button", name="开始生成").click()
            overlay = page.locator(".generation-overlay")
            overlay.wait_for(state="visible")
            parse_deadline = time.monotonic() + 30
            while time.monotonic() < parse_deadline:
                text = overlay.inner_text()
                observed_text.add(text)
                if "正在解析第" in text:
                    break
                page.wait_for_timeout(50)
            upload_calls_before_refresh = len([url for url in document_requests if url.endswith("/documents/uploads")])
            page.reload(wait_until="networkidle")
            page.get_by_label("直接输入考纲").wait_for(state="visible")
            page.wait_for_function("document.querySelector('#syllabus-text')?.value.length > 0")
            start_button = page.get_by_role("button", name="开始生成")
            start_button.wait_for(state="visible")
            deadline = time.monotonic() + 45
            while start_button.is_disabled() and time.monotonic() < deadline:
                if overlay.is_visible():
                    observed_text.add(overlay.inner_text())
                page.wait_for_timeout(100)
            result["refresh_restored_syllabus"] = page.get_by_label("直接输入考纲").input_value() == "1.1 Durable Knowledge Structure"
            result["refresh_reused_parsed_pdf"] = not start_button.is_disabled()
            start_button.click()
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline and "/learn" not in page.url:
                if overlay.is_visible():
                    observed_text.add(overlay.inner_text())
                page.wait_for_timeout(100)
            result["entered_learning_page"] = "/learn" in page.url
            result["saw_upload_stage"] = any("正在上传完整 PDF" in text for text in observed_text)
            result["saw_parse_progress"] = any("正在解析第" in text and "已完成文本提取" in text for text in observed_text)
            result["saw_ocr_count"] = any("OCR 0 页" in text for text in observed_text)
            result["used_create_upload_api"] = any(url.endswith("/documents/uploads") for url in document_requests)
            result["used_confirm_api"] = any(url.endswith("/confirm") for url in document_requests)
            result["polled_document_progress"] = any(
                "/documents/" in url and not url.endswith("/confirm") and "latest" not in url
                for url in document_requests
            )
            result["did_not_reupload_after_refresh"] = len(
                [url for url in document_requests if url.endswith("/documents/uploads")]
            ) == upload_calls_before_refresh
            result["console_errors"] = console_errors
            browser.close()

    print(json.dumps(result, ensure_ascii=False))
    required = (
        "entered_learning_page",
        "saw_upload_stage",
        "saw_parse_progress",
        "saw_ocr_count",
        "used_create_upload_api",
        "used_confirm_api",
        "polled_document_progress",
        "refresh_restored_syllabus",
        "refresh_reused_parsed_pdf",
        "did_not_reupload_after_refresh",
    )
    if not all(result.get(item) is True for item in required) or result["console_errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
