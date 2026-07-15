import { initialProjects, Project as LearningMaterial } from "@/components/learning/data";

export type ProjectStatus = "not_uploaded" | "completed";

export type MockSourceDocument = {
  id: string;
  name: string;
  size: number;
  mimeType: "application/pdf";
  kind: "course_material" | "syllabus";
};

export type MockSyllabus = {
  text: string;
  document: MockSourceDocument | null;
};

export type MockGeneration = {
  status: "idle" | "processing" | "completed";
  startedAt: string | null;
  completedAt: string | null;
  provider: "mock";
};

export type MockStudyProject = {
  id: string;
  name: string;
  description: string;
  createdAt: string;
  status: ProjectStatus;
  sourceDocuments: MockSourceDocument[];
  syllabus: MockSyllabus;
  generation: MockGeneration;
  learningMaterial: LearningMaterial | null;
};

const completedProject = (material: LearningMaterial, date: string): MockStudyProject => ({
  id: material.id,
  name: material.name,
  description: material.meta,
  createdAt: date,
  status: "completed",
  sourceDocuments: [{ id: `${material.id}-source`, name: `${material.name}课程资料.pdf`, size: 2_480_000, mimeType: "application/pdf", kind: "course_material" }],
  syllabus: { text: "按照课程章节与考试范围整理重点内容。", document: null },
  generation: { status: "completed", startedAt: date, completedAt: date, provider: "mock" },
  learningMaterial: material,
});

export const initialMockProjects: MockStudyProject[] = [
  completedProject(initialProjects[0], "2026-07-12T09:30:00.000Z"),
  completedProject(initialProjects[1], "2026-07-10T14:20:00.000Z"),
  {
    id: "history-upload",
    name: "中国近现代史",
    description: "课程复习",
    createdAt: "2026-07-08T08:45:00.000Z",
    status: "not_uploaded",
    sourceDocuments: [],
    syllabus: { text: "", document: null },
    generation: { status: "idle", startedAt: null, completedAt: null, provider: "mock" },
    learningMaterial: null,
  },
];

export function createGeneratedMaterial(project: MockStudyProject): LearningMaterial {
  const template = initialProjects[0];
  return { ...template, id: project.id, name: project.name, meta: "已生成", documentTitle: `${project.name}复习材料` };
}
