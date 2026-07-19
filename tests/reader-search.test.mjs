import assert from "node:assert/strict";
import test from "node:test";

import { classifyContentBlocks } from "../src/components/learning/content-format.ts";
import { searchProject } from "../src/components/learning/reader-search.ts";
import { toggleExpandedVersions } from "../src/components/learning/drawer-state.ts";

const project = {
  id: "project",
  name: "中文课程",
  meta: "学习材料",
  documentTitle: "学习材料",
  chapters: [{
    id: "chapter-source",
    number: "01",
    title: "资料章节：市场失灵",
    points: [{
      id: "knowledge-externality",
      title: "知识点：外部性",
      bulletPoints: [{
        id: "bullet-effects",
        versions: {
          original: { title: "内部标题：完整解释", content: ["详细正文只在详细版出现。\n\n1. 私人成本\n2. 社会成本"] },
          recitation: { title: "内部标题：标准表达", content: ["标准版正文包含考试表达。"] },
          keywords: { title: "内部标题：记忆线索", content: ["简洁关键词", "成本偏离"] },
        },
      }],
    }],
  }],
};

test("searches source chapter, knowledge and inner titles", () => {
  assert.equal(searchProject(project, "original", "市场失灵", classifyContentBlocks)[0].kind, "资料章节");
  assert.equal(searchProject(project, "original", "外部性", classifyContentBlocks)[0].kind, "知识点");
  assert.equal(searchProject(project, "original", "完整解释", classifyContentBlocks)[0].kind, "内部标题");
});

test("searches current-version body and list items with stable targets", () => {
  const body = searchProject(project, "original", "详细正文", classifyContentBlocks);
  assert.equal(body[0].targetId, "bullet-effects-original-block-0");
  const list = searchProject(project, "original", "社会成本", classifyContentBlocks);
  assert.equal(list[0].kind, "列表项");
  assert.equal(list[0].targetId, "bullet-effects-original-block-1-item-1");
});

test("switching versions recalculates body results without duplicating other versions", () => {
  assert.equal(searchProject(project, "recitation", "标准版正文", classifyContentBlocks).length, 1);
  assert.equal(searchProject(project, "recitation", "详细正文", classifyContentBlocks).length, 0);
  assert.equal(searchProject(project, "keywords", "简洁关键词", classifyContentBlocks).length, 1);
});

test("empty and unmatched searches return no results", () => {
  assert.deepEqual(searchProject(project, "original", "", classifyContentBlocks), []);
  assert.deepEqual(searchProject(project, "original", "不存在", classifyContentBlocks), []);
});

test("global editor accordions can all close, all open, or leave only the middle open", () => {
  let expanded = new Set(["original", "recitation", "keywords"]);
  expanded = toggleExpandedVersions(expanded, "original");
  expanded = toggleExpandedVersions(expanded, "recitation");
  expanded = toggleExpandedVersions(expanded, "keywords");
  assert.equal(expanded.size, 0);
  expanded = toggleExpandedVersions(expanded, "recitation");
  assert.deepEqual([...expanded], ["recitation"]);
  expanded = toggleExpandedVersions(expanded, "original");
  expanded = toggleExpandedVersions(expanded, "keywords");
  assert.equal(expanded.size, 3);
});
