// @ts-check
/* The rail's system status (UX overhaul, 2026-09-26): one button in the sidebar footer that says whether the workspace is
   reachable and busy, and opens a popover with the worker heartbeat, the email gateway, the last verified backup, the disks
   and the work running or queued, each linking to where it is handled.

   It also owns the 'ready for you' and 'finished' notices (amendment 12): the shell reads /api/workspace/overview on its
   poll, and comparing two successive reads says what changed in between (a scan the worker finished and that now needs the
   instructor, a queue that emptied, a new problem). Each notice is a toast with a link to the stage page where the work is
   done. Everything here is a VIEW of saved records: it decides, clears and sends nothing, names no student, and never calls
   a backup verified that the server did not report as complete, nor infers progress from an old heartbeat (the wording is
   Home's Workspace health). */

import { scanNext } from "./scan-flow.js";
import { setBusy } from "./ui.js";

const WORKER = {
  active: "Worker responding",
  busy: "Work in progress · heartbeat overdue",
  stalled: "Worker not responding · running work may be stuck",
  stale: "Worker heartbeat overdue",
  unknown: "Worker has not reported yet",
};

/** 'Sep 26, 2:16 AM' in the browser's own clock (heartbeats and backups are instants, not campus dates). */
function when(iso) {
  if (!iso) return "never";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "unknown" : date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function bytes(value) {
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let amount = Number(value) || 0, unit = 0;
  while (amount >= 1000 && unit < units.length - 1) { amount /= 1000; unit += 1; }
  return `${amount.toLocaleString(undefined, { maximumFractionDigits: amount < 10 && unit ? 1 : 0 })} ${units[unit]}`;
}

/** 'just now', '12 s ago', '3 min ago'. @param {number} since ms timestamp @param {number} [now] */
export function ago(since, now = Date.now()) {
  if (!since) return "not yet";
  const seconds = Math.max(0, Math.round((now - since) / 1000));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds} s ago`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} min ago` : `${Math.round(minutes / 60)} h ago`;
}

/** The backup line: 'Verified …' only for a backup the server reported complete. @param {any} backup */
export function backupLabel(backup) {
  if (!backup) return "Backup status unavailable";
  if (backup.verified_at) return `Verified ${when(backup.verified_at)}`;
  return ({ disabled: "Backup not configured", not_run: "No verified backup recorded", unavailable: "Backup status unavailable" })[backup.status]
    || `Backup ${String(backup.status || "status unknown").replaceAll("_", " ")} · not verified`;
}

/** Problems of the workspace itself (worker, storage, disks): the server's own #overview attention cards. @param {any} overview */
function healthIssues(overview) {
  return (overview?.attention || []).filter((item) => String(item.href || "").startsWith("#overview")).reduce((sum, item) => sum + (Number(item.count) || 0), 0);
}

/**
 * The button's words and tone. `online` is the shell's last /api/state read (null before the first).
 * @param {any} overview @param {{online: boolean | null}} options
 */
export function statusSummary(overview, { online }) {
  if (online === null) return { label: "Connecting to server…", tone: "neutral" };
  if (!online) return { label: "Server unavailable", tone: "danger" };
  if (!overview?.operations) return { label: "Connected", tone: "success" };
  const { totals, worker } = overview.operations;
  if (healthIssues(overview) || ["stalled", "stale"].includes(worker?.state)) return { label: "Connected · check health", tone: "warning" };
  if (totals?.running) return { label: `Connected · ${totals.running.toLocaleString()} running`, tone: "info" };
  if (totals?.pending) return { label: `Connected · ${totals.pending.toLocaleString()} queued`, tone: "info" };
  return { label: "Connected · all clear", tone: "success" };
}

/**
 * The popover's body.
 * @param {any} overview /api/workspace/overview (may be null before its first read)
 * @param {{online: boolean | null, email: boolean | null, updated: string}} facts
 * @param {(value: unknown) => string} escape
 */
