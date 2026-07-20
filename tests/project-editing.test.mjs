import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeProjectEditValue,
  PROJECT_DESCRIPTION_MAX_LENGTH,
  PROJECT_NAME_MAX_LENGTH,
  replaceUpdatedProject,
} from "../src/lib/project-editing.ts";

test("project edit values trim surrounding whitespace and expose UI limits", () => {
  assert.deepEqual(
    normalizeProjectEditValue({ name: "  马克思基本原理  ", description: "  期末复习  " }),
    { name: "马克思基本原理", description: "期末复习" },
  );
  assert.equal(PROJECT_NAME_MAX_LENGTH, 50);
  assert.equal(PROJECT_DESCRIPTION_MAX_LENGTH, 500);
});

test("successful editing replaces the project without changing its position", () => {
  const projects = [
    { id: "first", name: "第一门课" },
    { id: "edit", name: "旧名称" },
  ];
  const updated = { id: "edit", name: "新名称", description: "新描述" };
  const result = replaceUpdatedProject(projects, null, updated);

  assert.deepEqual(result.projects.map((project) => project.id), ["first", "edit"]);
  assert.equal(result.projects[1], updated);
});

test("editing the active project updates its visible task name immediately", () => {
  const activeDocument = { project_id: "edit", project_name: "旧名称", document_id: "document" };
  const updated = { id: "edit", name: "新名称", description: null };
  const result = replaceUpdatedProject([updated], activeDocument, updated);

  assert.equal(result.activeDocument?.project_name, "新名称");
  assert.equal(result.activeDocument?.document_id, "document");
});
