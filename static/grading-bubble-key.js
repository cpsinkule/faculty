// Bubble sheet grading: each version's answer key, clicked in like Gradescope's, and every sheet scored against it.
// The key is saved on the server as a new revision of the term's grading plan. Scores are derived from the saved key and each
// sheet's current reading every time the page loads; nothing is graded, published or emailed here. Browser memory holds only
// the unsaved draft, which survives re-renders and navigation within this tab.
import { icon } from "./icons.js";
import { mountBubbleAnalysis } from "./grading-bubble-analysis.js";
import { mountGradescope } from "./grading-gradescope.js";
import { emptyMarkup, plural as count } from "./ui.js";
import { viewLink } from "./file-viewer.js";
import { importFromGradescope } from "./grading-gradescope-rubrics.js";

const LETTERS = ["A", "B", "C", "D", "E"];
const SCORING = {
  exact: ["Exact", "Full points only when the letters marked are exactly the key's."],
  partial: ["Partial", "A share of the points for each correct letter marked; nothing when any other letter is marked."],
  either: ["Either/Or", "Full points when any correct letter is marked; nothing when any other letter is marked. With every letter keyed, every sheet earns the points, a blank included."],
};
const STATUS = { scored: "Scored", needs_review: "Needs review in the Inbox", no_version: "No version marked", no_key: "No key for its version" };
const drafts = new Map(); // grading plan ID -> {base, key, version, all, requestId, filter, editing, search, sort, rows}
const ROWS = 50; // score rows shown before "Show all"

const letterOf = index => String.fromCharCode(65 + index);

// The server's key (answered questions as a list) as the editor holds it (questions by number).
function fromSaved(key, suggested) {
  if (!key) return { defaults: { points: "1", scoring: "exact" }, versions: (suggested.length ? suggested : ["A"]).map(letter => ({ letter, questions: {} })) };
  return { defaults: { ...key.defaults }, versions: key.versions.map(v => ({ letter: v.letter, questions: Object.fromEntries(v.questions.map(q => [q.number, { ...q }])) })) };
}

