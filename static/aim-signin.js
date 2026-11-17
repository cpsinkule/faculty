/* Import from AIM (2026-09-26; aim_import.py). With a saved sign-in on the server (the saved Gradescope sign-in, which is
   the instructor's NetID sign-in too) it starts at once and goes straight to Duo; otherwise the instructor types their
   NetID and password into Faculty's own form;
   a private browser on the server signs in with them, shows Duo's verification code here, continues past AIM's FERPA
   access-policy page and exports. A step it does not recognize opens the live page (Chrome's screencast) under "Show the
   page", where the instructor clicks and types; the driver takes over again after it. The password leaves this page
   once, in the request that starts the sign-in, and is not kept here.
   Capture AIM's form (2026-10-15, AIM FORM CAPTURE) is the same window: it signs in the same way, never exports, and once in
   AIM shows the page; Record this form keeps the structure of the form open there (never what is in it). */

import { CAMPUS_ZONE } from "./calendar-feed.js";
import { captureDialogIntent, dialogSession } from "./ui.js";

const POLL = 700;            // ms between status reads while the dialog is open and visible
const HIDDEN_POLL = 3000;    // while the tab is hidden (the server keeps the browser IDLE minutes after the last read)
const NETID_KEY = "faculty.aim.netid"; // the NetID only (never the password), a per-browser convenience
const SPECIAL = new Set(["Enter", "Tab", "Backspace", "Delete", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"]);
const STEPS = {
  starting: "Starting a private browser on your server…", working: "Signing in…", netid: "Signing in to TAMU…",
  password: "Signing in to TAMU…", stay_signed_in: "Signing in…", duo_choose: "Working with Duo…", duo_trust: "Finishing Duo…", duo_redirect: "Going on to Duo…",
  policy: "Continuing past AIM’s access policy…", aim: "Signed in to AIM. Exporting…", canvas: "Signed in to Canvas. Keeping its session…", unknown: "Signing in…",
  google_email: "Signing in to Google…", google: "Signed in to Google. Keeping its session…",
  howdy: "Signed in to Howdy. Reading rosters…", guest: "Opening Howdy’s sign-in…", checking: "Howdy is checking the browser…",
};

/* The same window signs in to Canvas (2026-09-27; canvas/signin.py): its route, words and end differ. AIM's ends with the two
   exports; Canvas's with state "done", once the server kept Canvas's session and discarded the browser. */
const SITES = {
  aim: {
    route: "/api/accommodations/aim", title: "Import from AIM", home: "AIM home", submit: "Sign in and import", again: "choose Import from AIM again",
    lead: "Sign in with your NetID; Faculty exports both files and discards its browser afterwards.", ferpa: true,
    hint: "Your password goes to TAMU’s sign-in page through Faculty’s server and is not kept. Faculty continues past AIM’s FERPA access-policy page for you: signing in here is your acknowledgment of that policy.",
  },
  canvas: {
    route: "/api/canvas/signin", title: "Sign in to Canvas", home: "Canvas home", submit: "Sign in", again: "choose Sign in to Canvas again",
    lead: "Faculty signs in with your NetID and keeps Canvas’s session, so Duo is asked only now and then.", ferpa: false,
    hint: "Your password goes to TAMU’s sign-in page through Faculty’s server and is not kept. Faculty keeps only Canvas’s session, in a private file on the server.",
  },
};
// GOOGLE SIGN-IN (ABSENCES "DEPARTMENT MAKEUP EXAMS", 2026-10-22; absences/google_signin.py): the department's makeup exam form
// takes a response only from a signed-in TAMU Google account, so the same window signs in to Google and Faculty keeps that session.
SITES.google = {
  route: "/api/absences/department-makeups/google/signin", title: "Sign in to Google", home: "The makeup form", submit: "Sign in",
  again: "choose Sign in to Google again", ferpa: false,
  lead: "Faculty signs in to your TAMU Google account with your NetID and keeps Google’s session, so it can send department makeup requests.",
  hint: "Your password goes to TAMU’s sign-in page through Faculty’s server and is not kept. Faculty keeps only Google’s session for the form, in a private file on the server.",
};
// IMPORT FROM HOWDY (CANVAS, 2026-11-01; howdy_import.py): the same window signs in to Howdy and reads each section's class roster;
// `start` names the course page whose sections it reads, and the rosters come back as `files`, one CSV per section number.
SITES.howdy = {
  route: "/api/howdy", title: "Import from Howdy", home: "Howdy sign-in", submit: "Sign in and import", again: "choose Import from Howdy again",
  ferpa: false, exporting: "Reading rosters from Howdy…", unexported: "Signed in, but Howdy did not give the rosters.", retry: "Read the rosters again",
  lead: "Faculty signs in to Howdy with your NetID, reads each section’s class roster, and discards its browser afterwards.",
  hint: "Your password goes to TAMU’s sign-in page through Faculty’s server and is not kept. Faculty only reads from Howdy.",
};
// The form capture window: AIM's sign-in, its words and FERPA note, ending in the live page and Record this form.
SITES.capture = {
  ...SITES.aim, title: "Capture AIM's form", submit: "Sign in", again: "choose Capture AIM's form again", capture: true,
  lead: "Sign in, open AIM's prescheduling form in the page, then choose Record this form. Faculty keeps the form's fields and buttons only, never what is in them.",
};

// SEND TO AIM (SCHEDULING, 2026-10-27; scheduling/aim_send.py): AIM's sign-in started from the DR quizzes tab's Send to AIM, which
// adds the ready quiz dates, uploads their accessible copies, then exports the DR bookings for the ordinary import review.
SITES.send = {
  ...SITES.aim, title: "Send to AIM", submit: "Sign in and send", again: "choose Send to AIM again",
  lead: "Faculty adds each ready DR quiz date to AIM, uploads its accessible copy to the students' requests, then exports the DR bookings for you to import.",
  hint: "Your password goes to TAMU’s sign-in page through Faculty’s server and is not kept. Faculty continues past AIM’s FERPA access-policy page for you: signing in here is your acknowledgment of that policy.",
};

// DR REQUEST EMAILS (SCHEDULING, 2026-11-01): a Follow-up card's upload of one DR quiz's accessible copy. Its Approve started the
// send on the server with the saved sign-in; this window follows that session (`attach`) so Duo's code shows here.
SITES.upload = {
  ...SITES.send, title: "Upload to AIM", again: "approve the upload again from its Follow-up card",
  lead: "Faculty signs in to AIM, uploads the quiz's accessible copy to the students' requests, then imports the DR bookings.",
};

const storedNetid = () => { try { return localStorage.getItem(NETID_KEY) || ""; } catch { return ""; } };
const storeNetid = (value) => { try { localStorage.setItem(NETID_KEY, value); } catch { /* private window: typed next time */ } };

/**
 * @param {{api: Function, showDialog: (markup: string) => void, dialogHeader: (title: string, subtitle?: string) => string,
 *   escape: (value: unknown) => string, onExported: (result: {term: string, files: Record<string, File>, problems: string[]}) => void}} options
 */
export function openAimSignIn(options) {
  return openSignIn({ ...options, site: "aim" });
}

/**
 * Send to AIM: `start` is {route, body}, the term's Send route and its request ID and fingerprint; `onExported` gets the bookings
 * export as Import from AIM's does, with `sent`, what Faculty did in AIM.
 */
export function openAimSend(options) {
  return openSignIn({ ...options, site: "send" });
}

/**
 * Import from Howdy: `start` is {route, body}, the course page's Howdy route; `onExported` gets each section's roster in `files`,
 * keyed by section number.
 */
export function openHowdyRosters(options) {
  return openSignIn({ ...options, site: "howdy" });
}

/**
 * The upload a Follow-up card's Approve started (`attach`: the session status its route answered): the same window, following it.
 */
export function openAimUpload(options) {
  return openSignIn({ ...options, site: "upload" });
}

/**
 * Capture AIM's form: the AIM sign-in, then the live page and Record this form; it lists the forms recorded so far.
 * @param {{api: Function, showDialog: (markup: string) => void, dialogHeader: (title: string, subtitle?: string) => string, escape: (value: unknown) => string}} options
 */
export function openAimCapture(options) {
  return openSignIn({ ...options, site: "capture" });
}

/**
 * Canvas's sign-in: resolves with the signed-in user's name once Faculty keeps the session, or null when the window closes first.
 * @param {{api: Function, showDialog: (markup: string) => void, dialogHeader: (title: string, subtitle?: string) => string, escape: (value: unknown) => string}} options
 * @returns {Promise<string | null>}
 */
export function openCanvasSignIn(options) {
  return new Promise((resolve) => {
    let done = false;
    // showDialog runs before openSignIn's first await, so the dialog is open here.
    void openSignIn({ ...options, site: "canvas", onDone: (status) => { done = true; resolve(String(status.user || "")); }, onClosed: () => resolve(null) });
    document.querySelector("#dialog")?.addEventListener("close", () => { if (!done) resolve(null); }, { once: true });
  });
}

/**
 * Google's sign-in for the department's makeup form: resolves with the signed-in account once Faculty keeps the session, or null
 * when the window closes first.
 * @param {{api: Function, showDialog: (markup: string) => void, dialogHeader: (title: string, subtitle?: string) => string, escape: (value: unknown) => string}} options
 * @returns {Promise<string | null>}
 */
export function openGoogleSignIn(options) {
  return new Promise((resolve) => {
    let done = false;
    void openSignIn({ ...options, site: "google", onDone: (status) => { done = true; resolve(String(status.user || "")); }, onClosed: () => resolve(null) });
    document.querySelector("#dialog")?.addEventListener("close", () => { if (!done) resolve(null); }, { once: true });
  });
}

/** The endpoint of a department makeup request card (ABSENCES "DEPARTMENT MAKEUP EXAMS"). */
export const DEPARTMENT_REQUEST = "absences.department_makeup";

/**
 * SIGN IN WHEN NEEDED (ABSENCES "DEPARTMENT MAKEUP EXAMS", requirement, 2026-10-23): before a department makeup request is sent, Google is asked whether Faculty's kept sign-in still opens the form signed
 * in. If not, the Google sign-in window opens by itself (the saved NetID sign-in, then Duo's push or code) and resolves true once
 * Faculty keeps the new session; false when the window is closed first. A failed check lets the send go on (the server refuses it
 * without the sign-in, sending nothing).
 * @param {{api: Function, showDialog: (markup: string) => void, dialogHeader: (title: string, subtitle?: string) => string, escape: (value: unknown) => string, toast?: Function}} options
 * @returns {Promise<boolean>}
 */
export async function ensureGoogleSignIn(options) {
  const intended = captureDialogIntent(document.querySelector("#dialog"));
  let status = null;
  try { status = await options.api("/api/absences/department-makeups/google?check=1"); } catch { return true; }
  if (!intended()) return false;
  if (!status?.configured || status.signed_in) return true;
  options.toast?.("Faculty's Google sign-in has ended. Signing in again first, then sending.");
  return Boolean(await openGoogleSignIn(options));
}

async function openSignIn({ api, showDialog, dialogHeader, escape, onExported = () => {}, onDone = () => {}, onClosed = () => {}, site: key, start = null, attach = null }) {
  const site = SITES[key];
  showDialog(`<form class="aim-signin" data-aim-signin novalidate>${dialogHeader(site.title, site.lead)}
    <div class="dialog-body">
      <div class="aim-signin-credentials" data-aim-credentials hidden>
        <label class="field">NetID<input name="netid" autocomplete="username" autocapitalize="off" spellcheck="false" required value="${escape(storedNetid())}"></label>
        <label class="field">Password<input name="password" type="password" autocomplete="current-password" required></label>
        <p class="field-hint">${site.hint}</p>
      </div>
      <div class="aim-signin-progress" data-aim-progress hidden>
        <p class="field-hint" data-aim-who hidden></p>
        <p class="aim-signin-step" data-aim-step role="status" aria-live="polite"></p>
        <div class="aim-signin-code" data-aim-code hidden><span>Enter this code in Duo Mobile</span><strong data-aim-digits></strong></div>
        <div class="aim-signin-duo" data-aim-duo hidden>
          <button type="button" class="button" data-aim-resend>Send the push again</button>
          <details class="disclosure aim-signin-passcode" data-aim-passcode-box><summary>Use a Duo passcode instead</summary>
            <div class="aim-signin-passcode-row"><label class="field">Passcode<input data-aim-passcode inputmode="numeric" autocomplete="one-time-code" maxlength="10"></label>
            <button type="button" class="button" data-aim-use-passcode>Use passcode</button></div></details>
        </div>
        <p class="field-hint" data-aim-ferpa hidden>Faculty continues past AIM’s FERPA access-policy page for you: ${site.capture ? "signing in here" : key === "send" || key === "upload" ? "starting this send" : "starting this import"} is your acknowledgment of that policy.</p>
      </div>
      <p class="form-error" role="alert" hidden></p>
      <details class="aim-signin-page" data-aim-page>
        <summary>Show the page</summary>
        <div class="aim-signin-bar"><span class="aim-signin-place" data-aim-place></span>
          <span class="aim-signin-nav"><button type="button" class="text-button" data-aim-nav="back">Back</button><button type="button" class="text-button" data-aim-nav="home">${site.home}</button></span></div>
        <div class="aim-signin-view" data-aim-view>
          <canvas class="aim-signin-frame" width="1100" height="760" aria-hidden="true" data-aim-frame hidden></canvas>
          <textarea class="aim-signin-keys" data-aim-keys aria-label="The sign-in page: click to point, then type" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"></textarea>
        </div>
        <p class="aim-signin-hint">Click in the page, then type; paste works too. Faculty carries on by itself after a step it recognizes.</p>
      </details>
      ${site.capture ? `<section class="aim-signin-captures"><h3>Recorded forms</h3><p class="field-hint" data-aim-recorded role="status" aria-live="polite" hidden></p>
        <ul data-aim-captures><li class="field-hint">Loading…</li></ul></section>` : ""}
    </div>
    <footer class="dialog-footer"><button type="button" class="text-button" data-aim-on-page>Sign in on the page instead</button><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary" hidden>${site.submit}</button>${site.capture ? '<button type="button" class="button primary" data-aim-record hidden>Record this form</button>' : ""}</footer></form>`);
  const dialog = /** @type {HTMLDialogElement} */ (document.querySelector("#dialog"));
  const form = /** @type {HTMLFormElement} */ (dialog.querySelector("[data-aim-signin]"));
  const alive = dialogSession(form, dialog);
  const $ = (selector) => /** @type {HTMLElement} */ (form.querySelector(selector));
  const netidInput = /** @type {HTMLInputElement} */ ($('[name="netid"]'));
  const passwordInput = /** @type {HTMLInputElement} */ ($('[name="password"]'));
  const credentialsBox = $("[data-aim-credentials]"), progress = $("[data-aim-progress]"), stepLine = $("[data-aim-step]");
  const codeBox = $("[data-aim-code]"), digits = $("[data-aim-digits]"), duoBox = $("[data-aim-duo]");
  const passcodeBox = /** @type {HTMLDetailsElement} */ ($("[data-aim-passcode-box]"));
  const passcodeInput = /** @type {HTMLInputElement} */ ($("[data-aim-passcode]"));
  const error = $('[role="alert"]'), pageBox = /** @type {HTMLDetailsElement} */ ($("[data-aim-page]"));
  const view = $("[data-aim-view]"), keys = /** @type {HTMLTextAreaElement} */ ($("[data-aim-keys]")), place = $("[data-aim-place]");
  const canvas = /** @type {HTMLCanvasElement} */ ($("[data-aim-frame]"));
  const paint = /** @type {CanvasRenderingContext2D} */ (canvas.getContext("2d"));
  const submit = /** @type {HTMLButtonElement} */ ($('[type="submit"]')), onPage = /** @type {HTMLButtonElement} */ ($("[data-aim-on-page]"));
  let base = "", width = 1100, height = 760, shown = 0, timer = 0, ended = false, exported = false, fetching = false, drawing = false;
  let closedBase = "";
  let asking = ""; // "netid", "password" or "export": what the Try again button does now
  let saved = false; // signing in with the sign-in saved on the server
  const record = /** @type {HTMLButtonElement | null} */ (form.querySelector("[data-aim-record]"));
  const recorded = form.querySelector("[data-aim-recorded]"), captures = form.querySelector("[data-aim-captures]");

  const say = (text) => { error.textContent = text; error.hidden = !text; };
  const stop = (text) => {
    ended = true; clearTimeout(timer); say(text);
    submit.disabled = true; onPage.hidden = true; keys.disabled = true; duoBox.hidden = true;
    if (record) record.hidden = true;
  };
  const close = () => {
    clearTimeout(timer);
    passwordInput.value = "";
    if (base && !exported && closedBase !== base) {
      closedBase = base;
      void api(`${base}/close`, "POST", {}).catch(() => {});
    }
    ended = true;
    onClosed();
  };
  dialog.addEventListener("close", close, { once: true });

  // --- the live page: a fallback, and there for anyone who wants to watch ---

  /** @type {Array<Record<string, unknown>>} */
  let pending = [];
  let flushing = Promise.resolve(), flushTimer = 0;
  const send = (event) => {
    if (!base || ended) return;
    const last = pending[pending.length - 1];
    if (event.type === "text" && last?.type === "text" && String(last.text).length + String(event.text).length <= 2000) last.text += event.text;
    else pending.push(event);
    clearTimeout(flushTimer);
    flushTimer = window.setTimeout(flush, 8);
  };
  const flush = () => {
    const events = pending.splice(0, 50);
    if (!events.length) return;
    flushing = flushing.then(() => { if (!ended && alive()) return api(`${base}/input`, "POST", { events }); }).catch((cause) => { if (cause?.status === 410) stop(cause.message); });
    if (pending.length) flushTimer = window.setTimeout(flush, 0);
  };
  const point = (event) => {
    const box = keys.getBoundingClientRect();
    const x = Math.round((event.clientX - box.left) * width / box.width), y = Math.round((event.clientY - box.top) * height / box.height);
    return { x: Math.min(width - 1, Math.max(0, x)), y: Math.min(height - 1, Math.max(0, y)) };
  };
  keys.addEventListener("click", (event) => { send({ type: "click", ...point(event) }); });
  let wheel = { dx: 0, dy: 0 }, wheelTimer = 0;
  keys.addEventListener("wheel", (event) => {
    event.preventDefault();
    wheel.dx += event.deltaX; wheel.dy += event.deltaY;
    const at = point(event);
    clearTimeout(wheelTimer);
    wheelTimer = window.setTimeout(() => { const { dx, dy } = wheel; wheel = { dx: 0, dy: 0 }; send({ type: "wheel", ...at, dx: Math.round(dx), dy: Math.round(dy) }); }, 60);
  }, { passive: false });
  keys.addEventListener("keydown", (event) => {
    if (event.isComposing) return;
    if ((event.metaKey || event.ctrlKey) && !event.altKey) {
      const key = event.key.toLowerCase();
      if (key === "v") return; // the paste event carries the text
      if (["a", "c", "x", "z"].includes(key)) { event.preventDefault(); send({ type: "key", key: `Control+${key}` }); }
      return;
    }
    if (SPECIAL.has(event.key)) {
      event.preventDefault();
      send({ type: "key", key: event.key === "Tab" && event.shiftKey ? "Shift+Tab" : event.key });
    }
  });
  const typed = () => { const text = keys.value.replace(/[\r\n]+/g, ""); keys.value = ""; if (text) send({ type: "text", text }); };
  keys.addEventListener("input", (event) => { if (!(/** @type {InputEvent} */ (event)).isComposing) typed(); });
  keys.addEventListener("compositionend", typed);
  keys.addEventListener("paste", (event) => {
    event.preventDefault();
    const text = (event.clipboardData?.getData("text") || "").replace(/[\r\n]+/g, "").slice(0, 2000);
    if (text) send({ type: "text", text });
  });
  form.querySelectorAll("[data-aim-nav]").forEach((button) => {
    /** @type {HTMLButtonElement} */ (button).onclick = () => { send({ type: /** @type {HTMLElement} */ (button).dataset.aimNav }); keys.focus(); };
  });

  /** Pictures while the page is shown: each request waits on the server for a newer one, then the next goes at once. */
  async function pictures() {
    if (drawing) return;
    drawing = true;
    while (!ended && alive() && pageBox.open) {
      try {
        const response = await fetch(`${base}/frame?after=${shown}`, { cache: "no-store" });
        if (response.status === 410) { stop(`This sign-in has ended. Close it and ${site.again}.`); break; }
        if (response.status === 200) {
          const bitmap = await createImageBitmap(await response.blob());
          if (ended || !alive()) { bitmap.close(); break; }
          paint.drawImage(bitmap, 0, 0, width, height);
          bitmap.close();
          canvas.hidden = false;
          shown = Number(response.headers.get("X-Aim-Frame")) || shown + 1;
        } else if (!response.ok && response.status !== 204) {
          await new Promise((resolve) => setTimeout(resolve, 1000));
        }
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      if (document.hidden) await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    drawing = false;
  }
  pageBox.addEventListener("toggle", () => {
    if (!pageBox.open || !base) return;
    void api(`${base}/show`, "POST", {}).catch(() => {});
    void pictures();
    keys.focus();
  });

  // --- the sign-in itself ---

  const ask = (what, label) => {
    asking = what;
    submit.hidden = false; submit.disabled = false; submit.textContent = label;
  };

  function show(status) {
    if (!alive()) { close(); return; }
    place.textContent = status.place || "";
    const code = status.step === "duo_code" && status.code ? status.code : "";
    codeBox.hidden = !code; digits.textContent = code;
    const duo = String(status.step || "").startsWith("duo_") && !["duo_choose", "duo_trust", "duo_redirect"].includes(status.step);
    duoBox.hidden = !duo || status.state !== "signing_in";
    stepLine.textContent = status.step === "duo_code" ? "Open Duo Mobile and enter the code below." :
      status.step === "duo_push" ? "Approve the Duo push on your phone." :
      status.step === "duo_problem" ? "Duo did not get an answer." :
      status.step === "duo_passcode" ? "Enter a Duo passcode below." :
      status.send && status.state === "exporting" ? status.place || "Sending to AIM…" :
      status.state === "exporting" ? site.exporting || "Exporting from AIM…" :
      status.needs === "export" ? site.unexported || "Signed in, but AIM did not give its exports." :
      status.capture && status.step === "aim" ? "Signed in to AIM. Open the form in the page below, then choose Record this form." :
      STEPS[status.step] || "Signing in…";
    if (record) record.hidden = !(status.capture && status.step === "aim" && status.state === "signed_in");
    if (status.step === "duo_passcode" && !passcodeBox.open) passcodeBox.open = true;
    if (status.show_page && !pageBox.open) pageBox.open = true;
    if (["netid", "password"].includes(status.needs) && asking !== status.needs) {
      credentialsBox.hidden = false;
      netidInput.disabled = status.needs !== "netid";
      ask(status.needs, "Try again");
      (status.needs === "netid" ? netidInput : passwordInput).focus();
    } else if (status.needs === "export" && asking !== "export") {
      ask("export", site.retry || "Try the export again");
    }
    const again = saved && ["netid", "password"].includes(status.needs)
      ? " The saved sign-in (your Gradescope one) was not accepted: type your NetID password here, and save the new one with save-gradescope.py." : "";
    say(["netid", "password", "export", "passcode", "duo"].includes(status.needs) || status.state === "failed" ? `${status.message || ""}${again}` : "");
    if (status.state === "exported") void collect(status);
    else if (status.state === "done" && !exported) {
      exported = true; clearTimeout(timer);
      void api(`${base}/close`, "POST", {}).catch(() => {});
      dialog.close();  // first, as an export closes before onExported: onDone may open the next dialog
      onDone(status);
    }
    else if (status.state === "failed") stop(status.message || "The private browser on the server stopped.");
    else if (status.state === "closed") stop(`This sign-in has ended (it closes after 10 minutes without this window). Close it and ${site.again}.`);
  }

  async function poll() {
    if (ended || !alive()) { close(); return; }
    try {
      show(await api(base));
    } catch (cause) {
      if (cause?.status === 410) { stop(cause.message); return; }
    }
    if (!ended && !exported) timer = window.setTimeout(poll, document.hidden ? HIDDEN_POLL : POLL);
  }

  async function collect(status) {
    if (fetching || exported) return;
    fetching = true;
    try {
      /** @type {Record<string, File>} */
      const files = {};
      for (const item of status.files || []) {
        const response = await fetch(`${base}/files/${encodeURIComponent(item.kind)}`, { cache: "no-store" });
        if (!response.ok) throw new Error("The export could not be read from the server. Import from AIM again.");
        files[item.kind] = new File([await response.blob()], item.name, { type: response.headers.get("Content-Type") || "" });
        if (ended || !alive()) { close(); return; }
      }
      exported = true;
      clearTimeout(timer);
      void api(`${base}/close`, "POST", {}).catch(() => {});
      dialog.close();
      onExported({ term: status.term || "", files, problems: (status.problems || []).map((item) => String(item.message || "")).filter(Boolean),
        sent: status.sent || [] });
    } catch (cause) {
      stop(cause?.message || String(cause));
    } finally {
      fetching = false;
    }
  }

  const begin = async (body) => {
    follow(start ? await api(start.route, "POST", { ...body, ...start.body })
      : await api(site.route, "POST", site.capture ? { ...body, capture: true } : body));
  };
  /** @param {any} started */
  function follow(started) {
    base = `${site.route}/${encodeURIComponent(started.id)}`;
    if (ended || !alive()) { close(); return; }
    width = started.width || width; height = started.height || height;
    canvas.width = width; canvas.height = height;
    view.style.setProperty("--aim-ratio", `${width} / ${height}`);
    view.style.setProperty("--aim-scale", String(width / height));
    credentialsBox.hidden = true; progress.hidden = false; submit.hidden = true; onPage.hidden = true;
    show(started);
    void poll();
  }

  form.onsubmit = async (event) => {
    event.preventDefault();
    if (ended || submit.disabled) return;
    say("");
    submit.disabled = true; submit.setAttribute("aria-busy", "true");
    try {
      if (asking === "export") {
        asking = ""; submit.hidden = true;
        stepLine.textContent = site.exporting || "Exporting from AIM…";
        await api(`${base}/export`, "POST", { kinds: ["accommodations", "exams"] }, {}, 180000);
        return;
      }
      const netid = netidInput.value.trim(), password = passwordInput.value;
      if ((!base || asking === "netid") && !netid) { say("Enter your NetID."); netidInput.focus(); return; }
      if (!password) { say("Enter your password."); passwordInput.focus(); return; }
      if (netid) storeNetid(netid);
      passwordInput.value = "";
      if (!base) await begin({ netid, password });
      else {
        await api(`${base}/credentials`, "POST", asking === "netid" ? { netid, password } : { password });
        asking = ""; credentialsBox.hidden = true; submit.hidden = true;
        stepLine.textContent = "Signing in to TAMU…";
      }
    } catch (cause) {
      say(cause?.message || String(cause));
      if (cause?.status === 410) stop(cause.message);
    } finally {
      submit.disabled = ended; submit.removeAttribute("aria-busy");
    }
  };

  onPage.onclick = async () => {
    onPage.disabled = true; onPage.setAttribute("aria-busy", "true");
    try {
      await begin({});
      pageBox.open = true;
    } catch (cause) {
      say(cause?.message || String(cause));
      onPage.disabled = false;
    } finally {
      onPage.removeAttribute("aria-busy");
    }
  };

  /** @type {HTMLButtonElement} */ ($("[data-aim-resend]")).onclick = () => {
    if (!base || ended) return;
    say("");
    stepLine.textContent = "Sending the push again…";
    void api(`${base}/answer`, "POST", { action: "resend" }).catch((cause) => say(cause.message));
  };
  /** @type {HTMLButtonElement} */ ($("[data-aim-use-passcode]")).onclick = () => {
    const code = passcodeInput.value.replace(/\s+/g, "");
    if (!/^\d{6,10}$/.test(code)) { say("A Duo passcode is 6 to 10 digits."); return; }
    passcodeInput.value = "";
    say("");
    stepLine.textContent = "Using the passcode…";
    void api(`${base}/answer`, "POST", { action: "passcode", code }).catch((cause) => say(cause.message));
  };

  // --- the form capture: the forms recorded so far, and Record this form ---

  const when = (iso) => new Date(iso).toLocaleString("en-US", { timeZone: CAMPUS_ZONE, month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
  async function listCaptures() {
    if (!captures) return;
    try {
      const found = (await api("/api/accommodations/aim/captures")).captures || [];
      captures.innerHTML = found.length ? found.map((item) => `<li><span>${escape(when(item.received_at))}</span> <span class="aim-signin-place">${escape(item.page)}</span>
        <span>${escape(item.controls)} fields, ${escape(item.buttons)} buttons, ${escape(item.dropped)} left out</span> <a href="${escape(item.download)}" download="${escape(item.name)}">Download</a></li>`).join("")
        : '<li class="field-hint">No form recorded yet.</li>';
    } catch (cause) {
      captures.innerHTML = `<li class="form-error">${escape(cause?.message || String(cause))}</li>`;
    }
  }
  if (record && recorded) {
    record.onclick = async () => {
      if (!base || ended) return;
      say("");
      record.disabled = true; record.setAttribute("aria-busy", "true");
      try {
        const kept = await api(`${base}/record`, "POST", {});
        recorded.textContent = `Recorded ${kept.page}: ${kept.controls} fields and ${kept.buttons} buttons kept, ${kept.dropped} left out.`;
        recorded.hidden = false;
        await listCaptures();
      } catch (cause) {
        recorded.textContent = cause?.message || String(cause);  // here, not the alert line: each status read clears that
        recorded.hidden = false;
        if (cause?.status === 410) stop(cause.message);
      } finally {
        record.disabled = false; record.removeAttribute("aria-busy");
      }
    };
  }
  void listCaptures();

  // A session already started on the server (a Follow-up card's upload): followed with the sign-in saved there.
  if (attach) {
    saved = true;
    const who = $("[data-aim-who]");
    who.textContent = "Signing in with the sign-in saved on the server.";
    who.hidden = false;
    $("[data-aim-ferpa]").hidden = !site.ferpa;
    follow(attach);
    return;
  }
  // A sign-in saved on the server: straight to Duo, no form.
  try {
    const stored = await api(`${site.route}/saved`);
    if (ended || !alive()) { close(); return; }
    if (stored.saved && dialog.open && !ended) {
      saved = true;
      if (stored.netid) netidInput.value = stored.netid;
      const who = $("[data-aim-who]");
      who.textContent = `Signing in as ${stored.netid} with the sign-in saved on the server.`;
      who.hidden = false;
      $("[data-aim-ferpa]").hidden = !site.ferpa;
      await begin({ saved: true });
    } else {
      credentialsBox.hidden = false; submit.hidden = false;
      if (netidInput.value) passwordInput.focus(); else netidInput.focus();
      if (stored.problem) say(stored.problem);
    }
  } catch (cause) {
    credentialsBox.hidden = false; submit.hidden = false;
    say(cause?.message || String(cause));
  }
}
