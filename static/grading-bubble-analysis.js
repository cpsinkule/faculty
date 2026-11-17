// Bubble sheet item analysis (Grading → an assignment → Bubble sheets): how each keyed question went, one version at a time.
// Read only: it is drawn from GET …/bubble-sheets/analysis (grading/bubble_analysis.py), which changes, decides and approves
// nothing; the instructor corrects a key in the key editor above it, which a question here opens. The panel is a closed
// disclosure whose first opening reads the analysis; the answer is kept in browser memory per plan and read again only when the
// saved key or the scored sheets change (a key saved here or elsewhere, a scan cleared since). No student is named in it.
import { calloutMarkup, disclosureMarkup, emptyMarkup, loadingMarkup } from "./ui.js";
import { viewLink } from "./file-viewer.js";

const analyses = new Map(); // grading plan ID -> {open, signature, data}
const LETTERS = ["A", "B", "C", "D", "E"];
export const FLAG_LABELS = { possible_miskey: "Possible miskey", hard: "Hard", easy: "Easy", weak: "Weak" };
const CHOICE_LABELS = { blank: "Blank", multiple: "Several" };
const SCORING_LABELS = { exact: "Exact", partial: "Partial", either: "Either/Or" };

// What the analysis was read from: the saved key's revision and every scored sheet's version, answers and total. The same
// signature means the same analysis, so the panel keeps what it read; anything else reads it again.
export function analysisSignature(view) {
  let hash = 0x811c9dc5;
  const scored = (view?.sheets || []).filter(s => s.status === "scored");
  for (const s of scored) {
    for (const char of `${s.sheet_id}:${s.version}:${s.earned}:${(s.answers || []).join(",")}|`) hash = Math.imul(hash ^ char.charCodeAt(0), 0x01000193) >>> 0;
  }
  return `${view?.key?.revision ?? "none"}:${scored.length}:${hash.toString(16)}`;
}

// A percent as the page writes it: 50%, 78.5%.
export const percentText = value => value === null || value === undefined ? "—" : `${Number(Number(value).toFixed(1))}%`;
// Discrimination to two decimals with a true minus sign; "—" when it is not computed.
export const discriminationText = value => value === null || value === undefined ? "—" : Number(value).toFixed(2).replace("-", "−");

// What alpha says, in one plain line. Short quizzes run low even when every question is sound.
export function reliabilityText(alpha) {
  if (alpha >= .8) return "the questions rank students consistently";
  if (alpha >= .7) return "the questions rank students fairly consistently";
  if (alpha >= .5) return "the questions agree only moderately on who knows the material (short quizzes often do)";
  return "the questions agree little on who knows the material; look at the weak and possibly miskeyed ones";
}

// The version's lead line: its sheets, their average, and its reliability or why there is none yet.
export function versionLead(version, minimum = 10) {
  if (!version.sheets) return "No scored sheets for this version yet.";
  const parts = [`${version.sheets.toLocaleString()} ${version.sheets === 1 ? "sheet" : "sheets"}`, `average ${percentText(version.mean_pct)}${version.sd_pct !== null && version.sd_pct !== undefined && version.sheets > 1 ? ` (SD ${Number(version.sd_pct.toFixed(1))})` : ""}`];
  if (version.alpha !== null && version.alpha !== undefined) parts.push(`reliability α ${version.alpha.toFixed(2)}: ${reliabilityText(version.alpha)}`);
  else if (version.too_few) parts.push(`reliability and discrimination need at least ${minimum} scored sheets`);
  else parts.push("reliability is not computed: the totals do not vary");
  return parts.join(" · ");
}

// Every question the analysis calls possibly miskeyed, version by version in question order.
export function miskeyed(analysis) {
  return (analysis?.versions || []).flatMap(v => v.questions.flatMap(q => q.flags.filter(f => f.kind === "possible_miskey").map(f => ({ letter: v.letter, number: q.number, text: f.text }))));
}

// A question's choices as bars: A to E always, Blank and Several only when marked; each bar its share of the version's sheets.
export function choiceRows(question) {
  return question.choices.filter(c => LETTERS.includes(c.choice) || c.count > 0).map(c => ({ ...c, label: CHOICE_LABELS[c.choice] || c.choice, share: question.sheets ? c.count / question.sheets : 0 }));
}

function choiceTitle(c, question, size) {
  const what = c.choice === "multiple" ? `Several letters${c.common ? ` (most often ${c.common}, ${c.common_count})` : ""}` : c.choice === "blank" ? "Blank" : `${c.choice}${c.key ? " (the key)" : ""}`;
  const parts = [`${what}: ${c.count} of ${question.sheets} ${question.sheets === 1 ? "sheet" : "sheets"}`];
  if (c.count && c.mean_pct !== null) parts.push(`averaging ${percentText(c.mean_pct)} overall`);
  if (size && c.upper !== null) parts.push(`${c.upper} of the top ${size}, ${c.lower} of the bottom ${size}`);
  return parts.join(" · ");
}

