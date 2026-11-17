// @ts-nocheck
/* READABLE REVIEW of a model's draft (DESIGN-LANGUAGE principle 13, "No walls of text"; requirement, 2026-10-05). Shared by the makeup draft window (makeup-drafts.js) and the quiz draft page (quiz-drafts.js).

   A revision's notes and warnings are sorted under the problem they are about from what the revision already stores (nothing is
   asked again): what needs a look is a short marked line; what the writer and the rubric said shows as its first sentence beside
   the problem as printed; the full wording folds. A finding repeated on every version is said once. Nothing is dropped: a warning
   of no known shape stays at the top, about the whole draft. */
import { mathText } from "./grading-math.js";
import { disclosureMarkup } from "./ui.js";
import { viewLink } from "./file-viewer.js";

/** Prose split into paragraphs (blank lines), and a paragraph's "Problem N: …" sentences into their own. @param {unknown} text */
export function paragraphs(text) {
  return String(text || "").split(/\n\s*\n/).flatMap((block) => block.split(/(?<=[.!?])\s+(?=Problem \d+:)/)).map((p) => p.trim()).filter(Boolean);
}

/** A paragraph's sentences: split after . ! or ? before a capital, digit, "(" or "$", never inside $…$ math. @param {unknown} text */
export function sentences(text) {
  const s = String(text || "").trim(), out = [];
  let start = 0, math = false;
  for (let i = 0; i < s.length; i++) {
    if (s[i] === "$") { if (s[i + 1] === "$") i++; math = !math; continue; }
    if (math || !".!?".includes(s[i]) || !/\s/.test(s[i + 1] || "")) continue;
    let next = i + 1;
    while (next < s.length && /\s/.test(s[next])) next++;
    if (next < s.length && !/[A-Z0-9($]/.test(s[next])) continue;
    out.push(s.slice(start, i + 1).trim());
    start = next;
  }
  if (start < s.length) out.push(s.slice(start).trim());
  return out.filter(Boolean);
}

/** The first sentence of a note, its **bold** marks and list dashes set aside. @param {unknown} text */
export function firstSentence(text) {
  const first = paragraphs(text)[0] || "";
  const lead = first.split("\n").find((line) => line.trim() && !/^\s*\*\*[^*]+\*\*:?\s*$/.test(line)) || first;
  return sentences(plain(lead.replace(/^\s*[-•]\s+/, "")))[0] || "";
}

const capital = (text) => text.charAt(0).toUpperCase() + text.slice(1);
const lower = (text) => (/^[A-Z][a-z]/.test(text) ? text.charAt(0).toLowerCase() + text.slice(1) : text);
const plain = (text) => String(text).replace(/\*\*([^*]+)\*\*/g, "$1");
const listed = (letters) => `${letters.length === 1 ? "Version" : "Versions"} ${letters.join(", ")}`;
// The layout's room warning ("leaves 2.9 cm to write in", under its 3 cm floor) says what to check (2026-10-06: "3 to check" over
// lines that only gave a measure).
const asked = (text) => text.replace(/^leaves (\d+(?:\.\d+)?) cm to write in\.?$/i, "Only $1 cm left to write in. Enough room?");

/**
 * The revision's notes and warnings sorted by problem: `checks` (what the layout, the page check and the rubric drafting flagged),
 * `writer` (the writer's paragraphs about the problem: "Problem N: …", or a paragraph that starts with "Problem N" or "For Problem
 * N") and `rubric` (the rubric's notes); the rest is about the whole draft. A finding on some versions names them, on every one of
 * several says so once.
 * @param {any} result a quiz or makeup revision's result
 */
export function reviewNotes(result) {
  const versions = result.quiz?.versions || [];
  const problems = Object.fromEntries((result.quiz?.problems || []).map((p) => [String(p.number), { checks: [], writer: "", rubric: "" }]));
  const whole = { checks: [], notes: [] };
  const own = new Set();
  for (const r of result.rubrics || []) {
    if (!r.rubric?.notes || !problems[r.problem]) continue;
    problems[r.problem].rubric = r.rubric.notes;
    own.add(`Problem ${r.problem} rubric: ${r.rubric.notes}`);  // a makeup lists its rubric's notes among its warnings too
  }
  const found = new Map();
  const add = (problem, text, version = "") => {
    const key = `${problem}\u0000${text}`;
    const entry = found.get(key) || found.set(key, { problem, text, versions: [] }).get(key);
    if (version && !entry.versions.includes(version)) entry.versions.push(version);
  };
  for (const warning of result.warnings || []) {
    if (own.has(warning)) continue;
    const text = String(warning);
    let m;
    if ((m = /^Version ([A-Z]): Problem (\d+) (.+)$/s.exec(text)) && problems[m[2]]) add(m[2], capital(asked(m[3])), m[1]);
    else if ((m = /^Check, (?:Version ([A-Z]), )?problem (\d+): (.+)$/s.exec(text)) && problems[m[2]]) add(m[2], capital(m[3]), m[1] || "");
    else if ((m = /^Problem (\d+) rubric: (.+)$/s.exec(text)) && problems[m[1]]) add(m[1], capital(m[2]));
    else if ((m = /^Problem (\d+): (.+)$/s.exec(text)) && problems[m[1]]) add(m[1], capital(m[2]));
    else if ((m = /^Version ([A-Z]): (.+)$/s.exec(text))) add("", capital(m[2]), m[1]);
    else if ((m = /^Check(?:, Version ([A-Z]))?: (.+)$/s.exec(text))) add("", capital(m[2]), m[1] || "");
    else add("", text);
  }
  for (const { problem, text, versions: on } of found.values()) {
    const said = !on.length || versions.length < 2 ? text : `${on.length === versions.length ? "Every version" : listed(on)}: ${lower(text)}`;
    (problem ? problems[problem].checks : whole.checks).push(said);
  }
  for (const paragraph of paragraphs(result.notes)) {
    const about = /^(?:(?:For|In|On) )?Problem (\d+)(:?)\s*/.exec(paragraph);
    const target = about && problems[about[1]];
    if (!target) { whole.notes.push(paragraph); continue; }
    target.writer = [target.writer, about[2] ? paragraph.slice(about[0].length) : paragraph].filter(Boolean).join("\n\n");
  }
  return { whole, problems };
}

/**
 * Where a problem sits on its printed page (the LaTeX layout's fractions of the page): from just above its top to the next problem
 * on the page (so the space to write between them shows), else to just under its last printed line. Null when the layout does not
 * place it on one page.
 * @param {any} layout `versions[letter].layout.problems` @param {number} number
 */
export function problemCrop(layout, number) {
  const spot = layout?.[String(number)];
  if (!spot || !Number.isFinite(spot.top) || spot.page !== (spot.end_page ?? spot.page)) return null;
  const after = Object.values(layout).filter((other) => other.page === spot.page && other.top > spot.top).map((other) => other.top);
  const bottom = Math.max(spot.bottom ?? spot.body ?? spot.top, spot.body ?? 0);
  // Up to the next problem, less the room a section heading between them takes; the last problem on a page to just under its last
  // printed line (the page's footer sits higher or lower as the class's reserved bottom changed, so no fixed line keeps it out;
  // the space left to write is said in its check, and the crop opens the whole page).
  const end = after.length ? Math.min(...after) - 0.022 : bottom + 0.008;
  const top = Math.max(0, spot.top - 0.002);
  return { page: spot.page, top, end: Math.min(1, Math.max(end, bottom + 0.006)) };
}

/** Prose as paragraphs, "- " lines as a list, a "**Heading:**" line as a label, other ** marks set aside; math typeset later by
 * renderMath. @param {unknown} text @param {{e: Function}} h */
export function proseMarkup(text, h) {
  return paragraphs(text).map((block) => {
    const out = [];
    let lead = [], items = [];
    const prose = () => { if (lead.length) out.push(`<p>${mathText(plain(lead.join(" ")), h)}</p>`); lead = []; };
    const list = () => { if (items.length) out.push(`<ul>${items.map((line) => `<li>${mathText(plain(line), h)}</li>`).join("")}</ul>`); items = []; };
    for (const line of block.split("\n").map((l) => l.trim()).filter(Boolean)) {
      const heading = /^\*\*([^*]+)\*\*:?$/.exec(line);
      if (heading) { prose(); list(); out.push(`<h5>${h.e(heading[1].replace(/:$/, ""))}</h5>`); }
      else if (/^[-•]\s/.test(line)) { prose(); items.push(line.replace(/^[-•]\s+/, "")); }
      else { list(); lead.push(line); }
    }
    prose(); list();
    return out.join("");
  }).join("");
}

/** What needs a look: short lines, each marked (needs you). @param {string[]} list @param {{e: Function}} h */
export function checksMarkup(list, h) {
  return list.length ? `<ul class="review-checks">${list.map((c) => `<li>${mathText(c, h)}</li>`).join("")}</ul>` : "";
}

/** The count of what needs a look, and where it is marked. @param {any} notes reviewNotes(...) @param {number[]} numbers */
export function countMarkup(notes, numbers, escape) {
  const flagged = numbers.filter((n) => notes.problems[n]?.checks.length);
  const count = notes.whole.checks.length + flagged.reduce((sum, n) => sum + notes.problems[n].checks.length, 0);
  // What concerns the whole draft is listed right under this line; the rest is marked under its problem.
  const where = flagged.length ? `Marked under ${flagged.length === 1 ? "problem" : "problems"} ${flagged.join(flagged.length === 2 ? " and " : ", ")}.` : "";
  return `<p class="review-count">${count ? `<span class="chip" data-tone="warning">${count} to check</span>${escape(where)}` : "Nothing was flagged by the page check or the rubric."}</p>`;
}

/**
 * The problem as printed: a framed SVG crop of its page (the page's CSP refuses inline styles; a viewBox needs none) that opens
 * the page larger. The text block only: the class's side margins (22 mm, facultyassignment.cls) are cut off, so it reads larger.
 * @param {{crop: any, page: any, pdf: string, name: string, src: string, number: number|string, escape: Function}} o
 */
export function cropMarkup({ crop, page, pdf, name, src, number, escape }) {
  if (!crop || !page) return "";
  const left = Math.round(page.width_px * MARGIN), wide = page.width_px - 2 * left;
  return `<a class="review-crop" ${viewLink(pdf, name, escape, crop.page)} aria-label="Problem ${escape(number)} as printed on page ${crop.page}: open the page larger">
    <svg viewBox="${left} ${Math.round(crop.top * page.height_px)} ${wide} ${Math.round((crop.end - crop.top) * page.height_px)}" role="img" aria-label="Problem ${escape(number)}, page ${crop.page}">
    <image href="${escape(src)}" width="${page.width_px}" height="${page.height_px}"/></svg></a>`;
}
const MARGIN = 0.09;  // of the page width cut off each side: inside the class's 22 mm side margins

/** A note's first sentence; one that ends with a colon keeps the short list it introduces (2026-10-06: "The answers on the four
 * versions:" stood alone, its list folded away). @param {string} text @param {{e: Function}} h */
function leadMarkup(text, h) {
  const first = firstSentence(text) || plain(text);
  if (!/:\s*$/.test(first)) return mathText(first, h);
  const items = (paragraphs(text)[0] || "").split("\n").filter((line) => /^\s*[-•]\s/.test(line)).map((line) => line.replace(/^\s*[-•]\s+/, ""));
  return `${mathText(first, h)}${items.length ? `<ul>${items.map((item) => `<li>${mathText(plain(item), h)}</li>`).join("")}</ul>` : ""}`;
}

/** The answer of one version of a problem, for the fact row beside it (2026-10-06): the correct choice with its wording, or the final
 * answers by part (a list when there are several). Empty when the revision gives none. @param {any} problem @param {any} content
 * @param {{e: Function}} h */
export function answerMarkup(problem, content, h) {
  if (!content) return "";
  if (problem.kind === "multiple_choice") {
    const letter = String(content.correct_choice || ""), wording = content.choices?.["abcdefghij".indexOf(letter.toLowerCase())];
    return letter ? `(${h.e(letter)})${wording ? ` ${mathText(wording, h)}` : ""}` : "";
  }
  const answers = (content.final_answers || []).map((a) => `${a.part ? `(${h.e(a.part)}) ` : ""}${mathText(a.answer, h)}`);
  return answers.length > 1 ? `<ul class="review-answers">${answers.map((a) => `<li>${a}</li>`).join("")}</ul>` : answers.join("");
}

// A key part's label as every page writes it (grading-groups.js `keyMarkup`): "Part a:" for a lettered part, the label for a named value.
const partLabel = (label, h) => (label ? `<strong>${/^[a-h]$/.test(label) ? `Part ${h.e(label)}` : h.e(label)}:</strong> ` : "");

/** The answer key read for one version of a problem while it was drafted (requirement, 2026-10-11), for the
 * fact row beside it: each part with its accepted answers, typeset as a saved key is (bare LaTeX, no $ signs; a list when there are
 * several parts), or that none could be read (its reason is the problem's marked check). Approving saves exactly what is shown.
 * Empty for a problem the revision read no key for (drafted before 2026-10-11, or without a rubric): its row is left out.
 * @param {any} reading `rubrics[i].keys[letter]`: {key, basis, unreadable} @param {{e: Function}} h */
export function keyReadMarkup(reading, h) {
  if (!reading) return "";
  const parts = (reading.key || []).map((part) => `${partLabel(part.label, h)}${part.accept.map((value) => mathText(value, h, true)).join(" or ")}`);
  if (!parts.length) return "None could be read. Approving saves no key for it.";
  return parts.length > 1 ? `<ul class="review-answers">${parts.map((part) => `<li>${part}</li>`).join("")}</ul>` : parts[0];
}

/** How each part of that key was found (the reader's `basis`: where it follows the worked solution, and where that contradicts the
 * printed problem or the full-credit line), for the fold that holds the rubric and solution. Empty when no part says.
 * @param {any} reading @param {{e: Function}} h */
export function keyBasisMarkup(reading, h) {
  const notes = (reading?.key || []).map((part, index) => [part.label, reading.basis?.[index] || ""]).filter(([, note]) => note);
  return notes.length ? `<h5>How the answer key was read</h5><ul>${notes.map(([label, note]) => `<li>${partLabel(label, h)}${mathText(note, h)}</li>`).join("")}</ul>` : "";
}

/** How many answer keys a revision read, one per problem and version, and how many it could not: what an approval saves. Null for
 * a revision that read none at all (drafted before 2026-10-11), whose approval says nothing of keys. @param {any} result */
export function keyCounts(result) {
  const readings = (result?.rubrics || []).flatMap((r) => Object.values(r.keys || {}));
  if (!readings.length) return null;
  const read = readings.filter((reading) => reading.key?.length).length;
  return { read, missing: readings.length - read };
}

/** What the model said about the whole draft: each paragraph's first sentence, the full wording (when there is more) in one fold.
 * @param {string[]} list @param {{e: Function}} h @param {string} [label] */
export function wholeMarkup(list, h, label = "All the model's notes") {
  if (!list.length) return "";
  const more = list.some((p) => sentences(plain(p).replace(/\n+/g, " ")).length > 1 || /\n/.test(p));
  return `<div class="review-whole"><ul>${list.map((p) => `<li>${leadMarkup(p, h)}</li>`).join("")}</ul>
    ${more ? disclosureMarkup({ label, body: proseMarkup(list.join("\n\n"), h), className: "review-fold" }) : ""}</div>`;
}

/** Each note's first sentence beside the problem, labelled, and the whole wording (when there is more) in one fold. `facts` are
 * rows drawn first and never folded: [label, markup] (the quiz page's answer for the version shown).
 * @param {[string, string][]} said [label, text] @param {{e: Function}} h @param {[string, string][]} [facts] */
export function saidMarkup(said, h, facts = []) {
  const shown = said.filter(([, text]) => text), fixed = facts.filter(([, markup]) => markup);
  if (!shown.length && !fixed.length) return "";
  const more = shown.some(([, text]) => sentences(plain(text).replace(/\n+/g, " ")).length > 1 || /\n/.test(text));
  return `<dl class="review-said">${fixed.map(([label, markup]) => `<dt>${h.e(label)}</dt><dd>${markup}</dd>`).join("")}${shown.map(([label, text]) => `<dt>${h.e(label)}</dt><dd>${leadMarkup(text, h)}</dd>`).join("")}</dl>
    ${more ? disclosureMarkup({ label: "All the model's notes", body: shown.map(([label, text]) => `<h5>${h.e(label)}</h5>${proseMarkup(text, h)}`).join(""), className: "review-fold" }) : ""}`;
}
