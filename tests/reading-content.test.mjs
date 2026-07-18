import assert from "node:assert/strict";
import test from "node:test";

import { classifyContentBlocks } from "../src/components/learning/content-format.ts";

test("keeps multiple paragraphs as separate readable blocks", () => {
  assert.deepEqual(classifyContentBlocks(["定义内容。", "解释与例子。"]), [
    { kind: "paragraph", items: ["定义内容。"] },
    { kind: "paragraph", items: ["解释与例子。"] },
  ]);
});

test("renders numbered legacy content as an ordered list", () => {
  assert.deepEqual(classifyContentBlocks(["特征如下：\n1. 能动性\n2. 可再生性\n3. 增值性"]), [
    { kind: "paragraph", items: ["特征如下："] },
    { kind: "ordered", items: ["能动性", "可再生性", "增值性"] },
  ]);
});

test("keeps old plain content compatible", () => {
  assert.deepEqual(classifyContentBlocks(["旧格式仍作为普通段落显示。"]), [{
    kind: "paragraph",
    items: ["旧格式仍作为普通段落显示。"],
  }]);
});
