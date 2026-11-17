import katex from "./vendor/katex/katex.js";
import {latexTree} from "./latex-text.js";

// Presentation only: never rewrite a retained transcript, rubric or editable field.
// KaTeX builds DOM with CSSOM styles under Faculty's existing strict CSP.
const OPTIONS = {throwOnError: true, trust: false, strict: "ignore", maxExpand: 200, maxSize: 10};
const escaped = (text, index) => {
  let slashes = 0;
  while (index > 0 && text[--index] === "\\") slashes++;
  return slashes % 2 === 1;
};

export function mathParts(value, bare = false) {
  const text = String(value || ""), parts = [];
  const delimiters = [["$$", "$$"], ["\\(", "\\)"], ["\\[", "\\]"], ["$", "$"]];
  let start = 0, i = 0;
  while (i < text.length) {
    const pair = delimiters.find(([left]) => text.startsWith(left, i) && !escaped(text, i) && (left !== "$" || !/\s/.test(text[i + 1] || " ")));
    if (!pair) { i++; continue; }
    let end = i + pair[0].length;
    for (; end < text.length; end++) {
      if (text.startsWith(pair[1], end) && !escaped(text, end) && (pair[1] !== "$" || !/\s/.test(text[end - 1]))) break;
    }
    if (end === text.length) { i += pair[0].length; continue; }
    if (start < i) parts.push({text: text.slice(start, i)});
    parts.push({text: text.slice(i, end + pair[1].length), math: text.slice(i + pair[0].length, end)});
    start = i = end + pair[1].length;
  }
  if (start < text.length) parts.push({text: text.slice(start)});
  // Structured math elements and the providers' bare LaTeX fields need no delimiters.
  // A control sequence may be a single character, including a space (e.g. 2\ 5).
  if (bare && !parts.some(p => "math" in p) && /\\[\s\S]|[=^_]/.test(text)) return [{text, math: text}];
  return parts;
}

export const mathText = (text, h, bare = false) => `<span class="grading-math" data-math${bare ? ' data-math-bare="true"' : ""}>${h.e(text || "")}</span>`;

