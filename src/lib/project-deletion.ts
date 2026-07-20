import type { ActiveDocument, BackendProject } from "./revia-api";

export const PROJECT_CONTEXT_MENU_WIDTH = 176;
export const PROJECT_CONTEXT_MENU_HEIGHT = 48;

export function projectDeletionConfirmation(projectName: string): string {
  return `确定永久删除“${projectName}”吗？\n\n相关资料、复习内容和正在运行的任务都会一并删除。删除后无法恢复。`;
}

export function clampProjectContextMenuPosition(
  clientX: number,
  clientY: number,
  viewportWidth: number,
  viewportHeight: number,
  menuWidth = PROJECT_CONTEXT_MENU_WIDTH,
  menuHeight = PROJECT_CONTEXT_MENU_HEIGHT,
  margin = 8,
): { x: number; y: number } {
  const maxX = Math.max(margin, viewportWidth - menuWidth - margin);
  const maxY = Math.max(margin, viewportHeight - menuHeight - margin);
  return {
    x: Math.min(Math.max(clientX, margin), maxX),
    y: Math.min(Math.max(clientY, margin), maxY),
  };
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