export function statusPanelMarkup(overview, { online, email, updated }, escape) {
  const row = (label, value, detail = "") => `<div class="status-row"><dt>${escape(label)}</dt><dd><strong>${escape(value)}</strong>${detail ? `<small>${escape(detail)}</small>` : ""}</dd></div>`;
  const operations = overview?.operations;
  const rows = [row("Server", online === null ? "Connecting…" : online ? "Connected" : "Unavailable", online === false ? "Your last loaded workspace is shown." : "")];
  if (operations) {
    const worker = operations.worker || {};
    rows.push(row("Worker", WORKER[worker.state] || "Worker status unavailable", `Last heartbeat: ${when(worker.heartbeat_at)}`));
  }
  rows.push(row("Email gateway", email === null ? "Checking…" : email ? "Configured" : "Not configured"));
  if (operations?.storage) {
    const { backup, disks = [] } = operations.storage;
    rows.push(row("Last backup", backupLabel(backup), backup?.error_count ? `${backup.error_count} backup error${backup.error_count === 1 ? "" : "s"}` : ""));
    for (const disk of disks) {
      rows.push(row(disk.label, disk.state === "unavailable" ? "Disk unavailable" : `${bytes(disk.free_bytes)} free`, disk.state === "unavailable" ? "Check the storage connection" : `${disk.free_percent}% available${disk.state === "low" ? " · Running low" : ""}`));
    }
  }
  const busy = (operations?.queues || []).filter((queue) => queue.running || queue.pending);
  const work = !operations ? "" : busy.length
    ? `<ul class="status-work">${busy.map((queue) => `<li><a href="${escape(queue.href)}"><span>${escape(queue.label)}</span><small>${[queue.running ? `${queue.running.toLocaleString()} running` : "", queue.pending ? `${queue.pending.toLocaleString()} queued` : ""].filter(Boolean).join(" · ")}</small></a></li>`).join("")}</ul>`
    : `<p class="status-idle">Nothing is running or queued.</p>`;
  return `<dl class="status-rows">${rows.join("")}</dl>${operations ? `<h3 class="status-heading">Running and queued</h3>${work}` : ""}
    <div class="status-foot"><span class="status-updated" data-status-updated>${escape(updated)}</span><a class="link-arrow" href="#overview?operations">Workspace health</a><button type="button" class="text-button" data-status-refresh>Refresh</button></div>`;
}

const WAITING = new Set(["wait"]);
const NEEDS = new Set(["todo", "check"]);

/**
 * What changed between two reads of the overview, as notices {message, label, href, tone, key}. Nothing on the first read.
 * `here` is the hash on screen: a notice pointing exactly there is left out (the page already shows it).
 * @param {any} previous @param {any} next @param {{here?: string, limit?: number}} [options]
 */
export function transitions(previous, next, { here = "", limit = 3 } = {}) {
  if (!previous || !next || previous.generated_at === next.generated_at) return [];
  const notices = [];
  const seenScans = new Map((previous.recent_scans || []).map((job) => [job.id, job]));
  // `recent_scans` is the few most recent scans still in the Inbox, so deleting, combining or starting over a scan (or restoring
  // one) brings an older scan into the list. Only a scan newer than every scan the last read listed has just arrived (2026-10-07:
  // deleting a test rescan announced a Quiz 3 scan from September 29 as "New scan received").
  const newest = Math.max(-Infinity, ...(previous.recent_scans || []).map((job) => Date.parse(job.created_at)).filter(Number.isFinite));
  let scanNotices = 0;
  for (const job of next.recent_scans || []) {
    const before = seenScans.get(job.id);
    const scan = job.scan || {}, headline = scan.headline || "A scan";
    if (!before) {
      if (!(Date.parse(job.created_at) > newest)) continue;
      if (!String(here).startsWith("#inbox")) notices.push({ key: `scan:${job.id}:new`, message: `New scan received: ${headline}`, label: "Open scan", href: job.href, tone: "info" });
      scanNotices += 1;
      continue;
    }
    const was = before.scan?.state;
    if (!WAITING.has(was) || was === scan.state) continue;
    const step = scanNext(job.id, scan.stages || []);
    if (NEEDS.has(scan.state)) notices.push({ key: `scan:${job.id}:ready`, message: `${headline}: ready for you`, label: step.label, href: step.href, tone: "info" });
    else if (scan.state === "done") notices.push({ key: `scan:${job.id}:done`, message: `${headline}: finished`, label: step.label, href: step.href, tone: "success" });
    else continue;
    scanNotices += 1;
  }
  const queues = new Map((previous.operations?.queues || []).map((queue) => [queue.key, queue]));
  for (const queue of next.operations?.queues || []) {
    const before = queues.get(queue.key);
    if (!before) continue;
    // The Inbox's own queues are told scan by scan above.
    if (scanNotices && String(queue.href).startsWith("#inbox")) continue;
    if ((queue.failed || 0) > (before.failed || 0)) {
      const failed = queue.failed - (before.failed || 0);
      notices.push({ key: `queue:${queue.key}:failed`, message: `${queue.label}: ${failed.toLocaleString()} failed`, label: "Open", href: queue.href, tone: "warning" });
    } else if ((before.running || 0) + (before.pending || 0) > 0 && !queue.running && !queue.pending) {
      notices.push({ key: `queue:${queue.key}:done`, message: `${queue.label}: finished`, label: "Open", href: queue.href, tone: "success" });
    }
  }
  const counted = new Map((previous.attention || []).map((item) => [`${item.href}|${item.label}`, Number(item.count) || 0]));
  for (const item of next.attention || []) {
    const href = String(item.href || "");
    if (href.startsWith("#inbox") && scanNotices) continue;
    if (notices.some((notice) => notice.href === href)) continue;
    const before = counted.get(`${href}|${item.label}`) || 0;
    if ((Number(item.count) || 0) <= before) continue;
    const health = href.startsWith("#overview");
    notices.push({ key: `attention:${href}|${item.label}`, message: health ? `Workspace health: ${item.label}` : `${item.label} (${Number(item.count).toLocaleString()})`, label: health ? "Workspace health" : "Open", href, tone: "warning" });
  }
  return notices.filter((notice) => notice.href !== here).slice(0, limit);
}

