// Files for the instructor's own records and the training downloads, a secondary section of Certification and Export (they were
// the Export step's, requirement, 2026-09-26). One click per file; each download is also an export record (`frq_exports`). Downloading publishes and
// sends nothing. The Gradescope Entry View is not offered: Send to Gradescope does that, and the per-question CSV holds the same
// selections (the export API still serves it).
const FILES = {csv: "approved-grades.csv", totals: "assignment-totals.csv", audit: "grading-audit.json", dataset: "grading-dataset.zip"};

function fileRow(kind, label, what, h, disabled = false) {
  return `<li><button type="button" class="button" data-export-kind="${h.e(kind)}"${disabled ? " disabled" : ""}>${h.e(label)}</button><span>${what}</span></li>`;
}

// `final`: how many responses have a final grade (the plan's own workflow counts); nothing to download before the first.
export function downloadsMarkup(final, h) {
  const none = !final;
  return `<section class="grading-panel grading-export-card" data-export-downloads><h3>Grades for your records</h3>
    <p class="supporting-copy">${none ? "Nothing to download until a grade is final." : "The final grades, checked again against the current papers and rubrics. Downloading publishes and sends nothing."}</p>
    <ul class="grading-export-list">${fileRow("csv", "Scores per question", "CSV · a row per student and question: score, rubric items, adjustments, comment", h, none)}${fileRow("totals", "Totals per student", "CSV · a row per student: the assignment total, or a subtotal", h, none)}${fileRow("audit", "Complete record", "JSON · everything behind each grade: readings, model steps and receipts", h, none)}</ul></section>`;
}

// Every grade is saved for training when it is made (`grading/training.py`); this only says how many and offers the downloads.
export function trainingMarkup(plan, training, h) {
  const mine = training?.assignment, all = training?.workspace, count = (n, one, many) => `${h.e(Number(n).toLocaleString())} ${n === 1 ? one : many}`;
  const saved = !training ? "Faculty could not count the saved grades."
    // "Grading decisions", not "grades": every decision is kept, a changed grade's earlier one too, so this is not the page's grade
    // count (usability audit, 2026-10-04: "934 grades saved: 15 decided by you" beside "14 / 933 decided by you" read as a mismatch).
    : mine.examples ? `${count(mine.examples, "grading decision", "grading decisions")} saved, earlier decisions on changed grades included: ${h.e(Number(mine.instructor).toLocaleString())} by you, ${h.e(Number(mine.automatic).toLocaleString())} finalized automatically.${mine.waiting ? ` ${count(mine.waiting, "more is", "more are")} being saved.` : ""}`
    : "No grade of this assignment is saved yet.";
  const rows = [mine?.examples ? `<li><a class="button" href="/api/grading/assignments/${h.e(plan.id)}/training.zip" download data-training-download>This assignment</a><span>ZIP · ${count(mine.examples, "grade", "grades")} with their answer crops</span></li>` : "",
    all?.examples ? `<li><a class="button" href="/api/grading/training.zip" download data-training-download-all>All assignments</a><span>ZIP · ${count(all.examples, "grade", "grades")} across every course and term</span></li>` : ""].join("");
  return `<section class="grading-panel grading-export-card" data-training-panel><h3>Saved for training</h3>
    <p class="section-lead">${saved}</p>
    <p class="supporting-copy">Kept automatically as each grade is made, to train or evaluate models later. There is nothing to do here unless you want the files.</p>
    ${rows ? `<ul class="grading-export-list">${rows}</ul>` : ""}
    <details class="disclosure grading-export-about"><summary><span>What the files hold</span></summary>
      <p>Each grade with its answer crops, readings and transcript, the rubric revision it was graded under, and the AI's and the final rubric lines and scores. A later grade of the same response never replaces an earlier one; the file marks which one counts. Students appear under an opaque key, each kept in one train, calibration or test split, and automatic grades are marked because nobody looked at them. The file is prepared before the download starts, about ten seconds per thousand grades. <code>manage_grading.py finetune-export</code> turns it into fine-tuning examples.</p>
      <ul class="grading-export-list">${fileRow("dataset", "Model comparison set", "ZIP · your own decisions only, with every model's proposal on the same responses, for <code>manage_grading.py benchmark</code>", h)}</ul>
    </details></section>`;
}

async function download(plan, kind) {
  const response = await fetch(`/api/grading/assignments/${plan.id}/export`, {method: "POST", cache: "no-store", headers: {"Content-Type": "application/json", "X-Teaching-Request": "1"}, body: JSON.stringify({kind})});
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || "The download could not be prepared.");
  }
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = FILES[kind]; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// Binds every download button under root (property handlers, so a patched redraw keeps them).
export function bindDownloads(root, plan, h) {
  root.querySelectorAll("[data-export-kind]").forEach(button => { button.onclick = () => h.run(button, () => download(plan, button.dataset.exportKind)); });
}
