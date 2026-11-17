import assert from "node:assert/strict";
import test from "node:test";
import { markdown } from "../static/assistant-markdown.js";

test("ASSISTANT deeply nested quotes stay readable without exhausting the stack", () => {
  const html = markdown(">".repeat(15000) + " <script>kept as text</script>");
  assert.ok((html.match(/<blockquote/g) || []).length <= 32);
  assert.match(html, /&lt;script&gt;kept as text&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
});

test("ASSISTANT deeply nested lists retain the remaining words as safe literal text", () => {
  const source = Array.from({length: 400}, (_, index) => "  ".repeat(index) + `- entry ${index} <b>text</b>`).join("\n");
  const html = markdown(source);
  assert.ok((html.match(/<ul>/g) || []).length <= 32);
  assert.match(html, /entry 399 &lt;b&gt;text&lt;\/b&gt;/);
  assert.doesNotMatch(html, /<b>/);
});
