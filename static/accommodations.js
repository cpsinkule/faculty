import { captureDialogIntent, dialogSession, wallDate, disclosureMarkup, tableMarkup, calloutMarkup, markBusy, setBusy } from "./ui.js";

/** The teaching term as the import dialogs and the page word it. */
export const termLabel = (item) => `${item.term === "Other" ? item.term_other : item.term} ${item.year}`;

/** The import dialogs' term field: a choice only when more than one term is current, otherwise stated. Read back as the form field `term`. */
function termFieldMarkup(terms, escape) {
  return terms.length > 1
    ? `<label class="field">Teaching term<select name="term"><option value="null" selected>All current terms</option>${terms.map((term) => `<option value="${escape(JSON.stringify(term))}">${escape(termLabel(term))}</option>`).join("")}</select></label>`
    : `<input type="hidden" name="term" value="${escape(terms.length ? JSON.stringify(terms[0]) : "null")}">${terms.length ? `<p class="accommodation-import-term">Term <strong>${escape(termLabel(terms[0]))}</strong></p>` : ""}`;
}

const METADATA_SHOWN = ["access coordinator", "access coordinator email", "request status", "notes", "campus", "title", "submitted on"];

/** One accommodation as it reads: the column name verbatim, and its value unless the export only says "Yes". */
export function accommodationLabel(item) {
  return `${item.name}${String(item.value || "").toLowerCase() === "yes" ? "" : `: ${item.value}`}`;
}

/**
 * The Accommodations page's cell: every active accommodation inline as a tag (names verbatim, escaped), then a small
 * closed disclosure for the coordinator, notes and source. `open` keeps a disclosure the instructor opened across redraws.
 */
export function accommodationTags(record, escape, { key = "", open = false } = {}) {
  if (!record) return '<span class="field-hint">—</span>';
  const metadata = Object.entries(record.metadata || {}).filter(([name, value]) => value && METADATA_SHOWN.includes(name.toLowerCase()));
  const tags = record.items.length
    ? `<ul class="accommodation-tags">${record.items.map((item) => `<li class="chip" data-tone="info">${escape(accommodationLabel(item))}</li>`).join("")}</ul>`
    : '<p class="field-hint">No active accommodations</p>';
  return `${tags}<details class="disclosure accommodation-source"${key ? ` data-key="${escape(key)}"` : ""}${open ? " open" : ""}><summary>Coordinator, notes &amp; source</summary>
    ${metadata.map(([name, value]) => `<p><strong>${escape(name)}:</strong> ${escape(value)}</p>`).join("")}
    <p class="field-hint">Updated ${escape(record.updated_at ? wallDate(record.updated_at.slice(0, 10), { year: true }) : "date not supplied")}</p><a href="#inbox/job/${escape(record.job_id)}">View import record</a></details>`;
}

export function scheduleTime(record) {
  const time = value => { const [hour, minute] = value.split(":"); return `${Number(hour) % 12 || 12}:${minute} ${Number(hour) < 12 ? "AM" : "PM"}`; };
  return `${time(record.start_time)}–${time(record.end_time)}`;
}

/** DR-center wall-clock values, never converted: 'Test 1 · Thu, Sep 17 · 7:05 PM–8:58 PM · 113 min · College Station'. */
export function scheduleText(record) {
  return `${record.exam_type} · ${wallDate(record.date)} · ${scheduleTime(record)} · ${record.length_minutes} min${record.campus ? ` · ${record.campus}` : ""}`;
}

/** The schedule's parts, the ones that differ from `previous` marked (both escaped). */
export function schedulePartsMarkup(record, previous, escape) {
  const read = (r) => ({ exam_type: r.exam_type, date: wallDate(r.date), time: scheduleTime(r), length: `${r.length_minutes} min`, campus: r.campus || "" });
  const now = read(record), before = previous ? read(previous) : null;
  return Object.entries(now).filter(([, value]) => value).map(([name, value]) => before && before[name] !== value ? `<mark>${escape(value)}</mark>` : escape(value)).join(" · ");
}

/** What changed from one schedule to the next, as 'Date: Thu, Sep 17 → Fri, Sep 18' lines (text). */
export function scheduleChanges(record, previous) {
  if (!previous) return [];
  const fields = [
    ["Exam", (r) => r.exam_type], ["Date", (r) => wallDate(r.date)], ["Time", (r) => scheduleTime(r)],
    ["Length", (r) => `${r.length_minutes} min`], ["Campus", (r) => r.campus || "—"],
    ["Requested on", (r) => r.requested_on ? wallDate(r.requested_on, { year: true }) : "—"],
  ];
  return fields.filter(([, read]) => read(record) !== read(previous)).map(([label, read]) => `${label}: ${read(previous)} → ${read(record)}`);
}

