import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../static/grading-math.js", import.meta.url), "utf8");
// KaTeX stubbed; the layout reader (static/latex-text.js) imported from its own file.
const standalone = source.replace(/^import katex .*$/m, "const katex = {};")
  .replace('"./latex-text.js"', JSON.stringify(new URL("../static/latex-text.js", import.meta.url).href));
const {mathParts, transcriptMarkup, entryRow} = await import(`data:text/javascript;base64,${Buffer.from(standalone).toString("base64")}`);

test("mixed prose and standard LaTeX delimiters preserve source text", () => {
  const text = String.raw`Decrease by $$\dfrac{168}{11}$$ units; \(x^2\), \[y=3\] and $z_1$.`;
  const parts = mathParts(text);
  assert.deepEqual(parts.filter(p => "math" in p).map(p => p.math), [String.raw`\dfrac{168}{11}`, "x^2", "y=3", "z_1"]);
  assert.equal(parts.map(p => p.text).join(""), text);
  assert.deepEqual(mathParts(String.raw`\frac{1}{2}`, true), [{text: String.raw`\frac{1}{2}`, math: String.raw`\frac{1}{2}`}]);
});

test("unclosed math, escaped currency and ordinary prose remain literal", () => {
  for (const text of [String.raw`Price \$5 and \$10`, "$5 and $10", "An unmatched $x", "plain words"]) {
    assert.equal(mathParts(text).map(p => p.text).join(""), text);
    assert.ok(mathParts(text).every(p => !("math" in p)));
  }
});

test("bare LaTeX recognizes spacing commands without letter commands", () => {
  for (const text of [String.raw`2\ 2\ 2\ 5\ 5\ 2`, String.raw`2\,5`, String.raw`2\;5`]) {
    assert.deepEqual(mathParts(text, true), [{text, math: text}]);
    assert.deepEqual(mathParts(text), [{text}]);
  }
  assert.deepEqual(mathParts("ordinary words", true), [{text: "ordinary words"}]);
});

test("a text element's explicit LaTeX spelling is rendered as LaTeX", () => {
  const h = {e: v => String(v)}, latex = String.raw`2\times2\text{ matrix}`;
  const reading = {lines: [{elements: [{type: "text", text: "2 x 2 matrix", latex}]}]};
  const before = JSON.stringify(reading);
  assert.match(transcriptMarkup(reading, h), /data-math-bare="true"/);
  assert.equal(JSON.stringify(reading), before);
  assert.doesNotMatch(transcriptMarkup({lines: [{elements: [{type: "text", text: "ordinary words", latex: ""}]}]}, h), /data-math-bare/);
});

// 2026-10-06: the reader copies a text line's words into `latex`; set as math they lost their spaces ("numberofpillarcandleboxesmade").
test("a text element whose latex only repeats its words stays text", () => {
  const h = {e: v => String(v)};
  const reading = {lines: [{elements: [{type: "text", text: "X= number of pillar candle boxes made", latex: "X= number of pillar candle boxes made"}]},
    {elements: [{type: "text", text: "Maximize", latex: "Maximize"}]}]};
  const before = JSON.stringify(reading), markup = transcriptMarkup(reading, h);
  assert.doesNotMatch(markup, /data-math-bare/);
  assert.match(markup, />X= number of pillar candle boxes made</);
  assert.match(markup, />Maximize</);
  assert.equal(JSON.stringify(reading), before);
});