// A text element's `latex` is its LaTeX spelling only when it is one (2\times2\text{ matrix}): the reader often copies the plain words
// into it, and math mode would set them in italics without their spaces ("numberofpillarcandleboxesmade", requirement, 2026-10-06).
const spelled = el => Boolean(el.latex) && el.latex !== el.text && /[\\^_]/.test(el.latex);
// A written row of entries (a tableau row "5 2 1 0 0 0 160", its labels "X Y S_1 S_2"): math mode ignores the spaces and would show
// "5210000160", so each entry is set apart. Never a line with an operator or a relation, whose spaces are only typing.
const ENTRY = /^(?:[-+−]?\d+(?:[.,/]\d+)?|[-+]?[A-Za-z]{1,5}(?:_\{?[A-Za-z0-9]{1,3}\}?)?'?|\|),?$/;
export function entryRow(source) {
  const tokens = String(source || "").trim().split(/\s+/).filter(Boolean);
  if (tokens.length < 2 || !tokens.every(t => ENTRY.test(t))) return null;
  // Two letters side by side are a product as often as two entries; two numbers never are.
  return tokens.length >= 3 || tokens.every(t => /\d/.test(t) && !/[A-Za-z]/.test(t)) ? tokens : null;
}
function elementMarkup(el, h) {
  if (el.type !== "math" && !spelled(el)) return mathText(el.text || el.latex, h);
  const source = el.latex || el.text, row = entryRow(source);
  return row ? `<span class="grading-transcript-row">${row.map(entry => mathText(`$${entry}$`, h)).join("")}</span>` : mathText(source, h, true);
}

export function transcriptMarkup(reading, h) {
  const relations = reading.layout_relations || [], alternatives = reading.reading_alternatives || [];
  const context = relations.length || alternatives.length ? `<details class="grading-transcript-context"><summary>Layout and uncertain readings</summary><ul>${relations.map(r => `<li><strong>${h.e(r.from_line)} → ${h.e(r.to_line)}</strong> · ${h.e(r.kind.replaceAll("_", " "))}${r.uncertain ? " · uncertain" : ""}: ${mathText(r.description, h)}</li>`).join("")}${alternatives.map(r => `<li><strong>${h.e(r.line_id)}</strong>: ${r.options.map(v => mathText(v, h, true)).join(" or ")}. ${mathText(r.description, h)}</li>`).join("")}</ul></details>` : "";
  if (reading.lines?.some(line => line.elements?.length)) {
    // A line the flag review corrected or added from the crop (grading/flag_resolution.py) says so, with what it saw and what the reader had.
    const marked = line => line.source !== "flag_review" ? "" : ` <span class="chip" data-tone="info" title="${h.e([line.saw, line.original ? `The reader read: ${line.original.map(el => el.text || el.latex).join(" ")}` : ""].filter(Boolean).join(" · "))}">${line.original ? "corrected by the flag review" : "added by the flag review"}</span>`;
    return reading.lines.map(line => `<div class="grading-transcript-line">${line.elements.map(el => {
      const state = ["crossed_out", "inserted", "unclear"].includes(el.state) ? el.state : "written";
      // Text elements can also provide a LaTeX spelling, such as \text{...} or 2\times2\text{ matrix} (elementMarkup).
      return `<span class="grading-writing-${state}"${state !== "written" ? ` title="${state.replaceAll("_", " ")}"` : ""}>${elementMarkup(el, h)}</span>`;
    }).join(" ")}${marked(line)}</div>`).join("") + context;
  }
  return mathText(reading.latex || reading.plain_text, h, Boolean(reading.latex)) + context;
}

function mathSpan(source, literal, display = false) {
  const span = document.createElement("span");
  if (source.length > 4000) { span.textContent = literal; return span; }
  try { katex.render(source, span, {...OPTIONS, displayMode: display, macros: {}}); }
  catch { span.textContent = literal; span.className = "grading-math-literal"; span.title = "Shown as text: this expression could not be rendered."; }
  return span;
}

// A question's printed layout (latex-text.js): built from plain nodes, its text as text nodes and its lengths through CSSOM.
const TAGS = {strong: "strong", em: "em", u: "u", code: "code", plain: "span", phantom: "span"};
function element(tag, className) { const el = document.createElement(tag); if (className) el.className = className; return el; }
function layout(nodes, into) {
  for (const n of nodes) {
    if (n.t === "text") into.append(document.createTextNode(n.v));
    // A command KaTeX cannot read alone (\left without \right, \includegraphics) stays as its source, without the warning.
    else if (n.t === "math") into.append(n.loose ? looseMath(n.src) : mathSpan(n.src, n.raw, n.display));
    else if (n.t === "group") layout(n.c, into);
    else if (n.t === "style") {
      const el = element(TAGS[n.s], n.s === "phantom" ? "latex-phantom" : "");
      if (n.s === "phantom") el.setAttribute("aria-hidden", "true");
      layout(n.c, el); into.append(el);
    } else if (n.t === "blank") {
      const el = element("span", "latex-blank"), label = element("span", "sr-only");
      if (n.width) el.style.width = n.width;
      label.textContent = "blank"; el.append(label); into.append(el);
    } else if (n.t === "gap") {
      const el = element("span", n.fill ? "latex-gap latex-fill" : "latex-gap");
      if (n.width) el.style.width = n.width;
      into.append(el);
    } else if (n.t === "br") into.append(document.createElement("br"));
    else if (n.t === "par") into.append(element("span", "latex-par"));
    else if (n.t === "block") { const el = element("span", "latex-block"); el.dataset.align = n.align; layout(n.c, el); into.append(el); }
    else if (n.t === "column") { const el = element("span", "latex-column"); if (n.width) el.style.width = n.width; layout(n.c, el); into.append(el); }
    else if (n.t === "figure") { const el = element("span", "latex-figure"); el.textContent = "Figure (on the printed page)"; into.append(el); }
    else if (n.t === "list") {
      const list = element(n.ordered ? "ol" : "ul", "latex-list");
      for (const item of n.items) {
        const li = document.createElement("li");
        if (item.label !== null && item.label !== undefined) { const label = element("strong"); label.textContent = `${item.label} `; li.append(label); }
        layout(item.c, li); list.append(li);
      }
      into.append(list);
    } else if (n.t === "table") {
      const table = element("table", n.ruled ? "latex-table latex-ruled" : "latex-table"), body = document.createElement("tbody");
      if (n.cols.some(col => col.width)) {
        const group = document.createElement("colgroup");
        for (const col of n.cols) { const el = document.createElement("col"); if (col.width) el.style.width = col.width; group.append(el); }
        table.append(group);
      }
      n.rows.forEach((row, index) => {
        const tr = document.createElement("tr");
        if (row.rule) tr.dataset.rule = String(row.rule);
        const below = row.below || (index === n.rows.length - 1 ? n.below : 0);
        if (below) tr.dataset.below = String(below);
        let column = 0;
        for (const cell of row.cells) {
          const td = document.createElement("td");
          if (cell.span > 1) td.colSpan = cell.span;
          const align = cell.align || n.cols[column]?.align;
          if (align === "c" || align === "r") td.dataset.align = align;
          layout(cell.c, td); tr.append(td);
          column += cell.span;
        }
        body.append(tr);
      });
      table.append(body); into.append(table);
    }
  }
}

function looseMath(source) {
  const span = document.createElement("span");
  try { katex.render(source, span, {...OPTIONS, macros: {}}); } catch { span.textContent = source; }
  return span;
}

export function renderMath(root) {
  for (const node of root.querySelectorAll("[data-math]:not([data-math-ready])")) {
    const original = node.textContent;
    node.dataset.mathReady = "true";
    // Malformed or excessive model output remains legible literal text.
    if (original.length > 20000) continue;
    const parts = mathParts(original, node.hasAttribute("data-math-bare"));
    const fragment = document.createDocumentFragment();
    const tree = parts.length === 1 && "math" in parts[0] ? null : latexTree(original);
    if (tree) {
      layout(tree, fragment);
      node.classList.add("latex-layout");
      node.replaceChildren(fragment);
      continue;
    }
    if (parts.length > 200) continue;
    // A formula set on its own line (the Assistant's answers mark it data-math-display) is drawn in display style.
    const display = node.hasAttribute("data-math-display");
    for (const part of parts) {
      if ("math" in part) fragment.append(mathSpan(part.math, part.text, display));
      else { const span = document.createElement("span"); span.textContent = part.text; fragment.append(span); }
    }
    node.replaceChildren(fragment);
  }
}