function questionRow(version, question, e) {
  const look = `${version.letter}:${question.number}`;
  const full = question.sheets ? `${percentText(question.full_pct)}${question.difficulty !== null && Math.abs(question.difficulty * 100 - question.full_pct) >= .05 ? ` <small>avg ${e(percentText(question.difficulty * 100))}</small>` : ""}` : "—";
  const choices = choiceRows(question).map(c => `<li class="bubble-choice"${c.key ? " data-key" : ""} title="${e(choiceTitle(c, question, version.group_size))}"><span class="bubble-choice-label">${e(c.label)}${c.key ? '<span class="sr-only"> (the key)</span>' : ""}</span><span class="bubble-choice-track" aria-hidden="true"><span class="bubble-choice-bar" data-share="${c.share}"></span></span><span class="bubble-choice-count">${e(c.count)}</span></li>`).join("");
  const flags = question.flags.map(f => `<span class="chip" data-tone="${f.kind === "possible_miskey" ? "warning" : "neutral"}" title="${e(f.text)}">${e(FLAG_LABELS[f.kind] || f.kind)}</span>`).join("");
  const scoring = question.scoring !== "exact" ? ` <small>${e(SCORING_LABELS[question.scoring] || question.scoring)}</small>` : "";
  return `<tr data-analysis-question="${e(look)}"><th scope="row"><button type="button" class="bubble-analysis-jump" data-analysis-look="${e(look)}" title="Open question ${e(question.number)} in the answer key">${e(question.number)}</button></th>
    <td>${e(question.answer)}${scoring}</td><td class="bubble-score" title="${e(`${question.full} of ${question.sheets} sheets earned full points`)}">${full}</td>
    <td class="bubble-score">${e(discriminationText(question.discrimination))}</td>
    <td class="bubble-score">${question.upper_pct === null ? "—" : `${e(percentText(question.upper_pct))} · ${e(percentText(question.lower_pct))}`}</td>
    <td><ul class="bubble-choices" aria-label="Choices on question ${e(question.number)}">${choices}</ul></td><td>${flags ? `<span class="chip-row">${flags}</span>` : ""}</td></tr>`;
}

function versionMarkup(analysis, version, several, e) {
  const flagged = version.questions.filter(q => q.flags.some(f => f.kind === "possible_miskey"));
  const miskeys = flagged.length ? `<div class="callout bubble-analysis-miskeys" data-tone="warning" role="status"><strong class="callout-title">Possibly miskeyed</strong><p>Check the key of ${flagged.length === 1 ? "this question" : "these questions"} before you certify.</p><ul>${flagged.map(q => `<li><button type="button" class="text-button" data-analysis-look="${e(version.letter)}:${q.number}">${several ? `Version ${e(version.letter)} · ` : ""}Question ${e(q.number)}</button> ${e(q.flags.find(f => f.kind === "possible_miskey").text)}</li>`).join("")}</ul></div>` : "";
  const table = version.questions.length ? `<div class="bubble-table-wrap" role="region" aria-label="Item analysis${several ? `, version ${e(version.letter)}` : ""}" tabindex="0"><table class="bubble-table bubble-analysis-table"><thead><tr>
      <th scope="col">Q</th><th scope="col">Key</th>
      <th scope="col" title="The share of sheets that earned the question's full points (and, under partial credit, the average share of its points).">Full credit</th>
      <th scope="col" title="How much better the sheets that did well overall did on this question than the rest, from −1 to 1. Above 0.3 is good; near 0 tells little apart; below 0 is worth a look at the key.">Discrimination</th>
      <th scope="col" title="Full credit among the top 27% and the bottom 27% of sheets by total score.">Top · bottom</th>
      <th scope="col" title="How many sheets marked each letter alone, left it blank, or marked several; the key's letters are drawn in ink.">Choices</th><th scope="col">Flags</th></tr></thead>
      <tbody>${version.questions.map(q => questionRow(version, q, e)).join("")}</tbody></table></div>` : `<p class="supporting-copy">Version ${e(version.letter)} has no answers in the key yet.</p>`;
  return `<section class="bubble-analysis-version" data-analysis-version="${e(version.letter)}" aria-label="Version ${e(version.letter)}">${several ? `<h4>Version ${e(version.letter)}</h4>` : ""}
    <p class="bubble-analysis-lead">${e(versionLead(version, analysis.minimum))}</p>${miskeys}${version.sheets ? table : ""}</section>`;
}

