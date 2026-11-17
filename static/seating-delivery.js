import { dialogSession, tableMarkup } from "./ui.js";
import { createDeliveryPreviews } from "./seating-delivery-api.js";
import { viewLink } from "./file-viewer.js";

// How an invitation row shows its delivery state in the Gmail and mail-merge dialogs.
function invitationDeliveryLabel(value) {
  return {not_sent: "Not sent by Faculty", sent: "Sent", failed: "Failed", unconfirmed: "Delivery unconfirmed", no_email: "No email address"}[value] || String(value || "Not sent by Faculty").replaceAll("_", " ");
}

/**
 * Delivery dialogs for an existing term-owned seating chart. This controller
 * owns browser request recovery only; records and approval remain server-owned.
 * Creating a preview never sends or publishes a response card.
 */
export function createSeatingDelivery({ api, escape, toast, showDialog, dialogHeader, gmailLabel, when, refreshChart, refresh }) {
  // Keep unresolved requests across close/reopen. Explicit retries reuse their
  // exact payload, and nothing in this controller automatically retries a send.
  const gmailSendRequests = new Map();
  const studentChartMailRequests = new Map();
  const previews = createDeliveryPreviews(api);
  const table = (headings, rows, label) => tableMarkup(escape, headings, rows, label);
  const excludedStudentsMarkup = (data) => data.skipped.length ? `<details class="seating-merge-skipped" ${data.eligible_count ? "" : "open"}><summary>Excluded students (${escape(String(data.skipped_count))})</summary>${table(["Student", "Section", "Reason"], data.skipped.map((item) => `<tr><td>${escape(item.name)}</td><td>${escape(item.section_number)}</td><td>${escape(item.reason)}</td></tr>`), "Excluded students and reasons")}</details>` : "";

  async function openStudentChartMail(current) {
    const endpoint = `/api/seating/${encodeURIComponent(current.id)}/student-email`;
    showDialog(`<div id="seating-student-email">${dialogHeader("Email student seating charts", escape(`${current.title} · ${current.room_name}`))}<div class="dialog-body"><p>Each student receives a separate PDF with their seat labelled <strong>Your seat</strong> and the seat number underneath. Every other occupied seat is shaded and labelled <strong>Occupied</strong>, with its seat number underneath. Student names, response-card statuses, and exam versions are omitted.</p><p class="field-hint">This sends seating charts separately from response-card invitations. Response cards and printing confirmation are not required.</p><label class="field">Audience<select name="audience" aria-label="Student chart email audience"><option value="unsent">Not emailed the current chart</option><option value="all">Send again (including previously emailed)</option></select></label><p class="field-hint">Emails already queued or with an unconfirmed delivery of this chart are excluded. If seating changes before delivery, the old queued chart is stopped. Review the current chart and send again after a change.</p><label class="field">Send from<select name="mail_provider" aria-label="Student chart email provider" disabled><option value="default">Faculty email (default)</option></select></label><p class="field-hint" data-student-email-sender role="status">Checking email configuration…</p><p data-student-email-connect hidden><a href="#response-cards?email" target="_blank" rel="noopener">Open email settings in Response Cards</a> (a new tab), then come back to this window and choose Refresh preview.</p><div class="seating-merge-preview" data-student-email-preview aria-live="polite"></div><section class="seating-merge-instructions" aria-labelledby="seating-student-email-subject-title"><h3 id="seating-student-email-subject-title">Email subject</h3><div class="seating-merge-subject" data-student-email-subject></div></section><div data-student-email-history></div><p class="form-error" role="alert" hidden></p><p class="field-hint" data-student-email-result role="status"></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Close</button><button type="button" class="button" data-student-email-refresh>Refresh preview</button><button type="button" class="button primary" data-student-email-retry hidden>Retry request</button><button type="button" class="button primary" data-student-email-send disabled>Send emails</button></footer></div>`);
    const host = document.querySelector("#seating-student-email"), dialog = document.querySelector("#dialog");
    const audience = host.querySelector('[name="audience"]'), provider = host.querySelector('[name="mail_provider"]');
    const previewHost = host.querySelector("[data-student-email-preview]"), history = host.querySelector("[data-student-email-history]");
    const send = host.querySelector("[data-student-email-send]"), retry = host.querySelector("[data-student-email-retry]");
    const refreshPreview = host.querySelector("[data-student-email-refresh]"), error = host.querySelector('[role="alert"]');
    const result = host.querySelector("[data-student-email-result]"), sender = host.querySelector("[data-student-email-sender]");
    const connect = host.querySelector("[data-student-email-connect]"), subject = host.querySelector("[data-student-email-subject]");
    let preview = null, previewRequestId = "", requestGeneration = 0, sending = false, loading = false;
    const active = dialogSession(host, dialog);
    const delivery = (value) => ({pending: "Queued", sending: "Sending", accepted: "Sent", sent: "Sent", failed: "Failed", unconfirmed: "Delivery unconfirmed", cancelled: "Cancelled"}[value] || String(value || "Unknown").replaceAll("_", " "));
    function controls() {
      const pending = studentChartMailRequests.has(current.id);
      audience.disabled = sending || pending;
      provider.disabled = sending || pending || loading;
      refreshPreview.disabled = sending || pending || loading;
      retry.hidden = !pending;
      retry.disabled = sending;
      send.disabled = sending || pending || loading || !preview?.can_send || !preview?.eligible_count;
      send.textContent = preview?.eligible_count ? `Send ${preview.eligible_count} email${preview.eligible_count === 1 ? "" : "s"}` : "Send emails";
      send.setAttribute("aria-busy", String(sending));
    }
    function uncertain() {
      error.textContent = "Faculty could not confirm whether these chart emails were queued. Retry this request to check its result; the same request will not queue duplicates.";
      error.hidden = false;
      controls();
    }
    function drawPreview(data) {
      const mail = data.mail || {};
      const selectedProvider = data.mail_provider || provider.value;
      provider.innerHTML = `<option value="default">Faculty email (default)</option>${mail.gmail?.connected ? `<option value="gmail">${escape(gmailLabel(mail))}</option>` : selectedProvider === "gmail" ? '<option value="gmail">Gmail — disconnected</option>' : ""}`;
      provider.value = selectedProvider;
      sender.textContent = data.sender ? `From: ${data.sender}. This choice applies to these chart emails only.` : "The selected email provider is unavailable. Choose an available provider or open email settings.";
      connect.hidden = Boolean(data.sender);
      subject.textContent = data.subject || "";
      previewHost.innerHTML = `<div class="seating-merge-counts"><strong>${escape(String(data.eligible_count))} ready to send</strong><span>${escape(String(data.skipped_count))} excluded</span></div>${data.recipients.length ? table(["Student", "Email", "Section", "Seat", "Personal chart"], data.recipients.map((item) => {
        const query = new URLSearchParams({enrollment_id: item.enrollment_id, fingerprint: data.fingerprint, audience: data.audience, mail_provider: data.mail_provider});
        return `<tr><td>${escape(item.name)}</td><td>${escape(item.email)}</td><td>${escape(String(item.section_number ?? ""))}</td><td>${escape(item.seat)}</td><td><a ${viewLink(`${endpoint}/preview?${query}`, `Seating chart for ${item.name}`, escape)} aria-label="${escape(`Preview PDF for ${item.name}`)}">Preview PDF</a></td></tr>`;
      }), "Student seating charts to email") : '<p class="seating-merge-empty">No students qualify for this audience. Review the exclusions or choose Send again.</p>'}${excludedStudentsMarkup(data)}`;
      const attempts = data.history || [];
      history.innerHTML = `<section class="seating-merge-instructions" aria-labelledby="seating-student-email-history-title"><h3 id="seating-student-email-history-title">Chart email history</h3><p class="field-hint">Refresh preview to follow delivery. This history covers chart emails only.</p>${attempts.length ? table(["Student", "Email", "Seat", "Status", "Queued at", "Details"], attempts.map((item) => `<tr><td>${escape(item.name || "")}</td><td>${escape(item.email || "")}</td><td>${escape(item.seat || "")}</td><td>${escape(delivery(item.status))}</td><td>${escape(when(item.created_at))}</td><td>${escape(item.error || "")}</td></tr>`), "Student chart email delivery history") : '<p class="seating-merge-empty">No chart emails have been queued.</p>'}</section>`;
    }
    async function loadPreview() {
      if (sending || studentChartMailRequests.has(current.id)) return;
      const token = ++requestGeneration;
      const selected = {audience: audience.value, mail_provider: provider.value};
      preview = null; previewRequestId = ""; loading = true; controls(); error.hidden = true; result.textContent = "";
      subject.textContent = ""; history.innerHTML = ""; sender.textContent = "Checking email configuration…"; connect.hidden = true;
      previewHost.innerHTML = '<p class="loading-state">Checking students and their seating charts…</p>';
      previewHost.setAttribute("aria-busy", "true");
      try {
        const data = await previews.studentCharts(current.id, selected.audience, selected.mail_provider);
        if (token !== requestGeneration || !active()) return;
        preview = data; previewRequestId = crypto.randomUUID();
        drawPreview(data);
      } catch (cause) {
        if (token !== requestGeneration || !active()) return;
        previewHost.innerHTML = ""; sender.textContent = "Student chart email preview unavailable.";
        error.textContent = cause.message; error.hidden = false;
      } finally {
        if (token === requestGeneration && active()) { loading = false; previewHost.removeAttribute("aria-busy"); controls(); }
      }
    }
    async function queueCharts(record) {
      if (sending) return;
      const payload = record.payload;
      sending = true; studentChartMailRequests.set(current.id, record);
      const clearRequest = () => { if (studentChartMailRequests.get(current.id)?.payload.request_id === payload.request_id) studentChartMailRequests.delete(current.id); };
      preview = null; controls(); error.hidden = true; result.textContent = "Queuing student chart emails…";
      try {
        const response = await fetch(endpoint, {method: "POST", cache: "no-store", signal: AbortSignal.timeout(60000),
          headers: {"Content-Type": "application/json", "X-Teaching-Request": "1"}, body: JSON.stringify(payload)});
        let receipt;
        try { receipt = await response.json(); } catch { throw new Error("The queue response could not be read."); }
        if (!response.ok) {
          // Validation errors reject the whole batch. Unknown outcomes retain
          // the request ID so an explicit retry recovers the original receipt.
          if ([400, 401, 403, 404, 409, 422].includes(response.status) && typeof receipt.error === "string") {
            clearRequest();
            if (active()) {
              error.textContent = `${receipt.error} Refresh the preview and review it before sending.`;
              error.hidden = false; previewHost.innerHTML = "";
            }
            return;
          }
          throw new Error("The queue response could not be confirmed.");
        }
        if (!Number.isInteger(receipt.queued) || receipt.queued < 0 || receipt.request_id !== payload.request_id || receipt.chart_id !== current.id) throw new Error("The queue response could not be confirmed.");
        clearRequest();
        const message = `${receipt.queued} student chart email${receipt.queued === 1 ? "" : "s"} queued.`;
        if (active()) {
          previewHost.innerHTML = "";
          result.textContent = `${message} Refresh preview to follow delivery.`;
        }
        toast(message);
      } catch {
        if (active()) uncertain();
      } finally {
        sending = false;
        if (active()) {
          if (result.textContent === "Queuing student chart emails…") result.textContent = "";
          controls();
        }
      }
    }
    audience.onchange = () => { void loadPreview(); };
    provider.onchange = () => { void loadPreview(); };
    refreshPreview.onclick = () => { void loadPreview(); };
    send.onclick = () => {
      if (!preview?.can_send || !preview?.eligible_count || sending || studentChartMailRequests.has(current.id)) return;
      void queueCharts({payload: {audience: preview.audience, mail_provider: preview.mail_provider, fingerprint: preview.fingerprint, request_id: previewRequestId}, preview});
    };
    retry.onclick = () => { const record = studentChartMailRequests.get(current.id); if (record) void queueCharts(record); };
    const previous = studentChartMailRequests.get(current.id);
    if (previous) {
      audience.value = previous.payload.audience;
      drawPreview(previous.preview);
      sender.textContent = "A previous student chart email request needs confirmation.";
      uncertain();
    } else await loadPreview();
  }

  async function openGmailSend(current) {
    const endpoint = `/api/seating/${encodeURIComponent(current.id)}/gmail-send`;
    showDialog(`<div id="seating-gmail-send">${dialogHeader("Send with Gmail", escape(`${current.title} · ${current.room_name}`))}<div class="dialog-body"><p>Send response-card invitations directly from your connected Gmail account. Faculty tracks these deliveries. Your default email provider remains unchanged.</p><p data-gmail-sender role="status">Checking the connected account…</p><p data-gmail-connect hidden><a href="#response-cards?email" target="_blank" rel="noopener">Connect Gmail in Response Cards</a> (a new tab), then come back to this window and choose Refresh preview.</p><label class="field">Audience<select name="audience" aria-label="Gmail send audience"><option value="unsent">Not emailed yet</option><option value="pending">All awaiting response (including previously emailed)</option></select></label><p class="field-hint">These filters use Faculty’s delivery records. Students with completed cards or an invitation already queued are excluded. All awaiting response includes earlier and unconfirmed deliveries; check those before sending another copy. Faculty cannot track emails you sent manually from a spreadsheet.</p><div class="seating-merge-preview" data-gmail-preview aria-live="polite"></div><p class="form-error" role="alert" hidden></p><p class="field-hint" data-gmail-result role="status"></p><section class="seating-merge-instructions" aria-labelledby="seating-gmail-message-title"><h3 id="seating-gmail-message-title">Invitation email</h3><p>Each email uses the original response-card invitation and personal link. It begins with “Hello,” and does not include the student's name. An actual replacement card retains its updated-card notice.</p><p class="field-hint">Original subject; replacement cards keep the UPDATED prefix shown above.</p><div class="seating-merge-subject" data-gmail-subject></div></section></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Close</button><button type="button" class="button" data-gmail-refresh>Refresh preview</button><button type="button" class="button primary" data-gmail-retry hidden>Retry request</button><button type="button" class="button primary" data-gmail-send disabled>Send emails</button></footer></div>`);
    const host = document.querySelector("#seating-gmail-send"), dialog = document.querySelector("#dialog");
    const audience = host.querySelector('[name="audience"]'), previewHost = host.querySelector("[data-gmail-preview]");
    const send = host.querySelector("[data-gmail-send]"), retry = host.querySelector("[data-gmail-retry]");
    const refreshPreview = host.querySelector("[data-gmail-refresh]"), error = host.querySelector('[role="alert"]');
    const result = host.querySelector("[data-gmail-result]"), sender = host.querySelector("[data-gmail-sender]");
    const connect = host.querySelector("[data-gmail-connect]"), subject = host.querySelector("[data-gmail-subject]");
    let preview = null, previewRequestId = "", requestGeneration = 0, sending = false;
    const active = dialogSession(host, dialog);
    function controls() {
      const pending = gmailSendRequests.has(current.id);
      audience.disabled = sending || pending;
      refreshPreview.disabled = sending || pending;
      retry.hidden = !pending;
      retry.disabled = sending;
      send.disabled = sending || pending || !preview?.can_send || !preview?.eligible_count;
      send.textContent = preview?.eligible_count ? `Send ${preview.eligible_count} email${preview.eligible_count === 1 ? "" : "s"}` : "Send emails";
    }
    function uncertain() {
      error.textContent = "Faculty could not confirm whether these invitations were queued. Retry this request to check its result; the same request will not queue duplicates.";
      error.hidden = false;
      controls();
    }
    async function loadPreview() {
      if (sending || gmailSendRequests.has(current.id)) return;
      const token = ++requestGeneration;
      preview = null; previewRequestId = ""; controls(); error.hidden = true; result.textContent = ""; subject.textContent = "";
      sender.textContent = "Checking the connected account…"; connect.hidden = true;
      previewHost.innerHTML = '<p class="loading-state">Checking invitations…</p>';
      previewHost.setAttribute("aria-busy", "true");
      try {
        const data = await previews.invitations(current.id, audience.value);
        if (token !== requestGeneration || !active()) return;
        preview = data; previewRequestId = crypto.randomUUID();
        sender.textContent = data.gmail?.connected ? `From: ${data.sender}` : "Gmail is not connected.";
        connect.hidden = Boolean(data.gmail?.connected);
        subject.textContent = data.subject;
        previewHost.innerHTML = `<div class="seating-merge-counts"><strong>${escape(String(data.eligible_count))} ready to send</strong><span>${escape(String(data.skipped_count))} excluded</span></div>${data.recipients.length ? table(["Student", "Email", "Section", "Notice", "Faculty delivery", "Subject"], data.recipients.map((item) => `<tr><td>${escape(item.name)}</td><td>${escape(item.email)}</td><td>${escape(String(item.section_number))}</td><td>${item.kind === "dr" ? "DR testing" : "Seat assignment"}</td><td>${escape(invitationDeliveryLabel(item.delivery))}</td><td>${escape(item.subject || data.subject)}</td></tr>`), "Invitations to send with Gmail") : '<p class="seating-merge-empty">No invitations qualify for this audience. Review the exclusions or choose the broader audience.</p>'}${excludedStudentsMarkup(data)}`;
      } catch (cause) {
        if (token !== requestGeneration || !active()) return;
        previewHost.innerHTML = ""; sender.textContent = "Gmail delivery preview unavailable.";
        error.textContent = cause.message; error.hidden = false;
      } finally {
        if (token === requestGeneration && active()) { previewHost.removeAttribute("aria-busy"); controls(); }
      }
    }
    async function queueInvitations(payload) {
      if (sending) return;
      sending = true; gmailSendRequests.set(current.id, payload);
      const clearRequest = () => { if (gmailSendRequests.get(current.id)?.request_id === payload.request_id) gmailSendRequests.delete(current.id); };
      let confirmed = false;
      preview = null; controls(); error.hidden = true; result.textContent = "Queuing invitations…";
      try {
        const response = await fetch(endpoint, {method: "POST", cache: "no-store", signal: AbortSignal.timeout(60000),
          headers: {"Content-Type": "application/json", "X-Teaching-Request": "1"}, body: JSON.stringify(payload)});
        let receipt;
        try { receipt = await response.json(); } catch { throw new Error("The queue response could not be read."); }
        if (!response.ok) {
          // These API rejections occur before queue publication. An unknown
          // server/proxy outcome keeps its request ID for an explicit retry.
          if ([400, 401, 403, 404, 409, 422, 503].includes(response.status) && typeof receipt.error === "string") {
            clearRequest();
            if (active()) {
              error.textContent = `${receipt.error} Refresh the preview and review it before sending.`;
              error.hidden = false;
              if (response.status === 503) connect.hidden = false;
              previewHost.innerHTML = "";
            }
            return;
          }
          throw new Error("The queue response could not be confirmed.");
        }
        if (!Number.isInteger(receipt.queued) || receipt.queued < 0 || receipt.request_id !== payload.request_id || receipt.chart_id !== current.id || typeof receipt.sender !== "string" || !receipt.sender) throw new Error("The queue response could not be confirmed.");
        clearRequest();
        confirmed = true;
        const message = `${receipt.queued} invitation${receipt.queued === 1 ? "" : "s"} queued through ${receipt.sender}.`;
        if (active()) {
          previewHost.innerHTML = "";
          result.textContent = `${message} The Inbox worker sends them; refresh the chart to follow delivery.`;
        }
        toast(message);
      } catch {
        if (active()) uncertain();
      } finally {
        sending = false;
        if (active()) {
          if (result.textContent === "Queuing invitations…") result.textContent = "";
          controls();
        }
      }
      if (confirmed) {
        try { await refreshChart(); await refresh(); }
        catch { toast("Invitations were queued, but the chart could not be refreshed. Refresh the page to follow delivery.", true); }
      }
    }
    audience.onchange = () => { void loadPreview(); };
    refreshPreview.onclick = () => { void loadPreview(); };
    send.onclick = () => {
      if (!preview?.can_send || !preview?.eligible_count || sending || gmailSendRequests.has(current.id)) return;
      void queueInvitations({audience: preview.audience, fingerprint: preview.fingerprint, request_id: previewRequestId});
    };
    retry.onclick = () => { const payload = gmailSendRequests.get(current.id); if (payload) void queueInvitations(payload); };
    const previous = gmailSendRequests.get(current.id);
    if (previous) {
      audience.value = previous.audience;
      sender.textContent = "A previous Gmail queue request needs confirmation.";
      uncertain();
    } else await loadPreview();
  }

  async function openMailMerge(current) {
    const endpoint = `/api/seating/${encodeURIComponent(current.id)}/mail-merge`;
    showDialog(`<div id="seating-mail-merge">${dialogHeader("Gmail mail merge", escape(`${current.title} · ${current.room_name}`))}<div class="dialog-body"><p>Download the response-card invitations as a spreadsheet for Gmail. Exporting does not send email or mark invitations as sent in Faculty.</p><label class="field">Audience<select name="audience" aria-label="Mail merge audience"><option value="unsent">Not emailed yet</option><option value="pending">All awaiting response (including previously emailed)</option></select></label><p class="field-hint">These filters use Faculty’s delivery records. Gmail sends are not tracked here, so a new export may include students you emailed manually. Queued Faculty mail is excluded; the broader audience includes earlier or unconfirmed deliveries.</p><div class="seating-merge-preview" data-merge-preview aria-live="polite"></div><p class="form-error" role="alert" hidden></p><p class="field-hint" data-merge-result role="status"></p><section class="seating-merge-instructions" aria-labelledby="seating-merge-instructions-title"><h3 id="seating-merge-instructions-title">Send with Gmail</h3><ol><li>Import the downloaded <strong>.xlsx</strong> file into Google Sheets. The first tab, <strong>Recipients</strong>, is ready to use; no manual copying of recipient data is needed.</li><li>In Gmail, open <strong>Compose → mail merge → Add spreadsheet</strong>. Choose the sheet and map <strong>Email</strong> and <strong>FirstName</strong>. FirstName contains the generic value <strong>Student</strong> for everyone; no student names appear in the recipient sheet.</li><li>Use this subject exactly; Gmail does not merge subject fields:<div class="seating-merge-subject" data-merge-subject></div></li><li>In the email body, type <strong>@</strong> and choose the <strong>Message</strong> merge chip. The resulting chip is <strong>@Message</strong>; pasting that text alone does not insert a merge field. The Message column contains the original invitation and personal link, with a generic Hello greeting and no student names, including for previously emailed cards.</li><li>Preview the merged messages in Gmail, then send there. Personal links appear as plain URLs in Message; Gmail cannot merge values into hyperlinked text.</li></ol><p class="field-hint">Gmail mail merge requires an eligible Workspace account. The workbook includes an Instructions tab. Its links are personal; keep the spreadsheet private.</p></section></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Close</button><button type="button" class="button" data-merge-refresh>Refresh preview</button><button type="button" class="button primary" data-merge-download disabled>Download .xlsx</button></footer></div>`);
    const host = document.querySelector("#seating-mail-merge");
    const dialog = document.querySelector("#dialog");
    const audience = host.querySelector('[name="audience"]');
    const previewHost = host.querySelector("[data-merge-preview]");
    const download = host.querySelector("[data-merge-download]");
    const refreshPreview = host.querySelector("[data-merge-refresh]");
    const error = host.querySelector('[role="alert"]');
    const result = host.querySelector("[data-merge-result]");
    let preview = null, requestGeneration = 0, downloading = false;
    const active = dialogSession(host, dialog);
    async function loadPreview() {
      if (downloading) return;
      const token = ++requestGeneration;
      preview = null; download.disabled = true; error.hidden = true; result.textContent = "";
      previewHost.innerHTML = '<p class="loading-state">Checking invitations…</p>';
      previewHost.setAttribute("aria-busy", "true");
      try {
        const data = await previews.mailMerge(current.id, audience.value);
        if (token !== requestGeneration || !active()) return;
        preview = data;
        previewHost.innerHTML = `<div class="seating-merge-counts"><strong>${escape(String(data.eligible_count))} ready to export</strong><span>${escape(String(data.skipped_count))} excluded</span></div>${data.recipients.length ? table(["Student", "Email", "Section", "Notice", "Faculty delivery"], data.recipients.map((item) => `<tr><td>${escape(item.name)}</td><td>${escape(item.email)}</td><td>${escape(String(item.section_number))}</td><td>${item.kind === "dr" ? "DR testing" : "Seat assignment"}</td><td>${escape(invitationDeliveryLabel(item.delivery))}</td></tr>`), "Invitations included in the spreadsheet") : '<p class="seating-merge-empty">No invitations qualify for this audience. Review the exclusions or choose the broader audience.</p>'}${excludedStudentsMarkup(data)}`;
        host.querySelector("[data-merge-subject]").textContent = data.subject;
        download.disabled = !data.eligible_count;
      } catch (cause) {
        if (token !== requestGeneration || !active()) return;
        previewHost.innerHTML = ""; error.textContent = cause.message; error.hidden = false;
      } finally {
        if (token === requestGeneration && active()) previewHost.removeAttribute("aria-busy");
      }
    }
    audience.onchange = () => { void loadPreview(); };
    refreshPreview.onclick = () => { void loadPreview(); };
    download.onclick = async () => {
      if (!preview || downloading || !preview.eligible_count) return;
      const selected = preview;
      downloading = true; audience.disabled = true; download.disabled = true; refreshPreview.disabled = true;
      error.hidden = true; result.textContent = "Preparing spreadsheet…";
      try {
        const query = new URLSearchParams({audience: selected.audience, fingerprint: selected.fingerprint});
        const response = await fetch(`${endpoint}/download?${query}`, {cache: "no-store", signal: AbortSignal.timeout(30000)});
        if (!response.ok) {
          let message = "The spreadsheet could not be exported.";
          try { message = (await response.json()).error || message; } catch { /* Keep the readable fallback for a proxy error. */ }
          if (response.status === 409) { preview = null; message += " Refresh the preview and review the updated audience before downloading."; }
          throw new Error(message);
        }
        const blob = await response.blob();
        if (!blob.size) throw new Error("The spreadsheet was empty. Refresh the preview and try again.");
        if (!active()) return;
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url; link.download = String(selected.filename || "exam-mail-merge.xlsx").replace(/[\\/]/g, "_"); link.hidden = true;
        document.body.appendChild(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 30000);
        result.textContent = "Spreadsheet download started. Import it into Google Sheets and send from Gmail. Faculty's delivery records are unchanged.";
      } catch (cause) {
        if (!active()) return;
        error.textContent = cause.name === "TimeoutError" ? "The download timed out. Refresh the preview and try again." : cause.message || "The download failed. Check your connection and try again.";
        error.hidden = false; result.textContent = "";
      } finally {
        downloading = false;
        if (active()) { audience.disabled = false; refreshPreview.disabled = false; download.disabled = !preview?.eligible_count; }
      }
    };
    await loadPreview();
  }

  return { openStudentChartMail, openGmailSend, openMailMerge };
}
