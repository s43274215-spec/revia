import assert from "node:assert/strict";
import test from "node:test";

import { projectDeletionConfirmation, removeDeletedProject } from "../src/lib/project-deletion.ts";

const projects = [
  { id: "keep", name: "保留项目" },
  { id: "delete", name: "待删除项目" },
];

test("delete confirmation explains permanent project and task cleanup", () => {
  const message = projectDeletionConfirmation("马克思基本原理");
  assert.match(message, /永久删除“马克思基本原理”/);
  assert.match(message, /正在运行的任务都会一并删除/);
  assert.match(message, /无法恢复/);
});

test("successful deletion removes the project and its active task from dashboard state", () => {
  const result = removeDeletedProject(
    projects,
    { project_id: "delete", document_id: "document" },
    "delete",
  );
  assert.deepEqual(result.projects.map((project) => project.id), ["keep"]);
  assert.equal(result.activeDocument, null);
});
