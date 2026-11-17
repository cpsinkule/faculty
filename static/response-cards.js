import { bindFilterChips, calloutMarkup, disclosureMarkup, emptyMarkup, filterChipsMarkup, loadingMarkup, registerLeaveGuard, markBusy, setBusy, dialogSession } from "./ui.js";
import { studentPicker } from "./student-picker.js";
import { viewTable } from "./file-viewer.js";
import { issueLabel } from "./response-card-label.js";

const FIELD_TYPES = {
  static: "Information", acknowledgment: "Acknowledgment", short_text: "Short text", long_text: "Long text",
  single_select: "Single choice", multi_select: "Multiple choice", confirmation: "Confirmation",
  boolean: "Yes / No", integer: "Integer", decimal: "Decimal", date: "Date", time: "Time",
  radio: "Radio group", attestation: "Typed attestation",
};
const CHOICES = ["single_select", "multi_select", "radio"];
const POLICIES = {once: "One submission", until_due: "Allow changes until due", unlimited: "Unlimited submissions", instructor_unlock: "Instructor unlock required"};
// Human names for the stored card states (never the raw codes).
const STATUS_LABELS = {issued: "Not opened", opened: "Opened", started: "Started", submitted: "Submitted", verified: "Verified",
  needs_review: "Needs review", expired: "Expired", revoked: "Revoked"};
const STATUS_TONES = {needs_review: "warning", verified: "success", submitted: "success", revoked: "danger", expired: "danger"};
const DELIVERY_LABELS = {not_sent: "Not sent", queued: "Queued", sent: "Sent", failed: "Failed", unconfirmed: "Unconfirmed", no_email: "No email address"};
const PUBLISH_LABELS = {pending: "Not published", published: "Published", failed: "Failed", complete: "Withdrawn"};
const RESPONDED = ["submitted", "verified", "needs_review"];
const AWAITING = ["issued", "opened", "started"];
// The issue page's filters. `?status=needs_review` and `?delivery=problem` (links from Home, the exam hub and the attention
// strip) open on theirs.
const FILTERS = [["", "All"], ["needs_review", "Flagged"], ["not_delivered", "Not delivered"], ["awaiting", "Awaiting"], ["responded", "Responded"],
  ["unpublished", "Not published"], ["closed", "Revoked or expired"], ["receipt_problem", "Receipt unconfirmed"], ["revocation_pending", "Revocation pending"]];
// Each stored card status can also be chosen on its own (the select's "Individual status" group; `?status=opened` and so on).
const INDIVIDUAL = ["issued", "opened", "started", "submitted", "verified", "expired", "revoked"];
// Seating's typed UIN confirmation: never exported with the answers (amendment 10).
const UIN_FIELD = "confirm_uin";

/** One answer as text: a list joined with "; ", yes/no for a box, nothing for no answer. @param {unknown} value */
export function cellText(value) {
  return value === null || value === undefined ? "" : Array.isArray(value) ? value.join("; ") : typeof value === "boolean" ? (value ? "Yes" : "No") : String(value);
}

