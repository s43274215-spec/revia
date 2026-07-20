import assert from "node:assert/strict";
import test from "node:test";

import { documentRecoverySummary } from "../src/lib/document-recovery.ts";

test("failed document recovery keeps the original file and completed-page checkpoint visible", () => {
  const summary = documentRecoverySummary({
    processing_status: "failed",
    can_resume: true,
    original_name: "1.pdf",
    processed_pages: 56,
    total_pages: 100,
    error_message: "对象存储下载暂时失败（RequestTimeout），请稍后继续识别",
  });

  assert.equal(summary.canResume, true);
  assert.equal(summary.filename, "1.pdf");
  assert.equal(summary.progress, "56 / 100 页");
  assert.match(summary.reason, /RequestTimeout/);
});

test("missing source disables resume and keeps re-upload as the recovery path", () => {
  const summary = documentRecoverySummary({
    processing_status: "failed",
    can_resume: false,
    original_name: "missing.pdf",
    processed_pages: 12,
    total_pages: 40,
    error_message: "原始 PDF 已不存在，请重新上传",
  });

  assert.equal(summary.canResume, false);
  assert.match(summary.reason, /重新上传/);
});