const BLOCKED = ["unmatched", "ambiguous", "stale"];
// The server's message when the other export reaches an importer (accommodation_import.DR_HINT, dr_exam_import.ACCOMMODATION_HINT).
export const WRONG_EXPORT = {
  exams: /Use Import accommodations\.?$/,
  accommodations: /Use Import DR exam bookings\.?$/,
};

/** The preview, problems first: rows that can't be imported grouped by reason, then new, changed, and unchanged folded. */
export function previewMarkup(preview, exams, escape) {
  const counts = preview.counts;
  const matched = counts.added + counts.updated + counts.unchanged;
  const blocked = preview.rows.filter((row) => BLOCKED.includes(row.status));
  const place = (row) => row.label || `${row.code} · Section ${row.section}`;
  // 'Last, First' as the rosters and the DR table show it (added by the server's preview for display only).
  const who = (row) => row.display_name || row.name;
  const what = (row) => exams ? scheduleText(row.schedule) : (row.accommodations || []).map(accommodationLabel).join("; ") || "No active accommodations";
  const changing = counts.added + counts.updated;
  const parts = [`<h3>${matched} of ${preview.total} rows match your rosters</h3>`,
    `<p class="accommodation-preview-summary"><strong>${changing} will be imported</strong>${blocked.length ? ` · ${blocked.length} can’t be` : ""}</p>`,
    `<p class="field-hint">${counts.added} new · ${counts.updated} updated · ${counts.unchanged} unchanged</p>`];
  if (!changing && !blocked.length && matched) parts.push(calloutMarkup({ tone: "success", title: "Everything in this file is already saved.", body: "Importing it again changes nothing." }));
  if (blocked.length) {
    const reasons = new Map();
    for (const row of blocked) reasons.set(row.reason, [...(reasons.get(row.reason) || []), row]);
    parts.push(`<section class="accommodation-preview-group" data-tone="warning"><h4>Can’t be imported <span class="chip" data-tone="warning">${blocked.length}</span></h4>${[...reasons].map(([reason, rows]) => `<p class="accommodation-preview-reason"><strong>${escape(reason)}</strong> (${rows.length})</p><ul>${rows.map((row) => `<li>${escape(who(row))} · ${escape(place(row))} · row ${escape(row.row)}</li>`).join("")}</ul>`).join("")}</section>`);
  }
  const added = preview.rows.filter((row) => row.status === "added");
  if (added.length) {
    parts.push(`<section class="accommodation-preview-group"><h4>New <span class="chip">${added.length}</span></h4>${tableMarkup(escape, ["Student", "Course & section", exams ? "Booking" : "Accommodations"],
      added.map((row) => `<tr><td>${escape(who(row))}</td><td>${escape(place(row))}</td><td>${escape(what(row))}</td></tr>`), "New rows")}</section>`);
  }
  const updated = preview.rows.filter((row) => row.status === "updated");
  if (updated.length) {
    parts.push(`<section class="accommodation-preview-group"><h4>Changed <span class="chip">${updated.length}</span></h4>${tableMarkup(escape, ["Student", "Course & section", "Previously", "Now"],
      updated.map((row) => exams
        ? `<tr><td>${escape(who(row))}</td><td>${escape(place(row))}</td><td>${row.previous_schedule ? escape(scheduleText(row.previous_schedule)) : "—"}</td><td>${schedulePartsMarkup(row.schedule, row.previous_schedule, escape)}</td></tr>`
        : `<tr><td>${escape(who(row))}</td><td>${escape(place(row))}</td><td>${row.previous_accommodations ? escape(row.previous_accommodations.map(accommodationLabel).join("; ") || "No active accommodations") : "—"}</td><td>${escape(what(row))}</td></tr>`), "Changed rows")}</section>`);
  }
  const unchanged = preview.rows.filter((row) => row.status === "unchanged");
  if (unchanged.length) {
    parts.push(disclosureMarkup({ label: "Unchanged", hint: unchanged.length, className: "accommodation-preview-unchanged",
      body: `<ul>${unchanged.map((row) => `<li>${escape(who(row))} · ${escape(place(row))}</li>`).join("")}</ul>` }));
  }
  return parts.join("");
}

