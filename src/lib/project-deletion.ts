import type { ActiveDocument, BackendProject } from "./revia-api";

export function projectDeletionConfirmation(projectName: string): string {
  return `确定永久删除“${projectName}”吗？\n\n相关资料、复习内容和正在运行的任务都会一并删除。删除后无法恢复。`;
}

export function removeDeletedProject(
  projects: BackendProject[],
  activeDocument: ActiveDocument | null,
  projectId: string,
): { projects: BackendProject[]; activeDocument: ActiveDocument | null } {
  return {
    projects: projects.filter((project) => project.id !== projectId),
    activeDocument: activeDocument?.project_id === projectId ? null : activeDocument,
  };
}
