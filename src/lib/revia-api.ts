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

type DocumentProcessingResponse = {
  document: { id: string; processing_status: "uploaded" | "parsing" | "parsed" | "failed"; error_message: string | null };
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

export async function uploadPDF(projectId: string, kind: "course_material" | "syllabus", file: File): Promise<string> {
  const form = new FormData();
  form.set("kind", kind);
  form.set("file", file);
  const result = await apiRequest<DocumentProcessingResponse>(`/projects/${projectId}/documents`, { method: "POST", body: form });
  return result.document.id;
}

export function saveSyllabus(projectId: string, text: string, documentId: string | null): Promise<void> {
  return apiRequest<void>(`/projects/${projectId}/syllabus`, {
    method: "PUT",
    headers: jsonHeaders,
    body: JSON.stringify({ text: text.trim() || null, document_id: documentId }),
  });
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
