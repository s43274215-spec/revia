import type { ActiveDocument, BackendProject } from "./revia-api";

export const PROJECT_NAME_MAX_LENGTH = 50;
export const PROJECT_DESCRIPTION_MAX_LENGTH = 500;

export type ProjectEditValue = {
  name: string;
  description: string;
};

export function normalizeProjectEditValue(value: ProjectEditValue): ProjectEditValue {
  return {
    name: value.name.trim(),
    description: value.description.trim(),
  };
}

export function replaceUpdatedProject(
  projects: BackendProject[],
  activeDocument: ActiveDocument | null,
  updatedProject: BackendProject,
): { projects: BackendProject[]; activeDocument: ActiveDocument | null } {
  return {
    projects: projects.map((project) => project.id === updatedProject.id ? updatedProject : project),
    activeDocument: activeDocument?.project_id === updatedProject.id
      ? { ...activeDocument, project_name: updatedProject.name }
      : activeDocument,
  };
}
