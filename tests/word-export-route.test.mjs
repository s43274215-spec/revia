import assert from "node:assert/strict";
import test from "node:test";

import { GET } from "../src/app/api/backend/projects/[projectId]/exports/word/route.ts";

test("Word export route streams the backend response and download headers", async () => {
  const originalFetch = globalThis.fetch;
  const originalBaseUrl = process.env.REVIA_API_BASE_URL;
  let forwardedUrl = "";
  let forwardedCookie = "";
  try {
    process.env.REVIA_API_BASE_URL = "https://backend.example/api/v1/";
    globalThis.fetch = async (input, init) => {
      forwardedUrl = String(input);
      forwardedCookie = new Headers(init?.headers).get("cookie") ?? "";
      return new Response(new Uint8Array([80, 75, 3, 4]), {
        headers: {
          "content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          "content-disposition": "attachment; filename*=UTF-8''Revia.docx",
        },
      });
    };

    const response = await GET(
      new Request("https://revia.example/api/backend/projects/project-id/exports/word?version=all", {
        headers: { cookie: "revia_session=signed-session" },
      }),
      { params: Promise.resolve({ projectId: "project-id" }) },
    );

    assert.equal(forwardedUrl, "https://backend.example/api/v1/projects/project-id/exports/word?version=all");
    assert.equal(forwardedCookie, "revia_session=signed-session");
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("content-disposition"), "attachment; filename*=UTF-8''Revia.docx");
    assert.deepEqual([...new Uint8Array(await response.arrayBuffer())], [80, 75, 3, 4]);
  } finally {
    globalThis.fetch = originalFetch;
    if (originalBaseUrl === undefined) delete process.env.REVIA_API_BASE_URL;
    else process.env.REVIA_API_BASE_URL = originalBaseUrl;
  }
});