/** One CSV cell, spreadsheet-escaped: a leading =, +, -, @, tab or return is neutralized and quoting follows RFC 4180. */
export function csvCell(value) {
  let text = cellText(value);
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

/** Whether a card field's answer is the student's UIN: checked as a digest, or against an expected value naming `student.uin`. */
function asksForUin(field) {
  const check = field.verification;
  if (!check) return false;
  return check.mode === "uin-digest" || /student\s*\.\s*uin/i.test(String(check.expected ?? ""));
}

/**
 * The answers of an issue as CSV rows: student, section, email, status, submitted time and one column per question. The typed UIN
 * confirmation, verification details and expected values are never included, nor are links, UINs or enrollment IDs: a field checked
 * against the student's UIN (a digest, or any mode whose expected value names `student.uin`) is left out, since its answer is the UIN.
 */
export function answersCsv(issue) {
  return answersRows(issue).map((row) => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
}

/** The rows `answersCsv` writes, header first, each answer as it is (the viewer shows them as text). @param {any} issue */
export function answersRows(issue) {
  const fields = (issue.schema?.fields || []).filter((field) => field.type !== "static" && field.id !== UIN_FIELD && !asksForUin(field));
  const labels = Object.fromEntries((issue.schema?.fields || []).map((field) => [field.id, field.label || field.id]));
  const rows = [["Student", "Section", "Email", "Response status", "Submitted at", "Answers differing from the card", ...fields.map((field) => field.label || field.id)]];
  for (const recipient of issue.recipients || []) {
    const submission = recipient.current_submission;
    const answers = submission?.answers || {};
    const differs = Object.entries(submission?.verification || {}).filter(([key, value]) => value?.matched === false && key !== UIN_FIELD).map(([key]) => labels[key] || key);
    rows.push([recipient.student_name, recipient.section_number, recipient.email || "", STATUS_LABELS[recipient.status] || recipient.status,
      submission?.submitted_at || "", differs, ...fields.map((field) => answers[field.id])]);
  }
  return rows;
}

export function createResponseCards({api, escape, toast, showDialog, dialogHeader, header = null}) {
  const base = "/api/response-cards";
  let context = null;
  let definitionId = "";
  let termId = "";
  let route = "";
  let generation = 0;
  let contextGeneration = 0;
  let editing = null;
  let dirty = false;
  let editorAnswers = {};
  const root = () => document.querySelector("#response-cards-section");
  const href = (kind, id) => `#response-cards/${kind}/${encodeURIComponent(id)}`;
  const fieldId = () => `field_${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`;
  const when = (value) => value ? new Date(value).toLocaleString() : "—";
  const shortDate = (value) => value ? new Date(value).toLocaleDateString(undefined, {month: "short", day: "numeric"}) : "";
  const shortWhen = (value) => value ? new Date(value).toLocaleString(undefined, {weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}) : "";
  const plural = (count, word, many = `${word}s`) => `${Number(count).toLocaleString()} ${count === 1 ? word : many}`;
  const option = (id, label, selected = "") => `<option value="${escape(id)}" ${String(id) === String(selected) ? "selected" : ""}>${escape(label)}</option>`;
  const formError = () => '<p class="form-error" role="alert" hidden></p>';
  const back = () => '<a class="back-link rc-back" aria-label="All response cards" href="#response-cards">← All response cards</a>';
  const crumbs = (label) => [{label: "Home", href: "#overview"}, {label: "Response Cards", href: label ? "#response-cards" : undefined}, ...(label ? [{label}] : [])];
  const query = () => new URLSearchParams(location.hash.split("?")[1] || "");
  const dropQuery = () => { if (location.hash.includes("?")) history.replaceState(history.state, "", location.hash.split("?")[0]); };

  // Unsaved template edits: the shell asks before leaving (the same question for a hash change and for closing the tab).
  registerLeaveGuard(() => dirty && route.startsWith("#response-cards/template/") && root() && !root().hidden
    ? "Discard your unsaved template changes?" : null);

  // Templates belong to a course definition; issues use a retained term and memberships.
  async function loadContext(selectedTerm = "") {
    const run = ++contextGeneration;
    const result = await api(`${base}/context${selectedTerm ? `?term_id=${encodeURIComponent(selectedTerm)}` : ""}`);
    if (run !== contextGeneration) return result;
    context = result;
    if (!context.definitions.some((item) => item.id === definitionId)) definitionId = context.definitions[0]?.id || "";
    const terms = context.terms.filter((item) => item.definition_id === definitionId);
    if (!terms.some((item) => item.id === termId)) termId = terms.find((item) => item.is_current)?.id || terms[0]?.id || "";
    return context;
  }

  async function perform(form, operation) {
    const page = location.hash.split("?")[0];
    const dialog = document.querySelector("#dialog");
    const session = dialog.contains(form) ? dialogSession(form, dialog) : () => form.isConnected;
    const current = () => page === location.hash.split("?")[0] && session();
    if (!current()) return;
    if (form.dataset.saving === "true") return;
    const controls = [...form.querySelectorAll("button, input, select, textarea")];
    const disabled = controls.map((item) => item.disabled);
    const error = form.querySelector('[role="alert"]');
    if (error) { error.hidden = true; error.textContent = ""; }
    form.dataset.saving = "true";
    controls.forEach((item) => { item.disabled = true; });
    try { await operation(current); }
    catch (cause) {
      if (error && current()) { error.textContent = cause.message; error.hidden = false; }
      else toast(cause.message, true);
    } finally {
      form.dataset.saving = "false";
      controls.forEach((item, index) => { item.disabled = disabled[index]; });
    }
  }

  // The page's last header: the shell puts its default back whenever the route changes, so a return to the same page
  // (which keeps its content) names its object again.
  let lastHeader = null;
  function setHeader(spec) { lastHeader = spec; header?.set(spec); }

  async function render(force = false) {
    const target = location.hash.split("?")[0];
    if (!force && target === route && root().childElementCount) {
      if (lastHeader && lastHeader.route.split("?")[0] === target) header?.set({...lastHeader, route: location.hash});
      applyListQuery();
      return;
    }
    const current = ++generation;
    route = target;
    root().innerHTML = loadingMarkup("Loading response cards…");
    try {
      await loadContext();
      if (current !== generation || root().hidden) return;
      const match = target.match(/^#response-cards\/(template|issue)\/(.+)$/);
      if (match?.[1] === "template") {
        const id = decodeURIComponent(match[2]);
        const template = id === "new" ? {id: null, name: "", definition_id: definitionId, schema: {title: "", description: "", fields: []}, versions: []} : await api(`${base}/templates/${encodeURIComponent(id)}`);
        if (current !== generation || root().hidden) return;
        editing = structuredClone(template); dirty = false; editorAnswers = {};
        renderEditor();
      } else if (match?.[1] === "issue") {
        const issue = await api(`${base}/issues/${encodeURIComponent(decodeURIComponent(match[2]))}`);
        if (current !== generation || root().hidden) return;
        renderIssue(issue);
      } else await renderList(current);
    } catch (cause) {
      if (current !== generation) return;
      root().innerHTML = `${back()}<div class="empty-state"><h3>Response cards unavailable</h3><p role="alert">${escape(cause.message)}</p><button class="button" id="rc-retry">Try again</button></div>`;
      root().querySelector("#rc-retry").onclick = () => render(true);
    }
  }

  // ---- The list: what needs attention across current terms, then this term's issues by exam, then the course's templates.

  function ago(value) {
    if (!value) return "";
    const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000));
    if (!Number.isFinite(seconds)) return "";
    if (seconds < 60) return `${seconds} s ago`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
    return `on ${shortDate(value)}`;
  }

  /** The page's one refresh control. Configured: the sync status with Sync now (which reloads the page after syncing). Not
   * configured: the warning callout already says sync waits for setup, so the list shows nothing here and an issue page a plain
   * Reload (`reload`). */
  function syncLine(buttonId, {reload = false} = {}) {
    if (!context.configured) return reload ? `<p class="rc-sync-line"><button type="button" class="text-button" id="${buttonId}">Reload</button></p>` : "";
    const synced = context.sync?.synced_at;
    const text = `Responses sync automatically every minute${synced ? ` · last synced ${ago(synced)}` : " · not synced yet"}.`;
    const stale = synced && Date.now() - new Date(synced).getTime() > 15 * 60 * 1000;
    return `<p class="rc-sync-line" role="status"${stale ? ' data-stale="true"' : ""}><span>${escape(text)}</span><button type="button" class="text-button" id="${buttonId}">Sync now</button></p>`;
  }

  function sectionNumbers(ids) {
    const numbers = (ids || []).map((id) => context.sections.find((item) => item.id === id)?.number).filter((value) => value !== undefined);
    return numbers.length ? `Section${numbers.length === 1 ? "" : "s"} ${numbers.join(", ")}` : "";
  }

  function attentionCount(counts = {}) {
    return (counts.needs_review || 0) + (counts.delivery_problem || 0) + (counts.receipt_problem || 0) + (counts.revocation_pending || 0);
  }

  function attentionChips(issue) {
    const counts = issue.counts || {};
    const chips = [];
    if (counts.needs_review) chips.push(`<a class="chip" data-tone="warning" href="${href("issue", issue.id)}?status=needs_review">${escape(plural(counts.needs_review, "flagged answer"))}</a>`);
    if (counts.delivery_problem) chips.push(`<a class="chip" data-tone="danger" href="${href("issue", issue.id)}?delivery=problem">${escape(`${plural(counts.delivery_problem, "email")} not delivered`)}</a>`);
    if (counts.receipt_problem) chips.push(`<a class="chip" data-tone="warning" href="${href("issue", issue.id)}?status=receipt_problem">${escape(`${plural(counts.receipt_problem, "receipt")} unconfirmed`)}</a>`);
    if (counts.revocation_pending) chips.push(`<a class="chip" data-tone="warning" href="${href("issue", issue.id)}?status=revocation_pending">${escape(`${plural(counts.revocation_pending, "revocation")} pending`)}</a>`);
    return chips.join("");
  }

  function attentionStrip(attention) {
    const items = attention?.items || [];
    if (!items.length) return "";
    return `<section class="rc-attention" id="rc-attention" aria-labelledby="rc-attention-title"><h3 id="rc-attention-title" tabindex="-1">Needs attention <span class="chip" data-tone="warning">${escape(items.length.toLocaleString())}</span></h3><ul class="rc-attention-list">${items.map((item) => {
      const where = [item.course_code, item.term_label, item.room_name].filter(Boolean).join(" · ");
      return `<li><a class="rc-attention-title" href="${href("issue", item.id)}${item.counts.needs_review ? "?status=needs_review" : item.counts.delivery_problem ? "?delivery=problem" : ""}">${escape(issueLabel(item))}</a><span class="rc-attention-where">${escape(where)}</span><span class="chip-row">${attentionChips(item)}</span></li>`;
    }).join("")}</ul></section>`;
  }

  function issueCard(item) {
    const counts = item.counts || {};
    const live = Math.max(0, (counts.total || 0) - (counts.revoked || 0));
    const responded = RESPONDED.reduce((sum, key) => sum + (counts[key] || 0), 0);
    const facts = [`${responded.toLocaleString()} of ${live.toLocaleString()} responded`];
    const unpublished = live - (counts.published || 0);
    if (unpublished > 0) facts.push(`${unpublished.toLocaleString()} not published`);
    if (counts.expired) facts.push(`${counts.expired.toLocaleString()} expired`);
    if (item.due_at) facts.push(`Due ${shortWhen(item.due_at)}`);
    const eyebrow = [item.room_name, sectionNumbers(item.section_ids), `issued ${shortDate(item.created_at)}`].filter(Boolean).join(" · ");
    return `<article class="card card--link rc-issue" data-href="${href("issue", item.id)}"${attentionCount(counts) ? ' data-attention="true"' : ""}><p class="eyebrow">${escape(eyebrow)}</p><h4><a href="${href("issue", item.id)}">${escape(issueLabel(item))}</a></h4><p class="rc-issue-line">${escape(facts.join(" · "))}</p><progress class="rc-progress" max="${Math.max(live, 1)}" value="${responded}" aria-label="${escape(`${responded} of ${live} responded`)}"></progress>${attentionCount(counts) ? `<div class="chip-row">${attentionChips(item)}</div>` : ""}</article>`;
  }

  function issueGroups(issues) {
    const groups = new Map();
    for (const item of issues) {
      const key = item.assignment_title || "";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    }
    const rank = (list) => list.reduce((sum, item) => sum + attentionCount(item.counts), 0);
    return [...groups.entries()]
      .map(([title, list]) => [title, list.sort((a, b) => attentionCount(b.counts) - attentionCount(a.counts) || String(b.created_at).localeCompare(String(a.created_at)))])
      .sort((a, b) => rank(b[1]) - rank(a[1]) || (a[0] ? 0 : 1) - (b[0] ? 0 : 1) || String(b[1][0].created_at).localeCompare(String(a[1][0].created_at)))
      .map(([title, list]) => `<section class="rc-exam-group" aria-label="${escape(title || "Other cards")}"><h4 class="rc-group-title">${escape(title || "Other cards")}</h4><div class="rc-issue-list">${list.map(issueCard).join("")}</div></section>`).join("");
  }

  // Archived (a view, certified_archive.py): cards of an assessment whose grades are certified fold away, flagged or not: they no
  // longer need the instructor (2026-09-27). Their counts and chips are shown as recorded.
  function issuesMarkup(issues) {
    const archived = issues.filter((item) => item.archived);
    const live = issues.filter((item) => !archived.includes(item));
    return `${live.length ? issueGroups(live) : `<p class="supporting-copy">Every card issued this term belongs to an assignment with certified grades: see Archived below.</p>`}${archived.length
      ? disclosureMarkup({label: "Archived · grades certified", hint: archived.length, key: "response-cards:archived", className: "rc-archived", body: issueGroups(archived)}) : ""}`;
  }

  function templateCard(item) {
    const managed = item.managed === "seating" ? "Used by seating charts" : item.managed === "exams" ? "Used by the Exams page" : "";
    return `<a class="card card--link rc-card" href="${href("template", item.id)}"><h4>${escape(item.name)}</h4><p class="rc-clamp">${escape(item.schema?.description || item.schema?.title || "Edit fields, preview, and issue a frozen version.")}</p>${managed ? `<span class="chip" data-tone="neutral">${escape(managed)}</span>` : ""}</a>`;
  }

  async function renderList(current = generation) {
    setHeader({route: "#response-cards", title: "Response Cards", subtitle: "Notices and questions for each student, with their responses close at hand.", crumbs: crumbs(),
      action: definitionId ? {label: "New template", icon: "plus", run: () => { location.hash = href("template", "new"); }} : null});
    if (!definitionId) {
      root().innerHTML = emptyMarkup({icon: "book", title: "Start with a course", body: "Templates belong to a course and are shared by all of its terms and sections.", action: {label: "Open courses", href: "#all-courses"}});
      return;
    }
    const [templates, issues, attention] = await Promise.all([
      api(`${base}/templates?definition_id=${encodeURIComponent(definitionId)}`),
      termId ? api(`${base}/issues?term_id=${encodeURIComponent(termId)}`) : {issues: []},
      api(`${base}/attention`).catch(() => ({items: []})),
    ]);
    if (current !== generation || root().hidden) return;
    const terms = context.terms.filter((item) => item.definition_id === definitionId);
    const term = terms.find((item) => item.id === termId);
    root().innerHTML = `${!context.configured ? calloutMarkup({tone: "warning", className: "rc-notice", title: "The public response service is not configured.", body: "You can prepare templates and issues here; publishing and response sync start after setup."}) : ""}
      ${attentionStrip(attention)}
      ${syncLine("rc-sync")}
      <section class="rc-block" aria-labelledby="rc-issues-title"><div class="toolbar rc-list-toolbar"><h3 id="rc-issues-title">Issued cards</h3><label class="field">Course<select aria-label="Course" id="rc-course">${context.definitions.map((item) => option(item.id, item.code, definitionId)).join("")}</select></label><label class="field">Term<select aria-label="Term" id="rc-term">${terms.map((item) => option(item.id, item.label || `${item.term === "Other" ? item.term_other : item.term} ${item.year}`, termId)).join("")}</select></label></div>
        ${issues.issues.length ? issuesMarkup(issues.issues) : emptyMarkup({title: `No cards issued${term ? ` in ${term.label}` : ""}`, body: "Open a template below and issue one of its frozen versions."})}</section>
      <section class="rc-block" aria-labelledby="rc-templates-title"><h3 id="rc-templates-title">Course templates</h3><p class="field-hint">Shared across every term and section of this course.</p><div class="rc-cards">${templates.templates.map(templateCard).join("") || '<p class="rc-empty supporting-copy">Start with a syllabus acknowledgment, an exam confirmation, or a question you need answered: use New template.</p>'}</div></section>
      ${deliveryPanel()}`;
    root().querySelector("#rc-course").onchange = (event) => { definitionId = event.target.value; termId = ""; void render(true); };
    root().querySelector("#rc-term").onchange = (event) => { termId = event.target.value; void render(true); };
    const sync = root().querySelector("#rc-sync");
    if (sync) sync.onclick = (event) => runButton(event.currentTarget, () => syncNow());
    root().querySelectorAll(".rc-issue[data-href]").forEach((card) => card.addEventListener("click", (event) => {
      if (event.target.closest("a, button") || event.defaultPrevented) return;
      location.hash = card.dataset.href;
    }));
    root().querySelector("#rc-gmail-connect")?.addEventListener("click", (event) => runButton(event.currentTarget, async () => { const result = await api(`${base}/gmail/connect`, "POST", {}); window.location.assign(result.url); }));
    root().querySelector("#rc-gmail-disconnect")?.addEventListener("click", (event) => runButton(event.currentTarget, async () => { await api(`${base}/gmail/disconnect`, "POST", {}); toast("Gmail disconnected."); await render(true); }));
    root().querySelector("#rc-mail-test")?.addEventListener("click", openMailTest);
    applyListQuery();
  }

  /** #response-cards?email opens Email settings, ?attention the attention strip (Settings, Home, seating links); then the query goes. */
  function applyListQuery() {
    // Only once the list is on screen: a poll can arrive while it is still loading.
    if (route !== "#response-cards" || !root().querySelector("#rc-issues-title")) return;
    const params = query();
    if (params.has("email")) {
      const details = root().querySelector("#rc-email-settings");
      if (!details) return;
      details.open = true;
      details.scrollIntoView({block: "start"});
      details.querySelector("summary")?.focus({preventScroll: true});
      dropQuery();
    } else if (params.has("attention")) {
      const strip = root().querySelector("#rc-attention");
      if (strip) {
        strip.scrollIntoView({block: "start"});
        strip.querySelector("h3")?.focus({preventScroll: true});
      } else toast("Nothing needs attention in Response Cards.");
      dropQuery();
    }
  }

  async function syncNow() {
    const result = await api(`${base}/sync`, "POST", {});
    toast(result.has_more ? "Synced a batch. More responses remain; sync again to continue." : "Responses synchronized.");
    await render(true);
  }

  function deliveryPanel() {
    const mail = context.mail || {};
    const gmail = mail.gmail || {};
    let body, actions = "";
    const status = {resend: `Sent through Resend as ${mail.sender}`, gmail: `Sent through Gmail as ${mail.sender}`, relay: `Sent through the email relay as ${mail.sender}`}[mail.provider]
      || "No default email provider";
    if (mail.provider === "resend") {
      body = `Student invitations and submission confirmations are sent through Resend by default as <strong>${escape(mail.sender)}</strong>. Temporary sending limits pause the queue until delivery can resume.`;
    } else if (mail.provider === "gmail") {
      body = `Student email is sent as <strong>${escape(mail.sender)}</strong> through Gmail, connected ${escape(when(gmail.connected_at))}. Sent messages appear in that account's Sent folder.`;
    } else if (mail.provider === "relay") {
      body = `Student invitations and submission confirmations are sent through the configured email relay as <strong>${escape(mail.sender)}</strong>.`;
    } else {
      body = "No default email provider is configured. Cards can still be published and their links copied by hand.";
    }
    if (gmail.connected) {
      if (mail.provider !== "gmail") body += ` Gmail is connected as <strong>${escape(gmail.email)}</strong> since ${escape(when(gmail.connected_at))}. Choose it under Send from for an individual email action. ${mail.provider === "resend" ? "Resend remains the default, including automatic submission confirmations." : "This choice does not change the default for other messages."}`;
      actions += '<button class="button" id="rc-gmail-disconnect">Disconnect Gmail</button>';
    } else if (gmail.configured) {
      body += ` Connect Gmail to add your account as a sending option.${mail.provider === "resend" ? " Resend remains the default." : ""}`;
      actions += '<button class="button" id="rc-gmail-connect">Connect Gmail</button>';
    }
    if (mailAvailable()) actions = '<button class="button" id="rc-mail-test">Send a test email</button>' + actions;
    const line = `${status}${gmail.connected && mail.provider !== "gmail" ? ` · Gmail connected (${gmail.email})` : ""}.`;
    return `<section class="rc-block rc-delivery" aria-labelledby="rc-delivery-title"><h3 id="rc-delivery-title">Email delivery</h3><p class="rc-delivery-line">${escape(line)}</p>${disclosureMarkup({label: "Email settings", key: "rc-email-settings", id: "rc-email-settings", body: `<p class="supporting-copy">${body}</p>${actions ? `<div class="toolbar">${actions}</div>` : ""}`})}</section>`;
  }

  function mailAvailable() {
    return Boolean(context.mail_configured || context.mail?.gmail?.connected);
  }

  function mailProviderField() {
    const mail = context.mail || {};
    const provider = {resend: "Resend", relay: "Email relay", gmail: "Gmail"}[mail.provider];
    const label = provider ? `Faculty default · ${provider} · ${mail.sender}` : "Faculty default · unavailable";
    return `<label class="field">Send from<select name="mail_provider" aria-label="Send from">${option("default", label, "default")}${mail.gmail?.connected && mail.provider !== "gmail" ? option("gmail", `Gmail · ${mail.gmail.email}`) : ""}</select></label><p class="field-hint">This choice applies to this email action. Automatic submission confirmations use the Faculty default.</p>`;
  }

  function configureMailForm(form, onChange = () => {}) {
    const select = form.elements.namedItem("mail_provider");
    const update = () => {
      form.querySelector('[type="submit"]').disabled = select.value === "default" && !context.mail_configured;
      onChange(select.value);
    };
    select.addEventListener("change", update);
    update();
  }

  function openMailTest() {
    showDialog(`<form id="rc-mail-test-form">${dialogHeader("Send a test email", "Check delivery to your own address.")}<div class="dialog-body">${mailProviderField()}<p id="rc-mail-test-recipient"></p>${formError()}</div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Send test email</button></footer></form>`);
    const form = document.querySelector("#rc-mail-test-form");
    configureMailForm(form, (provider) => {
      form.querySelector("#rc-mail-test-recipient").textContent = `To: ${provider === "gmail" ? context.mail.gmail.email : context.mail.reply_to}`;
    });
    form.onsubmit = (event) => {
      event.preventDefault();
      const mail_provider = form.elements.namedItem("mail_provider").value;
      void perform(form, async (current) => {
        const result = await api(`${base}/mail/test`, "POST", {mail_provider});
        if (current()) document.querySelector("#dialog").close();
        toast(`Test message sent to ${result.recipient}.`);
      });
    };
  }

  function openIssueMail(issue) {
    showDialog(`<form id="rc-email-form">${dialogHeader("Email links to students", escape(issueLabel(issue)))}<div class="dialog-body"><p class="section-lead">Queue one invitation for each published card that has not been emailed yet, using each student's roster address.</p>${mailProviderField()}${formError()}</div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Send emails</button></footer></form>`);
    const form = document.querySelector("#rc-email-form");
    configureMailForm(form);
    form.onsubmit = (event) => {
      event.preventDefault();
      const mail_provider = form.elements.namedItem("mail_provider").value;
      void perform(form, async (current) => {
        const result = await api(`${base}/issues/${encodeURIComponent(issue.id)}/send`, "POST", {mail_provider});
        // The emails are queued whatever happened to the dialog meanwhile: always say so; only an open dialog closes and redraws.
        const queued = result.queued || {};
        toast(`${queued.queued || 0} email${queued.queued === 1 ? "" : "s"} queued to send${queued.no_email ? `; ${queued.no_email} student${queued.no_email === 1 ? " has" : "s have"} no roster address` : ""}.`);
        if (!current()) return;
        document.querySelector("#dialog").close();
        await render(true);
      });
    };
  }

  /** Queue one more copy of a single card's invitation; the server's resend_block decides whether it may go. */
  function openResend(item) {
    showDialog(`<form id="rc-resend-form">${dialogHeader("Resend email", escape(item.student_name))}<div class="dialog-body"><p class="section-lead">Queue one more copy of this card's link${item.email ? ` to ${escape(item.email)}` : ""}.</p>${mailProviderField()}${formError()}</div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Resend email</button></footer></form>`);
    const form = document.querySelector("#rc-resend-form");
    configureMailForm(form);
    form.onsubmit = (event) => {
      event.preventDefault();
      const mail_provider = form.elements.namedItem("mail_provider").value;
      void perform(form, async (current) => {
        await api(`${base}/cards/${encodeURIComponent(item.id)}/resend`, "POST", {mail_provider});
        toast("Email queued to send.");
        if (!current()) return;
        document.querySelector("#dialog").close();
        await render(true);
      });
    };
  }

  async function runButton(button, operation) {
    if (button.disabled) return;
    setBusy(button, true);
    try { await operation(); }
    catch (cause) { toast(cause.message, true); }
    finally { setBusy(button, false); }
  }

  // ---- The template editor.

  function captureEditor() {
    const form = root().querySelector("#rc-editor");
    if (!form) return;
    editing.name = form.elements.namedItem("name").value;
    editing.definition_id = form.elements.namedItem("definition_id").value;
    editing.schema.title = form.elements.namedItem("title").value;
    editing.schema.description = form.elements.namedItem("description").value;
    editing.schema.confirmation_message = form.elements.namedItem("confirmation_message").value;
    root().querySelectorAll(".rc-field-editor").forEach((node, index) => {
      const value = (key) => node.querySelector(`[data-prop="${key}"]`)?.value || "";
      const field = editing.schema.fields[index];
      field.label = value("label"); field.type = value("type");
      field.required = field.type !== "static" && node.querySelector('[data-prop="required"]').checked;
      if (CHOICES.includes(field.type)) field.options = value("options").split("\n").map((item) => item.trim()).filter(Boolean);
      else delete field.options;
      if (value("condition")) {
        const other = editing.schema.fields.find((item) => item.id === value("condition"));
        let equals = value("equals");
        if (["boolean", "acknowledgment"].includes(other?.type) && ["true", "false"].includes(equals)) equals = equals === "true";
        else if (["integer", "decimal"].includes(other?.type) && equals.trim() && Number.isFinite(Number(equals))) equals = Number(equals);
        if (other?.type === "multi_select") equals = value("equals").split("\n").map((item) => item.trim()).filter(Boolean);
        field.show_if = {field: value("condition"), equals};
      } else delete field.show_if;
      if (value("expected") && field.type !== "static") {
        let expected = value("expected");
        const typed = (item) => {
          if (["boolean", "acknowledgment"].includes(field.type) && ["true", "false"].includes(item)) return item === "true";
          if (["integer", "decimal"].includes(field.type) && item.trim() && Number.isFinite(Number(item))) return Number(item);
          return item;
        };
        if (value("mode") === "one-of" || field.type === "multi_select") expected = expected.split("\n").map((item) => item.trim()).filter(Boolean).map(typed);
        else if (value("mode") === "exact") expected = typed(expected);
        field.verification = {expected, mode: value("mode"), on_mismatch: value("mismatch")};
      } else delete field.verification;
    });
  }

  function fieldEditor(field, index) {
    const prior = editing.schema.fields.slice(0, index).filter((item) => item.type !== "static");
    return `<fieldset class="rc-field-editor" data-field-index="${index}"><legend>Field ${index + 1} · ${escape(FIELD_TYPES[field.type])}</legend><div class="rc-field-actions"><button type="button" class="text-button" data-move="-1" aria-label="Move field ${index + 1} up" ${index ? "" : "disabled"}>↑ Up</button><button type="button" class="text-button" data-move="1" aria-label="Move field ${index + 1} down" ${index < editing.schema.fields.length - 1 ? "" : "disabled"}>↓ Down</button><button type="button" class="text-button" data-remove="${index}">Remove field ${index + 1}</button></div>
      <label class="field">Field type<select aria-label="Field type" data-prop="type">${Object.entries(FIELD_TYPES).map(([key, label]) => option(key, label, field.type)).join("")}</select></label>
      <label class="field">${field.type === "static" ? "Information text" : "Question or statement"}<textarea aria-label="${field.type === "static" ? "Information text" : "Question or statement"}" data-prop="label" required maxlength="${field.type === "static" ? 10000 : 2000}">${escape(field.label)}</textarea></label><p class="field-hint supporting-copy">${field.type === "static" ? "Give students the instructions they need; this field does not ask for a response." : "Ask one clear question or state exactly what students should confirm."}</p>
      ${field.type !== "static" ? `<label class="rc-check"><input type="checkbox" data-prop="required" ${field.required ? "checked" : ""}>Required response</label>` : '<input type="checkbox" data-prop="required" hidden>'}
      ${CHOICES.includes(field.type) ? `<label class="field">Choices, one per line<textarea aria-label="Choices, one per line" data-prop="options" required>${escape((field.options || []).join("\n"))}</textarea></label>` : ""}
      <details class="disclosure rc-rules" ${field.show_if || field.verification ? "open" : ""}><summary><span>Visibility and verification</span></summary><div class="disclosure-body">
      <label class="field">Show this field<select aria-label="Show this field" data-prop="condition">${option("", "Always")}${prior.map((item, i) => option(item.id, `${i + 1}. ${item.label || FIELD_TYPES[item.type]}`, field.show_if?.field)).join("")}</select></label><label class="field">When that answer equals<textarea aria-label="When that answer equals" data-prop="equals" placeholder="Exact choice, text, true, or false">${escape(Array.isArray(field.show_if?.equals) ? field.show_if.equals.join("\n") : field.show_if?.equals ?? "")}</textarea></label><p class="field-hint">For multiple choice, enter one selected option per line in the same order as the choices.</p>
      ${field.type !== "static" ? `<label class="field">Expected answer, optional<textarea aria-label="Expected answer, optional" data-prop="expected" placeholder="{{student.full_name}}">${escape(Array.isArray(field.verification?.expected) ? field.verification.expected.join("\n") : field.verification?.expected ?? "")}</textarea></label><div class="rc-two-columns"><label class="field">Verification<select aria-label="Verification" data-prop="mode">${["exact", "case-insensitive", "normalized", "numeric", "one-of"].map((item) => option(item, item === "one-of" ? "One of (one answer per line)" : item.replaceAll("-", " "), field.verification?.mode || (["integer", "decimal"].includes(field.type) ? "numeric" : ["boolean", "acknowledgment", "multi_select"].includes(field.type) ? "exact" : "normalized"))).join("")}</select></label><label class="field">On mismatch<select aria-label="On mismatch" data-prop="mismatch">${option("ALLOW_AND_FLAG", "Accept and flag for review", field.verification?.on_mismatch || "ALLOW_AND_FLAG")}${option("BLOCK_SUBMISSION", "Block submission", field.verification?.on_mismatch)}${option("ALLOW", "Accept", field.verification?.on_mismatch)}</select></label></div>` : ""}</div></details></fieldset>`;
  }

  /** What the editor's one primary does now: save the draft, freeze it as the next version, or issue the latest version. */
  function editorState() {
    const versions = editing.versions || [];
    const latest = versions[0];
    const next = (latest?.number || 0) + 1;
    if (!editing.id || dirty) return {kind: "save", label: "Save draft", next};
    if (!latest || JSON.stringify(latest.schema) !== JSON.stringify(editing.schema)) return {kind: "freeze", label: `Freeze as version ${next}`, next};
    return {kind: "issue", label: `Issue version ${latest.number}…`, version: latest};
  }

  function editorFooter() {
    const state = editorState();
    const note = {save: editing.id ? "Save before freezing a new version." : "Save the draft to keep it.",
      freeze: "A frozen version never changes; students receive a frozen version.",
      issue: "This version matches the saved draft."}[state.kind];
    return `<span id="rc-save-status" class="field-hint" role="status">${dirty ? "Unsaved changes" : editing.id ? "Saved draft" : "New draft"}</span><span class="field-hint rc-footer-note">${escape(note)}</span><button type="${state.kind === "save" ? "submit" : "button"}" class="button primary" id="rc-primary" data-editor-action="${state.kind}">${escape(state.label)}</button>`;
  }

  function versionList() {
    const versions = editing.versions || [];
    return `<section class="rc-version-list" aria-label="Frozen versions"><h4>Frozen versions</h4>${versions.map((version) => `<div><span>Version ${escape(version.number)} · ${escape(when(version.created_at))}</span><button type="button" class="text-button" data-issue-version="${escape(version.id)}" aria-label="Issue version ${escape(version.number)}">Issue…</button></div>`).join("") || '<p class="field-hint">Freeze the saved draft to choose who receives it. Issued cards keep their version even if you edit the template later.</p>'}</section>`;
  }

  function editorHeader() {
    const course = context.definitions.find((item) => item.id === editing.definition_id);
    setHeader({route, title: editing.name || (editing.id ? "Template" : "New template"), context: course ? `${course.code} · Course template` : "Course template",
      subtitle: "Shared across every term and section of the course; issued cards keep the version they were issued.",
      crumbs: crumbs(editing.name || (editing.id ? "Template" : "New template")), action: null});
  }

  function refreshFooter() {
    const footer = root().querySelector(".rc-editor-footer");
    if (footer) { footer.innerHTML = editorFooter(); bindPrimary(); }
  }

  function renderEditor() {
    const fields = editing.schema.fields;
    const course = context.definitions.find((item) => item.id === editing.definition_id);
    const managed = editing.managed === "seating" ? "Faculty keeps this template for seating confirmations: each confirmation freezes its current wording, so edits here reach the next confirmation's cards."
      : editing.managed === "exams" ? "The Exams page issues this template for requests and feedback; edits reach the cards it issues next." : "";
    editorHeader();
    root().innerHTML = `${back()}<div class="rc-builder"><form id="rc-editor" class="course-panel"><div class="rc-editor-top"><span class="eyebrow">${editing.id ? "Edit template" : "New template"}</span>${editing.id ? `<span class="chip" data-tone="neutral" title="A template stays with its course">${escape(course?.code || "Course")}</span><input type="hidden" name="definition_id" value="${escape(editing.definition_id)}">` : ""}</div>
      ${managed ? calloutMarkup({tone: "info", title: "Managed by Faculty", body: managed}) : ""}
      ${editing.id ? "" : `<label class="field">Course owner<select aria-label="Course owner" name="definition_id">${context.definitions.map((item) => option(item.id, item.code, editing.definition_id)).join("")}</select></label>`}<label class="field">Template name<input name="name" required maxlength="120" value="${escape(editing.name)}" placeholder="Syllabus acknowledgment"></label><label class="field">Card title<input name="title" required maxlength="200" value="${escape(editing.schema.title)}" placeholder="Please review the syllabus"></label><label class="field">Introduction<textarea aria-label="Introduction" name="description" maxlength="8000">${escape(editing.schema.description)}</textarea></label>
      <label class="field">Confirmation message<textarea aria-label="Confirmation message" name="confirmation_message" maxlength="2000">${escape(editing.schema.confirmation_message || "Thank you. Your response has been saved.")}</textarea></label><details class="disclosure rc-variables"><summary><span>Personalization variables</span></summary><div class="disclosure-body"><p class="supporting-copy">Use these in card text or expected answers. Values are frozen for each recipient when you issue the card. A context must provide every variable you use.</p><div>${(context.variables || []).map((item) => `<code>{{${escape(item)}}}</code>`).join(" ")}</div></div></details>
      <div id="rc-fields">${fields.map(fieldEditor).join("")}</div><div class="toolbar"><label class="field">New field type<select aria-label="New field type" id="rc-add-type">${Object.entries(FIELD_TYPES).map(([key, label]) => option(key, label, "short_text")).join("")}</select></label><button type="button" class="button" id="rc-add-field">Add field</button></div>${formError()}<footer class="rc-editor-footer">${editorFooter()}</footer></form>
      <aside class="rc-preview-panel">${versionList()}<span class="eyebrow">Interactive preview</span><p class="field-hint">Try the card as a student would; nothing is saved and placeholders stay until an issue fills them.</p><div id="rc-preview" class="rc-preview"></div></aside></div>`;
    renderPreview();
    const form = root().querySelector("#rc-editor");
    form.addEventListener("input", () => {
      const was = dirty;
      dirty = true; captureEditor(); renderPreview();
      if (!was) refreshFooter();
      else root().querySelector("#rc-save-status").textContent = "Unsaved changes";
    });
    form.addEventListener("change", (event) => {
      if (event.target.matches('[data-prop="type"]')) { captureEditor(); dirty = true; renderEditor(); }
    });
    form.onsubmit = (event) => {
      event.preventDefault(); captureEditor();
      const payload = {definition_id: editing.definition_id, name: editing.name, schema: editing.schema, ...(editing.id ? {version: editing.version} : {})};
      void perform(form, async (current) => {
        const saved = await api(`${base}/templates${editing.id ? `/${encodeURIComponent(editing.id)}` : ""}`, editing.id ? "PUT" : "POST", payload);
        if (!current()) return;
        editing = saved; dirty = false; definitionId = saved.definition_id;
        toast("Template saved.");
        if (location.hash !== href("template", saved.id)) location.hash = href("template", saved.id);
        else renderEditor();
      });
    };
    root().querySelector("#rc-add-field").onclick = () => {
      captureEditor(); const type = root().querySelector("#rc-add-type").value;
      editing.schema.fields.push({id: fieldId(), type, label: "", required: false, ...(CHOICES.includes(type) ? {options: []} : {})});
      dirty = true; renderEditor(); root().querySelector(".rc-field-editor:last-child textarea").focus();
    };
    root().querySelectorAll("[data-move]").forEach((button) => { button.onclick = () => {
      captureEditor(); const index = Number(button.closest("[data-field-index]").dataset.fieldIndex); const next = index + Number(button.dataset.move);
      const candidate = [...fields]; [candidate[index], candidate[next]] = [candidate[next], candidate[index]];
      if (candidate.some((field, i) => field.show_if && !candidate.slice(0, i).some((prior) => prior.id === field.show_if.field))) { toast("This move would place a conditional field before its question. Adjust its visibility rule first.", true); return; }
      editing.schema.fields = candidate; dirty = true; renderEditor(); root().querySelectorAll(".rc-field-editor")[next].querySelector("select").focus();
    }; });
    root().querySelectorAll("[data-remove]").forEach((button) => { button.onclick = () => {
      captureEditor(); const index = Number(button.dataset.remove); const id = fields[index].id;
      if (fields.some((field) => field.show_if?.field === id)) { toast("Another field depends on this answer. Change that visibility rule before removing it.", true); return; }
      fields.splice(index, 1); dirty = true; renderEditor();
    }; });
    root().querySelectorAll("[data-issue-version]").forEach((button) => { button.onclick = () => openIssue(button.dataset.issueVersion); });
    bindPrimary();
  }

  function bindPrimary() {
    const button = root().querySelector("#rc-primary");
    const form = root().querySelector("#rc-editor");
    if (!button || !form) return;
    const state = editorState();
    if (state.kind === "freeze") button.onclick = () => perform(form, async (current) => {
      await api(`${base}/templates/${encodeURIComponent(editing.id)}/versions`, "POST", {version: editing.version});
        if (!current()) return;
      const updated = await api(`${base}/templates/${encodeURIComponent(editing.id)}`); if (!current()) return; editing = updated; toast("Immutable version created."); renderEditor();
    });
    else if (state.kind === "issue") button.onclick = () => openIssue(state.version.id);
    else button.onclick = null;
  }

  function previewField(field) {
    const id = `rc-preview-${field.id}`; const label = escape(field.label || "Untitled field");
    const required = field.required ? ' <span class="rc-required">Required</span>' : "";
    if (field.type === "static") return `<p class="rc-static">${label}</p>`;
    if (field.type === "acknowledgment") return `<label class="rc-check"><input id="${id}" name="${escape(field.id)}" type="checkbox" ${editorAnswers[field.id] === true ? "checked" : ""}>${label}${required}</label>`;
    if (field.type === "boolean") return `<label class="field">${label}${required}<select id="${id}" name="${escape(field.id)}">${option("", "Choose…")}${option("true", "Yes", String(editorAnswers[field.id]))}${option("false", "No", String(editorAnswers[field.id]))}</select></label>`;
    if (["radio", "multi_select"].includes(field.type)) return `<fieldset class="rc-preview-choice"><legend>${label}${required}</legend>${(field.options || []).map((value) => `<label class="rc-check"><input type="${field.type === "radio" ? "radio" : "checkbox"}" name="${escape(field.id)}" value="${escape(value)}" ${(Array.isArray(editorAnswers[field.id]) ? editorAnswers[field.id].includes(value) : editorAnswers[field.id] === value) ? "checked" : ""}>${escape(value)}</label>`).join("")}</fieldset>`;
    if (field.type === "single_select") return `<label class="field">${label}${required}<select id="${id}" name="${escape(field.id)}">${option("", "Choose…")}${(field.options || []).map((value) => option(value, value, editorAnswers[field.id])).join("")}</select></label>`;
    if (field.type === "long_text") return `<label class="field">${label}${required}<textarea id="${id}" name="${escape(field.id)}">${escape(editorAnswers[field.id] || "")}</textarea></label>`;
    const type = {integer: "number", decimal: "number", date: "date", time: "time"}[field.type] || "text";
    return `<label class="field">${label}${required}<input id="${id}" name="${escape(field.id)}" type="${type}" ${field.type === "decimal" ? 'step="any"' : ""} value="${escape(editorAnswers[field.id] ?? "")}" ${field.type === "attestation" ? 'placeholder="Type your full name"' : ""}></label>`;
  }

  function renderPreview() {
    const preview = root().querySelector("#rc-preview");
    if (!preview) return;
    preview.innerHTML = `<h3>${escape(editing.schema.title || "Your card title")}</h3><p class="rc-static">${escape(editing.schema.description)}</p>${editing.schema.fields.map((field) => `<div class="rc-preview-field" data-preview-field="${escape(field.id)}">${previewField(field)}</div>`).join("")}${editing.schema.fields.length ? '<p class="field-hint">Preview only · nothing is submitted</p>' : '<p class="field-hint">Add a field to see your card take shape.</p>'}`;
    const update = () => {
      const visible = new Set();
      editing.schema.fields.forEach((field) => {
        const condition = field.show_if;
        const answer = condition ? editorAnswers[condition.field] : undefined;
        const show = !condition || (visible.has(condition.field) && (JSON.stringify(answer) === JSON.stringify(condition.equals)));
        const node = preview.querySelector(`[data-preview-field="${field.id}"]`);
        node.hidden = !show;
        if (show) visible.add(field.id);
      });
    };
    preview.oninput = (event) => {
      const field = editing.schema.fields.find((item) => item.id === event.target.name);
      if (!field) return;
      const input = event.target;
      if (field.type === "acknowledgment") editorAnswers[field.id] = input.checked;
      else if (field.type === "multi_select") editorAnswers[field.id] = [...preview.querySelectorAll(`input[name="${field.id}"]:checked`)].map((item) => item.value);
      else if (field.type === "boolean") editorAnswers[field.id] = input.value === "" ? "" : input.value === "true";
      else if (["integer", "decimal"].includes(field.type)) editorAnswers[field.id] = input.value === "" ? "" : Number(input.value);
      else editorAnswers[field.id] = input.value;
      update();
    };
    update();
  }

  // ---- The Issue dialog: Who (term, sections, students) → Timing → Exam context (optional).

  async function openIssue(versionId) {
    const definition = editing.definition_id;
    const terms = context.terms.filter((item) => item.definition_id === definition && item.is_current);
    if (!terms.length) { toast("Add a current course term and roster before issuing cards.", true); return; }
    showDialog(`<form id="rc-issue-form">${dialogHeader("Issue response cards", "Choose the students who should receive this frozen version.")}<div class="dialog-body"><h3 class="rc-dialog-step">Who</h3><label class="field">Term<select aria-label="Term" name="term_id">${terms.map((item) => option(item.id, `${item.code} · ${item.label}`, termId)).join("")}</select></label><div id="rc-audience"><p>Loading audience…</p></div>
      <h3 class="rc-dialog-step">Timing</h3><label class="field">Submission policy<select aria-label="Submission policy" name="policy">${Object.entries(POLICIES).map(([key, label]) => option(key, label, "once")).join("")}</select></label><div class="rc-two-columns"><label class="field">Due date and time<input name="due_at" type="datetime-local"></label><label class="field">Link expiry, optional<input name="expires_at" type="datetime-local"></label></div><p class="field-hint">Dates use this browser’s timezone. Issuing freezes each recipient’s card now; publishing and email are separate steps.</p>
      <div id="rc-context"></div>${formError()}</div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary" id="rc-issue-submit">Create issue</button></footer></form>`);
    const form = document.querySelector("#rc-issue-form");
    let audienceGeneration = 0;
    const audience = async () => {
      const tick = ++audienceGeneration;
      setBusy(form.querySelector("#rc-issue-submit"), true);
      try {
        const data = await api(`${base}/context?term_id=${encodeURIComponent(form.elements.term_id.value)}`);
        if (tick !== audienceGeneration || !form.isConnected) return;
        const sections = data.sections.filter((item) => item.term_id === form.elements.term_id.value);
        const assignments = data.assignments.filter((item) => item.definition_id === definition);
        const charts = data.seating_charts.filter((item) => item.term_id === form.elements.term_id.value);
        const students = data.students || [];
        const picker = studentPicker(escape, {id: "rc-student-find", label: "Find a student", value: "",
          groups: sections.map((section) => ({label: `Section ${section.number}`, options: students.filter((item) => item.section_id === section.id).map((item) => ({value: item.enrollment_id + "|" + item.section_id, text: `${item.name} · ${item.section_number}`}))}))});
        form.querySelector("#rc-audience").innerHTML = `<fieldset class="rc-audience"><legend>Sections</legend>${sections.map((item) => `<label class="rc-check"><input type="checkbox" name="section_ids" value="${escape(item.id)}" checked>Section ${escape(item.number)} <span class="field-hint">${escape(item.count)} students</span></label>`).join("")}</fieldset><label class="field">Audience<select aria-label="Audience" name="audience_mode">${option("sections", "Everyone in selected sections")}${option("students", "Selected students in these sections")}</select></label><fieldset class="rc-audience rc-student-selection" hidden><legend>Students</legend>${students.length ? `<div class="field">Add a student${picker.markup}</div><p class="field-hint" role="status" data-chosen-count></p>` : ""}<div class="rc-student-list">${students.map((item) => `<label class="rc-check" data-section-id="${escape(item.section_id)}"><input type="checkbox" name="enrollment_ids" value="${escape(item.enrollment_id)}" data-student-key="${escape(item.enrollment_id + "|" + item.section_id)}">${escape(item.name)} · ${escape(item.section_number)}</label>`).join("") || '<p class="field-hint">No imported students in this term.</p>'}</div></fieldset>`;
        form.querySelector("#rc-context").innerHTML = disclosureMarkup({label: "Exam context (optional)", className: "rc-context", open: false,
          body: `<label class="field">Assignment context, optional<select aria-label="Assignment context, optional" name="assignment_id">${option("", "None")}${assignments.map((item) => option(item.id, item.title)).join("")}</select></label><label class="field">Seating chart context, optional<select aria-label="Seating chart context, optional" name="seating_chart_id">${option("", "None")}${charts.map((item) => option(item.id, `${item.room_name} · ${assignments.find((a) => a.id === item.assignment_id)?.title || "Exam"}`)).join("")}</select></label><p class="field-hint">Seating and DR variables need matching context. A chart limits the audience to its seated students.</p><p class="field-hint rc-chart-effect" role="status" data-chart-effect></p>`});
        const updateStudents = () => {
          const selected = new Set([...form.querySelectorAll('[name="section_ids"]:checked')].map((item) => item.value));
          form.querySelector(".rc-student-selection").hidden = form.elements.audience_mode.value !== "students";
          form.querySelectorAll("[data-section-id]").forEach((item) => { item.hidden = !selected.has(item.dataset.sectionId); if (item.hidden) item.querySelector("input").checked = false; });
          const count = form.querySelector("[data-chosen-count]");
          if (count) count.textContent = `${form.querySelectorAll('[name="enrollment_ids"]:checked').length} chosen`;
        };
        form.elements.audience_mode.onchange = updateStudents;
        form.querySelectorAll('[name="section_ids"]').forEach((item) => { item.onchange = updateStudents; });
        form.querySelector(".rc-student-list").addEventListener("change", updateStudents);
        picker.bind(form, (value) => {
          const box = form.querySelector(`[data-student-key="${CSS.escape(value)}"]`);
          const row = box?.closest("[data-section-id]");
          if (box && row && !row.hidden) { box.checked = true; box.scrollIntoView({block: "nearest"}); }
          else if (box) toast("That student's section is not selected.", true);
          updateStudents();
          // Ready for the next name: the picker adds students, the ticks below hold the choice.
          const holder = form.querySelector('[data-student-picker="rc-student-find"]');
          setTimeout(() => { holder.querySelector("[data-picker-value]").value = ""; const text = holder.querySelector("[data-picker-text]"); text.value = ""; text.placeholder = "Type another name or section…"; }, 0);
        });
        form.elements.seating_chart_id.onchange = () => {
          const chart = charts.find((item) => item.id === form.elements.seating_chart_id.value);
          const effect = form.querySelector("[data-chart-effect]");
          if (chart) {
            form.elements.assignment_id.value = chart.assignment_id;
            form.querySelectorAll('[name="section_ids"]').forEach((item) => { item.checked = chart.section_ids.includes(item.value); });
            updateStudents();
            const numbers = sections.filter((item) => chart.section_ids.includes(item.id)).map((item) => item.number);
            effect.textContent = `Changed: assignment set to ${assignments.find((a) => a.id === chart.assignment_id)?.title || "the chart's exam"}; sections set to ${numbers.join(", ") || "none"}. Only students seated in ${chart.room_name} receive cards.`;
          } else effect.textContent = "";
        };
        updateStudents(); setBusy(form.querySelector("#rc-issue-submit"), false);
      } catch (cause) { if (tick === audienceGeneration) { markBusy(form.querySelector("#rc-issue-submit"), false); const error = form.querySelector('[role="alert"]'); error.hidden = false; error.textContent = cause.message; } }
    };
    form.elements.term_id.onchange = audience;
    const requestId = crypto.randomUUID();
    form.onsubmit = (event) => {
      event.preventDefault();
      const values = new FormData(form);
      const payload = {version_id: versionId, term_id: values.get("term_id"), section_ids: values.getAll("section_ids"), policy: values.get("policy"), due_at: values.get("due_at") ? new Date(values.get("due_at")).toISOString() : null, expires_at: values.get("expires_at") ? new Date(values.get("expires_at")).toISOString() : null, request_id: requestId};
      if (values.get("assignment_id")) payload.assignment_id = values.get("assignment_id");
      if (values.get("seating_chart_id")) payload.seating_chart_id = values.get("seating_chart_id");
      if (values.get("audience_mode") === "students") {
        payload.enrollment_ids = [...new Set(values.getAll("enrollment_ids"))];
        if (!payload.enrollment_ids.length) { const error = form.querySelector('[role="alert"]'); error.hidden = false; error.textContent = "Select at least one student."; return; }
      }
      void perform(form, async (current) => {
        const issue = await api(`${base}/issues`, "POST", payload);
        toast("Issue created. Publish it when you are ready to share the links.");
        if (!current()) return;
        document.querySelector("#dialog").close(); location.hash = href("issue", issue.id);
      });
    };
    await audience();
  }

  // ---- The issue page.

  function safeLink(value) {
    try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) ? url.href : ""; } catch { return ""; }
  }

  const statusOf = (item) => String(item.status || "").toLowerCase();
  const hasResponded = (item) => Boolean(item.current_submission) || RESPONDED.includes(statusOf(item));
  const isClosed = (item) => ["revoked", "expired"].includes(statusOf(item));
  const isAwaiting = (item) => item.publish_status === "published" && AWAITING.includes(statusOf(item)) && !hasResponded(item);
  // The server's own rule (response_cards.service.delivery_problem): an unopened, unanswered card whose invitation failed or
  // was not confirmed. A replaced card carries a distribution block instead.
  const notDelivered = (item) => statusOf(item) === "issued" && !item.current_submission_id && ["failed", "unconfirmed"].includes(item.delivery_status);
  // The server's attention rule (`_issue_record`): a submission receipt whose delivery failed or was not confirmed, on a card
  // that was not replaced.
  const receiptProblem = (item) => !item.revocation && (item.mail || []).some((mail) => mail.kind === "confirmation" && ["unconfirmed", "failed"].includes(mail.status));
  const canResend = (item) => isAwaiting(item) && item.email && item.delivery_status !== "queued" && !item.distribution_block && item.delivery_status !== "not_sent";

  function filtersOf(item) {
    const tokens = [];
    if (hasResponded(item)) tokens.push("responded");
    if (statusOf(item) === "needs_review") tokens.push("needs_review");
    if (isAwaiting(item)) tokens.push("awaiting");
    if (notDelivered(item)) tokens.push("not_delivered");
    if (item.publish_status !== "published" && !isClosed(item)) tokens.push("unpublished");
    if (isClosed(item)) tokens.push("closed");
    if (receiptProblem(item)) tokens.push("receipt_problem");
    if (item.revocation === "pending") tokens.push("revocation_pending");
    // The card's own stored status, for the select's individual choices ("needs_review" is already Flagged).
    if (INDIVIDUAL.includes(statusOf(item))) tokens.push(statusOf(item));
    return tokens;
  }

  /** The fields whose answers differed from what the card expected (never the expected values themselves). */
  function differing(item) {
    const verification = item.current_submission?.verification || {};
    const labels = Object.fromEntries((item.schema?.fields || []).map((field) => [field.id, field.label]));
    return Object.entries(verification).filter(([, value]) => value?.matched === false).map(([key]) => labels[key] || key);
  }

  function issuePrimary(issue, recipients) {
    const unpublished = recipients.filter((item) => item.publish_status !== "published" && !isClosed(item)).length;
    const unsent = recipients.filter((item) => isAwaiting(item) && item.email && item.delivery_status === "not_sent" && !item.distribution_block).length;
    // Publishing leads; cards already published but not emailed can still be emailed beside it (a card that keeps failing to
    // publish must not hold the others back).
    const email = unsent && mailAvailable() ? [{label: `Email ${plural(unsent, "student")}`, icon: "link", run: () => openIssueMail(issue)}] : [];
    // Without the response service nothing can be published: Publish is a disabled secondary saying why (the page's callout says it
    // once), never a greyed-out primary.
    if (unpublished && !context.configured) return {action: null, secondary: [{label: `Publish ${plural(unpublished, "card")}`, icon: "cards", disabled: true,
      title: "The public response service is not configured.", run: () => {}}, ...email], done: ""};
    if (unpublished) return {action: {label: `Publish ${plural(unpublished, "card")}`, icon: "cards", run: (event) => publish(issue, event.currentTarget || document.querySelector("#primary-action"))},
      secondary: email, done: ""};
    if (unsent && mailAvailable()) return {action: {label: `Email ${plural(unsent, "student")}`, icon: "link", run: () => openIssueMail(issue)}, done: ""};
    const published = recipients.filter((item) => item.publish_status === "published").length;
    const emailed = recipients.filter((item) => ["sent", "queued"].includes(item.delivery_status)).length;
    return {action: null, done: recipients.length ? `${plural(published, "card")} published${mailAvailable() ? ` · ${emailed.toLocaleString()} emailed` : " · email is not configured, so share links by hand"}.` : ""};
  }

  async function publish(issue, button) {
    const target = button instanceof HTMLButtonElement ? button : document.querySelector("#primary-action");
    await runButton(target, async () => {
      try { await api(`${base}/issues/${encodeURIComponent(issue.id)}/publish`, "POST", {}, {}, 300000); toast("Cards published. Links are ready to share."); }
      finally { await render(true); }
    });
  }

  function renderIssue(issue) {
    const recipients = issue.recipients || [];
    const label = issueLabel(issue);
    const responded = recipients.filter(hasResponded).length;
    // Revoked cards without a response leave the count; a response stays counted whatever happened to its card later.
    const live = recipients.filter((item) => statusOf(item) !== "revoked" || hasResponded(item)).length;
    const flagged = recipients.filter((item) => statusOf(item) === "needs_review").length;
    const problems = recipients.filter(notDelivered).length;
    const awaiting = recipients.filter(isAwaiting);
    const primary = issuePrimary(issue, recipients);
    const params = query();
    const asked = params.get("status");
    let filter = params.get("delivery") === "problem" ? "not_delivered" : FILTERS.some(([key]) => key && key === asked) || INDIVIDUAL.includes(asked) ? asked : "";
    const tokens = new Map(recipients.map((item) => [item.id, filtersOf(item)]));
    const counts = Object.fromEntries(FILTERS.map(([key]) => [key, key ? recipients.filter((item) => tokens.get(item.id).includes(key)).length : recipients.length]));
    const chips = FILTERS.filter(([key]) => !key || counts[key] || key === filter).map(([value, text]) => ({value, label: text, count: counts[value]}));
    const individual = INDIVIDUAL.filter((key) => key === filter || recipients.some((item) => statusOf(item) === key));
    const facts = [issue.assignment_title, issue.room_name, sectionNumbers(issue.section_ids), `issued ${shortDate(issue.created_at)}`, POLICIES[issue.policy] || issue.policy,
      issue.due_at ? `due ${shortWhen(issue.due_at)}` : "", issue.expires_at ? `expires ${shortWhen(issue.expires_at)}` : ""].filter(Boolean);
    setHeader({route, title: label, context: [issue.course_code, issue.term_label].filter(Boolean).join(" · "), subtitle: facts.join(" · "), crumbs: crumbs(label),
      action: primary.action, secondary: primary.secondary || []});
    const examHref = issue.exam_event_id ? `#exams/${encodeURIComponent(issue.exam_event_id)}` : issue.assignment_id ? `#exams/assessment/${encodeURIComponent(issue.assignment_id)}/${encodeURIComponent(issue.term_id)}` : "";
    const links = [issue.seating_chart_id ? `<a class="link-arrow" href="#seating/${encodeURIComponent(issue.seating_chart_id)}">From seating chart${issue.room_name ? ` ${escape(issue.room_name)}` : ""}</a>` : "",
      examHref ? `<a class="link-arrow" href="${examHref}">Exam${issue.assignment_title ? ` · ${escape(issue.assignment_title)}` : ""}</a>` : ""].filter(Boolean).join("");
    const status = [`${responded.toLocaleString()} of ${live.toLocaleString()} responded`, flagged ? `${flagged.toLocaleString()} flagged` : "", problems ? `${plural(problems, "email")} not delivered` : ""].filter(Boolean).join(" · ");
    root().innerHTML = `${back()}${links ? `<p class="rc-issue-links">${links}</p>` : ""}<section class="course-panel rc-issue-page">
      ${context.configured ? `<p class="rc-issue-note">Anyone holding a card's link can respond; a response is not proof of identity.</p>${disclosureMarkup({label: "About links and email", key: "rc-about-links", className: "rc-about", body: `<p class="supporting-copy">Publishing makes each card available at its private link. <strong>Email links</strong> queues each published card’s link once, with the subject “${issue.context && issue.context.makeup_offer_id ? "IMPORTANT: Book your … makeup" : "IMPORTANT: Information about your upcoming …"}”; a confirmation email goes back to each student after they submit. Resend queues one more copy for a single card that still awaits a response; nothing is resent automatically, and a delivery Faculty could not confirm is never retried on its own.</p>`})}`
        : calloutMarkup({tone: "warning", title: "Not published yet", body: "The public response service is not configured, so these cards are saved here only."})}
      <div class="rc-issue-status"><p><strong>${escape(status)}</strong>${primary.done ? `<span class="rc-done"> · ${escape(primary.done)}</span>` : ""}</p><progress class="rc-progress" max="${Math.max(live, 1)}" value="${responded}" aria-label="${escape(`${responded} of ${live} responded`)}"></progress></div>
      ${syncLine("rc-issue-sync", {reload: true})}
      ${filterChipsMarkup("rc-status", chips, filter, {label: "Show recipients"})}
      <div class="toolbar"><label class="field">Find a recipient<input type="search" id="rc-recipient-search" data-page-search placeholder="Name, email, or section"></label><label class="field">Response status<select aria-label="Response status" id="rc-status-filter">${FILTERS.map(([key, text]) => option(key, text === "All" ? "All responses" : text, filter)).join("")}${individual.length ? `<optgroup label="Individual status">${individual.map((key) => option(key, STATUS_LABELS[key], filter)).join("")}</optgroup>` : ""}</select></label><span id="rc-recipient-count" role="status" class="toolbar-count"></span><div class="toolbar-actions"><button type="button" class="text-button" id="rc-copy-emails" ${awaiting.some((item) => item.email) ? "" : "disabled"}>Copy emails (${awaiting.filter((item) => item.email).length.toLocaleString()} awaiting)</button><button type="button" class="text-button" id="rc-download-answers" ${recipients.some((item) => item.current_submission) ? "" : "disabled"}>Answers (CSV)</button></div></div>
      <div class="rc-table-scroll" tabindex="0" role="region" aria-label="Recipient cards"><table class="rc-table"><thead><tr><th>Student</th><th>Section</th><th>Response</th><th>Delivery</th><th>Publication</th><th>Actions</th></tr></thead><tbody>${recipients.map((item) => {
        const link = safeLink(item.link);
        const state = statusOf(item);
        const differs = differing(item);
        return `<tr data-recipient="${escape(item.id)}" data-search="${escape(`${item.student_name} ${item.email || ""} ${item.section_number}`.toLowerCase())}" data-filters="${escape(tokens.get(item.id).join(" "))}"><th scope="row">${escape(item.student_name)}<span class="rc-cell-note">${escape(item.email)}</span></th><td>${escape(item.section_number)}</td><td><span class="chip"${STATUS_TONES[state] ? ` data-tone="${STATUS_TONES[state]}"` : ""}>${escape(STATUS_LABELS[state] || state)}</span>${differs.length ? `<span class="rc-cell-note rc-differs">Differs: ${escape(differs.join(", "))}</span>` : ""}</td><td>${notDelivered(item) ? `<span class="chip" data-tone="danger">${escape(DELIVERY_LABELS[item.delivery_status])}</span>` : escape(DELIVERY_LABELS[item.delivery_status || "not_sent"] || item.delivery_status)}${item.distribution_block ? `<span class="rc-cell-note">${escape(item.distribution_block)}</span>` : ""}${receiptProblem(item) ? '<span class="rc-cell-note">Submission receipt not confirmed</span>' : ""}</td><td>${escape(PUBLISH_LABELS[item.publish_status] || item.publish_status)}${item.error ? `<span class="rc-cell-note">${escape(item.error)}</span>` : ""}${item.revocation === "pending" ? '<span class="rc-cell-note">Replaced · revocation pending</span>' : ""}</td><td><div class="rc-row-actions"><button class="text-button" data-history="${escape(item.id)}">View responses</button>${link ? `<button class="text-button" data-copy="${escape(item.id)}">Copy link</button>` : ""}${canResend(item) && mailAvailable() ? `<button class="text-button" data-resend="${escape(item.id)}">Resend email</button>` : ""}<button class="text-button" data-control="unlock" data-card-id="${escape(item.id)}" ${state === "revoked" || !context.configured || item.publish_status !== "published" ? "disabled" : ""}>Unlock</button><button class="text-button" data-control="revoke" data-card-id="${escape(item.id)}" ${state === "revoked" || !context.configured || item.publish_status !== "published" ? "disabled" : ""}>Revoke</button></div></td></tr>`;
      }).join("")}</tbody></table></div></section>`;
    const select = root().querySelector("#rc-status-filter");
    const apply = () => {
      const search = root().querySelector("#rc-recipient-search").value.trim().toLowerCase();
      let visible = 0;
      root().querySelectorAll("[data-recipient]").forEach((node) => {
        node.hidden = !node.dataset.search.includes(search) || Boolean(filter && !node.dataset.filters.split(" ").includes(filter));
        if (!node.hidden) visible++;
      });
      root().querySelector("#rc-recipient-count").textContent = `${visible} of ${recipients.length} recipients`;
    };
    const choose = (value) => {
      filter = value;
      select.value = value;
      root().querySelectorAll('[data-filter-group="rc-status"] [data-filter]').forEach((chip) => chip.setAttribute("aria-pressed", String(chip.dataset.filter === value)));
      // The address keeps the filter, so Back and a reload come back to it; links use ?status= and ?delivery=problem.
      const next = value === "not_delivered" ? "?delivery=problem" : value ? `?status=${encodeURIComponent(value)}` : "";
      if (location.hash.split("?")[0] === route && location.hash !== route + next) history.replaceState(history.state, "", route + next);
      apply();
    };
    bindFilterChips(root().querySelector(".rc-issue-page"), "rc-status", choose);
    select.onchange = () => choose(select.value);
    root().querySelector("#rc-recipient-search").oninput = apply;
    apply();
    root().querySelector("#rc-issue-sync").onclick = (event) => runButton(event.currentTarget, () => (context.configured ? syncNow() : render(true)));
    root().querySelector("#rc-copy-emails").onclick = () => copyText([...new Set(awaiting.map((item) => item.email).filter(Boolean))].join(", "),
      `${plural(new Set(awaiting.map((item) => item.email).filter(Boolean)).size, "address", "addresses")} copied. Nothing was sent.`, "Emails awaiting a response");
    // The answers open in the app's file viewer as a table (VIEWER); its Download saves the same CSV, built here as before.
    root().querySelector("#rc-download-answers").onclick = () => viewTable({ name: `${label} · answers`, rows: answersRows(issue).map((row) => row.map(cellText)),
      text: answersCsv(issue), saveAs: `${label.replace(/[^\w.-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80) || "response-card"}-answers.csv` });
    root().querySelectorAll("[data-history]").forEach((button) => { button.onclick = () => showHistory(recipients.find((item) => item.id === button.dataset.history), issue.schema); });
    root().querySelectorAll("[data-resend]").forEach((button) => { button.onclick = () => openResend(recipients.find((item) => item.id === button.dataset.resend)); });
    root().querySelectorAll("[data-copy]").forEach((button) => { button.onclick = () => runButton(button, async () => {
      let item = recipients.find((item) => item.id === button.dataset.copy);
      if (issue.context?.exam_event_id) {
        const current = await api(`${base}/issues/${encodeURIComponent(issue.id)}`);
        item = current.recipients.find((recipient) => recipient.id === button.dataset.copy);
        if (!item?.link) throw new Error(item?.distribution_block || "This card link is no longer available.");
      }
      const link = safeLink(item.link);
      try { await navigator.clipboard.writeText(link); toast("Card link copied."); }
      catch { showDialog(`${dialogHeader("Student card link", "Anyone with this link can access this card.")}<div class="dialog-body"><label class="field">Copy this link<input id="rc-copy-link" readonly value="${escape(link)}"></label></div><footer class="dialog-footer"><button class="button" data-action="close">Close</button></footer>`); document.querySelector("#rc-copy-link").select(); }
    }); });
    root().querySelectorAll("[data-control]").forEach((button) => { button.onclick = () => {
      const item = recipients.find((recipient) => recipient.id === button.dataset.cardId);
      const action = button.dataset.control;
      showDialog(`<form id="rc-control-form">${dialogHeader(action === "revoke" ? "Revoke this card?" : "Unlock another submission?", escape(item.student_name))}<div class="dialog-body"><p>${action === "revoke" ? "This link will stop accepting responses. The existing response history is retained." : "Allow this student to submit again under this card’s policy. Earlier responses remain in the history."}</p>${formError()}</div><footer class="dialog-footer"><button class="button" type="button" data-action="close">Cancel</button><button class="button primary" type="submit">${action === "revoke" ? "Revoke card" : "Unlock card"}</button></footer></form>`);
      const form = document.querySelector("#rc-control-form"); const requestId = crypto.randomUUID();
      form.onsubmit = (event) => { event.preventDefault(); void perform(form, async (current) => { await api(`${base}/cards/${encodeURIComponent(item.id)}/control`, "POST", {action, request_id: requestId});
        toast(action === "revoke" ? "Card revoked." : "Card unlocked."); if (!current()) return; document.querySelector("#dialog").close(); await render(true); }); };
    }; });
  }

  /** Copy to the clipboard, or show the text to copy by hand when the browser refuses. Sends nothing. */
  async function copyText(text, done, title) {
    try { await navigator.clipboard.writeText(text); toast(done); }
    catch { showDialog(`${dialogHeader(escape(title), "Copy these addresses into your own email.")}<div class="dialog-body"><label class="field">Addresses<textarea id="rc-copy-text" readonly rows="6">${escape(text)}</textarea></label></div><footer class="dialog-footer"><button class="button" data-action="close">Close</button></footer>`); document.querySelector("#rc-copy-text").select(); }
  }


  function showHistory(recipient, schema) {
    schema = recipient.schema || schema;
    const labels = Object.fromEntries((schema?.fields || []).map((field) => [field.id, field.label]));
    // Stored answers keep the order the browser assembled them in; show them in the card's own field order.
    const order = (schema?.fields || []).map((field) => field.id);
    const position = (key) => (order.indexOf(key) === -1 ? order.length : order.indexOf(key));
    const orderedAnswers = (answers) => Object.entries(answers || {}).sort((a, b) => position(a[0]) - position(b[0]));
    const submissions = recipient.submissions?.length ? recipient.submissions : recipient.current_submission ? [recipient.current_submission] : [];
    // Each checked answer says whether it matched what the card expected; the expected value itself is never shown.
    const mark = (item, key) => {
      const result = item.verification?.[key];
      if (typeof result?.matched !== "boolean") return "";
      return result.matched ? ' <span class="rc-mark" data-matched="true">✓ <span>Matches the card</span></span>' : ' <span class="rc-mark" data-matched="false">✗ <span>Differs from what the card expected</span></span>';
    };
    showDialog(`${dialogHeader("Response history", `${escape(recipient.student_name)} · Section ${escape(recipient.section_number)}`)}<div class="dialog-body rc-history">${submissions.map((item, index) => `<section><h3>Response ${index + 1} · ${escape(STATUS_LABELS[item.status] || item.status)}</h3><p class="field-hint">${escape(when(item.submitted_at))}${recipient.current_submission?.id === item.id ? " · Current response" : ""}</p><dl class="detail-group">${orderedAnswers(item.answers).map(([key, value]) => `<dt>${escape(labels[key] || key)}${mark(item, key)}</dt><dd>${escape(Array.isArray(value) ? value.join(", ") : typeof value === "boolean" ? (value ? "Yes" : "No") : typeof value === "object" ? JSON.stringify(value) : String(value))}</dd>`).join("")}</dl></section>`).join("") || '<p>No submitted responses have been synchronized for this card.</p>'}</div><footer class="dialog-footer"><button class="button" data-action="close">Close</button></footer>`);
  }

  return {render};
}