/** What the finished import did, beyond its message: assignments a DR import created to link new bookings. */
export function completionMarkup(result, exams, escape) {
  const created = result?.exam_links?.created_assignments || [];
  const linked = created.length ? `<p class="accommodation-import-links">${created.map((item) => `New assignment created: ${escape(item.title)}${item.code ? ` (${escape(item.code)})` : ""}`).join("<br>")}</p>` : "";
  return `${linked}<a href="${exams ? "#accommodations?tab=exams&amp;when=all" : "#accommodations"}" class="link-arrow" data-import-show>${exams ? "Show these bookings" : "Show these students"}</a>`;
}

export async function importAccommodations({api, showDialog, dialogHeader, escape, toast, onImported, kind = "accommodations", file: carried = null}) {
  const dialog = document.querySelector("#dialog");
  const intended = captureDialogIntent(dialog);
  const exams = kind === "exams", title = exams ? "Import DR exam bookings" : "Import accommodations";
  const endpoint = `/api/accommodations${exams ? "/exams" : ""}`;
  try {
    const options = await api("/api/accommodations/options");
    if (!intended()) return;
    const termField = termFieldMarkup(options.terms, escape);
    const policy = exams
      ? "Matches UIN, course, section, and exam name. This upload replaces dates and times for matching bookings and keeps their history. Bookings missing from a filtered export stay saved. Times are local to the DR center."
      : "Matches UIN, course code, and section across current rosters. Students missing from this export keep their saved accommodations. Older records cannot replace newer ones.";
    showDialog(`<form id="accommodation-import-form">${dialogHeader(title, "One export for all your current course rosters")}
      <div class="dialog-body"><p class="accommodation-import-lead">${exams ? "Upload the DR center’s booking export." : "Upload the accommodation export."} You see what changes before anything is saved.</p>${termField}
      <label class="field">${exams ? "DR exam export" : "Accommodation export"}<input type="file" name="file" accept=".xlsx" required></label>
      ${disclosureMarkup({ label: "How matching works", body: `<p>${escape(policy)}</p>`, className: "accommodation-import-policy" })}
      <p class="form-error" role="alert" hidden></p><div class="accommodation-import-switch" hidden></div><div id="accommodation-preview" aria-live="polite"></div></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Close</button><button type="submit" class="button primary">Preview import</button></footer></form>`);
    const form = document.querySelector("#accommodation-import-form"), host = form.querySelector("#accommodation-preview"), button = form.querySelector('[type="submit"]');
    const active = dialogSession(form, dialog);
    const switcher = form.querySelector(".accommodation-import-switch");
    if (carried) {
      try { const transfer = new DataTransfer(); transfer.items.add(carried); form.elements.file.files = transfer.files; } catch { /* the file is chosen again */ }
    }
    let preview = null, key = null, submitted = false;
    const reset = () => { preview = null; key = null; host.innerHTML = ""; button.textContent = "Preview import"; switcher.hidden = true; switcher.innerHTML = ""; };
    form.elements.file.onchange = reset; form.elements.term.onchange = reset;
    form.onsubmit = async event => {
      event.preventDefault(); if (form.dataset.busy || !active()) return;
      const error = form.querySelector('[role="alert"]'); error.hidden = true;
      const file = form.elements.file.files[0];
      if (!file || file.size > 10 * 1024 * 1024 || !/\.xlsx$/i.test(file.name)) { error.textContent = "Choose an XLSX export of 10 MB or less."; error.hidden = false; return; }
      const data = new FormData(form);
      form.dataset.busy = "true"; button.disabled = true; form.elements.file.disabled = true; form.elements.term.disabled = true;
      try {
        if (!preview) {
          preview = await api(`${endpoint}/preview`, "POST", data);
          if (!active()) return;
          host.innerHTML = previewMarkup(preview, exams, escape);
          button.textContent = exams ? "Import matching bookings" : "Import matching students";
          key = crypto.randomUUID();
        } else {
          data.append("preview_hash", preview.preview_hash); data.append("idempotency_key", key);
          const job = await api(`${endpoint}/import`, "POST", data);
          submitted = true;
          if (!active()) return;
          host.innerHTML = `<p role="status">Import queued. Your rosters will update when processing finishes.</p><a href="#inbox/job/${escape(job.id)}" data-import-job>View import record</a><div class="accommodation-import-done"></div>`;
          host.querySelector("[data-import-job]").onclick = () => document.querySelector("#dialog").close();
          button.hidden = true;
          const poll = async () => {
            if (!active()) return;
            try {
              const latest = await api(`/api/inbox/jobs/${job.id}`);
              if (!active()) return;
              if (["pending","running"].includes(latest.status)) { setTimeout(poll, 800); return; }
              host.querySelector('[role="status"]').textContent = latest.status === "completed" ? latest.result.message : latest.error || `Import ${latest.status}.`;
              if (latest.status === "completed") {
                const done = host.querySelector(".accommodation-import-done");
                if (done) {
                  done.innerHTML = completionMarkup(latest.result, exams, escape);
                  done.querySelector("[data-import-show]").onclick = () => document.querySelector("#dialog").close();
                }
                onImported?.();
                // The Accommodations page reloads its records at once (the shell's own refresh may be recent).
                document.dispatchEvent(new CustomEvent("accommodations-imported"));
                toast(exams ? "DR exam bookings imported." : "Accommodations imported.");
              }
            } catch (cause) { host.querySelector('[role="status"]').textContent = cause.message; }
          };
          void poll();
        }
      } catch (cause) {
        error.textContent = cause.message; error.hidden = false;
        if (/preview|changed/i.test(cause.message)) reset();
        // The other export in this importer: offer the right one, carrying the chosen file across.
        if (WRONG_EXPORT[exams ? "exams" : "accommodations"].test(cause.message)) {
          const other = exams ? "Import accommodations" : "Import DR exam bookings";
          switcher.innerHTML = `<button type="button" class="button" data-import-switch>Switch to ${escape(other)}</button>`;
          switcher.hidden = false;
          switcher.querySelector("[data-import-switch]").onclick = () => {
            const chosen = form.elements.file.files[0] || null;
            void importAccommodations({api, showDialog, dialogHeader, escape, toast, onImported, kind: exams ? "accommodations" : "exams", file: chosen});
          };
        }
      } finally {
        delete form.dataset.busy; form.elements.file.disabled = submitted; form.elements.term.disabled = submitted;
        button.disabled = preview ? !(preview.counts.added + preview.counts.updated + preview.counts.unchanged) : false;
      }
    };
  } catch (cause) { toast(cause.message, true); }
}