// What a save sends: answered questions only, in order. A setting on a question without an answer is not kept.
function payload(draft) {
  return {
    defaults: { points: String(draft.defaults.points).trim(), scoring: draft.defaults.scoring },
    versions: draft.versions.map(v => ({ letter: v.letter, questions: Object.values(v.questions).filter(q => q.answer).sort((a, b) => a.number - b.number)
      .map(q => ({ number: q.number, answer: q.answer, ...(q.points !== undefined && q.points !== "" ? { points: String(q.points).trim() } : {}), ...(q.scoring ? { scoring: q.scoring } : {}) })) })),
  };
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const answered = version => Object.values(version.questions).filter(q => q.answer).length;
const highest = draft => Math.max(0, ...draft.versions.flatMap(v => Object.values(v.questions).filter(q => q.answer).map(q => q.number)));

// The saved key in one line per version: questions in groups of five, a dot for an unanswered one, (AB) for two letters.
export function compactKey(version) {
  const answers = Object.fromEntries(version.questions.map(q => [q.number, q.answer]));
  const last = Math.max(0, ...version.questions.map(q => q.number));
  const groups = [];
  for (let first = 1; first <= last; first += 5) {
    const letters = Array.from({ length: Math.min(5, last - first + 1) }, (_, i) => answers[first + i] ? (answers[first + i].length > 1 ? `(${answers[first + i]})` : answers[first + i]) : "·");
    groups.push({ first, letters: letters.join(" ") });
  }
  return groups;
}

// Questions whose saved answer most scored sheets missed while most marked one other letter (grun:P14): perhaps a wrong key.
// From the server's own per-question stats; it only points at the question, the instructor decides.
export function secondLooks(view) {
  const found = [];
  for (const version of view.key?.versions || []) {
    for (const question of version.questions) {
      const stat = view.stats?.[version.letter]?.[question.number];
      if (!stat?.sheets || stat.sheets < 5) continue;
      const share = stat.full / stat.sheets;
      if (share < .5 && stat.common && stat.common !== question.answer) found.push({ letter: version.letter, number: question.number, share, common: stat.common, sheets: stat.sheets });
    }
  }
  return found;
}

// The score rows as the toolbar asks: the filter, a name or section search, an order, and the first ROWS unless all are asked.
export function scoreRows(sheets, { filter = "all", search = "", sort = "scan" } = {}) {
  const words = search.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const kept = sheets.filter(s => filter === "all" || (filter === "scored" ? s.status === "scored" : s.status !== "scored"))
    .filter(s => words.every(word => `${s.student || ""} ${s.section || ""}`.toLowerCase().includes(word)));
  const score = s => s.status === "scored" ? Number(s.earned) : Number.NEGATIVE_INFINITY;
  const order = { name: (a, b) => (a.student || "￿").localeCompare(b.student || "￿"), low: (a, b) => score(a) - score(b), high: (a, b) => score(b) - score(a) }[sort];
  return order ? [...kept].sort(order) : kept;
}

export async function bubbleView(host, plan, h, refresh, active) {
  const base = `/api/grading/assignments/${encodeURIComponent(plan.id)}/bubble-sheets`;
  let view = await h.api(base);
  if (!active()) return;
  const e = h.e;
  // The page is five regions, each drawn on its own: the summary strip, the key, the item analysis (read only, closed until
  // opened), the Gradescope panel (it keeps its own state) and the scores. A letter click redraws the key only; the table is drawn
  // again only when the saved key or its own controls change.
  const outer = host;
  outer.innerHTML = `<header class="grading-heading grading-stage-heading"><div><h3>Bubble sheets</h3><p class="section-lead">Multiple-choice grading from the bubble sheets read in the Inbox, scored the way Gradescope scores them.</p></div></header>
    <div data-bubble-head></div><div data-bubble-page></div><div data-bubble-analysis></div><div data-gradescope-host id="bubble-gradescope"></div><div data-bubble-scores></div>`;
  host = outer.querySelector("[data-bubble-page]");
  const headHost = outer.querySelector("[data-bubble-head]"), scoresHost = outer.querySelector("[data-bubble-scores]");
  let sheetsOnGradescope = null;
  let saving = false;

  function state() {
    let draft = drafts.get(plan.id);
    const revision = view.key?.revision ?? null;
    if (draft && draft.base !== revision) {
      // Saved from another window since this draft began: the saved key wins, and the instructor is told.
      if (!same(payload(draft.key), payload(fromSaved(view.key, view.suggested_versions)))) h.toast("The answer key was saved elsewhere, so your unsaved changes were set aside. The saved key is shown.", true);
      draft = { ...draft, base: revision, key: fromSaved(view.key, view.suggested_versions), requestId: "", editing: false };
      drafts.set(plan.id, draft);
    }
    if (!draft) {
      draft = { base: revision, key: fromSaved(view.key, view.suggested_versions), version: "A", all: false, requestId: "", filter: "all", editing: false, search: "", sort: "scan", rows: false };
      drafts.set(plan.id, draft);
    }
    if (!draft.key.versions.some(v => v.letter === draft.version)) draft.version = draft.key.versions[0].letter;
    return draft;
  }
  const saved = () => payload(fromSaved(view.key, view.suggested_versions));
  const dirty = draft => !same(payload(draft.key), saved());
  // The editor is open while there is no saved key, while the draft differs from it, or when the instructor opened it.
  const editing = draft => !view.key || draft.editing || dirty(draft);
  const stateText = draft => dirty(draft) ? "Unsaved changes" : view.key
    ? `Saved · revision ${view.key.revision} · ${new Date(view.key.created_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}` : "Not saved yet";
  // Any change to the draft is a new command: a retried save of unchanged content reuses its request ID, so a lost response
  // returns the revision it made instead of a second one.
  const changed = draft => { draft.requestId = ""; };

  // --- the summary strip and "Worth a second look" (from the saved key and the server's counts; links jump within the page) ---
  function headMarkup() {
    const c = view.counts, waiting = c.needs_review + c.in_inbox, unscored = c.no_version + c.no_key;
    const keyText = view.key ? `Key saved · revision ${view.key.revision}` : "No answer key saved yet";
    const gs = sheetsOnGradescope ? `<button type="button" class="text-button" data-jump="gradescope">On Gradescope <b>${e(Number(sheetsOnGradescope.sent || 0).toLocaleString())}</b> of ${e(Number(sheetsOnGradescope.matched || 0).toLocaleString())}</button>` : "";
    const looks = secondLooks(view);
    const look = looks.length ? `<div class="callout bubble-second-look" data-tone="warning" role="status" data-second-look><strong class="callout-title">Worth a second look</strong><p>On ${looks.length === 1 ? "this question" : "these questions"} most scored sheets missed the saved answer and most marked one other letter. Check the key.</p><ul>${looks.slice(0, 8).map(l => `<li><button type="button" class="text-button" data-look="${e(l.letter)}:${l.number}">${view.key.versions.length > 1 ? `Version ${e(l.letter)} · ` : ""}Question ${l.number}: ${Math.round(l.share * 100)}% full credit, most marked ${e(l.common)}</button></li>`).join("")}</ul>${looks.length > 8 ? `<p>and ${looks.length - 8} more, marked in the key.</p>` : ""}</div>` : "";
    return `<nav class="bubble-summary" aria-label="Bubble sheets at a glance" data-bubble-summary>
      <button type="button" class="text-button${view.key ? "" : " bubble-summary-attention"}" data-jump="key">${e(keyText)}</button>
      <button type="button" class="text-button" data-jump="scores"><b>${e(c.scored.toLocaleString())}</b> scored</button>
      ${waiting ? `<button type="button" class="text-button" data-jump="scores"><b>${e(waiting.toLocaleString())}</b> waiting for review</button>` : ""}
      ${unscored ? `<button type="button" class="text-button bubble-summary-attention" data-jump="scores"><b>${e(unscored.toLocaleString())}</b> not scored</button>` : ""}
      ${gs}</nav>${look}`;
  }
  function drawHead() {
    if (!active() || !headHost.isConnected) return;
    headHost.innerHTML = headMarkup();
  }
  headHost.addEventListener("click", event => {
    const jump = event.target.closest("[data-jump]");
    if (jump) {
      const target = { key: "#bubble-key-title", scores: "#bubble-scores-title", gradescope: "#bubble-gradescope" }[jump.dataset.jump];
      const node = outer.querySelector(target);
      node?.scrollIntoView({ block: "start" });
      if (node) { node.setAttribute("tabindex", "-1"); node.focus({ preventScroll: true }); }
      return;
    }
    const look = event.target.closest("[data-look]");
    if (!look) return;
    const [letter, text] = look.dataset.look.split(":");
    openQuestion(letter, Number(text));
  });
  // Open the editor on that question of that version (every question shown if it lies past the ones on screen).
  function openQuestion(letter, number) {
    const draft = state();
    draft.editing = true;
    if (draft.key.versions.some(v => v.letter === letter)) draft.version = letter;
    if (number > Math.max(view.read_questions || 25, highest(draft.key), 1)) draft.all = true;
    drawKey();
    const question = host.querySelector(`[data-number="${number}"]`);
    question?.scrollIntoView({ block: "center" });
    (question?.querySelector('[aria-pressed="true"]') || question?.querySelector("[data-letter]"))?.focus({ preventScroll: true });
  }
  const analysis = mountBubbleAnalysis(outer.querySelector("[data-bubble-analysis]"), plan, h, active, { base, onLook: openQuestion });

  function questionMarkup(version, number) {
    const question = version.questions[number] || { number, answer: "" };
    const custom = [question.scoring ? SCORING[question.scoring][0] : "", question.points !== undefined && question.points !== "" ? `${question.points} pt${Number(question.points) === 1 ? "" : "s"}` : ""].filter(Boolean).join(" · ");
    // How the SAVED answer went on the scored sheets; shown only while the draft's answer is still that one.
    const savedAnswer = view.key?.versions.find(v => v.letter === version.letter)?.questions.find(q => q.number === number)?.answer;
    const stat = savedAnswer && savedAnswer === question.answer ? view.stats?.[version.letter]?.[number] : null;
    const share = stat?.sheets ? stat.full / stat.sheets : null;
    const doubt = stat && stat.sheets >= 5 && share < .5 && stat.common && stat.common !== question.answer;
    const statText = stat?.sheets ? `${Math.round(share * 100)}%` : "";
    const statTitle = stat?.sheets ? `${stat.full} of ${stat.sheets} scored sheets earned full points.${stat.common && stat.common !== question.answer ? ` Most marked ${stat.common} (${stat.common_count}).` : ""}` : "";
    return `<li class="bubble-key-question${question.answer ? " answered" : ""}" data-number="${number}"><div class="bubble-key-qhead"><span class="bubble-key-number">Question ${number}</span>
      <button type="button" class="bubble-key-gear" data-question-settings="${number}" aria-label="Question ${number} settings" title="Points and scoring for question ${number}">${icon("settings")}</button>
      ${custom ? `<span class="bubble-key-custom">${e(custom)}</span>` : ""}${statText ? `<span class="bubble-key-stat${doubt ? " doubt" : ""}" title="${e(statTitle)}">${doubt ? `${e(statText)} · most ${e(stat.common)}` : e(statText)}</span>` : ""}</div>
      <div class="bubble-key-letters" role="group" aria-label="Question ${number} answer">${LETTERS.map(letter => `<button type="button" class="bubble-key-letter" data-letter="${letter}" aria-pressed="${question.answer.includes(letter)}" aria-label="Question ${number}: ${letter}">${letter}</button>`).join("")}</div></li>`;
  }

  // The saved key, compact: one line per version and one button to change it (grun:P6).
  function compactMarkup(draft) {
    const [label] = SCORING[view.key.defaults.scoring];
    const own = view.key.versions.reduce((n, v) => n + v.questions.filter(q => q.scoring || (q.points !== undefined && q.points !== "")).length, 0);
    return `<section class="grading-panel bubble-key bubble-key-saved" data-bubble-key aria-labelledby="bubble-key-title">
      <div class="bubble-key-top"><h3 id="bubble-key-title">Answer key</h3><p class="bubble-key-state" role="status" tabindex="-1">${e(stateText(draft))}</p></div>
      <dl class="bubble-key-compact">${view.key.versions.map(v => `<div data-compact-version="${e(v.letter)}"><dt>Version ${e(v.letter)} <small>${e(count(v.questions.length, "question"))}</small></dt><dd>${compactKey(v).map(g => `<span class="bubble-key-chunk"><b>${g.first}</b> ${e(g.letters)}</span>`).join("") || "<small>No answer yet</small>"}</dd></div>`).join("")}</dl>
      <p class="supporting-copy">Each question: ${e(view.key.defaults.points)} ${Number(view.key.defaults.points) === 1 ? "point" : "points"}, ${e(label)}${own ? ` · ${e(count(own, "question"))} with ${own === 1 ? "its" : "their"} own settings` : ""}.</p>
      <div class="grading-actions"><button type="button" class="button" data-edit-key>Edit answer key</button><button type="button" class="button" data-import-key>Import from Gradescope</button></div></section>`;
  }

  function keyMarkup(draft) {
    const version = draft.key.versions.find(v => v.letter === draft.version);
    const read = view.read_questions || 0;
    const shown = draft.all ? view.question_limit : Math.min(view.question_limit, Math.max(read || 25, highest(draft.key), 1));
    const isDirty = dirty(draft);
    const last = draft.key.versions.at(-1);
    const [label, note] = SCORING[draft.key.defaults.scoring];
    return `<section class="grading-panel bubble-key" data-bubble-key aria-labelledby="bubble-key-title">
      <div class="bubble-key-top"><h3 id="bubble-key-title">Answer key</h3>
        <p class="bubble-key-state${isDirty ? " unsaved" : ""}" role="status" tabindex="-1">${e(stateText(draft))}</p></div>
      <p class="section-lead">Click the correct answer of every question on each version, then save.</p>
      <div class="bubble-key-versions"><div class="bubble-version-tabs" role="tablist" aria-label="Answer key versions">${draft.key.versions.map(v => `<button type="button" role="tab" id="bubble-version-${v.letter}" aria-controls="bubble-key-grid" aria-selected="${v.letter === draft.version}" tabindex="${v.letter === draft.version ? 0 : -1}" data-version="${v.letter}">Version ${v.letter}<span>${answered(v)}</span></button>`).join("")}</div>
        <div class="bubble-version-actions">${draft.key.versions.length < 26 ? `<button type="button" class="text-button" data-add-version>+ Add version ${letterOf(draft.key.versions.length)}</button>` : ""}${draft.key.versions.length > 1 ? `<button type="button" class="text-button" data-remove-version>Remove version ${last.letter}</button>` : ""}</div></div>
      <div class="bubble-key-settings"><fieldset class="bubble-key-defaults"><legend>Question grading defaults</legend>
        <label class="field">Points<input name="points" type="number" min="0" max="1000" step="0.01" inputmode="decimal" value="${e(draft.key.defaults.points)}" data-default-points aria-describedby="bubble-default-note"></label>
        <div class="bubble-scoring" role="radiogroup" aria-label="Scoring">${Object.entries(SCORING).map(([key, [name]]) => `<button type="button" role="radio" aria-checked="${draft.key.defaults.scoring === key}" data-default-scoring="${key}">${name}</button>`).join("")}</div>
        <p class="field-hint" id="bubble-default-note"><strong>${e(label)}:</strong> ${e(note)} These apply to every question without its own setting (${icon("settings")}), on every version.</p></fieldset>
        <p class="field-hint bubble-key-howto">Click a letter to make it the answer; click it again to clear it. <kbd>Shift</kbd>-click adds another correct letter (score such a question with Partial or Either/Or). Or type: with a question's letter focused, type its answer and the next question takes focus; <kbd>Backspace</kbd> clears.</p></div>
      <div class="bubble-key-toolbar"><p>Version ${e(version.letter)} · ${count(answered(version), "question")} answered${read ? ` · the sheets were read with ${count(read, "question")}` : ""}</p>
        <div>${shown < view.question_limit ? `<button type="button" class="text-button" data-show-all>Show all ${view.question_limit} questions</button>` : draft.all ? `<button type="button" class="text-button" data-show-fewer>Show fewer questions</button>` : ""}${answered(version) ? `<button type="button" class="text-button" data-clear-version>Clear version ${e(version.letter)}</button>` : ""}</div></div>
      <ol class="bubble-key-grid" id="bubble-key-grid" role="tabpanel" aria-labelledby="bubble-version-${e(version.letter)}">${Array.from({ length: shown }, (_, i) => questionMarkup(version, i + 1)).join("")}</ol>
      <footer class="bubble-key-footer"><p class="supporting-copy">A question without an answer is not scored. Saving records a new revision (earlier ones are kept), and every sheet is scored again from it as read and reviewed in the Inbox.</p>
        <div class="grading-actions">${isDirty && view.key ? '<button type="button" class="button" data-discard>Discard changes</button>' : ""}${!isDirty && view.key ? '<button type="button" class="button" data-close-editor>Done</button>' : ""}<button type="button" class="button" data-import-key>Import from Gradescope</button><button type="button" class="button primary" data-save ${isDirty && !saving ? "" : "disabled"}>Save answer key</button></div></footer></section>`;
  }

  // --- scores: the counts and notices, then a toolbar and a table of at most ROWS rows until all are asked for ---
  function rowsMarkup(draft) {
    const rows = scoreRows(view.sheets, draft), shown = draft.rows ? rows : rows.slice(0, ROWS);
    const row = s => `<tr data-status="${e(s.status)}"><td>${s.student ? e(s.student) : "<em>No student recorded</em>"}</td><td>${e(s.section || "—")}</td><td>${e(s.version || s.marked_version || "—")}</td>
      <td class="bubble-score">${s.status === "scored" ? `<strong>${e(s.earned)}</strong> / ${e(s.possible)}` : `<small>${e(STATUS[s.status])}</small>`}</td>
      <td>${s.status === "scored" ? (s.wrong.length ? e(s.wrong.join(", ")) : "<small>none</small>") : `<small>${e(s.reason)}</small>`}</td>
      <td><a class="bubble-sheet-link" href="#inbox/job/${e(s.job_id)}/sheets" aria-label="Open the scan with ${s.student ? e(s.student) + "’s" : "this"} sheet (page ${e(s.page)})">Page ${e(s.page)}</a></td></tr>`;
    // The shared components (empty state, stat tiles, callouts), never the Inbox bubble panel's rounded ones (review fix GR-4).
    if (!rows.length) return emptyMarkup({kind: "filtered", title: "No sheets in this view.", action: draft.search || draft.filter !== "all" ? {label: "Show every sheet", data: {scoreClear: ""}} : null});
    return `<div class="bubble-table-wrap" role="region" aria-label="Scored bubble sheets" tabindex="0"><table class="bubble-table"><thead><tr><th scope="col">Student</th><th scope="col">Section</th><th scope="col">Version</th><th scope="col">Score</th><th scope="col">Missed</th><th scope="col">Scan</th></tr></thead><tbody>${shown.map(row).join("")}</tbody></table></div>
      ${rows.length > shown.length ? `<p class="bubble-rows-more"><span>Showing ${shown.length} of ${rows.length.toLocaleString()}.</span> <button type="button" class="text-button" data-score-all>Show all ${rows.length.toLocaleString()}</button></p>` : ""}`;
  }

  function scoresMarkup(draft) {
    const c = view.counts, sheets = view.sheets;
    const scored = sheets.filter(s => s.status === "scored");
    const waiting = c.needs_review + c.in_inbox, unscored = c.no_version + c.no_key;
    const average = letter => {
      const group = scored.filter(s => !letter || s.version === letter);
      if (!group.length) return "";
      const earned = group.reduce((sum, s) => sum + Number(s.earned), 0) / group.length, possible = Number(group[0].possible);
      return `${letter ? `Version ${letter}: ` : ""}average ${earned.toFixed(2).replace(/\.?0+$/, "")} of ${group[0].possible}${possible ? ` (${Math.round(earned / possible * 100)}%)` : ""} over ${count(group.length, "sheet")}`;
    };
    const letters = [...new Set(scored.map(s => s.version))].sort();
    const averages = (letters.length > 1 ? letters.map(average) : [average("")]).filter(Boolean);
    const inbox = view.waiting_scans.length ? `<div class="callout" data-tone="info" role="status"><p>${count(c.in_inbox, "sheet")} in ${count(view.waiting_scans.length, "scan")} ${view.waiting_scans.length === 1 ? "is" : "are"} still in the Inbox. They are scored once that scan's review is finished. <a href="#inbox/job/${e(view.waiting_scans[0].job_id)}">Open ${view.waiting_scans.length === 1 ? "it" : "the first"} in the Inbox →</a></p></div>` : "";
    const beyond = view.beyond_read.length ? `<div class="callout" data-tone="warning" role="status"><p><strong>Question${view.beyond_read.length === 1 ? "" : "s"} ${e(view.beyond_read.join(", "))} ${view.beyond_read.length === 1 ? "has" : "have"} an answer, but some sheets were read with only ${count(view.fewest_read, "question")}.</strong> Marks past the question count were ignored, so those sheets cannot earn ${view.beyond_read.length === 1 ? "it" : "them"}. Read the sheets again in the Inbox with the right question count.</p></div>` : "";
    const option = (value, text, current) => `<option value="${value}" ${current === value ? "selected" : ""}>${text}</option>`;
    return `<section class="grading-panel bubble-scores" aria-labelledby="bubble-scores-title"><div class="bubble-key-top"><h3 id="bubble-scores-title">Scored sheets</h3>
        ${view.key && sheets.length ? `<a class="button" ${viewLink(`${base}/scores.csv`, "Scores", e)}>Scores (CSV)</a>` : ""}</div>
      ${sheets.length || c.in_inbox ? `<div class="bubble-score-counts" role="group" aria-label="Scoring results"><span class="stat" data-bubble-count="clean"><strong>${c.scored}</strong><span>Scored</span></span><span class="stat"${waiting ? " data-attention" : ""}><strong>${waiting}</strong><span>Waiting for review</span></span><span class="stat"${unscored ? " data-attention" : ""}><strong>${unscored}</strong><span>Not scored</span></span></div>` : ""}
      ${averages.length ? `<p class="bubble-score-averages">${averages.map(e).join(" · ")}</p>` : ""}${inbox}${beyond}
      ${!sheets.length ? emptyMarkup({title: "No bubble sheets scored yet.", body: c.in_inbox ? "Finish reviewing the scan in the Inbox and its sheets appear here." : "Bubble sheets are read from a scan in the Inbox, under its Bubble sheets step. You can save the key before they arrive."})
        : `<div class="bubble-results-toolbar"><p class="supporting-copy">${view.key ? "Missed lists the questions that did not earn full points." : "Save an answer key to score these sheets."}</p>
          <div class="bubble-results-controls"><label class="field bubble-filter">Find<input type="search" data-score-search data-page-search value="${e(draft.search)}" placeholder="Student or section" aria-label="Find a student or section" autocomplete="off"></label>
          <label class="field bubble-filter">Show<select data-score-filter aria-label="Show sheets">${option("all", `All (${sheets.length})`, draft.filter)}${option("scored", `Scored (${c.scored})`, draft.filter)}${option("unscored", `Not scored (${sheets.length - c.scored})`, draft.filter)}</select></label>
          <label class="field bubble-filter">Order<select data-score-sort aria-label="Order sheets">${option("scan", "Scan order", draft.sort)}${option("name", "Name", draft.sort)}${option("low", "Score, lowest first", draft.sort)}${option("high", "Score, highest first", draft.sort)}</select></label></div></div>
        <div data-score-rows>${rowsMarkup(draft)}</div>`}</section>`;
  }

  function drawRows() {
    const slot = scoresHost.querySelector("[data-score-rows]");
    if (slot) slot.innerHTML = rowsMarkup(state());
  }
  function drawScores() {
    if (!active() || !scoresHost.isConnected) return;
    scoresHost.innerHTML = scoresMarkup(state());
  }
  scoresHost.addEventListener("input", event => {
    if (!event.target.matches("[data-score-search]")) return;
    const draft = state();
    draft.search = event.target.value;
    draft.rows = false;
    drawRows();
  });
  scoresHost.addEventListener("change", event => {
    const draft = state();
    if (event.target.matches("[data-score-filter]")) draft.filter = event.target.value;
    else if (event.target.matches("[data-score-sort]")) draft.sort = event.target.value;
    else return;
    draft.rows = false;
    drawRows();
  });
  scoresHost.addEventListener("click", event => {
    const draft = state();
    if (event.target.closest("[data-score-all]")) {
      draft.rows = true;
      drawRows();
      scoresHost.querySelector(`tbody tr:nth-child(${ROWS + 1}) a`)?.focus();
    } else if (event.target.closest("[data-score-clear]")) {
      Object.assign(draft, { search: "", filter: "all", rows: false });
      drawScores();
      scoresHost.querySelector("[data-score-search]")?.focus();
    }
  });

  function drawKey({ focus } = {}) {
    if (!active() || !host.isConnected) return;
    const draft = state();
    host.innerHTML = editing(draft) ? keyMarkup(draft) : compactMarkup(draft);
    bind(draft);
    if (focus) host.querySelector(focus)?.focus();
  }
  // After a save or on first load: every region reads the new view.
  function draw({ focus } = {}) {
    drawHead();
    drawKey({ focus });
    drawScores();
    analysis.update(view);
  }

  function setAnswer(draft, number, letter, add) {
    const version = draft.key.versions.find(v => v.letter === draft.version);
    const question = version.questions[number] || (version.questions[number] = { number, answer: "" });
    const chosen = new Set(question.answer);
    if (letter === null) chosen.clear();
    else if (add) chosen.has(letter) ? chosen.delete(letter) : chosen.add(letter);
    else if (question.answer === letter) chosen.clear();
    else { chosen.clear(); chosen.add(letter); }
    question.answer = LETTERS.filter(l => chosen.has(l)).join("");
    changed(draft);
  }

  function questionDialog(draft, number) {
    const version = draft.key.versions.find(v => v.letter === draft.version);
    const question = version.questions[number] || { number, answer: "" };
    const defaults = draft.key.defaults;
    h.dialog(`Question ${number}`, `Version ${version.letter}. Leave points or scoring on the default to follow the question defaults.`,
      `<fieldset class="bubble-key-dialog-letters"><legend>Correct answer</legend>${LETTERS.map(letter => `<label class="grading-check"><input type="checkbox" name="letter" value="${letter}" ${question.answer.includes(letter) ? "checked" : ""}>${letter}</label>`).join("")}</fieldset>
       <label class="field">Points<input name="points" type="number" min="0" max="1000" step="0.01" inputmode="decimal" value="${e(question.points ?? "")}" placeholder="${e(defaults.points)} (default)" aria-label="Points"></label>
       ${h.select("Scoring", "scoring", [["", `Default (${SCORING[defaults.scoring][0]})`], ...Object.entries(SCORING).map(([key, [name]]) => [key, name])], question.scoring || "")}
       <p class="field-hint">${Object.values(SCORING).map(([name, text]) => `<strong>${e(name)}:</strong> ${e(text)}`).join("<br>")}</p>`,
      "Apply", async values => {
        const points = String(values.get("points") || "").trim();
        if (points && !(Number(points) >= 0 && Number(points) <= 1000)) throw new Error("Points must be between 0 and 1000.");
        version.questions[number] = { number, answer: values.getAll("letter").join(""), ...(points ? { points } : {}), ...(values.get("scoring") ? { scoring: values.get("scoring") } : {}) };
        changed(draft);
        h.close();
        drawKey({ focus: `[data-question-settings="${number}"]` });
      });
  }

  function bind(draft) {
    // GRADESCOPE "IMPORTING RUBRICS FROM GRADESCOPE" (2026-10-25): the bubble-sheet exam's key read from Gradescope and saved as a new
    // revision; this page's draft then follows the saved key.
    host.querySelector("[data-import-key]")?.addEventListener("click", () => importFromGradescope(plan, h, () => { drafts.delete(plan.id); return refresh(); }, active, { key: true }));
    host.querySelector("[data-edit-key]")?.addEventListener("click", () => { draft.editing = true; drawKey({ focus: `[data-version="${draft.version}"]` }); });
    host.querySelector("[data-close-editor]")?.addEventListener("click", () => { draft.editing = false; drawKey({ focus: "[data-edit-key]" }); });
    const grid = host.querySelector(".bubble-key-grid");
    if (!grid) return;
    grid.addEventListener("click", event => {
      const settings = event.target.closest("[data-question-settings]");
      if (settings) return questionDialog(draft, Number(settings.dataset.questionSettings));
      const button = event.target.closest(".bubble-key-letter");
      if (!button) return;
      const number = Number(button.closest("[data-number]").dataset.number);
      setAnswer(draft, number, button.dataset.letter, event.shiftKey || event.ctrlKey || event.metaKey);
      drawKey({ focus: `[data-number="${number}"] [data-letter="${button.dataset.letter}"]` });
    });
    // Typing a key from a printed list: a letter answers the focused question and moves on to the next one.
    grid.addEventListener("keydown", event => {
      const item = event.target.closest("[data-number]");
      if (!item || event.altKey || event.ctrlKey || event.metaKey) return;
      const number = Number(item.dataset.number), key = event.key.toUpperCase();
      if (LETTERS.includes(key)) {
        event.preventDefault();
        setAnswer(draft, number, key, event.shiftKey);
        const next = host.querySelector(`[data-number="${number + 1}"]`) ? number + 1 : number;
        drawKey({ focus: `[data-number="${next}"] [data-letter="${next === number ? key : "A"}"]` });
      } else if (event.key === "Backspace" || event.key === "Delete") {
        event.preventDefault();
        setAnswer(draft, number, null, false);
        drawKey({ focus: `[data-number="${number}"] [data-letter="A"]` });
      }
    });
    const tabs = [...host.querySelectorAll('[role="tab"][data-version]')];
    for (const tab of tabs) {
      tab.onclick = () => { draft.version = tab.dataset.version; drawKey({ focus: `[data-version="${draft.version}"]` }); };
      tab.onkeydown = event => {
        const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
        if (!step) return;
        event.preventDefault();
        const next = tabs[(tabs.indexOf(tab) + step + tabs.length) % tabs.length];
        draft.version = next.dataset.version;
        drawKey({ focus: `[data-version="${draft.version}"]` });
      };
    }
    host.querySelector("[data-add-version]")?.addEventListener("click", () => {
      draft.key.versions.push({ letter: letterOf(draft.key.versions.length), questions: {} });
      draft.version = draft.key.versions.at(-1).letter;
      changed(draft);
      drawKey({ focus: `[data-version="${draft.version}"]` });
    });
    host.querySelector("[data-remove-version]")?.addEventListener("click", () => {
      const removed = draft.key.versions.pop();
      if (draft.version === removed.letter) draft.version = draft.key.versions.at(-1).letter;
      changed(draft);
      h.toast(`Version ${removed.letter} removed from the draft. Save to keep this, or discard the changes.`);
      drawKey();
    });
    host.querySelector("[data-clear-version]")?.addEventListener("click", () => {
      draft.key.versions.find(v => v.letter === draft.version).questions = {};
      changed(draft);
      drawKey();
    });
    host.querySelector("[data-show-all]")?.addEventListener("click", () => { draft.all = true; drawKey({ focus: "[data-show-fewer]" }); });
    host.querySelector("[data-show-fewer]")?.addEventListener("click", () => { draft.all = false; drawKey({ focus: "[data-show-all]" }); });
    const points = host.querySelector("[data-default-points]");
    points.addEventListener("input", () => {
      draft.key.defaults.points = points.value;
      changed(draft);
      // Typing keeps its focus: only the save state follows. The full key is drawn again on the next click.
      const status = host.querySelector(".bubble-key-state");
      status.textContent = stateText(draft);
      status.classList.toggle("unsaved", dirty(draft));
      host.querySelector("[data-save]").disabled = saving || !dirty(draft);
    });
    for (const choice of host.querySelectorAll("[data-default-scoring]")) {
      choice.onclick = () => { draft.key.defaults.scoring = choice.dataset.defaultScoring; changed(draft); drawKey({ focus: `[data-default-scoring="${choice.dataset.defaultScoring}"]` }); };
    }
    host.querySelector("[data-discard]")?.addEventListener("click", () => {
      const kept = { filter: draft.filter, search: draft.search, sort: draft.sort };
      drafts.delete(plan.id);
      Object.assign(state(), kept, { editing: true });
      drawKey({ focus: ".bubble-key-state" });
    });
    host.querySelector("[data-save]").addEventListener("click", event => h.run(event.currentTarget, async () => {
      if (saving) return;
      if (!draft.requestId) draft.requestId = crypto.randomUUID();
      const submitted = payload(draft.key);
      const body = { ...submitted, base_revision: draft.base, request_id: draft.requestId };
      saving = true;
      try {
        const next = await h.api(`${base}/key`, "POST", body);
        if (!active()) return;
        const current = state(), edited = !same(payload(current.key), submitted);
        view = next;
        Object.assign(current, { base: view.key.revision, requestId: "", editing: edited });
        if (edited) {
          // FRONTEND: a save response cannot replace changes typed after that save began.
          const status = host.querySelector(".bubble-key-state");
          if (status) { status.textContent = stateText(current); status.classList.toggle("unsaved", dirty(current)); }
          drawHead(); drawScores(); analysis.update(view);
        } else {
          current.key = fromSaved(view.key, view.suggested_versions);
          draw({ focus: ".bubble-key-state" });
        }
        h.toast(`Answer key saved as revision ${view.key.revision}. ${count(view.counts.scored, "sheet")} scored.${edited ? " Newer edits are still unsaved." : ""}`);
      } finally {
        saving = false;
        const button = host.querySelector("[data-save]");
        if (button && active()) button.disabled = !dirty(state());
      }
    }));
  }

  draw();
  // Upload only (requirement, 2026-09-25): the matched sheets go to Gradescope's bubble-sheet exam, which scores them itself.
  void mountGradescope(outer.querySelector("[data-gradescope-host]"), plan, h, active, "sheets", {onStatus: status => {
    sheetsOnGradescope = status?.configured && status.coverage?.sheets?.matched ? status.coverage.sheets : null;
    drawHead();
  }});
}
