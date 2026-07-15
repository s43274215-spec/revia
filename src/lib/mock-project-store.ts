import { initialMockProjects, MockStudyProject } from "./mock-projects";

const STORAGE_KEY = "revia-mock-projects-v1";

export function readProjects(): MockStudyProject[] {
  if (typeof window === "undefined") return initialMockProjects;
  const saved = window.localStorage.getItem(STORAGE_KEY);
  if (!saved) return initialMockProjects;
  try { return JSON.parse(saved) as MockStudyProject[]; } catch { return initialMockProjects; }
}

export function writeProjects(projects: MockStudyProject[]) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(projects));
}

export function addProject(input: { name: string; description: string }): MockStudyProject {
  const project: MockStudyProject = {
    id: `project-${Date.now()}`,
    name: input.name,
    description: input.description,
    createdAt: new Date().toISOString(),
    status: "not_uploaded",
    sourceDocuments: [],
    syllabus: { text: "", document: null },
    generation: { status: "idle", startedAt: null, completedAt: null, provider: "mock" },
    learningMaterial: null,
  };
  writeProjects([project, ...readProjects()]);
  return project;
}

export function getProject(id: string) {
  return readProjects().find((project) => project.id === id) ?? null;
}

export function updateProject(id: string, update: (project: MockStudyProject) => MockStudyProject) {
  const projects = readProjects();
  const next = projects.map((project) => project.id === id ? update(project) : project);
  writeProjects(next);
  return next.find((project) => project.id === id) ?? null;
}
