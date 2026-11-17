// @ts-check
/* The shell's page header, breadcrumb trail, tab title and sidebar attention badges (UX overhaul, 2026-09-26).

   The shell (app.js) gives every route its defaults from one declarative views table (header.defaults). A view that knows
   its object better (a scan's headline, an assignment's title) calls header.set({route, ...}) from its own render; the
   call is ignored unless spec.route is the route on screen, and it lasts until the route's path changes. The header only
   PAINTS: it never decides a count or a state (the badges are the server's /api/workspace/overview attention cards). */

import { escape } from "./ui.js";

/** The route's path: the hash without '#' and without its '?query'. @param {string} [hash] */
export function routePath(hash = "") {
  return String(hash || "").replace(/^#/, "").split("?")[0];
}

/**
 * The breadcrumb list: every crumb but the last is a link when it has an href; the last is the page (aria-current).
 * @param {{label: string, href?: string}[]} crumbs
 * @param {string} [homeIcon] markup placed before the first crumb's label (decorative)
 */
export function crumbsMarkup(crumbs, homeIcon = "") {
  const items = (crumbs || []).filter((crumb) => crumb && crumb.label);
  return `<ol>${items.map((crumb, index) => {
    const lead = index === 0 ? homeIcon : "";
    if (index === items.length - 1) return `<li aria-current="page">${lead}<span>${escape(crumb.label)}</span></li>`;
    return crumb.href
      ? `<li><a href="${escape(crumb.href)}">${lead}<span>${escape(crumb.label)}</span></a></li>`
      : `<li>${lead}<span>${escape(crumb.label)}</span></li>`;
  }).join("")}</ol>`;
}

/** '(3) Test 1 · Fall teaching', or 'Rooms · Faculty' without attention or a workspace name.
 * @param {string} label @param {number} [attention] @param {string} [suffix] */
export function tabTitle(label, attention = 0, suffix = "") {
  const count = Number(attention) > 0 ? `(${Number(attention)}) ` : "";
  return `${count}${label || "Faculty"} · ${String(suffix || "").trim() || "Faculty"}`;
}

/** A view's spec over the shell's defaults: keys the view leaves undefined keep the default (null clears an action). */
/** @param {any} defaults @param {any} override @returns {any} */
export function resolveHeader(defaults, override) {
  const base = { variant: "compact", title: "", context: "", subtitle: "", crumbs: [], action: null, secondary: [], updated: "", label: undefined, attention: false, ...(defaults || {}) };
  if (!override) return base;
  const merged = { ...base };
  for (const [key, value] of Object.entries(override)) if (value !== undefined) merged[key] = value;
  // A view that names its object also names the trail's last crumb, unless it gave its own trail.
  if (override.title && override.crumbs === undefined && merged.crumbs.length > 1) {
    merged.crumbs = [...merged.crumbs.slice(0, -1), { label: override.crumbLabel || override.title }];
  }
  return merged;
}

/** Sidebar entries that show a boxed attention count, and the href prefixes the server's attention cards use for them. */
export const BADGE_ROUTES = [
  ["inbox", "#inbox"],
  ["response-cards", "#response-cards"],
  ["exams", "#exams"],
  ["grading", "#grading"],
  ["documents", "#documents"],
  ["seating", "#seating"],
  ["absences", "#absences"],
  ["follow-ups", "#follow-ups"],
  ["follow-ups", "#office-hours"],
  // RECORDINGS "ONLY WHAT IS FACULTY'S IS KEPT": the recordings that wait for Keep or Don't keep (Home's card `recordings:keep`).
  ["recordings", "#recordings"],
];

/**
 * Attention cards → {counts: {inbox: 3, ...}, health: n, total}. Operations cards (#overview?operations: the worker,
 * storage, backups) become Home's health dot and are not counted in the tab title; everything else counts once.
 * A Teaching Inbox card counts only when the server marks it `derived: true`, i.e. counted from the Inbox's own filing
 * (`listed_as`, PRESENT only). A scan's stored status stays needs_review forever, so the stored count would grow with
 * every scan the Inbox already files as finished (review W1-DQ-1): such a card stays on Home and gets no badge.
 * A card the server marks `counted_in` (another card's key) names things that card already counts (NEEDS ME, 2026-10-06: a
 * possible wellbeing concern is a follow-up that needs you): it stays on Home and adds nothing here.
 * @param {{href?: string, count?: number, derived?: boolean, counted_in?: string}[]} items
 */
export function attentionBadges(items) {
  /** @type {Record<string, number>} */
  const counts = {};
  let health = 0;
  let total = 0;
  for (const item of items || []) {
    const href = String(item?.href || "");
    const count = Math.max(0, Number(item?.count) || 0);
    if (!count || item?.counted_in) continue;
    if (href.startsWith("#overview")) { health += count; continue; }
    const match = BADGE_ROUTES.find(([, prefix]) => href === prefix || href.startsWith(prefix + "/") || href.startsWith(prefix + "?"));
    if (!match) continue;
    if (match[0] === "inbox" && item?.derived !== true) continue;
    counts[match[0]] = (counts[match[0]] || 0) + count;
    total += count;
  }
  return { counts, health, total };
}

/**
 * The rail entry the page belongs to. One Courses entry stands for both lists and every course page, except a course's Canvas
 * pages, which are the Canvas entry's. Assignments stands for every assignment page, Teaching review (no rail entry of its own since
 * 2026-10-18: the Assignments index strip carries it) and a scan page opened inside the assignment shell (`inAssignment`, the
 * shell's `faculty:assignment-context`, I4); Follow-up for office hours. Pure.
 * @param {{view: string, courseSub?: string, path: string, inAssignment?: boolean}} where
 */
export function navEntry({ view, courseSub = "", path, inAssignment = false }) {
  if (view === "course" && courseSub === "canvas") return "canvas";
  if (["all-courses", "current-courses", "course"].includes(view)) return "current-courses";
  if (["assignments", "documents", "exams", "exam-day", "grading", "grade-delivery", "teaching-review"].includes(view)) return "assignments";
  if (view === "inbox" && inAssignment && String(path || "").startsWith("inbox/job/")) return "assignments";
  return view === "office-hours" ? "follow-ups" : view;
}

/** The badge routes whose cards are an assignment's work: the one Assignments entry stands for all three (ASSIGNMENT-WORKSPACE). */
export const ASSIGNMENT_BADGES = ["documents", "exams", "grading"];

/** The BADGE_ROUTES entry an attention card's href belongs to, or "". @param {string} href */
export function badgeRoute(href) {
  const match = BADGE_ROUTES.find(([, prefix]) => href === prefix || href.startsWith(prefix + "/") || href.startsWith(prefix + "?"));
  return match ? match[0] : "";
}

/**
 * The Assignments badge: how many decisions wait for the instructor across assignments, counted as the assignment tabs and the
 * index count them, one per needs-you item (requirement, 2026-10-18): an attention card of an
 * assignment's work (`grading:<plan>:review` is "Review grades", one decision, whatever number of responses it holds) counts
 * once. Cards with no count or `counted_in` another card count nothing, as in attentionBadges. The index lists the same cards at
 * its top (`assignmentDecisionCards`), so the badge can always be read there. A makeup version waiting to be approved opens the
 * assignment's own address and says so itself (`assignment_work`, ASSIGNMENT-WORKSPACE, 2026-11-05). Pure.
 * @template {{href?: string, count?: number, counted_in?: string, assignment_work?: boolean}} T
 * @param {T[] | null | undefined} items
 * @returns {T[]}
 */
export function assignmentDecisionCards(items) {
  return (items || []).filter((item) => Number(item?.count) > 0 && !item?.counted_in
    && (item?.assignment_work || ASSIGNMENT_BADGES.includes(badgeRoute(String(item?.href || "")))));
}

/** The Assignments badge's number. @param {{href?: string, count?: number, counted_in?: string}[] | null | undefined} items */
export const assignmentDecisions = (items) => assignmentDecisionCards(items).length;

/**
 * The header band's label (DESIGN-LANGUAGE §5): a view's own `label`, else its context line (course · term · count), unless
 * the breadcrumb already says every part of it (Ledger review, 2026-10-04: "MATH 140 · FALL 2026 · GRADEBOOK" under a trail that
 * reads MATH 140 · Fall 2026 / … / Gradebook said it twice); a label with anything new is kept whole. No date: the clock beside the trail shows it (header-clock.js still
 * keeps #today current). Never a student name: views pass objects and counts, as the context line
 * always has. Pure.
 * @param {{label?: string, context?: string, crumbs?: {label?: string}[]}} spec
 * @returns {{text: string, date: boolean}}
 */
export function plateLabel(spec) {
  const trail = (spec?.crumbs || []).map((crumb) => String(crumb?.label || "").toLowerCase());
  const said = (/** @type {string} */ part) => trail.some((crumb) => crumb === part || crumb.split(" · ").includes(part));
  const fresh = (/** @type {string} */ text) => String(text).split(" · ").every((part) => !part.trim() || said(part.trim().toLowerCase())) ? "" : String(text);
  if (typeof spec?.label === "string") return { text: fresh(spec.label), date: false };
  if (spec?.context) return { text: fresh(spec.context), date: false };
  return { text: "", date: false };
}

/**
 * The subtitle split for paint: with `attention`, its first clause (an attention count, "5 things need you") is printed as
 * the orange needs-you block (DESIGN-LANGUAGE §4) and the rest follows in ink.
 * @param {string} subtitle @param {boolean} [attention]
 * @returns {{lead: string, rest: string}}
 */
export function subtitleParts(subtitle, attention = false) {
  const text = String(subtitle || "");
  if (!attention) return { lead: "", rest: text };
  const at = text.indexOf(" · ");
  return at < 0 ? { lead: text, rest: "" } : { lead: text.slice(0, at), rest: text.slice(at) };
}

/**
 * The header painter. `icon(name)` returns decorative SVG markup; `onPaint(resolved)` (the title's fit, page-title-fit.js) runs after
 * every paint with the resolved spec.
 * @param {{doc?: Document, icon: (name: string) => string, location?: Location, onPaint?: (resolved: any) => void}} options
 */
export function createHeader({ doc = globalThis.document, icon, location: where = globalThis.location, onPaint = () => {} }) {
  /** @type {any} */ let defaults = null;
  /** @type {any} */ let override = null;
  let attention = 0;
  let suffix = "";
  let painted = "";
  // The markup last written into each header button host. Reading innerHTML back never matches an icon's source
  // (`<path/>` serializes as `<path></path>`), so comparing with it rewrote the buttons, and lost their focus, on every poll.
  /** @type {WeakMap<Element, string>} */ const writtenMarkup = new WeakMap();
  /** @type {any} */ let resolved = resolveHeader(null, null);
  const $ = (/** @type {string} */ selector) => /** @type {HTMLElement | null} */ (doc.querySelector(selector));
  const current = () => routePath(where.hash);

  /** @param {HTMLElement | null} node @param {string} text */
  function setText(node, text) {
    if (node && node.textContent !== text) node.textContent = text;
  }

  /** @param {any} action */
  function paintAction(action) {
    const button = /** @type {HTMLButtonElement | null} */ ($("#primary-action"));
    if (!button) return;
    button.hidden = !action || Boolean(action.hidden);
    if (!action) return;
    const markup = icon(action.icon || "plus") + escape(action.label);
    if (writtenMarkup.get(button) !== markup) { button.innerHTML = markup; writtenMarkup.set(button, markup); }
    // Declarative actions keep the shell's data-action dispatch (and every data-* the old if-chain set); `run` actions
    // are called by the header's own listener below.
    for (const key of Object.keys(button.dataset)) if (key !== "headerRun") delete button.dataset[key];
    if (action.action) button.dataset.action = action.action;
    for (const [key, value] of Object.entries(action.data || {})) button.dataset[key] = String(value ?? "");
    button.dataset.headerRun = action.run ? "true" : "false";
    button.disabled = Boolean(action.disabled);
  }

  /** @param {any[]} secondary */
  function paintSecondary(secondary) {
    const host = $(".page-heading-secondary");
    if (!host) return;
    const markup = (secondary || []).map((/** @type {any} */ item, /** @type {number} */ index) => item.href
      ? `<a class="button" href="${escape(item.href)}">${item.icon ? icon(item.icon) : ""}${escape(item.label)}</a>`
      : `<button class="button" type="button" data-header-secondary="${index}"${item.action ? ` data-action="${escape(item.action)}"` : ""}${Object.entries(item.data || {}).map(([key, value]) => ` data-${key.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)}="${escape(String(value ?? ""))}"`).join("")}${item.disabled ? " disabled" : ""}${item.title ? ` title="${escape(item.title)}"` : ""}>${item.icon ? icon(item.icon) : ""}${escape(item.label)}</button>`).join("");
    if (writtenMarkup.get(host) !== markup) { host.innerHTML = markup; writtenMarkup.set(host, markup); }
    host.hidden = !markup;
  }

  function paint() {
    const override_ = override && override.route === current() ? override : null;
    resolved = resolveHeader(defaults, override_);
    const heading = $(".page-heading");
    if (heading) heading.dataset.variant = resolved.variant === "hero" ? "hero" : "compact";
    const label = plateLabel(resolved);
    const today = $("#today");
    if (today) today.hidden = !label.date;
    const context = $("#page-context");
    if (context) { setText(context, label.text); context.hidden = !label.text; }
    setText($("#page-title"), resolved.title || "");
    const subtitle = $("#page-subtitle");
    const { lead, rest } = subtitleParts(resolved.subtitle, Boolean(resolved.attention));
    if (subtitle) {
      const markup = lead ? `<span class="page-subtitle-lead">${escape(lead)}</span>${escape(rest)}` : escape(rest);
      if (writtenMarkup.get(subtitle) !== markup) { subtitle.innerHTML = markup; writtenMarkup.set(subtitle, markup); }
      // What needs you is said in the band beside the title (below); the sentence stays here for anything that reads it.
      subtitle.hidden = !resolved.subtitle || Boolean(lead);
    }
    // NEEDS YOU BESIDE THE TITLE (Ledger; requirement, 2026-10-04): an attention count is the orange chip
    // on the title's line, the rest of the sentence muted after it, and the row under the band keeps only the page's actions.
    const status = $("#page-status");
    if (status) {
      // A chip and a status line, not a sentence: no closing full stop.
      const chip = lead.replace(/\.$/, ""), after = rest.replace(/^ · /, "").replace(/\.$/, "");
      const markup = lead ? `<span class="page-subtitle-lead">${escape(chip)}</span>${after ? `<span class="page-status-rest">${escape(after)}</span>` : ""}` : "";
      if (writtenMarkup.get(status) !== markup) { status.innerHTML = markup; writtenMarkup.set(status, markup); }
      status.hidden = !lead;
    }
    const updated = $(".page-updated");
    if (updated) { setText(updated, resolved.updated || ""); updated.hidden = !resolved.updated; }
    paintAction(resolved.action);
    paintSecondary(resolved.secondary);
    const trail = $("#breadcrumb");
    const crumbs = resolved.crumbs?.length ? resolved.crumbs : [{ label: "Home", href: "#overview" }];
    const markup = crumbsMarkup(crumbs, icon("home"));
    if (trail && markup !== painted) { trail.innerHTML = markup; painted = markup; }
    const title = tabTitle(resolved.tab || crumbs.at(-1)?.label || resolved.title, attention, suffix);
    if (doc.title !== title) doc.title = title;
    onPaint(resolved);
  }

  // One listener serves every `run` action, so repainting never stacks handlers.
  doc.addEventListener("click", (event) => {
    const target = /** @type {HTMLElement | null} */ (event.target instanceof Element ? event.target : null);
    const primary = target?.closest("#primary-action");
    // A click's handler may rewrite the button it was given: the next paint writes the header's markup again, as before.
    if (primary) writtenMarkup.delete(primary);
    const host = target?.closest(".page-heading-secondary");
    if (host) writtenMarkup.delete(host);
    if (primary instanceof HTMLButtonElement && !primary.disabled && resolved.action?.run) {
      resolved.action.run(event);
      return;
    }
    const secondary = target?.closest("[data-header-secondary]");
    if (secondary instanceof HTMLElement && !(secondary instanceof HTMLButtonElement && secondary.disabled)) {
      resolved.secondary?.[Number(secondary.dataset.headerSecondary)]?.run?.(event);
    }
  });

  return {
    /** A view's header for the route on screen. Returns false (and changes nothing) for any other route. */
    /** @param {any} spec */
    set(spec) {
      if (!spec || routePath(spec.route) !== current()) return false;
      override = { ...spec, route: routePath(spec.route) };
      paint();
      return true;
    },
    /** Drop the view's header and show the shell's defaults for this route. */
    reset() {
      override = null;
      paint();
    },
    /** The shell's defaults for the route on screen (from the views table). A view's header for this route survives. */
    /** @param {any} spec */
    defaults(spec) {
      defaults = { ...spec, route: routePath(spec?.route ?? where.hash) };
      if (override && override.route !== current()) override = null;
      paint();
    },
    /** Attention total for the tab title and the workspace name used as its suffix. */
    setTitleParts({ count = attention, name = suffix } = {}) {
      attention = count;
      suffix = name;
      paint();
    },
    get: () => resolved,
  };
}
