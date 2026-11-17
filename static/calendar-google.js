// @ts-check
// The Calendar page's Google Calendar dialog (google_calendar/, AGENTS.md "Google Calendar sync"): connect the account Faculty
// already sends Response Card mail from, choose which Faculty events go into its main calendar and which Google calendars show
// here, Sync now, and Remove Faculty's events. Every write is the server's own route; nothing here decides what is sent.
import { runFormAction, dialogSession, calloutMarkup, disclosureMarkup, errorMessage } from "./ui.js";

const BASE = "/api/google-calendar";

/** "just now", "3 minutes ago", "2 hours ago" or a date, for an instant. @param {string | null | undefined} instant */
export function ago(instant, now = Date.now()) {
  if (!instant) return "";
  const minutes = Math.round((now - Date.parse(instant)) / 60000);
  if (!Number.isFinite(minutes)) return "";
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(instant));
}

/**
 * The toolbar's short word for the sync, from /api/calendar's `google` summary; null hides the button (Google not set up here).
 * @param {any} summary
 * @returns {{label: string, tone: string} | null}
 */
export function syncBadge(summary) {
  if (!summary?.configured) return null;
  if (!summary.permission) return { label: "Connect", tone: "idle" };
  if (!summary.enabled) return { label: "Off", tone: "idle" };
  if (summary.error) return { label: "Needs attention", tone: "danger" };
  return { label: summary.read_at ? "Synced" : "Starting", tone: "ok" };
}

/** @param {string} iso YYYY-MM-DD */
const wall = (iso) => new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(new Date(`${iso}T12:00:00Z`));

/**
 * Opens the dialog. `changed` is called after anything that changes what the Calendar shows.
 * `syncNow` (back from Google's sign-in) runs the first sync at once instead of waiting for the next round.
 * @param {{api: Function, escape: (value: string) => string, toast: Function, showDialog: Function, dialogHeader: Function, changed: Function, syncNow?: boolean}} tools
 */
