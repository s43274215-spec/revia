import { apiRequest } from "./api-base";

export type BackendProjectStatus = "not_uploaded" | "processing" | "completed" | "failed";

export type BackendProject = {
  id: string;
  name: string;
  description: string | null;
  status: BackendProjectStatus;
  created_at: string;
  updated_at: string;
};

export type GenerationStatus = "pending" | "parsing" | "matching" | "generating" | "validating" | "completed" | "partial_failed" | "failed";

export type GenerationJob = {
  id: string;
  project_id: string;
  status: GenerationStatus;
  provider: string;
  progress: number;
  processed_items: number;
  total_items: number;
  item_failures: { syllabus_item: string; reason: string }[];
  status_history: GenerationStatus[];
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type BackendContentVersion = { id: string; kind: "original" | "recitation" | "keywords"; title: string; content: string };
export type BackendBulletPoint = { id: string; position: number; versions: BackendContentVersion[]; sources: { text_chunk_id: string; page_start: number; page_end: number }[] };
export type BackendKnowledgePoint = { id: string; title: string; position: number; bullet_points: BackendBulletPoint[] };
export type BackendChapter = { id: string; title: string; position: number; knowledge_points: BackendKnowledgePoint[] };
export type LearningMaterialResponse = { project_id: string; chapters: BackendChapter[] };

export type DocumentProcessingStatus = "uploaded" | "queued" | "processing" | "parsing" | "interrupted" | "parsed" | "failed";

export type ActiveDocument = {
  document_id: string;
  project_id: string;
  filename: string;
  project_name: string;
  processing_status: DocumentProcessingStatus;
  processing_phase: string;
  current_page: number;
  total_pages: number;
  processed_pages: number;
  error_message: string | null;
};

export function activeDocumentStatusLabel(document: ActiveDocument): string {
  if (document.processing_phase === "resource_limited") return "等待自动恢复";
  if (document.processing_status === "queued") return "排队中";
  if (document.processing_status === "interrupted") return "等待恢复";
  return "正在处理";
}

export function activeDocumentDescription(document: ActiveDocument): string {
  return `${document.project_name} · ${document.filename} · ${activeDocumentStatusLabel(document)} · ${document.processed_pages} / ${document.total_pages || "?"} 页`;
}

export type DocumentProgress = {
  id: string;
  project_id: string;
  kind: "course_material" | "syllabus";
  original_name: string;
  mime_type: string;
  size_bytes: number;
  storage_backend: "local" | "s3";
  processing_status: DocumentProcessingStatus;
  total_pages: number;
  processed_pages: number;
  failed_pages: number;
  ocr_page_count: number;
  current_page: number;
  processing_phase: string;
  retry_count: number;
  retry_not_before: string | null;
  queue_priority: number;
  error_message: string | null;
  created_at: string;
  is_resuming: boolean;
  queue_position: number | null;
};

type DocumentUploadTarget = {
  document: DocumentProgress;
  upload_url: string;
  method: "PUT";
  headers: Record<string, string>;
  expires_at: number;
};

const jsonHeaders = { "Content-Type": "application/json" };

export function listProjects(): Promise<BackendProject[]> {
  return apiRequest<BackendProject[]>("/projects");
}

export function createProject(value: { name: string; description: string }): Promise<BackendProject> {
  return apiRequest<BackendProject>("/projects", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ name: value.name, description: value.description || null }),
  });
}

export function getBackendProject(projectId: string): Promise<BackendProject> {
  return apiRequest<BackendProject>(`/projects/${projectId}`);
}

export async function uploadPDF(
  projectId: string,
  kind: "course_material" | "syllabus",
  file: File,
  onProgress?: (progress: DocumentProgress | null, stage: "uploading" | "processing") => void,
): Promise<string> {
  if (!file.name.toLowerCase().endsWith(".pdf") || file.type !== "application/pdf") {
    throw new Error("仅支持 MIME 类型为 application/pdf 的 .pdf 文件");
  }
  if (file.size > 150 * 1024 * 1024) throw new Error("PDF 文件不能超过 150MB");
  const target = await apiRequest<DocumentUploadTarget>(`/projects/${projectId}/documents/uploads`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({
      kind,
      filename: file.name,
      content_type: file.type,
      size_bytes: file.size,
    }),
  });
  onProgress?.(target.document, "uploading");
  const uploaded = await fetch(target.upload_url, {
    method: target.method,
    headers: target.headers,
    body: file,
  });
  if (!uploaded.ok) throw new Error(`PDF 上传失败（HTTP ${uploaded.status}）`);
  let progress = await apiRequest<DocumentProgress>(
    `/projects/${projectId}/documents/${target.document.id}/confirm`,
    { method: "POST" },
  );
  onProgress?.(progress, "processing");
  while (progress.processing_status !== "parsed") {
    if (progress.processing_status === "failed") {
      throw new Error(progress.error_message || "PDF 解析失败");
    }
    await new Promise((resolve) => window.setTimeout(resolve, 700));
    progress = await getDocumentProgress(projectId, progress.id);
    onProgress?.(progress, "processing");
  }
  return progress.id;
}

export function getDocumentProgress(projectId: string, documentId: string): Promise<DocumentProgress> {
  return apiRequest<DocumentProgress>(`/projects/${projectId}/documents/${documentId}`);
}

export function getLatestDocument(
  projectId: string,
  kind: "course_material" | "syllabus",
): Promise<DocumentProgress | null> {
  return apiRequest<DocumentProgress | null>(`/projects/${projectId}/documents/latest?kind=${kind}`);
}

export function getActiveDocument(): Promise<ActiveDocument | null> {
  return apiRequest<ActiveDocument | null>("/projects/active-document");
}

export function saveSyllabus(projectId: string, text: string, documentId: string | null): Promise<void> {
  return apiRequest<void>(`/projects/${projectId}/syllabus`, {
    method: "PUT",
    headers: jsonHeaders,
    body: JSON.stringify({ text: text.trim() || null, document_id: documentId }),
  });
}

export function getSyllabus(projectId: string): Promise<{ text: string | null; document_id: string | null } | null> {
  return apiRequest<{ text: string | null; document_id: string | null } | null>(`/projects/${projectId}/syllabus`);
}

export function startGeneration(projectId: string, regenerate = false): Promise<GenerationJob> {
  const query = regenerate ? "?regenerate=true" : "";
  return apiRequest<GenerationJob>(`/projects/${projectId}/generation-jobs${query}`, { method: "POST" });
}

export function getGenerationJob(projectId: string, jobId: string): Promise<GenerationJob> {
  return apiRequest<GenerationJob>(`/projects/${projectId}/generation-jobs/${jobId}`);
}

export function getLearningMaterial(projectId: string): Promise<LearningMaterialResponse> {
  return apiRequest<LearningMaterialResponse>(`/projects/${projectId}/learning-material`);
}