const AIM_PARTS = { accommodations: "Accommodations", exams: "DR exam bookings" };

/** Import from AIM's review (requirement, 2026-09-26): both exports'
    previews in one dialog and one Import. Each part is the ordinary import (its own preview, preview hash, idempotency
    key and Inbox job, revalidated on the server); nothing is imported before the instructor's click. `onImported`
    receives the kinds that finished importing. */
export async function importAimExports({api, showDialog, dialogHeader, escape, toast, files, term: aimTerm = "", problems = [], onImported}) {
  const intended = captureDialogIntent(document.querySelector("#dialog"));
  const kinds = Object.keys(AIM_PARTS).filter((kind) => files[kind]);
  const endpoint = (kind) => `/api/accommodations${kind === "exams" ? "/exams" : ""}`;
  try {
    const options = await api("/api/accommodations/options");
    if (!intended()) return;
    const termField = termFieldMarkup(options.terms, escape);
    showDialog(`<form id="aim-import-form">${dialogHeader("Import from AIM", aimTerm ? `Exported from AIM · ${escape(aimTerm)}` : "Exported from AIM")}
      <div class="dialog-body">
        ${problems.length ? calloutMarkup({ tone: "warning", title: "Not exported", body: problems.join(" ") }) : ""}
        <p class="accommodation-import-lead">${kinds.length > 1 ? "Both previews are below." : "The preview is below."} Nothing is saved until you import.</p>${termField}
        ${kinds.map((kind) => `<section class="aim-import-part" data-aim-part="${kind}"><h3>${AIM_PARTS[kind]} <span class="accommodation-aim-file">${escape(files[kind].name)}</span></h3>
          <p class="aim-import-status" data-aim-status="${kind}" role="status" hidden></p><div data-aim-preview="${kind}" aria-live="polite"></div></section>`).join("")}
        <p class="form-error" role="alert" hidden></p>
      </div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Close</button><button type="submit" class="button primary" disabled>Previewing…</button></footer></form>`);
    const dialog = /** @type {HTMLDialogElement} */ (document.querySelector("#dialog"));
    const form = /** @type {HTMLFormElement} */ (dialog.querySelector("#aim-import-form"));
    const active = dialogSession(form, dialog);
    const button = /** @type {HTMLButtonElement} */ (form.querySelector('[type="submit"]'));
    const termInput = /** @type {HTMLInputElement | HTMLSelectElement} */ (form.elements.namedItem("term"));
    const part = (kind, name) => /** @type {HTMLElement} */ (form.querySelector(`[data-aim-${name}="${kind}"]`));
    /** @type {Record<string, any>} */
    let previews = {};
    /** @type {Record<string, string>} */
    const keys = {};
    let busy = false, submitted = false, round = 0;
    const importable = (kind) => Boolean(previews[kind] && (previews[kind].counts.added + previews[kind].counts.updated + previews[kind].counts.unchanged));
    const upload = (kind) => { const data = new FormData(); data.append("term", termInput.value); data.append("file", files[kind], files[kind].name); return data; };
    const ready = () => {
      const chosen = kinds.filter(importable);
      button.disabled = !chosen.length || busy;
      button.textContent = chosen.length === 2 ? "Import both" : chosen.length ? `Import ${AIM_PARTS[chosen[0]].replace("Accommodations", "accommodations").replace("DR exam bookings", "DR bookings")}` : "Nothing to import";
    };
    const preview = async () => {
      const mine = ++round;
      previews = {};
      setBusy(button, true); button.textContent = "Previewing…";
      await Promise.all(kinds.map(async (kind) => {
        const host = part(kind, "preview");
        host.innerHTML = `<p class="field-hint">Previewing…</p>`;
        try {
          const result = await api(`${endpoint(kind)}/preview`, "POST", upload(kind));
          if (mine !== round || !active()) return;
          previews[kind] = result; keys[kind] = crypto.randomUUID();
          host.innerHTML = previewMarkup(result, kind === "exams", escape);
        } catch (cause) {
          if (mine === round && active()) host.innerHTML = `<p role="alert">${escape(cause.message)}</p>`;
        }
      }));
      if (mine === round && active()) { markBusy(button, false); ready(); }
    };
    termInput.onchange = () => { if (!submitted) void preview(); };
    form.onsubmit = async (event) => {
      event.preventDefault();
      if (busy || submitted || !active()) return;
      const chosen = kinds.filter(importable);
      if (!chosen.length) return;
      busy = true; ready(); termInput.disabled = true;
      /** @type {Record<string, string>} */
      const jobs = {};
      for (const kind of chosen) { // one after the other: each takes the roster lifecycle lock in turn
        const status = part(kind, "status");
        status.hidden = false;
        const data = upload(kind);
        data.append("preview_hash", previews[kind].preview_hash); data.append("idempotency_key", keys[kind]);
        try {
          const job = await api(`${endpoint(kind)}/import`, "POST", data);
          jobs[kind] = job.id;
          status.innerHTML = `Import queued. <a href="#inbox/job/${escape(job.id)}" data-aim-job>View import record</a>`;
        } catch (cause) {
          status.textContent = `${cause.message}${/preview|changed/i.test(cause.message) ? " Close this and choose Review and import again." : ""}`;
        }
      }
      submitted = Object.keys(jobs).length > 0;
      busy = false;
      button.hidden = submitted;
      if (!submitted) { termInput.disabled = false; ready(); return; }
      form.querySelectorAll("[data-aim-job]").forEach((link) => { /** @type {HTMLElement} */ (link).onclick = () => dialog.close(); });
      const finished = [];
      // Each job is followed to its end even once the dialog is closed (requirement, 2026-11-01): the DR bookings job finished 10 s after the accommodations
      // one, the dialog was closed by then, and the page's "From AIM" box went on saying the bookings were not imported. Closed,
      // it stops writing to the dialog and gives up after five minutes; the box, the page and the toast still hear of the end.
      const until = Date.now() + 5 * 60_000;
      await Promise.all(Object.entries(jobs).map(async ([kind, id]) => {
        const status = part(kind, "status");
        for (;;) {
          if (!active() && Date.now() > until) return;
          try {
            const latest = await api(`/api/inbox/jobs/${id}`);
            if (["pending", "running"].includes(latest.status)) { await new Promise((resolve) => setTimeout(resolve, 800)); continue; }
            if (latest.status === "completed") finished.push(kind);
            if (!active()) return;
            status.textContent = latest.status === "completed" ? latest.result.message : latest.error || `Import ${latest.status}.`;
            if (latest.status === "completed") {
              const links = document.createElement("span");
              links.innerHTML = ` ${completionMarkup(latest.result, kind === "exams", escape)}`;
              links.querySelector("[data-import-show]")?.addEventListener("click", () => dialog.close());
              status.append(links);
            }
          } catch (cause) { if (active()) status.textContent = cause.message; }
          return;
        }
      }));
      if (finished.length) {
        onImported?.(finished);
        document.dispatchEvent(new CustomEvent("accommodations-imported"));
        toast(finished.length === 2 ? "Accommodations and DR exam bookings imported." : finished[0] === "exams" ? "DR exam bookings imported." : "Accommodations imported.");
      }
    };
    await preview();
  } catch (cause) { toast(cause.message, true); }
}

