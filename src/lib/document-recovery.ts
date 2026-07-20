import type { DocumentProgress } from "./revia-api";

type RecoverableDocument = Pick<
  DocumentProgress,
  "processing_status" | "can_resume" | "original_name" | "processed_pages" | "total_pages" | "error_message"
>;

export type DocumentRecoverySummary = {
  canResume: boolean;
  filename: string;
  progress: string;
  reason: string;
};

export function documentRecoverySummary(document: RecoverableDocument): DocumentRecoverySummary {
  return {
    canResume: document.processing_status === "failed" && document.can_resume,
    filename: document.original_name,
    progress: `${document.processed_pages} / ${document.total_pages || "?"} 页`,
    reason: document.error_message || "PDF 处理失败，可从已完成页面继续识别。",
  };
}