export async function openGoogleCalendar({ api, escape, toast, showDialog, dialogHeader, changed, syncNow = false }) {
  const e = (/** @type {unknown} */ value) => escape(String(value ?? ""));
  const dialog = /** @type {HTMLDialogElement} */ (document.getElementById("dialog"));
  showDialog(`${dialogHeader("Google Calendar", "Your Google calendar and Faculty's, both ways")}<div class="dialog-body"><p class="loading-state">Reading the sync…</p></div>`);
  const opened = dialog.firstElementChild;
  let status;
  try {
    status = await api(BASE);
  } catch (cause) {
    if (dialog.firstElementChild !== opened) return;
    showDialog(`${dialogHeader("Google Calendar", "")}<div class="dialog-body">${calloutMarkup({ tone: "danger", title: "The sync could not be read", body: errorMessage(cause) })}</div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Close</button></footer>`);
    return;
  }
  if (dialog.firstElementChild !== opened || !dialog.open) return;
  if (!status.permission) connectView(status);
  else settingsView(status, syncNow && status.settings.enabled && !status.sync.round_at);

  // What the sync does, in one closed disclosure (the lead stays one sentence).
  /** @param {{from: string, to: string} | undefined} span the sync window */
  function about(span) {
    return disclosureMarkup({ label: "What Faculty does in your Google calendar", className: "calendar-google-about", body: `<ul>
      <li>Faculty adds its events to your main calendar the way the Calendar page groups them: one event per exam and time (its rooms listed inside), one per exam's DR bookings at one time, one per deadline and per event of your own. Nobody is invited and Google sends no email.</li>
      <li>It only ever changes or deletes events it added. When one changes in Faculty, Faculty writes it again. A change you make to it in Google stays until then, and one you delete in Google comes back only if it changes in Faculty.</li>
      <li>Your Google events show here read-only, in campus time. Open one to change it in Google. Declined invitations and working-location markers are left out.</li>
      <li>It syncs about every two minutes${span ? `, from ${e(wall(span.from))} to ${e(wall(span.to))}` : ""}. Faculty's events outside those dates stay as they are in Google.</li>
      <li>Turning the sync off stops it and removes your Google events from Faculty. Faculty's events stay in Google until you choose Remove Faculty's events.</li></ul>` });
  }

  /** @param {any} current */
  function connectView(current) {
    const who = current.account ? `Faculty connects as <strong>${e(current.account)}</strong>, the account Response Card email is sent from. Google asks you to allow calendar access.`
      : "Google asks you to sign in and allow calendar access.";
    const body = !current.configured
      ? calloutMarkup({ tone: "warning", title: "Google is not set up on this server", body: "Faculty's Google client file is missing, so it cannot connect to Google. It is the same set-up Gmail sending in Response Cards uses." })
      : `<p class="section-lead">Put Faculty's exams, seating charts, DR bookings and deadlines in your main Google calendar, and show your Google events here.</p><p class="supporting-copy">${who}</p>${about(current.window)}`;
    showDialog(`<form id="google-calendar-connect">${dialogHeader("Google Calendar", "Your Google calendar and Faculty's, both ways")}<div class="dialog-body">${body}<p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button>${current.configured ? '<button type="submit" class="button primary">Connect Google Calendar</button>' : ""}</footer></form>`);
    const form = /** @type {HTMLFormElement} */ (document.getElementById("google-calendar-connect"));
    const requestId = crypto.randomUUID();
    form.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(form, async () => {
        const result = await api(`${BASE}/connect`, "POST", { request_id: requestId });
        window.location.assign(result.url);  // Google's sign-in, which returns to #calendar?google=1
      });
    };
  }

  /** @param {any} current @param {boolean} [first] run a sync as soon as the dialog shows */
  function settingsView(current, first = false) {
    const { settings, sync, counts } = current;
    const facts = [counts.in_google ? `${counts.in_google.toLocaleString()} Faculty event${counts.in_google === 1 ? "" : "s"} in Google` : "",
      settings.enabled ? `${counts.shown.toLocaleString()} Google event${counts.shown === 1 ? "" : "s"} shown here` : ""].filter(Boolean).join(" · ");
    const lead = !settings.enabled ? "The sync is off." : sync.read_at ? `Synced ${ago(sync.read_at)}.` : "The first sync starts within two minutes.";
    // The one problem the instructor fixes outside Faculty (the Calendar API switched off in Google Cloud) gets its link as the action.
    const enableAt = /https:\/\/console\.cloud\.google\.com\/apis\/library\/calendar-json\.googleapis\.com(\?project=[A-Za-z0-9-]+)?/.exec(sync.error || "")?.[0];
    const problem = sync.error ? calloutMarkup({ tone: "danger", title: "The last sync stopped", body: `${sync.error}${sync.paused_until ? " Faculty tries again on its own; Sync now tries at once." : ""}`,
      action: enableAt ? { label: "Turn on the Calendar API", href: enableAt } : null }) : "";
    const refused = current.refused?.length ? calloutMarkup({ tone: "warning", title: `Google refused ${current.refused.length} event${current.refused.length === 1 ? "" : "s"}`,
      body: current.refused.map((/** @type {any} */ item) => `${item.summary} (${wall(item.date)}): ${item.error}`).join(" · ") + " Faculty tries each again when it changes." }) : "";
    const missing = sync.last?.missing_calendars?.length ? calloutMarkup({ tone: "warning", title: "A chosen calendar is no longer available", body: "Untick it below, or check that this account can still see it." }) : "";
    const types = current.types.map((/** @type {any} */ type) => `<label class="checkbox-field"><input type="checkbox" name="send" value="${e(type.key)}" ${settings.send.includes(type.key) ? "checked" : ""}>${e(type.label)}</label>`).join("");
    showDialog(`<form id="google-calendar-settings">${dialogHeader("Google Calendar", e(current.account || ""))}<div class="dialog-body calendar-google">
      <p class="section-lead">${e(lead)}${facts ? ` <span class="supporting-copy">${e(facts)}</span>` : ""}</p>${problem}${refused}${missing}
      <label class="checkbox-field"><input type="checkbox" name="enabled" ${settings.enabled ? "checked" : ""}>Sync with Google Calendar</label>
      <fieldset class="choice-group" data-google-send><legend>Send to your main calendar</legend>${types}</fieldset>
      <fieldset class="choice-group" data-google-calendars><legend>Show here from Google</legend><p class="loading-state">Reading your calendars…</p></fieldset>
      ${about(current.window)}<p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="text-button danger-button" data-google-remove ${counts.in_google ? "" : "disabled"}>Remove Faculty's events…</button><button type="button" class="button" data-google-sync>Sync now</button><button type="button" class="button" data-action="close">Close</button><button type="submit" class="button primary">Save</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (document.getElementById("google-calendar-settings"));
    form.querySelectorAll('.callout a[href^="https://console.cloud.google.com/"]').forEach((link) => { link.setAttribute("target", "_blank"); link.setAttribute("rel", "noopener noreferrer"); });
    const current$ = dialogSession(form, dialog);
    const fields = /** @type {any} */ (form.elements);
    const listed = /** @type {HTMLElement} */ (form.querySelector("[data-google-calendars]"));
    let calendarsRead = false;
    const choices = () => [...listed.querySelectorAll('input[name="calendar"]')].filter((input) => /** @type {HTMLInputElement} */ (input).checked).map((input) => /** @type {HTMLInputElement} */ (input).value);
    void (async () => {
      try {
        const { calendars } = await api(`${BASE}/calendars`);
        if (!current$()) return;
        const chosen = new Set(settings.calendars);
        listed.innerHTML = `<legend>Show here from Google</legend>${calendars.map((/** @type {any} */ calendar) => {
          const value = calendar.primary ? "primary" : calendar.id;
          return `<label class="checkbox-field"><input type="checkbox" name="calendar" value="${e(value)}" ${chosen.has(value) || (calendar.primary && chosen.has(calendar.id)) ? "checked" : ""}>${e(calendar.name)}${calendar.primary ? " · main calendar" : ""}</label>`;
        }).join("")}`;
        calendarsRead = true;
      } catch (cause) {
        if (current$()) listed.innerHTML = `<legend>Show here from Google</legend><p class="supporting-copy">Your calendars could not be read (${e(errorMessage(cause))}). The ones already chosen stay chosen.</p>`;
      }
    })();
    form.onsubmit = (event) => {
      event.preventDefault();
      const send = [...form.querySelectorAll('input[name="send"]')].filter((input) => /** @type {HTMLInputElement} */ (input).checked).map((input) => /** @type {HTMLInputElement} */ (input).value);
      const payload = { base_revision: settings.revision, request_id: crypto.randomUUID(), enabled: fields.enabled.checked, send,
        ...(calendarsRead ? { calendars: choices() } : {}) };
      void runFormAction(form, async () => {
        const saved = await api(`${BASE}/settings`, "POST", payload);
        if (!current$()) return;
        toast(saved.settings.enabled ? "Google Calendar settings saved." : "Google Calendar sync turned off.");
        changed();
        // New choices take effect at once: the sync that follows sends, removes and reads what they now say.
        if (saved.settings.enabled && saved.settings.revision !== settings.revision) await syncOnce(false);
        else settingsView(saved);
      });
    };
    /** @param {boolean} announce */
    async function syncOnce(announce) {
      const result = await api(`${BASE}/sync`, "POST", {});
      if (!current$()) return;
      if (result.busy) toast("A sync is running now; it finishes in a moment.");
      else if (announce) toast(result.sync.error ? "The sync stopped. Its reason is shown." : "Synced with Google Calendar.");
      changed();
      settingsView(result);
    }
    const runSync = (announce = true) => runFormAction(form, () => syncOnce(announce));
    /** @type {HTMLButtonElement} */ (form.querySelector("[data-google-sync]")).onclick = () => void runSync();
    if (first) void runSync(true);
    /** @type {HTMLButtonElement} */ (form.querySelector("[data-google-remove]")).onclick = () => confirmRemove(current);
  }

  /** @param {any} current */
  function confirmRemove(current) {
    const count = current.counts.in_google;
    showDialog(`<form id="google-calendar-remove">${dialogHeader("Remove Faculty's events from Google?", e(current.account || ""))}<div class="dialog-body"><p>This deletes the ${count.toLocaleString()} event${count === 1 ? "" : "s"} Faculty added to your main Google calendar. Your own events are not touched.</p><p class="supporting-copy">Faculty then sends nothing to Google until you tick event types again. Your Google events keep showing here while the sync is on.</p><p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-google-back>Back</button><button type="submit" class="button danger">Remove ${count.toLocaleString()} event${count === 1 ? "" : "s"}</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (document.getElementById("google-calendar-remove"));
    const current$ = dialogSession(form, dialog);
    const requestId = crypto.randomUUID();
    /** @type {HTMLButtonElement} */ (form.querySelector("[data-google-back]")).onclick = () => settingsView(current);
    form.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(form, async () => {
        const result = await api(`${BASE}/remove`, "POST", { request_id: requestId, confirmation: "remove" });
        if (!current$()) return;
        toast(result.busy ? "Faculty's events will be removed with the sync that is running now." : result.counts.in_google
          ? `${result.counts.in_google.toLocaleString()} events are still in Google; the next sync removes them.` : "Faculty's events were removed from Google Calendar.");
        changed();
        settingsView(result);
      });
    };
  }
}