export function analysisMarkup(analysis, e, base) {
  const several = analysis.versions.length > 1;
  const about = disclosureMarkup({ label: "How to read this", body: `<p>Each version is read on its own, since versions may put their questions in another order. <strong>Full credit</strong> is how many sheets earned the question's points. <strong>Discrimination</strong> compares each sheet's answer with the rest of its score: a question the strong sheets got right and the weak ones missed scores near 1, and a question the strong sheets missed more often scores below 0, which is often a wrong key. <strong>Top · bottom</strong> compares the best and the weakest 27% of sheets directly. <strong>Reliability</strong> (Cronbach's α) says how consistently all the questions together rank students. A question is flagged as possibly miskeyed when more of the top sheets chose one other letter than earned its points, or when its discrimination is below −0.1. Nothing here changes a score or the key: change the key in the editor above and save it.</p>` });
  return `<div class="bubble-analysis-toolbar"><p class="supporting-copy">How each keyed question went on the scored sheets, to catch a wrong key before you certify.</p>
      <a class="button" ${viewLink(`${base}/analysis.csv`, "Item analysis", e)}>Item analysis (CSV)</a></div>
    ${analysis.versions.map(v => versionMarkup(analysis, v, several, e)).join("")}${about}`;
}

// The Item analysis panel. `update(view)` gives it the Bubble sheets page's current read (on mount and after a key is saved);
// `onLook(letter, number)` opens that question in the key editor.
export function mountBubbleAnalysis(host, plan, h, active, { base, onLook }) {
  const e = h.e;
  let memory = analyses.get(plan.id);
  if (!memory) analyses.set(plan.id, memory = { open: false, signature: "", data: null });
  let view = null, pending = "", drawn = null;
  host.innerHTML = `<details class="disclosure grading-panel grading-tools bubble-analysis" data-bubble-analysis-fold${memory.open ? " open" : ""}><summary><span id="bubble-analysis-title">Item analysis</span><span class="disclosure-hint" data-analysis-hint>Per question</span></summary><div class="disclosure-body" data-analysis-body></div></details>`;
  const fold = host.querySelector("details"), body = host.querySelector("[data-analysis-body]"), hint = host.querySelector("[data-analysis-hint]");

  // The summary names the possibly miskeyed questions of the analysis it holds, while that analysis is still the page's.
  const hintFor = data => {
    const count = data ? miskeyed(data).length : 0;
    hint.textContent = count ? `${count} possibly miskeyed` : "Per question";
  };
  function draw(markup) {
    if (markup === drawn) return;
    drawn = markup;
    body.innerHTML = markup;
    // Bar lengths through the CSSOM: the page's CSP forbids style attributes in markup.
    for (const bar of body.querySelectorAll("[data-share]")) bar.style.inlineSize = `${Math.max(0, Math.min(1, Number(bar.dataset.share))) * 100}%`;
  }
  async function show() {
    if (!view || !active() || !host.isConnected) return;
    const signature = analysisSignature(view);
    if (pending && pending !== signature) pending = "";
    hintFor(memory.data && memory.signature === signature ? memory.data : null);
    if (!fold.open) return;
    if (!view.key) return draw(emptyMarkup({ title: "No answer key saved yet.", body: "Save the key and this shows how each question went on the scored sheets." }));
    if (!view.counts.scored) return draw(emptyMarkup({ title: "No scored sheets yet.", body: "The analysis appears once sheets are scored against the key." }));
    if (memory.data && memory.signature === signature) return draw(analysisMarkup(memory.data, e, base));
    if (pending === signature) return;
    pending = signature;
    // The first read shows where the analysis is coming; a read after a new key keeps the earlier one on screen until it answers.
    if (body.querySelector(".bubble-analysis-toolbar")) body.setAttribute("aria-busy", "true");
    else draw(loadingMarkup("Reading the item analysis…", 3));
    try {
      const data = await h.api(`${base}/analysis`);
      if (pending !== signature) return; // a newer read of the page superseded this one
      Object.assign(memory, { signature, data });
      if (!active() || !host.isConnected) return;
      hintFor(data);
      if (fold.open) draw(analysisMarkup(data, e, base));
    } catch (error) {
      if (pending === signature && active() && host.isConnected) draw(calloutMarkup({ tone: "danger", title: "The item analysis could not be read.", body: error.message, action: { label: "Try again", data: { analysisRetry: "" } } }));
    } finally {
      if (pending === signature) {
        pending = "";
        body.removeAttribute("aria-busy");
      }
    }
  }
  fold.addEventListener("toggle", () => { memory.open = fold.open; void show(); });
  body.addEventListener("click", event => {
    if (event.target.closest("[data-analysis-retry]")) return void show();
    const look = event.target.closest("[data-analysis-look]");
    if (!look) return;
    const [letter, number] = look.dataset.analysisLook.split(":");
    onLook(letter, Number(number));
  });
  return { update(next) { view = next; void show(); } };
}