/**
 * Wire the rail's status button and popover, and the notices. `isOnline()` is the shell's connection state.
 * @param {{doc?: Document, api: (path: string) => Promise<any>, overviewStore: any, toast: Function, refresh: () => Promise<unknown>, escape: (value: unknown) => string, isOnline: () => boolean | null}} options
 */
export function mountSystemStatus({ doc = globalThis.document, api, overviewStore, toast, refresh, escape, isOnline }) {
  const button = /** @type {HTMLButtonElement | null} */ (doc.querySelector(".sidebar .server-status"));
  const panel = /** @type {HTMLElement | null} */ (doc.getElementById("status-pop"));
  const label = doc.getElementById("connection-label");
  if (!button || !panel || !label) return { update() {} };
  const body = /** @type {HTMLElement} */ (panel.querySelector("[data-status-body]"));
  let email = /** @type {boolean | null} */ (null);
  let emailRead = 0;
  let previous = /** @type {any} */ (null);
  let ticker = 0;

  const open = () => panel.matches(":popover-open");
  function paint() {
    const summary = statusSummary(overviewStore.get(), { online: isOnline() });
    if (label.textContent !== summary.label) label.textContent = summary.label;
    button.dataset.tone = summary.tone;
    if (!open()) return;
    const markup = statusPanelMarkup(overviewStore.get(), { online: isOnline(), email, updated: `Updated ${ago(overviewStore.at?.() || 0)}` }, escape);
    if (body.dataset.markup !== markup) { body.innerHTML = markup; body.dataset.markup = markup; }
  }

  function place() {
    const box = button.getBoundingClientRect();
    const width = Math.min(panel.offsetWidth || 340, innerWidth - 16);
    panel.style.left = `${Math.max(8, Math.min(box.left, innerWidth - width - 8))}px`;
    const height = panel.offsetHeight;
    const above = box.top - height - 8;
    panel.style.top = `${above >= 8 ? above : Math.min(box.bottom + 8, Math.max(8, innerHeight - height - 8))}px`;
  }

  async function readEmail() {
    if (Date.now() - emailRead < 60000) return;
    emailRead = Date.now();
    try { email = Boolean((await api("/api/inbox/access")).email_configured); } catch { email = null; emailRead = 0; }
    paint();
  }

  panel.addEventListener("toggle", (event) => {
    const opened = /** @type {any} */ (event).newState === "open";
    button.setAttribute("aria-expanded", String(opened));
    clearInterval(ticker);
    if (!opened) return;
    paint();
    place();
    void readEmail();
    // A read older than the poll is refreshed now; the 'Updated … ago' line keeps counting while the popover is open.
    void overviewStore.refresh({ maxAge: 15000 });
    ticker = setInterval(() => {
      const node = body.querySelector("[data-status-updated]");
      if (node) node.textContent = `Updated ${ago(overviewStore.at?.() || 0)}`;
    }, 1000);
  });
  panel.addEventListener("click", async (event) => {
    const target = /** @type {HTMLElement} */ (event.target);
    if (target.closest("a[href]")) { panel.hidePopover?.(); return; }
    const refreshButton = /** @type {HTMLButtonElement | null} */ (target.closest("[data-status-refresh]"));
    if (!refreshButton) return;
    setBusy(refreshButton, true);
    try {
      await refresh();
      await overviewStore.refresh();
      emailRead = 0;
      await readEmail();
    } finally {
      setBusy(refreshButton, false);
    }
  });
  addEventListener("resize", () => { if (open()) place(); });

  overviewStore.subscribe((data) => {
    if (!data) { paint(); return; }
    const notices = transitions(previous, data, { here: location.hash });
    previous = data;
    for (const notice of notices) {
      toast(notice.message, { tone: notice.tone, persist: false, action: { label: `${notice.label} →`, run: () => { location.hash = notice.href; } } });
    }
    paint();
    if (open()) place();
  });

  return { update: paint };
}
