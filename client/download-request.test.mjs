import test from "node:test";
import assert from "node:assert/strict";
import { downloadRequest } from "./src/download-request.js";

test("selective downloads pass each complete pattern literally; a blank selection preserves the original request", () => {
  const draft = { node: "spark-qa", repo: " qa/model ", name: " Variante ñ ", revision: " pinned ", patterns: "  *Q4_K_M.gguf\r\n\n configs/it's a file,ñ.json \n *.json\n" };
  const original = structuredClone(draft);
  assert.deepEqual(downloadRequest(draft), { node: "spark-qa", repo: "qa/model", name: "Variante ñ", revision: "pinned", include: ["*Q4_K_M.gguf", "configs/it's a file,ñ.json", "*.json"] });
  assert.deepEqual(draft, original);
  assert.deepEqual(downloadRequest({ ...draft, patterns: " \n\r\n\t" }), { node: "spark-qa", repo: "qa/model", name: "Variante ñ", revision: "pinned" });
});