// A tableau row in math mode lost its spaces too ("5 2 1 0 0 0 160" read 5210000160): each entry is set apart.
test("a written row of entries keeps its entries apart; equations and products are untouched", () => {
  const h = {e: v => String(v)};
  assert.deepEqual(entryRow("5 2 1 0 0 0 160"), ["5", "2", "1", "0", "0", "0", "160"]);
  assert.deepEqual(entryRow("2 -1 0 0 1 0 0"), ["2", "-1", "0", "0", "1", "0", "0"]);
  assert.deepEqual(entryRow("X Y S_1 S_2 S_3 P C"), ["X", "Y", "S_1", "S_2", "S_3", "P", "C"]);
  assert.deepEqual(entryRow("S_1, S_2, S_3, P"), ["S_1,", "S_2,", "S_3,", "P"]);
  assert.deepEqual(entryRow("60 160"), ["60", "160"]);
  for (const kept of [String.raw`2x \le y`, "5x+2y+s_1=160", String.raw`5lbs X + 2lbs Y \le 160lbs`, "x y", "X, Y", "-24x-20y+P=0", "x = 2 y"]) assert.equal(entryRow(kept), null, kept);
  const reading = {lines: [{elements: [{type: "math", latex: "2 -1 0"}]}, {elements: [{type: "math", latex: "2x-y+s_3=0"}]}]};
  const before = JSON.stringify(reading), markup = transcriptMarkup(reading, h);
  assert.match(markup, /<span class="grading-transcript-row"><span class="grading-math" data-math>\$2\$<\/span><span class="grading-math" data-math>\$-1\$<\/span><span class="grading-math" data-math>\$0\$<\/span><\/span>/);
  assert.match(markup, /data-math-bare="true">2x-y\+s_3=0</);
  assert.equal(JSON.stringify(reading), before);
});

test("structured transcript keeps crossed-out work, inserted work and uncertainty", () => {
  const reading = {plain_text: "exact original", lines: [{elements: [
    {type: "math", latex: "x=8", state: "crossed_out"},
    {type: "math", latex: String.raw`x=\frac{18}{3}`, state: "inserted"},
    {type: "text", text: '<img src=x onerror="alert(1)">', state: "unclear"},
  ]}]};
  const before = JSON.stringify(reading), h = {e: v => String(v).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;")};
  const markup = transcriptMarkup(reading, h);
  assert.match(markup, /grading-writing-crossed_out/);
  assert.match(markup, /grading-writing-inserted/);
  assert.match(markup, /grading-writing-unclear/);
  assert.doesNotMatch(markup, /<img/);
  assert.equal(JSON.stringify(reading), before);
});

test("a line the flag review corrected or added says so, with what it saw and what the reader had read, escaped", () => {
  const h = {e: v => String(v).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;")};
  const reading = {lines: [
    {id: "line-1", elements: [{type: "math", latex: "3x=18"}]},
    {id: "line-2", source: "flag_review", saw: '"8" is a 6 with a closed loop <b>', elements: [{type: "math", latex: "x=6"}], original: [{type: "math", text: "x = 8", latex: "x=8"}]},
    {id: "fr-1", source: "flag_review", saw: "(b) is circled", elements: [{type: "text", text: "(b)", latex: "(b)"}]},
  ]};
  const markup = transcriptMarkup(reading, h);
  assert.equal((markup.match(/by the flag review/g) || []).length, 2);
  assert.match(markup, /corrected by the flag review/);
  assert.match(markup, /added by the flag review/);
  assert.match(markup, /The reader read: x = 8/);
  assert.doesNotMatch(markup, /<b>/);
  assert.doesNotMatch(transcriptMarkup({lines: [reading.lines[0]]}, h), /flag review/);
});

test("layout and reading alternatives stay separate from literal math and are escaped", () => {
  const reading = {lines: [{elements: [{type: "math", latex: "x=8"}]}],
    layout_relations: [{from_line: "line-2", to_line: "line-1", kind: "side_calculation", description: '<img src=x> points back', uncertain: true}],
    reading_alternatives: [{line_id: "line-1", options: ["x=8", "x=6"], description: "Unclear digit"}]};
  const before = JSON.stringify(reading), h = {e: v => String(v).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;")};
  const markup = transcriptMarkup(reading, h);
  assert.match(markup, /Layout and uncertain readings/);
  assert.match(markup, /line-2 → line-1/);
  assert.match(markup, /side calculation · uncertain/);
  assert.match(markup, /Unclear digit/);
  assert.doesNotMatch(markup, /<img/);
  assert.equal(JSON.stringify(reading), before);
});
