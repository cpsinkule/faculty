import { mountScanReview } from "./scan-pages.js";
import { mountBubbleSheets } from "./bubble-sheets.js";
import { revealCurrentStage, scanCard, SCAN_TONES, STATUS_TONES } from "./scan-flow.js";
import { patchChildren, patchElement } from "./dom-patch.js";
import { actionMenuMarkup, calloutMarkup, captureDialogIntent, dialogSession, disclosureMarkup, emptyMarkup, loadingMarkup, registerLeaveGuard, setBusy } from "./ui.js";
import { viewLink } from "./file-viewer.js";
import { describesAPicture, pictureStripMarkup } from "./picture-search.js";
import { uploadFileWithProgress } from "./request.js";
/* The Inbox view is a client of the transport-independent command API. */
export function createInbox({
  api: client,
  icon,
  escape,
  showDialog,
  dialogHeader,
  toast,
  header = null,
  overviewStore = null,
}) {
  // The read under way ({key, promise}), which a background read of the same page joins (below). A write drops it, so a read
  // joined after a write (the shell's redraw after a refused one, say) is always one asked after it.
  let inflight = null;
  const api = Object.assign((path, method = "GET", ...rest) => {
    if (method !== "GET") inflight = null;
    return client(path, method, ...rest);
  }, {revalidate: client.revalidate});
  const root = document.querySelector("#inbox-section");
  const dialog = () => document.querySelector("#dialog");
  const formCurrent = (form) => dialogSession(form, dialog());
  const names = {
    pending: "Queued",
    running: "Running",
    completed: "Completed",
    failed: "Failed",
    needs_review: "Needs review",
    cancelled: "Cancelled",
  };
  // A submission's state chip in the canonical words (2026-10-18): Needs you, Working, Failed, Done. `names` above stays how the
  // audit history names retained event kinds.
  const chipNames = {pending: "Working", running: "Working", completed: "Done", failed: "Failed", needs_review: "Needs you", cancelled: "Cancelled"};
  // A scan's own state (its overview's), in the same words; a whole scan with nothing left says "Nothing to do".
  const SCAN_CHIPS = {todo: "Needs you", check: "Needs you", wait: "Working", done: "Nothing to do"};
  // Retained event kinds keep their stored names; these are only how the history shows them.
  const eventNames = {
    unlabeled_exams_requested: "Papers without labels requested", unlabeled_exams_read: "First pages read",
    unlabeled_exams_not_started: "Papers without labels not looked for", unlabeled_exams_stopped: "Looking for first pages stopped", unlabeled_exams_not_registered: "Papers without labels not registered automatically",
    unlabeled_exams_failed: "Reading papers without labels stopped", unlabeled_cover_reviewed: "First page reviewed",
    unlabeled_exams_registration_requested: "Papers without labels to register", unlabeled_cover_registered: "First page registered",
    unlabeled_cover_not_registered: "First page not registered", unlabeled_exams_grouping: "Registered papers grouped",
    scans_combined: "Combined from other scans", scan_started_over: "Started over from another scan",
    scan_start_over_applied: "Carried-over choices applied",
    alias_ocr_complete: "Recovery label OCR", alias_ocr_failed: "Recovery label OCR failed", alias_ocr_retry: "Recovery label OCR retried",
    automatic_page_recovery: "Page identified from its printed alias", manual_page_recovery: "Page identity corrected",
    page_inspection: "Page inspected", decode_requested: "Inspection requested", decode_retry: "Inspection retried",
    assignment_found: "Looked for its assignment", assignment_found_not_applied: "Assignment found but not applied",
    grading_associated: "Assignment chosen", scan_associated: "Assignment chosen for the scan", assignment_created: "Assignment created",
    versions_declared: "Exam versions declared", assembly_review: "Assembled paper reviewed", label_mode_decision: "Label setup decided",
    sequence_page_identification: "Pages grouped into papers", sequence_page_identification_undone: "Grouping undone",
    sequence_grouping_skipped: "Automatic grouping skipped", first_page_papers_not_assembled: "Papers not assembled at review",
    bubble_sheets_recognized: "Bubble sheets recognized", bubble_sheets_requested: "Bubble sheets requested", bubble_sheets_read: "Bubble sheets read",
    bubble_sheets_rematched: "Bubble sheets matched again", bubble_sheet_reviewed: "Bubble sheet reviewed",
    version_sort_requested: "Version sorting requested", version_sort_complete: "Sorted by printed version", version_sort_failed: "Version sorting failed",
    version_sort_retry: "Version sorting retried", deleted_from_inbox: "Deleted from the Inbox", restored_to_inbox: "Restored to the Inbox",
  };
  // An event's detail as a few facts: retained JSON details become "key: value" (IDs shortened, the full value on hover), plain text stays.
  const factValue = (value) => value === true ? "yes" : value === false ? "no" : value === null || value === "" ? "none"
    : Array.isArray(value) ? `${value.length} item${value.length === 1 ? "" : "s"}`
    // A command names its action (its other fields stay in "Command & result details").
    : typeof value === "object" ? (typeof value.action === "string" ? value.action : `${Object.keys(value).length} fields`) : String(value);
  // The keys an event shows as shortened IDs, and a retained detail that is a JSON object (not an array or a scalar), else null.
  const idKey = (key) => /(^|_)id$|_ids$|sha256$|fingerprint$/.test(key);
  const detailObject = (detail) => {
    if (!/^\s*\{/.test(detail || "")) return null;
    try { const data = JSON.parse(detail); return data && typeof data === "object" && !Array.isArray(data) ? data : null; } catch { return null; }
  };
  function eventFacts(detail) {
    const data = detailObject(detail);
    if (!data) return detail ? `<p>${escape(detail)}</p>` : "";
    const entries = Object.entries(data), id = ([key]) => idKey(key);
    const facts = entries.filter((entry) => !id(entry)).map(([key, value]) => `<span><b>${escape(key.replaceAll("_", " "))}</b> ${escape(factValue(value).replaceAll("_", " "))}</span>`);
    const ids = entries.filter(id).map(([key, value]) => `<code title="${escape(`${key}: ${factValue(value)}`)}">${escape(key.replaceAll("_", " "))} ${escape(factValue(value).slice(0, 8))}</code>`);
    return `<p class="inbox-audit-facts">${facts.join("")}${ids.length ? `<span class="inbox-audit-ids">${ids.join(" ")}</span>` : ""}</p>`;
  }
  // The record's history, oldest first, with each run of the same event kind shown once (64 recovery-label readings are one line
  // with "Show all 64"). Every retained event stays listed inside its run, drawn once the run is opened: a section scan's history
  // holds a thousand events (650 "Assembled paper reviewed"), 15,000 elements kept folded on every draw before (2026-10-02).
  const eventName = (kind) => eventNames[kind] || names[kind] || kind.replaceAll("_", " ");
  const eventItem = (event) => `<li><span class="inbox-audit-dot"></span><div><strong>${escape(eventName(event.kind))}</strong><small>${escape(date(event.at))} · ${escape(event.software_version)}</small>${eventFacts(event.detail)}</div></li>`;
  const auditRuns = (events) => {
    const runs = [];
    for (const event of events) (runs.at(-1)?.[0].kind === event.kind ? runs.at(-1) : runs[runs.push([]) - 1]).push(event);
    return runs;
  };
  function auditMarkup(job, openRuns = new Set()) {
    const runs = auditRuns(job.events);
    return `<ol>${runs.map((run, index) => {
      if (run.length < 3) return run.map(eventItem).join("");
      // What every event of the run says alike (e.g. "outcome no match"); what differs stays in the full list.
      const common = [];
      const parsed = run.map((event) => detailObject(event.detail));
      if (parsed.every(Boolean)) for (const [key, value] of Object.entries(parsed[0])) {
        if (idKey(key) || typeof value === "object") continue;
        if (parsed.every((data) => data[key] === value)) common.push(`${key.replaceAll("_", " ")} ${factValue(value).replaceAll("_", " ")}`);
      } else if (run.every((event) => event.detail === run[0].detail) && run[0].detail) common.push(run[0].detail);
      const key = `${index}:${run[0].kind}`;
      return `<li class="inbox-audit-run"><span class="inbox-audit-dot"></span><div><strong>${escape(eventName(run[0].kind))} · ${run.length} times</strong><small>${escape(date(run[0].at))} to ${escape(date(run.at(-1).at))}</small>${common.length ? `<p class="inbox-audit-facts">${common.map((fact) => `<span>${escape(fact)}</span>`).join("")}</p>` : ""}<details data-audit-run="${escape(key)}" ${openRuns.has(key) ? "open" : ""}><summary>Show all ${run.length}</summary><ol data-audit-body>${openRuns.has(key) ? run.map(eventItem).join("") : ""}</ol></details></div></li>`;
    }).join("")}</ol>`;
  }
  // Current holds every unfinished submission; each tile says whether it waits for you, failed, or is being worked on.
  // Current and History list scans only; every other submission (a quiz upload, archived documents, a roster sync) has its own
  // Submissions tab (INBOX "SCANS AND SUBMISSIONS", 2026-10-18), so the list opened after an exam is only paper.
  const views = [["current", "Current"], ["history", "History"], ["submissions", "Submissions"]];
  const CURRENT = ["needs_review", "pending", "running", "failed"];
  // The list read's additive `kind` for each tab: Deleted holds both kinds.
  const kindOf = (view) => view === "submissions" ? "command" : view === "deleted" ? "all" : "scan";
  const scanTab = (view) => ["current", "history", "archived"].includes(view);
  let catalog = null,
    status = "current",
    query = "",
    needsOnly = false,  // "Needs you" on Current: only scans that wait for the instructor (view state, never stored)
    assignmentFilter = "",  // one grading assignment's scans (its plan ID), on Current, History and Archived
    offset = 0,
    generation = 0,
    signature = "",
    route = "",
    listHref = "#inbox",
    openingCode = "";
  let shownJob = null;  // The job whose page is shown: its folded file groups and audit history are drawn from it when opened.
  let drawnJob = "";  // The job page's markup as last drawn, the scan card apart.
  let drawnPlace = "";  // the route (job, stage, view, search, page) last drawn: a menu open on it is not drawn over
  let missingJob = "";  // a job the server no longer has (404): not read again until the route changes
  // Whether the list on screen has work under way (anything queued, running or with the worker): Current then polls every 4 s,
  // otherwise every 15 s. A failed read counts as under way, so it is tried again soon.
  let lively = true;
  // Scans ticked to be combined: browser state for the list on screen, never stored.
  const selected = new Set();
  let selecting = false;  // "Select scans to combine" is on: the tick boxes show on the list's scans
  let listed = new Map();
  // A scan's headline as the list or its page last read it (its overview's words), so its page is named at once when opened.
  const headlines = new Map();
  const date = (value) =>
    value
      ? new Date(value).toLocaleString(undefined, {
          dateStyle: "medium",
          timeStyle: "short",
        })
      : "—";
  const size = (bytes) =>
    bytes >= 1048576
      ? `${(bytes / 1048576).toFixed(1)} MB`
      : `${Math.max(1, Math.round(bytes / 1024))} KB`;
  const label = (action) =>
    catalog?.actions.find((item) => item.name === action)?.label || action;
  // A job's state as the shared chip (components.css); `inbox-badge` stays as the hook tests and patches find it by.
  const badge = (job) => job.deleted
    ? '<span class="chip inbox-badge cancelled" data-tone="neutral">Deleted</span>'
    : `<span class="chip inbox-badge ${escape(job.status)}" data-tone="${STATUS_TONES[job.status] || "neutral"}">${chipNames[job.status] || escape(job.status)}</span>`;
  // A scan's page says what the scan itself says (its overview's state), never the stored "needs review" every retained scan keeps.
  // Before the scan has been read the chip waits hidden; the page's first read of the scan fills it (showScanBadge).
  const scanStateOf = (job) => scanItems.get(job.id)?.overview?.state ?? listed.get(job.id)?.scan?.state;
  const scanChip = (state) => `<span class="chip inbox-badge scan-${escape(state)}" data-tone="${SCAN_TONES[state] || "neutral"}">${SCAN_CHIPS[state]}</span>`;
  function pageBadge(job) {
    if (job.action !== "scan.receive" || job.deleted || job.status !== "needs_review") return badge(job);
    const state = scanStateOf(job);
    return SCAN_CHIPS[state] ? scanChip(state) : '<span class="chip inbox-badge" data-tone="neutral" hidden></span>';
  }
  function showScanBadge(job, item) {
    const state = item?.overview?.state, node = root.querySelector(".inbox-scan-job .inbox-detail-heading .inbox-badge");
    if (!node || !SCAN_CHIPS[state] || job.status !== "needs_review" || job.deleted) return;
    const next = document.createElement("template");
    next.innerHTML = scanChip(state);
    const chip = next.content.firstElementChild;
    if (node.className === chip.className && node.textContent === chip.textContent && !node.hidden) return;
    [node.className, node.textContent, node.dataset.tone, node.hidden] = [chip.className, chip.textContent, chip.dataset.tone, false];
  }
  const nonce = () =>
    [...crypto.getRandomValues(new Uint8Array(16))]
      .map((value) => value.toString(16).padStart(2, "0"))
      .join("");

  // A background read of the same page (the timed poll, the shell drawing the view again, the tab shown again) joins the read under
  // way instead of starting another. Every read after an action calls refresh(), so it never takes an answer asked before the action.
  const readKey = () => JSON.stringify([location.hash, status, offset, query, needsOnly, assignmentFilter]);
  // The list read: the tab's status (Submissions lists every status, unfinished first), its kind, and the scan filters.
  function listParams() {
    const params = new URLSearchParams({offset, q: query, files: "input", kind: kindOf(status)});
    if (status !== "submissions") params.set("status", status);
    if (status === "current" && needsOnly) params.set("needs", "1");
    if (scanTab(status) && assignmentFilter) params.set("assignment", assignmentFilter);
    return params;
  }
  let readAt = 0, readFor = "";  // when a read of the page with this key last finished
  async function refresh() {
    const entry = {key: readKey(), promise: load()};
    inflight = entry;
    try { return await entry.promise; } finally {
      if (inflight === entry) inflight = null;
      // A read that ended while the Inbox was hidden drew nothing (load() leaves a hidden page alone), so it does not count.
      if (!root.hidden) [readAt, readFor] = [Date.now(), entry.key];
      armPoll();
    }
  }
  // A scan the server no longer has is not asked for again until the instructor goes somewhere (render() clears it).
  const isMissing = () => Boolean(missingJob) && location.hash.match(/^#inbox\/job\/([a-f0-9-]+)/)?.[1] === missingJob;
  const poll = () => isMissing() ? Promise.resolve() : inflight && inflight.key === readKey() ? inflight.promise : refresh();
  // The timed poll (2026-10-02, it read every view every 4 s): a scan's page, and Current while anything listed is being worked on
  // or a scan is uploading, every 4 s; an idle Current every 15 s; History, Archived and Deleted every minute. It reschedules
  // itself after each read, waits while the tab or the Inbox is hidden or a dialog is open, and showing the tab again (or coming
  // back to the window) reads at once. Every action still reads straight away.
  let pollTimer = 0;
  const pollDelay = () => location.hash.startsWith("#inbox/job/") ? 4000 : status !== "current" && !(status === "submissions" && lively) ? 60000 : lively || uploading ? 4000 : 15000;
  function armPoll() {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(pollTick, pollDelay());
  }
  function pollTick() {
    if (!document.hidden && !root.hidden && !dialog()?.open) void poll().finally(armPoll);
    else armPoll();
  }
  const pollNow = () => { if (!document.hidden && !root.hidden && !dialog()?.open) void poll(); };
  document.addEventListener("visibilitychange", pollNow);
  window.addEventListener("focus", pollNow);
  // Leaving the Inbox for another section and coming back is coming back too: render() then reads at once, whatever the poll's
  // timing (a scan certified, associated or started over elsewhere would otherwise wait up to a minute). The shell sets `hidden`
  // on every redraw of another section; a redraw of the Inbox itself leaves the attribute alone.
  let leftSection = false;
  new MutationObserver(() => { if (root.hidden) leftSection = true; }).observe(root, {attributes: true, attributeFilter: ["hidden"]});

  async function load() {
    if (root.hidden) return;
    const current = ++generation;
    const requested = readKey();
    const active = () => current === generation && !root.hidden && requested === readKey();
    // A scan's own page, or the page of one stage of its flow graph (views of the same scan).
    // `sheets?sheet=<id>` also opens that one bubble sheet's review (the grading Certification page's drawer links there).
    const match = location.hash.match(/^#inbox\/job\/([a-f0-9-]+)(?:\/(students(?:\/matched)?|papers|pages|assignment|review|sheets(?:\?sheet=([A-Za-z0-9_-]+))?))?$/);
    // Which page this read is for: a failed background read of the page already on screen keeps it (below).
    const place = JSON.stringify([match?.[1], match?.[2], status, query, offset, needsOnly, assignmentFilter]);
    try {
      if (!catalog) catalog = await api("/api/inbox/catalog");
      if (!active()) return;
      // A job is read conditionally: an unchanged job (2 MB for a section scan) comes back as a 304 and the kept answer. The list
      // asks for the input files only, all a tile shows.
      const {data, etag} = match ? await api.revalidate(`/api/inbox/jobs/${match[1]}`)
        : {data: await api(`/api/inbox/jobs?${listParams()}`), etag: null};
      if (!active()) return;
      if (!match && offset && offset >= data.total) {
        offset = Math.max(0, Math.floor((data.total - 1) / 50) * 50);
        return void refresh();
      }
      root.querySelector(":scope > .inbox-stale")?.remove();
      // Anything listed being worked on keeps Current's poll at 4 s; the server's counts follow the search.
      if (!match) lively = Boolean((data.counts?.running || 0) + (data.counts?.pending || 0))
        || data.jobs.some((job) => ["pending", "running"].includes(job.status) || job.scan?.state === "wait");
      // A job's tag stands for its exact body. The list is drawn from whether the worker checked in within 90 s, not the time it
      // did (rewritten every 2 s), so a list that did not change is not drawn again and a worker that stops shows at the 90 s mark.
      const recent = !match && Boolean(data.worker_heartbeat && Date.now() - Date.parse(data.worker_heartbeat) < 90000);
      const next = match ? JSON.stringify([place, etag ?? data]) : JSON.stringify([place, {...data, worker_heartbeat: null}, recent]);
      if (signature === next && root.hasChildNodes()) return;
      // An open '⋯' menu is not redrawn under the pointer by a background poll of the same page; the next poll brings it up to date.
      // Going somewhere else (Back, a link) closes the menu and draws the new page at once.
      const menus = [...root.querySelectorAll(":popover-open")];
      if (menus.length && place === drawnPlace) return;
      for (const menu of menus) try { menu.hidePopover(); } catch { /* already closed */ }
      signature = next;
      drawnPlace = place;
      if (match?.[2]) renderStage(data, match[2] === "students/matched" ? "matched" : match[3] ? "sheets" : match[2], match[3] || null);
      else if (match) renderJob(data);
      else renderList(data, recent);
    } catch (error) {
      if (!active()) return;
      lively = true;
      // A scan removed for good, or a link that is out of date: say so once, and stop asking for it until the route changes.
      if (match && error?.status === 404) {
        missingJob = match[1];
        signature = "";
        drawnPlace = place;
        shownJob = null;
        // A scan when the list or its page said so (its headline); otherwise any submission (a roster sync, a quiz upload).
        const what = headlines.has(missingJob) || listed.get(missingJob)?.scan ? "scan" : "submission";
        root.innerHTML = emptyMarkup({icon: "folder", title: `This ${what} is no longer in the Inbox`, body: "It was permanently removed, or this link is out of date.",
          action: {label: "Back to Teaching Inbox", href: listHref}});
        return;
      }
      // A background read of the page on screen that failed (a restart, the nightly backup holding the lock) keeps the page, with its
      // scroll, open disclosures and typing, and says so at its top; the next good read takes the notice away. The signature is kept.
      if (place === drawnPlace && root.hasChildNodes() && !root.querySelector(".loading-state")) {
        root.querySelector(":scope > .inbox-stale")?.remove();
        root.insertAdjacentHTML("afterbegin", calloutMarkup({tone: "warning", className: "inbox-stale", title: "Couldn't refresh. Showing the last loaded results.", body: error.message}));
        return;
      }
      // A first load or a new page that failed: what was drawn is gone, so the next good read draws the page again even when
      // nothing in it changed (a service restart).
      signature = "";
      drawnPlace = "";
      root.innerHTML = `<div class="empty-state"><h2>Inbox unavailable</h2><p role="alert">${escape(error.message)}</p><button class="button" data-inbox-action="refresh">Try again</button></div>`;
    }
  }

  const combinable = (job) => Boolean(job.scan) && !job.deleted && !["pending", "running"].includes(job.status) && job.scan.state !== "wait";
  function selectionMarkup() {
    const count = selected.size;
    return `<div class="inbox-selection" data-inbox-selection role="region" aria-label="Scans selected to combine" ${count ? "" : "hidden"}><span>${count === 1 ? "1 scan selected. Select at least one more to combine them." : `${count} scans selected`}</span><button class="button primary" type="button" data-inbox-action="combine" ${count < 2 ? "disabled" : ""}>Combine into one scan</button><button class="text-button" type="button" data-inbox-action="clear-selection">Clear selection</button></div>`;
  }
  const showSelection = () => { root.querySelector("[data-inbox-selection]").outerHTML = selectionMarkup(); };

  // One scan of several: each PDF stays its own file of the new scan, and the originals move to Deleted.
  function openCombine() {
    const jobs = [...selected].map((id) => listed.get(id)).filter(Boolean)
      .sort((a, b) => a.received_at.localeCompare(b.received_at) || a.id.localeCompare(b.id));
    if (jobs.length < 2) return;
    // Kept for this dialog: sending again after a failure finishes the same combination instead of making another.
    const requestId = nonce();
    showDialog(`<form id="inbox-combine-form">${dialogHeader("Combine scans", `Make one scan of these ${jobs.length}.`)}<div class="dialog-body">
      <ol class="inbox-combine-list">${jobs.map((job) => {
        const names = job.artifacts.filter((item) => item.role === "input").map((item) => item.filename);
        return `<li><strong>${escape(names.join(" · ") || job.scan.headline)}</strong><small>${escape([job.scan.headline, ...job.scan.facts].join(" · "))} · ${escape(date(job.received_at))}</small></li>`;
      }).join("")}</ol>
      <p>The combined scan keeps every PDF exactly as it is, each as its own file, and reads each file on its own, so a paper never runs from one file into the next. Its pages are checked again from the start. Anything already done on these scans, such as an assignment chosen, papers grouped, bubble sheets read or first pages found, stays with them and is not carried over.</p>
      <p>The originals move to <strong>Deleted</strong>, where each can be restored. Nothing is erased.</p>
      <label class="checkbox-field"><input type="checkbox" name="confirmed"> Combine these scans and move the originals to Deleted.</label>
      <p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button class="button" type="button" data-action="close">Cancel</button><button class="button primary" type="submit">Combine scans</button></footer></form>`);
    const form = document.querySelector("#inbox-combine-form");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void busy(form, async (current) => {
        if (!form.elements.confirmed.checked) throw new Error("Tick the box to confirm combining these scans.");
        // Copying a section's scans takes a while; a lost answer is recovered by sending the same request again.
        const job = await api("/api/inbox/scans/combine", "POST", {job_ids: jobs.map((item) => item.id), request_id: requestId, confirmation: "combine"}, {}, 10 * 60 * 1000);
        if (!current()) return;
        selected.clear();
        dialog().close();
        signature = "";
        location.hash = `inbox/job/${job.id}`;
        toast("Scans combined. The new scan’s pages are being checked.");
      });
    });
  }

  // The '⋯' actions of one scan, on its tile and on its page: the same dialogs, confirmations and request IDs as before.
  // Start over stays enabled (the server says why when it refuses); Delete waits while the worker is busy with the scan.
  // Cancel is offered on the scan's own page only, behind a confirmation: a cancelled scan leaves Current. One not yet reviewed cannot be
  // queued again (Start over makes a fresh scan of the same PDFs); a reviewed one offers Retry job in this menu.
  function scanMenu(job, id, title, {page = false} = {}) {
    const busy = ["pending", "running"].includes(job.status) || job.scan?.state === "wait";
    const items = [];
    if (!job.deleted && !["pending", "running"].includes(job.status)) items.push({label: "Start over…", data: {inboxAction: "start-over", id: job.id, title}});
    // A reviewed scan cancelled by mistake comes back the same way (the server's retry returns it to review once the scan's review is done).
    const reviewed = job.instructor_review_required === false;
    if (job.authenticated && !job.deleted && (job.status === "failed" || (job.status === "cancelled" && reviewed))) items.push({label: "Retry scan", data: {inboxAction: "retry", id: job.id, version: job.version}});
    if (page && !job.deleted && !["running", "completed", "cancelled"].includes(job.status)) items.push({label: "Cancel scan…", data: {inboxAction: "cancel", id: job.id, version: job.version, scan: "true", reviewed: String(reviewed), title}});
    if (!job.deleted && !["pending", "running"].includes(job.status)) items.push({label: "Delete from Inbox…", danger: true, disabled: busy,
      title: busy ? "Wait for scan processing to finish before deleting." : "", data: {inboxAction: "delete", id: job.id, version: job.version, scan: "true", title}});
    return items.length ? actionMenuMarkup({id, label: "Scan actions", items}) : "";
  }

  // What the Current list holds, from the server's own filing of each row (its counts follow the search).
  // It counts what its tab lists (Current: scans; Submissions: the other submissions), and Current names any other submission that
  // waits for the instructor with a link to its tab, so nothing that needs them is out of sight.
  function statusStrip(data) {
    const counts = data.counts || {};
    const parts = [`${counts.needs_review || 0} need${(counts.needs_review || 0) === 1 ? "s" : ""} you`, `${(counts.running || 0) + (counts.pending || 0)} working`, `${counts.failed || 0} failed`];
    const others = data.kind_counts.command, waiting = (others.needs_review || 0) + (others.failed || 0);
    if (status === "current" && waiting) parts.push(`<a href="#inbox?status=submissions" data-inbox-submissions-link>${waiting} other submission${waiting === 1 ? "" : "s"} need${waiting === 1 ? "s" : ""} you</a>`);
    return parts.join(" · ");
  }

  let shownList = null;  // the list read on screen, so opening a group of scans draws its cards from it
  // PICTURE-SEARCH: a search that describes something also asks for the scanned pages that look like it ({query, results}), once
  // per search, never per poll. Nothing is shown while it is asked, when it finds nothing, or when picture search is not set up.
  let pictures = null;
  let pictureGeneration = 0;
  /** @param {Promise<unknown>} listed the search's own read of the list: the strip is drawn into that list, never an earlier one */
  async function findPictures(listed) {
    const run = ++pictureGeneration;
    const asked = query, drawn = listed.then(() => {}, () => {});
    pictures = null;
    if (!describesAPicture(asked)) return;
    try {
      const found = await api(`/api/picture-search?${new URLSearchParams({ q: asked, limit: "8" })}`);
      await drawn;
      if (run !== pictureGeneration || asked !== query) return;
      pictures = { query: asked, results: found.results || [] };
    } catch { return; }
    if (shownList && !root.hidden && root.querySelector(":scope > [data-inbox-list-page]")) renderList(shownList.data, shownList.recent);
  }
  // Groups of scans (by `plan:<id>`): open as last drawn, and open or closed as the instructor last set it (browser memory, kept
  // across views and polls). A toggle that differs from the drawn state is the instructor's (a redraw only sets what was drawn).
  const drawnGroups = new Map(), chosenGroups = new Map();
  // The scan tabs' filters (2026-10-18): on Current, all scans or only those that need you; on Current, History and Archived, one
  // assignment's scans (the read's `assignments`: the grading assignments its scans are filed under). View state only.
  function scanFilters(data) {
    if (!scanTab(status)) return "";
    const needs = status === "current" ? `<div class="filter-chips inbox-scan-filters" role="group" aria-label="Show scans">${[["all", "All scans", !needsOnly], ["needs", "Needs you", needsOnly]]
      .map(([key, name, on]) => `<button type="button" data-inbox-action="needs-filter" data-inbox-needs="${key}" aria-pressed="${on}">${name}</button>`).join("")}</div>` : "";
    const choices = data.assignments;
    const titles = new Map();
    for (const item of choices) titles.set(item.title, (titles.get(item.title) || 0) + 1);
    const known = choices.some((item) => String(item.plan_id) === assignmentFilter);
    const options = [`<option value="">All assignments</option>`, ...choices.map((item) => {
      const name = titles.get(item.title) > 1 ? [item.title, item.code, item.term].filter(Boolean).join(" · ") : item.title || "Assignment";
      return `<option value="${escape(String(item.plan_id))}" ${String(item.plan_id) === assignmentFilter ? "selected" : ""}>${escape(name)} (${item.scans})</option>`;
    }), ...(assignmentFilter && !known ? [`<option value="${escape(assignmentFilter)}" selected>This assignment (no scans here)</option>`] : [])];
    const pick = choices.length || assignmentFilter
      ? `<label class="inbox-assignment-filter"><span class="sr-only">Show the scans of one assignment</span><select data-inbox-assignment aria-label="Assignment">${options.join("")}</select></label>` : "";
    return needs || pick ? `<div class="inbox-scan-filter-row">${needs}${pick}</div>` : "";
  }
  function renderList(data, recent) {
    shownList = {data, recent};
    // Keep an unsubmitted draft and keyboard focus through worker updates.
    // Search is transient view state; messages retain their existing owners.
    const input = root.querySelector("#inbox-search");
    const draft = input?.value ?? query;
    const focused = root.querySelector("#inbox-search-form")?.contains(document.activeElement)
      ? document.activeElement.id : "";
    const selection = focused === "inbox-search"
      ? [input.selectionStart, input.selectionEnd, input.selectionDirection] : null;
    const ticked = root.contains(document.activeElement) ? document.activeElement.dataset.inboxSelect : "";
    listed = new Map(data.jobs.map((job) => [job.id, job]));
    for (const job of data.jobs) if (job.scan?.headline) headlines.set(job.id, job.scan.headline);
    for (const id of [...selected]) if (!listed.has(id) || !combinable(listed.get(id))) selected.delete(id);
    // Each tab's count is of what it lists: scans for Current, History and Archived, the other submissions for Submissions, both
    // for Deleted (the read's `kind_counts`).
    const scans = data.kind_counts.scan, others = data.kind_counts.command;
    const counts = {history: (scans.completed || 0) + (scans.cancelled || 0), current: CURRENT.reduce((sum, key) => sum + (scans[key] || 0), 0),
      archived: scans.archived || 0, deleted: (scans.deleted || 0) + (others.deleted || 0),
      submissions: Object.entries(others).reduce((sum, [key, count]) => sum + (key === "deleted" ? 0 : count), 0)};
    // A list already on the page is brought up to date in place, so the scan cards' graphs and a tile being pointed at stay put while
    // the worker moves; any other page is replaced. The list's listeners are delegated to the root.
    // A "Couldn't refresh" notice still on screen is kept: a good read removes it before drawing, so a list drawn under it (a group
    // of scans opened) is still the last loaded one.
    const stale = root.querySelector(":scope > .inbox-stale")?.outerHTML || "";
    const draw = root.querySelector(":scope > [data-inbox-list-page]") ? (html) => patchChildren(root, stale + html) : (html) => { root.innerHTML = html; };
    // The worker is a quiet dot; only a worker that has not checked in for 90 s is worth a notice (Home's Workspace health says more).
    const worker = recent ? "" : calloutMarkup({tone: "warning", title: "The Inbox worker has not checked in recently",
      body: "New scans and submissions wait until it responds.", action: {label: "Workspace health", href: "#overview?operations"}, className: "inbox-worker-notice"});
    // Archived (certified_archive.py): finished scans of assessments whose grades are certified, out of History; shown once there are any.
    const tabs = [...views.map(([key, name]) => [key, name, counts[key] || 0]), ...(counts.archived || status === "archived" ? [["archived", "Archived", counts.archived]] : []),
      ...(counts.deleted || status === "deleted" ? [["deleted", "Deleted", counts.deleted]] : [])];
    const more = `<div class="action-menu inbox-more"><button type="button" class="button action-menu-trigger" popovertarget="inbox-more-menu" aria-haspopup="menu" aria-expanded="false">More<span aria-hidden="true"> ▾</span></button><div id="inbox-more-menu" popover class="action-menu-list" role="menu" aria-label="More Inbox actions"><button type="button" role="menuitem" data-inbox-action="submit">New submission…</button><button type="button" role="menuitem" data-inbox-action="use-code">Use a submission code…</button><button type="button" role="menuitem" data-inbox-action="access">Codes &amp; API access…</button></div></div>`;
    // Current says what it holds at a glance; any view says which page of a long list or which search it shows.
    const noun = scanTab(status) ? "scan" : "submission";
    const range = data.jobs.length ? `Showing ${offset + 1}–${offset + data.jobs.length} of ${data.total} ${noun}${data.total === 1 ? "" : "s"}` : `No ${noun}${query ? "s" : ""}`;
    const paged = offset || data.has_more;
    // The scan filters (Needs you, one assignment): views of the same read, said in the summary while on.
    const filtering = scanTab(status) && Boolean((status === "current" && needsOnly) || assignmentFilter);
    const summary = [["current", "submissions"].includes(status) ? statusStrip(data) : "", query || paged || filtering ? `${range}${query ? ` matching “${escape(query)}”` : ""}` : ""].filter(Boolean).join(" · ");
    const empty = query || filtering
      ? emptyMarkup({icon: "search", title: `No ${noun}s match ${query ? "your search" : "these filters"}`, body: "Try fewer words, clear the filters, or choose another view.", kind: "filtered"})
      : status === "submissions"
        ? emptyMarkup({icon: "folder", title: "No other submissions", body: "Quiz uploads, document archives, roster imports and other commands sent to the Inbox appear here. Scans are under Current and History."})
      : status === "current"
        ? emptyMarkup({icon: "folder", title: "Nothing needs you right now", body: "Nothing is waiting for your review or being processed. To add a scan, upload it or drop its PDFs on this page.", action: {label: "Upload scanned assignment", data: {inboxAction: "upload"}}})
        : status === "history" ? emptyMarkup({icon: "folder", title: "No past submissions yet", body: "Completed and cancelled submissions appear here with their files and review history."})
        : status === "archived" ? emptyMarkup({icon: "folder", title: "Nothing archived", body: "Finished scans move here once their assignment’s grades are certified. Nothing is deleted."})
          : emptyMarkup({icon: "folder", title: "No jobs in this view", body: "Choose another view to see more submissions."});
    const tile = (job) => {
        const files = job.artifacts.filter((item) => item.role === "input");
        if (job.scan) {
          // A scan is a card with its flow graph (requirement, 2026-09-24), like an assignment card in Grading: each stage opens its page.
          const scan = job.scan;
          const deletingBlocked = ["pending", "running"].includes(job.status) || scan.state === "wait";
          const title = files[0]?.filename || scan.headline;
          // Tick scans to combine them into one; a scan the worker is still busy with waits, like its Delete action.
          const pick = !selecting || status === "deleted" || job.deleted ? "" : `<label class="inbox-select" title="${deletingBlocked ? "Wait for processing to finish before combining this scan." : "Select to combine with other scans"}"><input type="checkbox" data-inbox-select="${job.id}" ${selected.has(job.id) ? "checked" : ""} ${deletingBlocked ? "disabled" : ""}><span class="sr-only">Select ${escape(title)} to combine</span></label>`;
          const chip = SCAN_CHIPS[scan.state];
          const facts = [`<span class="chip inbox-badge scan-${escape(scan.state)}" data-tone="${SCAN_TONES[scan.state] || "neutral"}">${chip}</span>`, `<span>${files.slice(0, 2).map((file) => escape(file.filename)).join(" · ")}${files.length > 2 ? ` · +${files.length - 2} more` : ""} · ${escape(date(job.received_at))}</span>`,
            // The worker's work on it stays in sight, with how far it has got.
            ...scan.steps.filter((step) => step.kind === "wait" || step.progress).map((step) => `<span class="inbox-scan-step" data-step="wait">${escape(step.text)}</span>`)];
          return `<div class="inbox-scan-tile has-card${pick ? " selectable" : ""}" data-scan-state="${escape(scan.state)}">${pick}${scanCard({ jobId: job.id, overview: scan, escape, facts, menu: scanMenu(job, `scan-menu-${job.id}`, title), compact: (status === "history" || status === "archived") && scan.state === "done" })}</div>`;
        }
        // What is happening to it, on the tile itself: waiting, running, or the recorded reason it failed or waits for you.
        const note = job.deleted ? "" : job.status === "pending" ? "Waiting for the worker." : job.status === "running" ? `Running now${job.attempts > 1 ? `, attempt ${job.attempts}` : ""}.`
          : ["failed", "needs_review"].includes(job.status) ? job.error : "";
        const row = `<a class="inbox-job-row" href="#inbox/job/${job.id}"><span class="inbox-job-icon">${icon(job.action === "roster.sync" ? "users" : "folder")}</span><div class="inbox-job-main"><h3>${escape(label(job.action))}</h3><p>${escape(job.scope_label)}</p>${note ? `<p class="inbox-job-note" data-job-status="${escape(job.status)}">${escape(note)}</p>` : ""}${files.length ? `<p class="inbox-job-filenames">${files.slice(0, 3).map(file => escape(file.filename)).join(' · ')}${files.length > 3 ? ` · +${files.length - 3} more` : ''}</p>` : ''}<small>${escape(job.source)}${job.sender ? ` · ${escape(job.sender)}` : ''} · ${escape(date(job.received_at))} · ${files.length} attachment(s)</small></div><div class="inbox-job-state">${badge(job)}${icon("arrow-right")}</div></a>`;
        // A failed job is retried from its tile too: the same explicit, version-checked request as on its page.
        return job.status === "failed" && job.authenticated && !job.deleted
          ? `<div class="inbox-tile">${row}<div class="inbox-scan-tile-actions"><button type="button" class="button" data-inbox-action="retry" data-id="${job.id}" data-version="${job.version}">Retry job</button></div></div>` : row;
      };
    // Combining is offered where two or more scans are listed, behind one toggle (the ticks are in the way otherwise).
    const canSelect = status !== "deleted" && status !== "archived" && data.jobs.filter((job) => job.scan && !job.deleted).length > 1;
    if (!canSelect && selecting) { selecting = false; selected.clear(); }
    // Scans of one assignment fold into one row (2026-10-02: an exam's sixteen copier scans were sixteen full cards in a row): in
    // History and Archived always, in Current once two or more share it, at the place of the first. A group is open on its first
    // draw when one of its scans needs the instructor; after that, open or closed is the instructor's and survives polls. Every scan
    // keeps its own card inside and opens as before; a closed group draws none of them (Archived held 13,000 elements of folded
    // cards), and opening it draws them. Views only, from the list's own rows (`scan.assignment`, `pages`, `papers`).
    const planOf = (job) => (job.scan && !job.deleted && job.scan.assignment?.plan_id) || "";
    const byPlan = new Map();
    for (const job of data.jobs) if (planOf(job)) {
      if (!byPlan.has(planOf(job))) byPlan.set(planOf(job), []);
      byPlan.get(planOf(job)).push(job);
    }
    const grouped = (plan) => Boolean(plan) && (status === "history" || status === "archived" || (status === "current" && byPlan.get(plan).length > 1));
    // History groups a run of the same submission (same action, destination and source, each within a minute of the one before):
    // a rubric upload of 23 CSVs is one expandable row, not 23. Views only; every submission is still listed and opens as before.
    const runs = [];
    for (const job of data.jobs) {
      const plan = planOf(job);
      if (grouped(plan)) {
        if (!runs.some((run) => run.plan === plan)) runs.push({plan, jobs: byPlan.get(plan)});
        continue;
      }
      const last = Array.isArray(runs.at(-1)) ? runs.at(-1) : null, prior = last?.at(-1);
      // On Submissions only finished work joins a run: what is unfinished keeps its own row, with its Retry in sight.
      const finished = (item) => status === "history" || !CURRENT.includes(item.status);
      const joins = ["history", "submissions"].includes(status) && !job.scan && finished(job) && prior && finished(prior) && !prior.scan && prior.action === job.action && prior.scope_label === job.scope_label
        && prior.source === job.source && Math.abs(Date.parse(prior.received_at) - Date.parse(job.received_at)) <= 60000;
      if (joins) last.push(job); else runs.push([job]);
    }
    const openGroups = new Set([...root.querySelectorAll("[data-inbox-group][open]")].map((node) => node.dataset.inboxGroup));
    const many = (count, word) => `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;
    // Two groups with one title (two terms' "Exam 1" in History) say whose term each is.
    const titled = new Map();
    for (const run of runs) if (!Array.isArray(run)) {
      const title = run.jobs[0].scan.assignment.title || "";
      titled.set(title, (titled.get(title) || 0) + 1);
    }
    const scanGroup = ({plan, jobs}) => {
      const key = `plan:${plan}`, sum = (field) => jobs.reduce((total, job) => total + (Number(job.scan[field]) || 0), 0);
      const need = jobs.filter((job) => ["todo", "check"].includes(job.scan.state)).length, working = jobs.filter((job) => job.scan.state === "wait").length;
      // The instructor's own open or close stands; until they choose, a group opens whenever one of its scans needs them (also a
      // group first drawn closed while its scans were with the worker) and one opened that way stays open.
      const open = chosenGroups.has(key) ? chosenGroups.get(key) : need > 0 || openGroups.has(key);
      drawnGroups.set(key, open);
      const newest = jobs.map((job) => job.received_at).sort().at(-1);
      const chips = [[need, `${need} need${need === 1 ? "s" : ""} you`, SCAN_TONES.todo], [working, `${working} working`, SCAN_TONES.wait],
        [jobs.length - need - working, `${jobs.length - need - working} nothing to do`, SCAN_TONES.done]]
        .filter(([count]) => count).map(([, text, tone]) => `<span class="chip" data-tone="${tone}">${text}</span>`).join("");
      const assignment = jobs[0].scan.assignment, term = titled.get(assignment.title || "") > 1 ? [assignment.code, assignment.term].filter(Boolean).join(" · ") : "";
      // The header opens the assignment's Collect, where its scans are worked on (ASSIGNMENT-WORKSPACE; I3, 2026-10-18).
      const groupTitle = `${escape(assignment.title || "Assignment")} · ${many(jobs.length, "scan")}`;
      const collect = assignment.assignment_id && assignment.term_id
        ? `#assignments/${encodeURIComponent(assignment.assignment_id)}/${encodeURIComponent(assignment.term_id)}?area=collect` : "";
      const facts = [term, many(sum("pages"), "page"), sum("papers") ? many(sum("papers"), "paper") : "", `last received ${date(newest)}`].filter(Boolean).join(" · ");
      return `<details class="inbox-job-group inbox-scan-group" data-inbox-group="${escape(key)}" ${open ? "open" : ""}><summary><span class="inbox-job-icon">${icon("paper")}</span><span class="inbox-job-main"><strong class="inbox-job-group-title">${collect ? `<a href="${escape(collect)}" data-inbox-group-link title="Open this assignment’s Collect">${groupTitle}</a>` : groupTitle}</strong><small>${escape(facts)}</small></span><span class="inbox-job-state">${chips}</span></summary><div class="inbox-job-group-rows" data-inbox-group-rows>${open ? jobs.map(tile).join("") : ""}</div></details>`;
    };
    const listMarkup = runs.map((run) => {
      if (!Array.isArray(run)) return scanGroup(run);
      if (run.length === 1) return tile(run[0]);
      const first = run[0], files = run.reduce((sum, job) => sum + job.artifacts.filter((item) => item.role === "input").length, 0);
      const states = new Set(run.map((job) => job.status));
      return `<details class="inbox-job-group" data-inbox-group="${escape(first.id)}" ${openGroups.has(first.id) ? "open" : ""}><summary><span class="inbox-job-icon">${icon("folder")}</span><span class="inbox-job-main"><strong class="inbox-job-group-title">${escape(label(first.action))} · ${files} file${files === 1 ? "" : "s"}</strong><small>${escape(first.scope_label)} · ${escape(first.source)} · ${escape(date(run.at(-1).received_at))}</small></span><span class="inbox-job-state">${states.size === 1 ? badge(first) : ""}<span class="chip">${run.length} submissions</span></span></summary><div class="inbox-job-group-rows">${run.map(tile).join("")}</div></details>`;
    }).join("");
    draw(`<div class="inbox-list-page" data-inbox-list-page>${worker}
      <div class="toolbar inbox-toolbar">
        <div class="inbox-filters" role="group" aria-label="Filter inbox jobs">${tabs.map(([key, name, count]) => `<button type="button" data-inbox-action="filter" data-inbox-status="${key}" aria-pressed="${status === key}" class="${status === key ? "selected" : ""}">${name}<span>${count}</span></button>`).join("")}</div>
        <div class="toolbar-actions inbox-toolbar-actions">
          <form id="inbox-search-form" role="search" aria-label="Search Teaching Inbox"><div class="inbox-search-controls"><label class="inbox-search-field"><span class="sr-only">Search submissions</span><input id="inbox-search" type="search" maxlength="200" autocomplete="off" placeholder="Search filename, course, sender, job ID, or describe a page" value="${escape(draft)}" aria-describedby="inbox-search-hint" data-page-search></label><button id="inbox-search-submit" class="icon-button" type="submit" aria-label="Search" title="Search">${icon("search")}</button><label class="icon-button inbox-search-like" title="Search by a picture"><span class="sr-only">Search by a picture, or a PDF’s first page</span><input type="file" accept="image/*,application/pdf" class="sr-only" data-inbox-like>${icon("attach")}</label>${query || pictures?.like ? '<button id="inbox-search-clear" class="text-button" type="button" data-inbox-action="clear-search">Clear search</button>' : ''}</div><p id="inbox-search-hint" class="sr-only">Search filenames, course destinations, senders, actions, or job IDs. Every search word must match. Words that describe a page also find scanned pages that look like it.</p></form>
          <span class="inbox-worker" data-inbox-worker data-online="${recent ? "true" : "false"}" title="${recent ? "Inbox worker responding" : "Inbox worker has not checked in recently"}"><i class="inbox-dot ${recent ? "online" : ""}" aria-hidden="true"></i><span class="sr-only">${recent ? "Inbox worker responding" : "Inbox worker has not checked in recently"}</span></span>
          ${more}
        </div>
      </div>
      <div class="inbox-list-tools">
        <p id="inbox-search-summary" class="inbox-search-summary" role="status" ${summary ? "" : "hidden"}>${summary}</p>
        ${scanFilters(data)}
        ${canSelect ? `<button type="button" class="text-button inbox-select-toggle" data-inbox-action="toggle-select" aria-pressed="${selecting}">${selecting ? "Done selecting" : "Select scans to combine"}</button>` : ""}
      </div>
      ${selectionMarkup()}
      ${pictures?.like || (query && pictures?.query === query) ? pictureStripMarkup(pictures, escape, { open: Boolean(pictures.like) || !data.jobs.length }) : ""}
      <div class="inbox-job-list">${data.jobs.length ? listMarkup : empty.replace('class="empty-state"', 'class="empty-state inbox-empty"')}</div>
      ${paged ? `<div class="inbox-pagination"><button type="button" class="button" data-inbox-action="previous" ${offset ? "" : "disabled"}>Previous</button><span>Page ${Math.floor(offset / 50) + 1}</span><button type="button" class="button" data-inbox-action="next" ${data.has_more ? "" : "disabled"}>Next</button></div>` : ""}</div>`);
    revealCurrentStage(root);
    if (focused) root.querySelector(`#${focused}`)?.focus({ preventScroll: true });
    if (selection) root.querySelector("#inbox-search").setSelectionRange(...selection);
    if (ticked) root.querySelector(`[data-inbox-select="${CSS.escape(ticked)}"]`)?.focus({ preventScroll: true });
  }

  // One assignment's scans, or all of them: a view of the same list.
  root.addEventListener("change", async (event) => {
    const pick = event.target.closest?.("[data-inbox-assignment]");
    if (!pick) return;
    assignmentFilter = pick.value;
    offset = 0;
    signature = "";
    await refresh();
    root.querySelector("[data-inbox-assignment]")?.focus({preventScroll: true});
  });

  root.addEventListener("change", (event) => {
    const box = event.target.closest?.("[data-inbox-select]");
    if (!box) return;
    if (box.checked) selected.add(box.dataset.inboxSelect);
    else selected.delete(box.dataset.inboxSelect);
    showSelection();
  });

  // PICTURE-SEARCH by a picture: the instructor's own file is sent once, searched by, and kept nowhere. What looks like it is
  // drawn in the same strip, over the list as it stands.
  root.addEventListener("change", async (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || !input.matches("[data-inbox-like]") || !input.files?.length) return;
    const file = input.files[0], data = new FormData();
    const run = ++pictureGeneration, requested = location.hash;
    input.value = "";
    data.set("file", file);
    try {
      const found = await api("/api/picture-search/like?limit=8", "POST", data);
      if (run !== pictureGeneration || location.hash !== requested || root.hidden) return;
      pictures = { like: file.name, query: "", results: found.results || [] };
    } catch (error) {
      toast(error.message, true);
      return;
    }
    if (!pictures.results.length) toast("Nothing Faculty holds looks like that picture yet.");
    // A picture is its own search: the words in the box no longer say what is shown, so the list goes back to everything.
    query = "";
    offset = 0;
    signature = "";
    const box = root.querySelector("#inbox-search");
    if (box) box.value = "";
    await refresh();
    if (shownList && !root.hidden && root.querySelector(":scope > [data-inbox-list-page]")) renderList(shownList.data, shownList.recent);
  });

  root.addEventListener("submit", (event) => {
    if (event.target.id !== "inbox-search-form") return;
    event.preventDefault();
    const input = root.querySelector("#inbox-search");
    query = input.value.trim();
    input.value = query;
    offset = 0;
    signature = "";
    void findPictures(refresh());
  });

  function fileGroups(job) {
    const scan = job.action === "scan.receive";
    const isOcrReport = file => scan && file.role === "output" && file.filename.startsWith("scan-alias-ocr-");
    const isSorted = file => scan && file.role === "output" && (file.filename.startsWith("sorted-") || file.filename.startsWith("version-sort-report-"));
    // The images grading cut from the pages (grading/storage.py, five per response): kept with the scan, listed apart from its pages.
    const isCrop = file => scan && file.role === "output" && /^response-\d+-[a-z]+\.png$/.test(file.filename);
    return [
      ["original", scan ? "Original scan" : "Original attachments", job.artifacts.filter(file => file.role !== "output")],
      ["results", scan ? "Separated pages" : "Results", job.artifacts.filter(file => file.role === "output" && !isOcrReport(file) && !isSorted(file) && !isCrop(file))],
      ["ocr", "Recovery label OCR", job.artifacts.filter(isOcrReport)],
      ["sorted", "Sorted by version", job.artifacts.filter(isSorted)],
      ["crops", "Grading crops", job.artifacts.filter(isCrop)],
    ].filter(([key, , files]) => key === "original" || files.length);
  }
  const groupBody = (job, key, files) => `${key === "crops" ? '<p class="field-hint">Images grading cut from these pages; each response shows its own in Grading.</p>' : ""}<div class="inbox-artifacts">${files.map(file => `<a ${viewLink(`/api/inbox/artifacts/${file.id}/download`, file.filename, escape)} class="inbox-artifact">${icon("folder")}<span><strong>${escape(file.filename)}</strong><small>${file.role === "output" ? "Result" : "Original attachment"} · ${size(file.size)}</small></span>${icon("download")}</a>`).join("") || "<p>No attachments.</p>"}</div>
        ${key === "original" ? `<a class="text-button" ${viewLink(`/api/inbox/messages/${job.message_id}/original`, "Original submission", escape)}>Original submission ${icon("paper")}</a>` : ""}`;
  // A group's files are drawn only while it is open: a section scan has a file per page (650 of them, 22,000 elements with their
  // icons), and drawing them folded on every poll was most of the page's work (2026-09-26). Opening a group draws its files.
  function artifactGroups(job, openGroups) {
    return fileGroups(job).map(([key, title, files]) => `
      <details class="inbox-file-group" data-inbox-file-group="${key}" ${openGroups.has(key) ? "open" : ""}>
        <summary>${title}<span class="inbox-file-count">${files.length} ${files.length === 1 ? "file" : "files"}</span></summary>
        <div class="detail-group" data-inbox-file-body>${openGroups.has(key) ? groupBody(job, key, files) : ""}</div>
      </details>`).join("");
  }
  // Opening a folded part of a job's page draws it from the job on screen: a file group its files, a scan's record its audit
  // history, and a run of like events its events. Later draws see it open and keep it drawn.
  root.addEventListener("toggle", (event) => {
    const node = event.target;
    if (!node.matches) return;
    const group = node.matches("[data-inbox-group^='plan:']") ? node.dataset.inboxGroup : "";
    if (group && drawnGroups.has(group) && drawnGroups.get(group) !== node.open) {
      chosenGroups.set(group, node.open);
      drawnGroups.set(group, node.open);
    }
    if (!node.open) return;
    // A group of scans opened on the list: its cards are drawn from the list on screen (and kept while it stays open).
    if (group && node.querySelector(":scope > [data-inbox-group-rows]:empty") && shownList && root.contains(node)) {
      renderList(shownList.data, shownList.recent);
      return;
    }
    if (!shownJob || node.closest("[data-job-id]")?.dataset.jobId !== shownJob.id) return;
    if (node.matches("[data-inbox-file-group]")) {
      const body = node.querySelector(":scope > [data-inbox-file-body]");
      const found = fileGroups(shownJob).find(([key]) => key === node.dataset.inboxFileGroup);
      if (body && !body.childElementCount && found) body.innerHTML = groupBody(shownJob, found[0], found[2]);
    } else if (node.matches("[data-inbox-technical]")) {
      const audit = node.querySelector("[data-inbox-audit]");
      if (audit && !audit.querySelector("ol")) audit.innerHTML = `<h3>Audit history</h3>${auditMarkup(shownJob)}`;
    } else if (node.matches("[data-audit-run]")) {
      const body = node.querySelector(":scope > [data-audit-body]");
      const run = auditRuns(shownJob.events).find((items, index) => `${index}:${items[0].kind}` === node.dataset.auditRun);
      if (body && !body.childElementCount && run) body.innerHTML = run.map(eventItem).join("");
    }
  }, true);

  // A scan page is named by what the scan is (its overview's headline), not by the list's title (UX overhaul 2026-09-26). The header
  // only paints: every word comes from the job and the scan's overview. Stage pages add the stage to the trail.
  const STAGE_LABELS = {pages: "Scan", assignment: "Assignment", students: "Students", matched: "Matched papers", sheets: "Bubble sheets", papers: "Papers", review: "Review"};
  const STAGE_KEYS = {pages: "scan", matched: "students"};
  let lastHeader = null;  // the header last given to a job page: the shell's redraws, and a return to the same page, keep it
  const scanItems = new Map();  // job ID → the scan's last review item, so a redraw without one keeps the header's words
  // A scan page's one "Updated hh:mm:ss · Refresh" (UX overhaul 2026-09-26: it replaced seven inline Refresh buttons; polling is
  // unchanged), and on a scan with nothing left, the next scan in Current that needs the instructor. Browser memory only.
  const updatedAt = new Map();  // job ID → when its page last read the scan
  const nextUp = new Map();  // job ID → {id, headline} of the next Current scan that needs the instructor, or null
  const nextAsked = new Map();  // job ID → when Current was last read for it
  function toolsMarkup(job) {
    const time = updatedAt.get(job.id) || "", next = nextUp.get(job.id);
    return `<div class="inbox-page-tools" data-scan-tools>${next ? `<a class="link-arrow inbox-next-current" data-inbox-next-current href="#inbox/job/${escape(next.id)}" title="${escape(next.headline)}">Next in Current</a>` : ""}<span class="inbox-updated" data-scan-updated ${time ? "" : "hidden"}>${time ? `Updated ${escape(time)}` : ""}</span><button type="button" class="icon-button inbox-refresh" data-inbox-action="refresh-scan" aria-label="Refresh page review" title="Refresh">${icon("refresh")}</button></div>`;
  }
  function showTools(job) {
    for (const node of root.querySelectorAll("[data-scan-tools]")) {
      const markup = toolsMarkup(job);
      if (node.outerHTML === markup) continue;
      const focused = node.contains(document.activeElement);
      node.outerHTML = markup;
      if (focused) root.querySelector("[data-scan-tools] .inbox-refresh")?.focus({preventScroll: true});
    }
  }
  // A scan waiting on a worker that stopped checking in says so on its own pages, as the list does (2026-10-02: a scan stuck at
  // "Waiting for the worker to start checking its pages" said nothing about the worker). The worker's state is the shell's overview
  // read (no poll of its own, and nothing added to the job's read, whose tag must stay the job's); the scan's state is its last read.
  let workerState = overviewStore?.get()?.operations?.worker?.state || null;
  const workerSlot = '<div class="inbox-worker-slot" data-inbox-worker-slot hidden></div>';
  const workerWritten = new WeakMap();  // slot → the markup last written into it
  function showWorkerNotice() {
    const slot = root.querySelector("[data-inbox-worker-slot]"), jobId = slot?.closest("[data-job-id]")?.dataset.jobId;
    if (!slot || !jobId) return;
    const state = scanItems.get(jobId)?.overview?.state ?? listed.get(jobId)?.scan?.state;
    const markup = ["stalled", "stale", "unknown"].includes(workerState) && state === "wait"
      ? calloutMarkup({tone: "warning", title: "The Inbox worker has not checked in recently", body: "This scan waits until it responds.",
        action: {label: "Workspace health", href: "#overview?operations"}, className: "inbox-worker-notice"}) : "";
    if (workerWritten.get(slot) === markup) return;
    workerWritten.set(slot, markup);
    slot.innerHTML = markup;
    slot.hidden = !markup;
  }
  overviewStore?.subscribe((data) => {
    const next = data?.operations?.worker?.state || null;
    if (!data || next === workerState) return;
    workerState = next;
    showWorkerNotice();
  });
  // Every draw of the scan's page: when it was read, and (once it has nothing left) where the next scan that needs you is.
  function scanRead(job, item) {
    updatedAt.set(job.id, new Date().toLocaleTimeString(undefined, {hour: "2-digit", minute: "2-digit", second: "2-digit"}));
    const node = root.querySelector("[data-scan-updated]");
    if (node) { node.textContent = `Updated ${updatedAt.get(job.id)}`; node.hidden = false; }
    const finished = item?.overview?.state === "done";
    if (!finished) { if (nextUp.get(job.id)) { nextUp.set(job.id, null); showTools(job); } return; }
    if (Date.now() - (nextAsked.get(job.id) || 0) < 30000) return;
    nextAsked.set(job.id, Date.now());
    // The Inbox's own filing of Current: the first scan other than this one that waits for the instructor (a view; nothing is stored).
    api(`/api/inbox/jobs?${new URLSearchParams({status: "current", files: "input"})}`).then((data) => {
      const found = data.jobs.find((other) => other.id !== job.id && !other.deleted && other.scan && ["todo", "check"].includes(other.scan.state));
      nextUp.set(job.id, found ? {id: found.id, headline: found.scan.headline} : null);
      if (found) headlines.set(found.id, found.scan.headline);
      if (root.querySelector("[data-job-id]")?.dataset.jobId === job.id) showTools(job);
    }).catch(() => {});
  }
  const shellContext = () => Boolean(document.querySelector("#assignment-workspace[data-area]"));
  // When the shell's context goes away (the scan moved to no assignment), the scan's page names its own Inbox trail again.
  window.addEventListener("faculty:assignment-context", (event) => {
    if (header && !event.detail?.active && !shellContext() && lastHeader && lastHeader.route === location.hash.split("?")[0]) header.set(lastHeader);
  });
  function jobHeader(job, {item = null, page = null} = {}) {
    if (!header) return;
    if (item) scanItems.set(job.id, item);
    item ||= scanItems.get(job.id) || null;
    const scan = job.action === "scan.receive";
    const file = job.artifacts.find((artifact) => artifact.role !== "output")?.filename || "";
    if (item?.overview?.headline) headlines.set(job.id, item.overview.headline);
    const title = scan ? headlines.get(job.id) || file || "Scanned PDF" : label(job.action);
    const plan = item?.grading?.plan, paper = item?.recognition?.papers?.[0];
    const owner = plan || paper;
    const course = owner ? `${owner.code} · ${owner.term === "Other" ? owner.term_other : owner.term} ${owner.year}` : "";
    const context = (scan ? [course, file === title ? "" : file, `received ${date(job.received_at)}`] : [job.scope_label, `${job.source} submission`, `received ${date(job.received_at)}`]).filter(Boolean).join(" · ");
    const stage = page ? (item?.overview?.stages || []).find((entry) => entry.key === (STAGE_KEYS[page] || page)) : null;
    const stageLabel = page === "matched" ? STAGE_LABELS.matched : stage?.label || STAGE_LABELS[page] || "";
    const route = `#inbox/job/${job.id}${page ? `/${page === "matched" ? "students/matched" : page}` : ""}`;
    const crumbs = [{label: "Home", href: "#overview"}, {label: "Teaching Inbox", href: listHref},
      ...(page ? [{label: title, href: `#inbox/job/${job.id}`}, ...(page === "matched" ? [{label: "Students", href: `#inbox/job/${job.id}/students`}] : []), {label: stageLabel}] : [{label: title}])];
    lastHeader = {route, variant: "compact", title, context, subtitle: "", crumbs, action: null, tab: page ? `${stageLabel} · ${title}` : title};
    // Inside the assignment shell (I4: `#assignment-workspace[data-area]` while it has an assignment's context) the trail is the
    // shell's (Home › Assignments › <title> › Collect); the scan's words go on, its Inbox trail does not.
    header.set(shellContext() ? {...lastHeader, crumbs: undefined} : lastHeader);
  }

  function renderJob(job) {
    shownJob = job;
    const editable = !["running", "completed"].includes(job.status);
    // Keep the instructor's expanded groups through background job updates.
    // This is view state only; opening another submission starts collapsed.
    const sameJob = root.querySelector(".inbox-job-detail:not(.inbox-students-page)")?.dataset.jobId === job.id;
    const openGroups = new Set(sameJob
      ? [...root.querySelectorAll("[data-inbox-file-group][open]")].map(group => group.dataset.inboxFileGroup)
      : []);
    const openRuns = new Set(sameJob ? [...root.querySelectorAll("[data-audit-run][open]")].map(run => run.dataset.auditRun) : []);
    // A scan is read for what it holds, not for its command: its page leads with its scan checks, and the files, the
    // command and the history wait behind one disclosure (view state, kept through background updates like the file groups).
    const scan = job.action === "scan.receive", recordOpen = sameJob && root.querySelector("[data-inbox-record]")?.open;
    // Job ID, attempts, storage, the audit history and the raw command wait behind one "Technical details" fold (2026-10-18), its
    // history drawn only once it is opened (view state, kept through background updates).
    const technicalOpen = sameJob && Boolean(root.querySelector("[data-inbox-technical]")?.open);
    const scanName = job.artifacts.find(file => file.role !== "output")?.filename || label(job.action);
    // Anything not queued or running can be deleted from the Inbox. A scan's Start over, Cancel, Retry and Delete sit in its '⋯' menu
    // beside the state chip; another submission keeps its buttons in the record.
    const deleteButton = !job.deleted && !["pending", "running"].includes(job.status) ? `<button class="danger-button" data-inbox-action="delete" data-id="${job.id}" data-version="${job.version}" data-title="${escape(label(job.action))}">Delete from Inbox</button>` : "";
    const menu = scan ? scanMenu(job, `scan-page-menu-${job.id}`, scanName, {page: true}) : "";
    jobHeader(job);
    const deletedNotice = job.deleted ? calloutMarkup({tone: "warning", className: "inbox-deleted-notice", title: `Deleted from the Inbox on ${date(job.deleted.created_at)}.`,
      body: `It is left out of every list and nothing it holds counts anywhere.${job.deleted.reason ? ` Reason given: ${job.deleted.reason}.` : ""} Its original, its files and its history are retained.`,
      action: {label: "Restore to the Inbox", data: {inboxAction: "restore", id: job.id}}}) : "";
    // One row under the page header: the way back, then the state chip and (for a scan) its '⋯' actions. A scan's record says
    // nothing a scan's stored status implies ("This processor has not been implemented yet." is how every retained scan waits for
    // its review); only a scan job that failed shows its error.
    const markup = `<article class="inbox-job-detail ${scan ? "inbox-scan-job" : ""}" data-job-id="${job.id}" data-job-status="${escape(job.status)}"><header class="inbox-detail-heading"><a href="${escape(listHref)}" class="course-back-link back-link">${icon("arrow-left")}Back to Teaching Inbox</a>${scan && !job.deleted ? toolsMarkup(job) : ""}<div class="inbox-heading-state">${pageBadge(job)}${menu}</div></header>
      ${scan && !job.deleted ? workerSlot : ""}${deletedNotice}
      ${scan ? `<details class="course-panel inbox-record" data-inbox-record ${recordOpen || job.deleted ? "open" : ""}><summary>Files, submission details and history</summary>` : ""}
      ${job.error && (!scan || job.status === "failed") ? calloutMarkup({tone: job.status === "failed" ? "danger" : "warning", title: job.error, className: job.status === "failed" ? "inbox-job-error" : "inbox-job-notice"}) : ""}
      ${job.result.message ? `<div class="inbox-job-result">${icon("check")}<div><strong>${escape(job.result.message)}</strong>${job.result.course_url ? `<a class="text-button" href="${escape(job.result.course_url)}">Open section roster ${icon("arrow-right")}</a>` : ""}</div></div>` : ""}
      <div class="inbox-job-actions">${editable && job.authenticated && !job.attempts && !scan ? `<button class="button" data-inbox-action="review" data-id="${job.id}">Review command</button>` : ""}${editable && job.authenticated && (scan ? job.status === "failed" : ["failed", "needs_review", "cancelled"].includes(job.status)) ? `<button class="button" data-inbox-action="retry" data-id="${job.id}" data-version="${job.version}">${scan ? "Retry scan" : "Retry job"}</button>` : ""}${job.status === "needs_review" && job.authenticated && job.requires_approval && job.handler_available ? `<button class="button primary" data-inbox-action="approve" data-id="${job.id}">Review approval</button>` : ""}${editable && job.status !== "cancelled" && !scan ? `<button class="danger-button" data-inbox-action="cancel" data-id="${job.id}" data-version="${job.version}">Cancel job</button>` : ""}${!scan ? deleteButton : ""}</div>
      <div class="inbox-detail-grid"><section class="course-panel"><h3>Files & results</h3>${artifactGroups(job, openGroups)}</section><section class="course-panel"><h3>Submission details</h3><dl class="inbox-facts detail-group"><dt>Received</dt><dd>${escape(date(job.received_at))}</dd><dt>Sender</dt><dd>${escape(job.sender)}</dd></dl>${job.storage_error ? `<p class="form-error">${escape(job.storage_error)}</p>` : ""}</section></div>
      <details class="disclosure inbox-technical" data-inbox-technical ${technicalOpen ? "open" : ""}><summary><span>Technical details</span></summary><div class="disclosure-body">
      <dl class="inbox-facts detail-group"><dt>Attempts</dt><dd>${job.attempts}</dd><dt>Job ID</dt><dd><code>${job.id}</code></dd><dt>Storage</dt><dd>${escape({ active: "NVMe working copy", staged: "NVMe · awaiting nightly backup", sync_pending: "Updating NVMe copy", archived: "Verified HDD storage", restore_pending: "Preparing NVMe copy", archive_pending: "Preparing for nightly backup", purge_pending: "HDD verified; clearing NVMe" }[job.storage_status] || "Local workspace")}</dd></dl>
      <section class="inbox-audit" data-inbox-audit><h3>Audit history</h3>${technicalOpen ? auditMarkup(job, openRuns) : ""}</section><details class="disclosure inbox-command-details"><summary><span>Command &amp; result details</span></summary><div class="disclosure-body"><pre>${escape(JSON.stringify({ command: job.parameters, result: job.result }, null, 2))}</pre></div></details></div></details>${scan ? "</details>" : ""}</article>`;
    // The scan's card stays mounted while the job changes in the background (requirement, 2026-09-26). Every poll that saw a change used to rebuild the whole page, so the card went blank, fetched the scan
    // again from nothing and drew itself anew. Now the heading and the folded record are brought up to date in place, the card
    // keeps its elements, and it reads the scan again itself. It depends on the job's ID and status only, so a new status (or a
    // deletion) mounts it afresh.
    const article = root.querySelector(".inbox-job-detail:not(.inbox-students-page)");
    const kept = sameJob && scan && !job.deleted && article?.dataset.jobStatus === job.status ? article.querySelector(":scope > .document-scan-review") : null;
    if (kept && markup === drawnJob) { kept.dispatchEvent(new Event("scan-changed")); return; }
    drawnJob = markup;
    if (kept) {
      const template = document.createElement("template");
      template.innerHTML = markup;
      const chip = article.querySelector(".inbox-detail-heading .inbox-badge"), shown = chip && [chip.className, chip.textContent, chip.dataset.tone, chip.hidden];
      for (const part of [":scope > .inbox-detail-heading", ":scope > [data-inbox-record]"]) {
        const now = article.querySelector(part), next = template.content.querySelector(".inbox-job-detail")?.querySelector(part);
        if (now && next) patchElement(now, next);
      }
      // The badge says what the card's overview says (the card sets it); keep it until the card has read the scan again.
      const badge = article.querySelector(".inbox-detail-heading .inbox-badge");
      if (badge && shown && job.status === "needs_review") [badge.className, badge.textContent, badge.dataset.tone, badge.hidden] = shown;
      kept.dispatchEvent(new Event("scan-changed"));
      return;
    }
    root.innerHTML = markup;
    if (job.deleted) return;  // A deleted scan has nothing to review: its record stays readable above, and Restore brings the tools back.
    showWorkerNotice();
    void mountScanReview({root, job, api, escape, toast, showDialog, dialogHeader, onItem: (item) => { jobHeader(job, {item}); showScanBadge(job, item); scanRead(job, item); showWorkerNotice(); }});
  }

  // A stage of a scan's flow graph, on a page of its own (requirement, 2026-09-24): Scan, Assignment, Students (and Matched papers),
  // Bubble sheets, Papers and Review. Pages of one scan; views only, nothing is stored.
  function renderStage(job, page, sheet = null) {
    const view = `${job.id}/${page}`;
    const same = root.querySelector(".inbox-students-page")?.dataset.view === view;
    // The scan's record changed (a choice adds an event): redraw the list where it is, keeping the place in a long page.
    const mounted = same && !job.deleted ? root.querySelector(".inbox-students-page .document-scan-review") : null;
    if (mounted) return void mounted.dispatchEvent(new Event("scan-changed"));
    jobHeader(job, {page});
    const gone = job.deleted ? calloutMarkup({tone: "warning", className: "inbox-deleted-notice", title: "This scan was deleted from the Inbox.", body: "Open the scan to restore it.", action: {label: "Open the scan", href: `#inbox/job/${job.id}`}}) : "";
    root.innerHTML = `<header class="inbox-detail-heading inbox-stage-heading"><a href="#inbox/job/${job.id}" class="course-back-link back-link">${icon("arrow-left")}Back to scan</a>${job.deleted ? "" : toolsMarkup(job)}</header><article class="inbox-job-detail inbox-scan-job inbox-students-page" data-job-id="${job.id}" data-view="${escape(view)}">${job.deleted ? gone : workerSlot}</article>`;
    if (!same) window.scrollTo({top: 0, behavior: "instant"});
    if (job.deleted) return;
    showWorkerNotice();
    const review = mountScanReview({root, job, api, escape, toast, showDialog, dialogHeader, page, onItem: (item) => { jobHeader(job, {item, page}); scanRead(job, item); showWorkerNotice(); }});
    // On its own stage page the bubble sheet panel is part of the page: the page's heading and its one Review all stand for it.
    if (page === "sheets") void review.then(() => mountBubbleSheets({root, job, api, escape, toast, showDialog, dialogHeader, stage: true, sheet}));
  }

  function commandFields(command = {}, scanHint = false) {
    // What can run first, then the Accommodations page's imports, then what is not built yet. Scans have their own upload; a
    // command that already is a scan keeps its action. The option values are the actions' names, unchanged.
    const chosen = command.action || "document.archive";
    const option = (action) => `<option value="${action.name}" ${chosen === action.name ? "selected" : ""}>${escape(action.label)}${action.implemented ? "" : " · Processor pending"}</option>`;
    const workspace = (action) => action.scopes.includes("workspace");
    const ready = catalog.actions.filter((action) => action.implemented && !workspace(action) && action.name !== "scan.receive");
    const elsewhere = catalog.actions.filter((action) => workspace(action));
    const pending = catalog.actions.filter((action) => !action.implemented && !workspace(action) && action.name !== "scan.receive");
    const scans = catalog.actions.filter((action) => action.name === "scan.receive" && chosen === "scan.receive");
    return `<div class="field-row"><label class="field">Action<select name="action" aria-label="Action" required>${[...scans, ...ready].map(option).join("")}${elsewhere.length ? `<optgroup label="Use the Accommodations page">${elsewhere.map(option).join("")}</optgroup>` : ""}${pending.length ? `<optgroup label="Not built yet (kept for your review)">${pending.map(option).join("")}</optgroup>` : ""}</select></label></div><p id="inbox-action-hint" class="supporting-copy"></p>${scanHint ? '<p class="field-hint inbox-scan-hint">Scanned papers or bubble sheets? <button type="button" class="text-button" data-inbox-action="upload">Upload scanned assignment</button> instead.</p>' : ""}<div class="detail-group"><label class="field">Course destination<select name="target" aria-label="Course destination" required><option value="">Choose a course and scope…</option>${catalog.targets.map((target) => `<option value="${target.level}:${target.id}" data-level="${target.level}" ${command.target?.id === target.id && command.target?.level === target.level ? "selected" : ""}>${escape(target.label)}${target.is_current ? "" : " · Not current"}</option>`).join("")}</select></label><p class="supporting-copy">Use “Across all terms” for shared course files. Choose a term or section for records that belong only there.</p></div><details class="disclosure inbox-advanced" ${command.metadata && Object.keys(command.metadata).length ? "open" : ""}><summary><span>Advanced</span></summary><div class="disclosure-body"><label class="field">Metadata (JSON or YAML)<textarea name="metadata" rows="4" spellcheck="false" placeholder="quiz: 7&#10;version: B&#10;publish: false">${escape(command.metadata && Object.keys(command.metadata).length ? JSON.stringify(command.metadata, null, 2) : "")}</textarea></label></div></details>`;
  }

  function updateAction(form) {
    const action = catalog.actions.find(
      (item) => item.name === form.elements.action.value,
    );
    const workspace = action.scopes.includes("workspace");
    form.elements.target.disabled = workspace;
    form.elements.target.required = !workspace;
    form.elements.target.closest("label").hidden = workspace;
    form.querySelector("#inbox-action-hint").textContent = workspace ? "Use the Accommodations page to preview an accommodation or DR exam export." : action.implemented
      ? action.name === "roster.sync"
        ? "Imports one CSV or XLSX roster, preserving existing student IDs."
        : "Retains the original files with this course, term, or section."
      : "Files will be retained for review. This processor has not been built yet.";
    [...form.elements.target.options].forEach((option) => {
      if (option.value)
        option.disabled = !action.scopes.includes(option.dataset.level);
    });
    if (form.elements.target.selectedOptions[0]?.disabled)
      form.elements.target.value = "";
  }

  async function fieldsCommand(form) {
    const [level, id] = form.elements.target.value.split(":");
    // The server parses YAML metadata with the same bounded command parser.
    return {
      action: form.elements.action.value,
      target: form.elements.target.disabled ? null : { level, id },
      metadata_text: form.elements.metadata.value.trim(),
    };
  }

  async function busy(form, operation) {
    if (form.dataset.busy) return;
    form.dataset.busy = "true";
    const buttons = [...form.querySelectorAll("button")].filter(button => !button.disabled);
    const current = formCurrent(form);
    buttons.forEach((button) => (button.disabled = true));
    const error = form.querySelector(".form-error");
    if (error) error.hidden = true;
    try {
      await operation(current);
    } catch (cause) {
      if (error && current()) {
        error.textContent = cause.message;
        error.hidden = false;
      } else if (!error || current()) toast(cause.message, true);
    } finally {
      delete form.dataset.busy;
      buttons.forEach((button) => (button.disabled = false));
    }
  }

  async function openSubmit(job = null, code = null, makeCode = false) {
    showDialog(
      `${dialogHeader("Preparing submission…")}<div class="dialog-body"><p id="inbox-loading" role="status">Loading course destinations.</p></div>`,
    );
    const loading = document.querySelector("#inbox-loading");
    try {
      [catalog, job] = await Promise.all([
        api("/api/inbox/catalog"),
        typeof job === "string" ? api(`/api/inbox/jobs/${job}`) : job,
      ]);
      if (!loading.isConnected || !dialog().open)
        return;
      showDialog(
        `<form id="inbox-submit-form">${dialogHeader(makeCode ? "Create submission code" : job ? "Review command" : code ? "Submit using a code" : "New submission", code ? escape(code.label) : "Choose what to do with the files and where they belong.")}<div class="dialog-body">${code ? `<div class="callout inbox-job-notice" data-tone="warning">${escape(label(code.command.action))} · ${escape(catalog.targets.find((item) => item.id === code.command.target.id)?.label || "Saved destination")}</div>` : commandFields(job?.parameters || {}, !job && !makeCode)}${makeCode ? '<label class="field">Code label<input name="label" required maxlength="100" placeholder="e.g. Fall roster upload"></label><label class="field">Expires in<select name="hours"><option value="24">24 hours</option><option value="168" selected>7 days</option><option value="720">30 days</option></select></label><p class="field-hint">One use, one fixed action and destination. The code contains no course or student information.</p>' : job ? '<p class="field-hint">Original files remain unchanged. The new command and its scope will be recorded in the audit history.</p>' : '<label class="field">Attachments<input type="file" name="files" multiple></label><p class="field-hint">Up to 20 files, 10 MB per file and 25 MB total. Roster import takes one CSV or XLSX file.</p>'}<p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button class="button" type="button" data-action="close">Cancel</button><button class="button primary" type="submit">${makeCode ? "Create code" : job ? "Save reviewed command" : "Queue submission"}</button></footer></form>`,
      );
      const form = document.querySelector("#inbox-submit-form");
      if (!code) {
        if (makeCode) {
          for (const option of form.elements.action.options) {
            const action = catalog.actions.find(
              (item) => item.name === option.value,
            );
            option.disabled = !action.implemented || action.approval;
          }
          if (form.elements.action.selectedOptions[0]?.disabled) {
            form.elements.action.value = [...form.elements.action.options].find(option => !option.disabled)?.value || "";
          }
        }
        updateAction(form);
        form.elements.action.addEventListener("change", () =>
          updateAction(form),
        );
      }
      let requestId = nonce();
      form.addEventListener("input", () => {
        requestId = nonce();
      });
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        void busy(form, async (current) => {
          let command = code?.command;
          if (!code) {
            const fields = await fieldsCommand(form);
            command = await api("/api/inbox/command", "POST", fields);
          }
          if (!current()) return;
          if (makeCode) {
            const generated = await api("/api/inbox/codes", "POST", {
              command,
              label: form.elements.label.value,
              hours: Number(form.elements.hours.value),
            });
            if (current()) showSecret(generated, true);
            return;
          }
          let result;
          if (job)
            result = await api(`/api/inbox/jobs/${job.id}/review`, "POST", {
              version: job.version,
              command,
            });
          else {
            const data = new FormData();
            if (!code) data.set("command", JSON.stringify(command));
            data.set("idempotency_key", requestId);
            for (const file of form.elements.files.files)
              data.append("files", file);
            result = await api(
              "/api/inbox/submit",
              "POST",
              data,
              code ? { "X-Teaching-Submission-Code": code.token } : {},
            );
          }
          if (!current()) return;
          dialog().close();
          location.hash = `inbox/job/${result.id}`;
          signature = "";
          void refresh();
          toast(
            result.status === "needs_review"
              ? "Submission retained for review."
              : "Submission queued.",
          );
        });
      });
    } catch (error) {
      if (loading.isConnected) dialog().close();
      toast(error.message, true);
    }
  }

  // Scanner PDFs of an assignment, whole sections at a time. Each file is its own scan and its own request, retained
  // as the scanner address would retain it (instructor review required); sending a file again returns its scan.
  // One PDF per request with its byte progress (a section's scan takes minutes): the same POST, header and timeout as api(),
  // through XMLHttpRequest because fetch reports no upload progress. Never retried here: a retry is the instructor's click.
  function uploadWithProgress(file, onProgress) {
    return uploadFileWithProgress("/api/inbox/scans", file, {
      timeout: 60 * 60 * 1000,
      timeoutMessage: "The upload took too long. Upload it again: a file already received returns its scan.",
      unreadableMessage: "The server response could not be read. Refresh the page to check the result.",
      onProgress: (loaded) => onProgress(Math.min(loaded, file.size), file.size),
    });
  }
  let uploading = 0;  // uploads in flight: leaving the page would stop them
  registerLeaveGuard(() => uploading ? "Scans are still uploading. Leaving this page stops the uploads that have not finished." : null);
  const megabytes = (bytes) => (bytes / 1048576).toLocaleString(undefined, {maximumFractionDigits: bytes >= 10 * 1048576 ? 0 : 1});
  function uploadScans(dropped = null) {
    const LIMIT = 512 * 1048576;
    showDialog(
      `<form id="inbox-scan-upload">${dialogHeader("Upload scanned assignment", "PDFs from the scanner: students’ papers, bubble sheets, or both.")}<div class="dialog-body"><label class="field">Scanned PDFs<input type="file" name="files" accept="application/pdf,.pdf" multiple required></label><p class="field-hint">Up to 20 PDFs, each at most 512 MB and 1,500 pages; you can also drop them here or on the Inbox list. Each file becomes its own scan: its pages are checked for labels and bubble sheets, and papers that need nothing from you go on to grading by themselves. Nothing is sent outside Faculty.</p><p class="inbox-upload-progress" data-upload-progress role="status" hidden></p><ul class="inbox-upload-list" data-upload-list hidden></ul><p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button class="button" type="button" data-action="close">Close</button><button class="button primary" type="submit">Upload</button></footer></form>`,
    );
    const form = document.querySelector("#inbox-scan-upload");
    const current = formCurrent(form);
    const list = form.querySelector("[data-upload-list]"), progress = form.querySelector("[data-upload-progress]");
    // Files dropped on the Inbox list arrive chosen, exactly as if picked here.
    if (dropped?.length && typeof DataTransfer === "function") {
      const transfer = new DataTransfer();
      for (const file of dropped) transfer.items.add(file);
      form.elements.files.files = transfer.files;
    }
    // PDFs dropped on the dialog are chosen as if picked (the list's own drop handler leaves an open dialog alone).
    form.addEventListener("dragover", (event) => {
      if (event.target === form.elements.files || ![...(event.dataTransfer?.types || [])].includes("Files")) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    });
    form.addEventListener("drop", (event) => {
      if (event.target === form.elements.files || ![...(event.dataTransfer?.types || [])].includes("Files")) return;
      event.preventDefault();
      if (form.dataset.busy) return;
      if (typeof DataTransfer !== "function" || !event.dataTransfer.files.length) return;
      const transfer = new DataTransfer();
      for (const file of event.dataTransfer.files) transfer.items.add(file);
      form.elements.files.files = transfer.files;
      form.elements.files.dispatchEvent(new Event("change", {bubbles: true}));
    });
    // Files already sent from this dialog are not sent again when the instructor retries the ones that failed.
    let sent = new Map();
    form.elements.files.addEventListener("change", () => {
      sent = new Map();
      list.hidden = true;
      list.innerHTML = "";
      progress.hidden = true;
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void busy(form, async () => {
        const files = [...form.elements.files.files];
        if (!files.length) throw new Error("Choose the scanned PDFs to upload.");
        if (files.length > 20) throw new Error("Upload at most 20 PDFs at a time.");
        const wrong = files.find((file) => !/\.pdf$/i.test(file.name) && file.type !== "application/pdf");
        if (wrong) throw new Error(`${wrong.name} is not a PDF.`);
        const large = files.find((file) => file.size > LIMIT);
        if (large) throw new Error(`${large.name} is ${size(large.size)}; each scan can be at most 512 MB.`);
        list.hidden = false;
        list.innerHTML = files.map((file, index) => `<li data-upload="${index}"><strong>${escape(file.name)}</strong> <small>${size(file.size)}</small> <span data-upload-state>${sent.has(file) ? "Uploaded" : "Waiting"}</span></li>`).join("");
        const failed = [], deleted = [];
        form.elements.files.disabled = true;
        uploading += 1;
        try {
          for (const [index, file] of files.entries()) {
            const state = list.querySelector(`[data-upload="${index}"] [data-upload-state]`);
            if (sent.has(file)) continue;
            progress.hidden = false;
            progress.textContent = `File ${index + 1} of ${files.length}`;
            state.textContent = "Uploading…";
            try {
              // A section's scan takes minutes over a slow connection; a retry after a lost answer returns the same scan.
              const job = await uploadWithProgress(file, (loaded, total) => {
                state.textContent = loaded >= total ? "Checking the file…" : `${megabytes(loaded)} of ${megabytes(total)} MB`;
              });
              sent.set(file, job);
              state.innerHTML = job.deleted
                ? `Uploaded before and deleted. <a href="#inbox?status=deleted">Restore it under Deleted</a> to use it.`
                : `${job.existing ? "Already in the Inbox" : "Uploaded"} · <a href="#inbox/job/${escape(job.id)}">Open scan</a>`;
              if (job.deleted) deleted.push(file);
            } catch (cause) {
              state.textContent = cause.message;
              state.dataset.failed = "";
              failed.push(file);
            }
          }
        } finally { uploading -= 1; form.elements.files.disabled = false; }
        progress.textContent = `${files.length - failed.length} of ${files.length} file${files.length === 1 ? "" : "s"} uploaded`;
        signature = "";
        void refresh();
        if (failed.length) throw new Error(`${failed.length} of ${files.length} ${files.length === 1 ? "file was" : "files were"} not added. Upload again to retry ${failed.length === 1 ? "it" : "them"}.`);
        if (deleted.length) throw new Error(`${deleted.length === 1 ? "A file was" : `${deleted.length} files were`} uploaded before and deleted. Restore ${deleted.length === 1 ? "its scan" : "their scans"} under Deleted to use ${deleted.length === 1 ? "it" : "them"} again.`);
        const jobs = files.map((file) => sent.get(file));
        const owned = current();
        if (owned) dialog().close();
        // One scan opens at once when the Inbox is still on screen; from anywhere else the toast offers it instead of taking over.
        const open = (job) => ({label: "Open scan", run: () => { location.hash = `inbox/job/${job.id}`; }});
        const onList = /^#inbox(\?|$)/.test(location.hash);
        if (jobs.length === 1 && onList && owned) location.hash = `inbox/job/${jobs[0].id}`;
        toast(jobs.length === 1
          ? (jobs[0].existing ? "This scan was already in the Inbox." : "Scan uploaded. Its pages are being checked.")
          : `${jobs.length} scans uploaded. Their pages are being checked.`,
        jobs.length === 1 && (!onList || !owned) ? {tone: "success", action: open(jobs[0])} : false);
      });
    });
  }

  // PDFs dropped anywhere on the Inbox list open the upload with them chosen (never on a scan's page, where a drop means nothing).
  const onListPage = () => !root.hidden && Boolean(root.querySelector(":scope > [data-inbox-list-page]")) && !location.hash.startsWith("#inbox/job/");
  const carriesFiles = (event) => [...(event.dataTransfer?.types || [])].includes("Files");
  root.addEventListener("dragover", (event) => {
    if (!onListPage() || !carriesFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    root.querySelector("[data-inbox-list-page]")?.classList.add("inbox-dropping");
  });
  root.addEventListener("dragleave", (event) => {
    if (!root.contains(event.relatedTarget)) root.querySelector("[data-inbox-list-page]")?.classList.remove("inbox-dropping");
  });
  root.addEventListener("drop", (event) => {
    root.querySelector("[data-inbox-list-page]")?.classList.remove("inbox-dropping");
    if (!onListPage() || !carriesFiles(event)) return;
    event.preventDefault();
    if (dialog()?.open) return;
    const files = [...event.dataTransfer.files];
    if (files.length) uploadScans(files);
  });

  async function useCode(initial = "") {
    showDialog(
      `<form id="inbox-code-form">${dialogHeader("Use a submission code", "Paste the code from your QR scan or saved submission link.")}<div class="dialog-body"><label class="field">Submission code<input name="token" required value="${escape(initial)}" placeholder="ti:…" autocomplete="off" spellcheck="false"></label><p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button class="button" type="button" data-action="close">Cancel</button><button class="button primary" type="submit">Continue</button></footer></form>`,
    );
    const form = document.querySelector("#inbox-code-form");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void busy(form, async (current) => {
        const token = form.elements.token.value.trim();
        const code = await api("/api/inbox/codes/resolve", "POST", { token });
        if (current()) await openSubmit(null, { ...code, token });
      });
    });
  }

  function showSecret(value, code = false) {
    showDialog(
      `${dialogHeader(code ? "Your submission code" : "Your API key", "Save this now. The secret is shown only once.")}<div class="dialog-body">${code ? `<div class="inbox-qr">${value.qr_svg}</div><p>${escape(value.label)} · Expires ${escape(date(value.expires_at))}</p>` : `<p>${escape(value.label)}</p>`}<label class="field">${code ? "Submission code" : "API key"}<input readonly value="${escape(value.token)}" aria-label="${code ? "Generated submission code" : "Generated API key"}"></label>${code ? `<label class="field">Submission link<input readonly value="${escape(location.origin + location.pathname + "#inbox?code=" + encodeURIComponent(value.token))}"></label>` : '<p class="field-hint">Use this as a bearer token for the Inbox API. API keys cannot authorize consequential jobs or manage other keys.</p>'}</div><footer class="dialog-footer"><button class="button primary" type="button" data-action="close">Done</button></footer>`,
    );
  }

  async function access() {
    const intended = captureDialogIntent(dialog());
    const data = await api("/api/inbox/access");
    if (!intended()) return;
    showDialog(
      `${dialogHeader("Codes & API access", "Use fixed submission codes or connect your own scripts.")}<div class="dialog-body inbox-access"><div class="inbox-access-heading"><h3>Submission codes</h3><button class="button" data-inbox-action="new-code">Create code</button></div>${data.codes.map((item) => `<div class="inbox-access-row"><div><strong>${escape(item.label)}</strong><small>${item.revoked ? "Revoked" : item.used_message_id ? "Used" : Date.parse(item.expires_at) < Date.now() ? "Expired" : "Expires " + escape(date(item.expires_at))}</small></div>${!item.revoked && !item.used_message_id ? `<button class="text-button" data-inbox-action="revoke" data-kind="codes" data-id="${item.id}">Revoke</button>` : ""}</div>`).join("") || '<p class="field-hint">No submission codes yet.</p>'}<div class="inbox-access-heading"><h3>API keys</h3><button class="button" data-inbox-action="new-key">Create API key</button></div>${data.keys.map((item) => `<div class="inbox-access-row"><div><strong>${escape(item.label)}</strong><small>${item.revoked ? "Revoked" : "Active"}</small></div>${item.revoked ? "" : `<button class="text-button" data-inbox-action="revoke" data-kind="keys" data-id="${item.id}">Revoke</button>`}</div>`).join("") || '<p class="field-hint">No API keys yet.</p>'}${calloutMarkup({tone: "info", title: `Email delivery is ${data.email_configured ? "configured through a signed gateway" : "not configured. Web uploads, codes, API clients, and the local CLI are available"}.`, className: "inbox-access-email"})}</div><footer class="dialog-footer"><button class="button primary" data-action="close">Done</button></footer>`,
    );
  }

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-inbox-action]");
    if (!button || button.disabled) return;
    const action = button.dataset.inboxAction;
    try {
      if (action === "submit") return void openSubmit();
      if (action === "upload") return void uploadScans();
      if (action === "use-code") return void useCode();
      if (action === "combine") return void openCombine();
      if (action === "toggle-select") {
        selecting = !selecting;
        if (!selecting) selected.clear();
        signature = "";
        await refresh();
        root.querySelector('[data-inbox-action="toggle-select"]')?.focus({preventScroll: true});
        return;
      }
      if (action === "clear-selection") {
        selected.clear();
        root.querySelectorAll("[data-inbox-select]").forEach((box) => { box.checked = false; });
        return void showSelection();
      }
      if (action === "access") return void (await access());
      if (action === "new-code") return void openSubmit(null, null, true);
      if (action === "filter") {
        offset = 0;
        signature = "";
        const href = button.dataset.inboxStatus === "current" ? "#inbox" : `#inbox?status=${button.dataset.inboxStatus}`;
        if (location.hash === href) return void refresh();
        location.hash = href;
        return;
      }
      if (action === "needs-filter") {
        needsOnly = button.dataset.inboxNeeds === "needs";
        offset = 0;
        signature = "";
        await refresh();
        root.querySelector(`[data-inbox-needs="${needsOnly ? "needs" : "all"}"]`)?.focus({preventScroll: true});
        return;
      }
      if (action === "clear-search") {
        pictureGeneration++;
        query = "";
        pictures = null;
        offset = 0;
        root.querySelector("#inbox-search").value = "";
        signature = "";
        await refresh();
        root.querySelector("#inbox-search")?.focus({ preventScroll: true });
        return;
      }
      if (action === "previous" || action === "next") {
        offset = Math.max(0, offset + (action === "next" ? 50 : -50));
        return void refresh();
      }
      if (action === "refresh") {
        signature = "";
        return void refresh();
      }
      if (action === "refresh-scan") {
        // The scan page's one Refresh: the job, the scan's page (redrawn by the job's render) and a bubble sheet panel on it.
        signature = "";
        nextAsked.clear();  // and Current is read again for the next scan that needs you
        root.querySelectorAll(".bubble-sheets").forEach((panel) => panel.dispatchEvent(new CustomEvent("scan-association-changed")));
        return void (await refresh());
      }
      if (action === "revoke") {
        const current = formCurrent(button);
        await api(
          `/api/inbox/access/${button.dataset.kind}/${button.dataset.id}/revoke`,
          "POST",
          {},
        );
        if (current()) await access();
        return;
      }
      if (action === "new-key") {
        showDialog(
          `<form id="inbox-key-form">${dialogHeader("Create API key")}<div class="dialog-body"><label class="field">Key label<input name="label" required maxlength="100" placeholder="e.g. Home server scripts"></label><p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button class="button" type="button" data-action="close">Cancel</button><button class="button primary" type="submit">Create key</button></footer></form>`,
        );
        const form = document.querySelector("#inbox-key-form");
        form.addEventListener("submit", (event) => {
          event.preventDefault();
          void busy(form, async (current) => {
            const key = await api("/api/inbox/keys", "POST", {label: form.elements.label.value});
            if (current()) showSecret(key);
          });
        });
        return;
      }
      if (action === "review") return void openSubmit(button.dataset.id);
      if (action === "approve") {
        const intended = captureDialogIntent(dialog());
        const job = await api(`/api/inbox/jobs/${button.dataset.id}`);
        if (!intended()) return;
        showDialog(
          `<form id="inbox-approval-form">${dialogHeader("Approve this command", "This authorizes the exact command shown below for execution.")}<div class="dialog-body"><strong>${escape(label(job.action))}</strong><p>${escape(job.scope_label)}</p><pre class="inbox-approval-command">${escape(JSON.stringify(job.parameters, null, 2))}</pre><p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button class="button" type="button" data-action="close">Cancel</button><button class="button primary" type="submit">Approve execution</button></footer></form>`,
        );
        const form = document.querySelector("#inbox-approval-form");
        form.addEventListener("submit", (event) => {
          event.preventDefault();
          void busy(form, async (current) => {
            await api(`/api/inbox/jobs/${job.id}/approve`, "POST", {
              version: job.version,
            });
            if (current()) dialog().close();
            void refresh();
          });
        });
        return;
      }
      if (action === "delete") {
        // A warning that says exactly what deleting does and does not do, and a confirmation the server checks too.
        const fromTile = !!button.closest(".inbox-scan-tile");
        const scanJob = button.dataset.scan === "true" || !!root.querySelector(".inbox-scan-job");
        const title = button.dataset.title || "this submission";
        // A choice made from a '⋯' menu gives focus back to the menu's button when the dialog closes (its item is hidden by then).
        const menuButton = button.closest(".action-menu")?.querySelector(".action-menu-trigger");
        showDialog(`<form id="inbox-delete-form">${dialogHeader("Delete from the Inbox?", escape(title))}<div class="dialog-body">
          <p role="alert"><strong>This takes the submission out of the Inbox.</strong> It disappears from every list and search, and nothing it holds counts anywhere any more${scanJob ? ": its labeled pages no longer mark papers as seen or conflict with other scans, its grouped exams and bubble sheet readings are left out, and students it was matched to become free for other sheets" : ""}.</p>
          <p><strong>It does not undo anything already applied</strong> (a roster, accommodations or bookings imported from it stay as they are), and it does not erase anything: the original, its files and its history are retained and backed up as before, so no disk space is freed. You can bring it back from the <strong>Deleted</strong> tab with <strong>Restore to the Inbox</strong>.</p>
          <label class="field">Reason<span class="field-optional">optional</span><input name="reason" maxlength="500" placeholder="e.g. aborted scan, scanned again"></label>
          <label class="checkbox-field"><input type="checkbox" name="understood" required> I understand, delete it from the Inbox.</label>
          <p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button class="button" type="button" data-action="close">Keep it</button><button class="danger-button" type="submit">Delete from Inbox</button></footer></form>`);
        const form = document.querySelector("#inbox-delete-form"), error = form.querySelector(".form-error");
        const current = formCurrent(form), from = location.hash;
        if (menuButton) dialog().addEventListener("close", () => { if (menuButton.isConnected) menuButton.focus(); }, {once: true});
        form.onsubmit = async (event) => {
          event.preventDefault();
          const submit = form.querySelector('button[type="submit"]');
          if (submit.disabled) return;
          setBusy(submit, true);
          try {
            const id = button.dataset.id;
            await api(`/api/inbox/jobs/${id}/delete`, "POST", { version: Number(button.dataset.version), confirmation: "delete", reason: form.elements.reason.value });
            if (current()) dialog().close();
            // Deleting is a recorded tombstone: Restore undoes it at once, the same request as the Deleted tab's Restore to the Inbox.
            toast("Deleted from the Inbox.", {tone: "success", action: {label: "Restore", run: async () => {
              try {
                await api(`/api/inbox/jobs/${id}/restore`, "POST", {});
                signature = "";
                toast("Restored to the Inbox.");
                await refresh();
              } catch (cause) { toast(cause.message, true); }
            }}});
            if (fromTile) {
              signature = "";
              await refresh();
            } else if (location.hash === from) location.hash = "#inbox";
          } catch (cause) { error.textContent = cause.message; error.hidden = false; setBusy(submit, false); }
        };
        return;
      }
      if (action === "start-over") {
        // Kept for this dialog: sending again after a failure finishes the same start-over instead of making another scan.
        const requestId = nonce();
        const title = button.dataset.title || "this scan";
        showDialog(`<form id="inbox-start-over-form">${dialogHeader("Start this scan over?", escape(title))}<div class="dialog-body">
          <p>A new scan is made of the same PDFs, exactly as they were uploaded, and goes through every stage again from the start: its pages are checked, first pages found and read, students registered and papers grouped by the rules as they are now.</p>
          <p>This scan moves to <strong>Deleted</strong>, where it can be restored. Nothing is erased: what was done on it (grouping, readings, registrations) stays with it and is not carried over. A scan whose papers were already reviewed cannot be started over.</p>
          <label class="checkbox-field"><input type="checkbox" name="keep" checked> Keep its assignment and sections, and look for its papers' first pages again with the same settings once its pages are checked</label>
          <label class="checkbox-field"><input type="checkbox" name="confirmed"> Start this scan over and move it to Deleted.</label>
          <p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button class="button" type="button" data-action="close">Cancel</button><button class="button primary" type="submit">Start over</button></footer></form>`);
        const form = document.querySelector("#inbox-start-over-form");
        form.addEventListener("submit", (event) => {
          event.preventDefault();
          void busy(form, async (current) => {
            if (!form.elements.confirmed.checked) throw new Error("Tick the box to confirm starting this scan over.");
            // Copying a section's scan takes a while; a lost answer is recovered by sending the same request again.
            const made = await api(`/api/inbox/jobs/${button.dataset.id}/start-over`, "POST", {request_id: requestId, confirmation: "start over", keep: form.elements.keep.checked}, {}, 10 * 60 * 1000);
            if (!current()) return;
            dialog().close();
            signature = "";
            location.hash = `inbox/job/${made.id}`;
            toast("Started over. The new scan's pages are being checked.");
          });
        });
        return;
      }
      if (action === "restore") {
        setBusy(button, true);
        await api(`/api/inbox/jobs/${button.dataset.id}/restore`, "POST", {});
        toast("Restored to the Inbox.");
        await refresh();
        return;
      }
      if (action === "cancel" && button.dataset.scan === "true") {
        // Cancelling a scan not yet reviewed is not undone from its menu (Retry waits for the review, which a cancelled scan never gets), so it asks.
        const title = button.dataset.title || "this scan";
        const menuButton = button.closest(".action-menu")?.querySelector(".action-menu-trigger");
        showDialog(`<form id="inbox-cancel-form">${dialogHeader("Cancel this scan?", escape(title))}<div class="dialog-body">
          <p>It leaves <strong>Current</strong> and is listed under <strong>History</strong> as cancelled. Nothing is erased, and what was done on it stays with it.</p>
          ${button.dataset.reviewed === "true" ? "<p>To bring it back, open it from History and choose <strong>Retry scan</strong> in its ⋯ menu.</p>"
            : "<p>A scan you have not reviewed cannot be queued again. To work on the same PDFs later, open it from History and choose <strong>Start over</strong>.</p>"}
          <p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button class="button" type="button" data-action="close">Keep it</button><button class="danger-button" type="submit">Cancel scan</button></footer></form>`);
        const form = document.querySelector("#inbox-cancel-form"), error = form.querySelector(".form-error");
        const current = formCurrent(form);
        if (menuButton) dialog().addEventListener("close", () => { if (menuButton.isConnected) menuButton.focus(); }, {once: true});
        form.onsubmit = async (event) => {
          event.preventDefault();
          const submit = form.querySelector('button[type="submit"]');
          if (submit.disabled) return;
          setBusy(submit, true);
          try {
            await api(`/api/inbox/jobs/${button.dataset.id}/cancel`, "POST", {version: Number(button.dataset.version)});
            if (current()) dialog().close();
            toast("Scan cancelled. It is listed under History.", {tone: "success", action: {label: "History", run: () => { location.hash = "#inbox?status=history"; }}});
            signature = "";
            await refresh();
          } catch (cause) { error.textContent = cause.message; error.hidden = false; setBusy(submit, false); }
        };
        return;
      }
      if (["cancel", "retry"].includes(action)) {
        setBusy(button, true);
        await api(`/api/inbox/jobs/${button.dataset.id}/${action}`, "POST", {
          version: Number(button.dataset.version),
        });
        await refresh();
      }
    } catch (error) {
      toast(error.message, true);
    } finally {
      setBusy(button, false);
    }
  });
  armPoll();
  function resourceFile(file) {
    // One retained document: download it, with its Inbox submission as a quiet icon link beside it.
    const detail = [file.question, size(file.size)].filter(Boolean).join(" · ");
    return `<div class="inbox-course-file"><a class="inbox-artifact" ${viewLink(`/api/inbox/artifacts/${file.id}/download`, file.filename, escape)}>${icon("folder")}<span><strong>${escape(file.filename)}</strong><small>${escape(detail)}</small></span>${icon("download")}</a><a class="icon-button inbox-course-history" href="#inbox/job/${file.job_id}" aria-label="Submission history of ${escape(file.filename)}" title="Submission history">${icon("route")}</a></div>`;
  }

  function resourceGroups(files) {
    // Rubric sources are named by the assignment and exam version they belong to (read-only context from the server);
    // everything else is "Other documents". The order is the server's: newest first.
    const groups = new Map();
    for (const file of files) {
      const label = file.assignment_title ? `${file.assignment_title}${file.exam_version ? ` · Version ${file.exam_version}` : ""} rubrics` : "Other documents";
      const key = file.assignment_title ? `${file.assignment_id || file.assignment_title}:${file.exam_version || ""}` : "other";
      if (!groups.has(key)) groups.set(key, { key, label, files: [] });
      groups.get(key).files.push(file);
    }
    return [...groups.values()].sort((a, b) => (a.key === "other") - (b.key === "other") || a.label.localeCompare(b.label, undefined, { numeric: true }));
  }

  function resourcesMarkup(level, files) {
    const groups = resourceGroups(files);
    if (groups.length === 1 && groups[0].key === "other") return `<div class="inbox-artifacts">${files.map(resourceFile).join("")}</div>`;
    return `<div class="inbox-resource-groups">${groups.map((item) => disclosureMarkup({ label: item.label, hint: item.files.length, key: `resources:${level}:${item.key}`, className: "inbox-resource-group", body: `<div class="inbox-artifacts">${item.files.map(resourceFile).join("")}</div>` })).join("")}</div>`;
  }

  async function courseResources(group, section) {
    const nodes = [...document.querySelectorAll("[data-inbox-resources]")];
    if (!nodes.length) return;
    try {
      const data = await api(
        `/api/inbox/course-groups/${group.id}/resources${section === null ? "" : `?section=${section}`}`,
      );
      for (const node of nodes) {
        if (!node.isConnected) continue;
        const level = node.dataset.inboxResources;
        const files = data[level];
        node.hidden = !files.length && level !== "course";
        node.innerHTML = files.length
          ? `${level === "course" ? "" : `<h3>${level === "term" ? "Term documents" : "Section documents"}</h3>`}${resourcesMarkup(level, files)}`
          : '<p class="shared-resources-empty">No shared resources have been added yet.</p>';
      }
    } catch (error) {
      for (const node of nodes) {
        if (!node.isConnected) continue;
        node.hidden = false;
        node.innerHTML = `<p class="form-error">${escape(error.message)}</p>`;
      }
    }
  }
  return {
    courseResources,
    uploadScans,
    render() {
      // The shell repaints its defaults on every render; a job page shown again keeps the name it was given.
      if (header && lastHeader && lastHeader.route === location.hash.split("?")[0]) header.set(shellContext() ? {...lastHeader, crumbs: undefined} : lastHeader);
      // #inbox?codes (from Workspace settings) opens Codes & API access once, and #inbox?upload=1 (every "Upload scans" next step)
      // opens Upload scanned assignment once. The address becomes the list's first, so the read below (and the shell's second
      // draw, which joins it) is of the list's own address: one read.
      const params = new URLSearchParams(location.hash.split("?")[1] || "");
      const opening = location.hash.startsWith("#inbox/job/") ? [] : ["codes", "upload"].filter((key) => params.has(key));
      if (opening.length) {
        for (const key of opening) params.delete(key);
        const rest = params.toString();
        history.replaceState(history.state, "", `${location.pathname}${location.search}#inbox${rest ? `?${rest}` : ""}`);
      }
      if (!location.hash.startsWith("#inbox/job/")) {
        const requested = new URLSearchParams(location.hash.split("?")[1] || "").get("status");
        // Links to the former Needs review, Queued, Running and Failed tabs open Current.
        const selected = ["completed", "cancelled"].includes(requested) ? "history" : CURRENT.includes(requested) ? "current" : requested;
        const nextStatus = [...views.map(([key]) => key), "archived", "deleted"].includes(selected) ? selected : "current";
        if (nextStatus !== status) offset = 0;
        status = nextStatus;
        listHref = status === "current" ? "#inbox" : `#inbox?status=${status}`;
      }
      const moved = route !== location.hash;
      if (moved) {
        // The catalog is read again on each navigation, except between the pages of one scan.
        const jobOf = (hash) => hash.match(/^#inbox\/job\/([a-f0-9-]+)/)?.[1];
        if (!jobOf(route) || jobOf(route) !== jobOf(location.hash)) catalog = null;
        route = location.hash;
        signature = "";
        missingJob = "";
      }
      if (!root.hasChildNodes())
        root.innerHTML = loadingMarkup("Opening Teaching Inbox…");
      // The shell draws a view twice as it opens: the second draw of the same page joins the read the first one started. Its other
      // draws (its 30 s state poll) read only when this page's own poll would by now, so History is read once a minute and a page
      // just read is not read again; coming back from another section reads at once.
      const back = leftSection;
      leftSection = false;
      const due = back || Boolean(inflight) || readFor !== readKey() || Date.now() - readAt >= pollDelay();
      if (moved) void refresh();
      else if (due) void poll();
      if (opening.includes("codes")) void access().catch((error) => toast(error.message, true));
      if (opening.includes("upload")) uploadScans();
      const code = params.get("code");
      if (code && openingCode !== code) {
        openingCode = code;
        void useCode(code);
      }
    },
    openSubmit: () => openSubmit(),
  };
}
