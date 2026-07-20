import assert from "node:assert/strict";
import test from "node:test";

import {
  clampProjectContextMenuPosition,
  projectDeletionConfirmation,
  removeDeletedProject,
} from "../src/lib/project-deletion.ts";

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

test("project context menu stays inside the viewport near the lower-right edge", () => {
  assert.deepEqual(
    clampProjectContextMenuPosition(995, 795, 1000, 800),
    { x: 816, y: 704 },
  );
});

test("project context menu keeps ordinary pointer coordinates", () => {
  assert.deepEqual(
    clampProjectContextMenuPosition(320, 240, 1000, 800),
    { x: 320, y: 240 },
  );
});
