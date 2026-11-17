import { conditionsOf, renderSeatGrid, placeSeatGrid, seatFrame } from "./seat-grid.js";
import { createSeatingPrint } from "./seating-print.js";
import { createSeatingInteractions } from "./seating-interactions.js";
import { createSeatingDelivery } from "./seating-delivery.js";
import { createSeatingCreation } from "./seating-create.js";
import { dateFor } from "./class-schedule-model.js";
import { jobFiles, exportFiles, isStampsOnly, downloadable, bindPrintFiles } from "./print-files.js";
import { runFormAction, calloutMarkup, disclosureMarkup, emptyMarkup, filterChipsMarkup, scrollBehavior, stateLabel, wallDate, setBusy, dialogSession, captureDialogIntent } from "./ui.js";
import { chartGroups, chartProblems, chartState, drOthers, examHref, examRoute, examState, examTiming, hasRoomsPage, legendStates, listSummary, matchesFilter, nextAction, orderGroups, plural, recordedTime, reseatImpact, studentFilterOptions } from "./seating-state.js";

const STATUS = { seated: "Seated", dr_testing: "DR testing", unseated: "No seat", seat_unusable: "Seat unusable" };

export function createSeating({ api, icon, escape, toast, showDialog, dialogHeader, header, getCharts, refresh }) {
  let route = "";
  let chart = null;
  let generation = 0;
  let versionView = false;
  // Browser memory only: which students the table and the seat highlights show (reset when another chart opens).
  let studentFilter = "all";
  let filterChart = "";
  let foundSeat = "";
  let conflictSeats = new Set();
  const seatUpdateRequests = new Map();
  bindPrintFiles();
  const { openStudentChartMail, openGmailSend, openMailMerge } = createSeatingDelivery({
    api, escape, toast, showDialog, dialogHeader, gmailLabel, when, refresh,
    refreshChart: () => render(true),
  });
  const { openCreate } = createSeatingCreation({
    api, escape, toast, showDialog, dialogHeader, getCharts, refresh, relinkField, wizardBack, href,
  });
  const printing = createSeatingPrint({
    api, escape, toast, showDialog, dialogHeader, chartId, scope,
    getChart: () => chart, isVersionView: () => versionView,
    // The exam-day packet prints the page's seat chart: a packet read with a newer chart brings the page to it first.
    reloadChart: () => render(true),
    setVersionView: (value) => {
      versionView = Boolean(value);
      const root = document.querySelector("#seating-section");
      if (root && chart && !chart.missing) renderDetail(root);
    },
  });
  const { openPrint, openVersionPrint, openRoomRosterPrint, openPacketPrint } = printing;
  const interactions = createSeatingInteractions({
    api, escape, toast, showDialog, dialogHeader, chartId, renderDetail, render, refresh, closeSeatPopover,
    getChart: () => chart, setChart: (value) => { chart = value; },
    isVersionView: () => versionView, invalidateRender: () => { ++generation; },
  });
  const { versionStatusMarkup, clearVersionSelection, selectVersionSeat, openVersionAssign,
    openMove, clearMoveSelection, setMoveBusy, moveToSeat, openSeatHere } = interactions;

  function chartId() {
    const target = location.hash.slice(1).split("?")[0];
    // '#seating/exam/<assignment>/<term>' is an exam's page of rooms, not a chart.
    const match = examRoute(target) ? null : target.match(/^seating\/(.+)$/);
    try {
      return match ? decodeURIComponent(match[1]) : "";
    } catch {
      return match[1];
    }
  }

  function href(item) {
    return `#seating/${encodeURIComponent(item.id)}`;
  }

  function adoptChart(value, root = document.querySelector("#seating-section")) {
    if (chartId() !== value.id || !root?.isConnected) return false;
    ++generation; // A read begun before this write cannot replace its result.
    chart = value;
    renderDetail(root);
    return true;
  }

  function when(value) {
    // Recorded moments share one format and the campus clock with the server's lines ('Sat, Sep 26 · 8:43 AM').
    return recordedTime(value);
  }

  function scope(item) {
    return `${item.code} · ${item.term_label} · Section${item.sections.length === 1 ? "" : "s"} ${item.sections.join(", ")}`;
  }

  const perform = runFormAction;

  function card(item) {
    const counts = item.counts || {};
    const state = chartState(item);
    return `<a class="seating-card" href="${href(item)}" aria-label="Open ${escape(item.room_name)} seating for ${escape(item.title)}, ${escape(scope(item))}"><span class="seating-card-top"><span class="seating-card-icon">${icon("grid")}</span><span class="chip seating-card-status" data-state="${state.toLowerCase()}">${state}</span></span>${item.archived ? `<span class="chip" data-tone="neutral" title="Grades certified ${escape(shortCertified(item.archived))}: nothing here needs you; the chart is unchanged">Grades certified</span>` : `${item.check && !item.check.ok ? `<span class="seating-flag" title="${escape(flagText(item.check.counts))}">Needs attention · ${Object.values(item.check.counts).reduce((sum, value) => sum + value, 0)}</span>` : ""}${item.seat_updates_pending ? `<span class="seating-flag seating-flag-updates" title="Students moved after confirmation still need their updated seat cards">${escape(plural(item.seat_updates_pending, "updated seat card"))} to send</span>` : ""}`}<h3>${escape(item.room_name)}</h3><p>Section${item.sections.length === 1 ? "" : "s"} ${item.sections.join(", ")}${item.pool_rooms?.length ? ` · mixed with ${escape(item.pool_rooms.join(", "))}` : ""}</p><div class="room-facts"><span><strong>${item.seated_count}</strong> seated</span><span><strong>${counts.excluded_dr ?? 0}</strong> DR testing</span><span><strong>${counts.free_seats ?? "–"}</strong> free seats</span></div></a>`;
  }

  function flagText(counts = {}) {
    return [counts.dr_seated ? `${counts.dr_seated} seated with a DR booking` : "", counts.missing_seat ? `${counts.missing_seat} without a seat` : "",
      counts.seat_unusable ? `${counts.seat_unusable} on an unusable seat` : "", counts.not_in_roster ? `${counts.not_in_roster} no longer in the sections` : "", counts.room_review ? "room needs review" : ""].filter(Boolean).join(", ");
  }

  function groupChips(group) {
    // Derived from the list rows only: confirmation, response cards and exam versions (neutral chips; card colours stay reserved).
    const confirmed = group.confirmed === group.charts.length
      ? `Confirmed ${new Date(group.confirmedAt).toLocaleDateString(undefined, { month: "short", day: "numeric" })}${group.mode ? ` · ${group.mode === "stamps_only" ? "stamps only" : "exam PDF"}` : ""}`
      : group.confirmed ? `${group.confirmed} of ${plural(group.charts.length, "room")} confirmed` : "Not confirmed";
    const cards = group.cards.recipients ? `Cards ${group.cards.responded.toLocaleString()} of ${group.cards.recipients.toLocaleString()} responded${group.cards.flagged ? ` · ${group.cards.flagged.toLocaleString()} flagged` : ""}` : "";
    const versions = group.versions.length ? `Versions ${group.versions.map(([letter, count]) => `${letter} ${count}`).join(" · ")}` : "";
    const updates = group.seatUpdates ? `${plural(group.seatUpdates, "updated seat card")} to send` : "";
    const chips = [[confirmed, !group.confirmed ? "info" : "neutral"], [updates, "warning"], [cards, "neutral"], [versions, "neutral"]];
    return `<div class="chip-row seating-group-chips">${chips.filter(([text]) => text).map(([text, tone]) => `<span class="chip" data-tone="${tone}">${escape(text)}</span>`).join("")}</div>`;
  }

  function sectionsLabel(numbers) {
    return `Section${numbers.length === 1 ? "" : "s"} ${numbers.join(", ")}`;
  }

  function examCard(group) {
    // SEATING "One tile per exam": the list holds one tile for each exam. It opens the exam's rooms; an exam with one room
    // and every section seated opens that room's chart itself.
    const state = examState(group);
    const rooms = group.charts.length;
    const scope = `${group.code} · ${group.term_label}`;
    const flagged = group.archived ? 0 : group.flagged; // certified grades resolve a flagged chart (the chart itself is unchanged)
    const flaggedRooms = group.charts.filter((item) => item.check && !item.check.ok).map((item) => `${item.room_name}: ${flagText(item.check.counts)}`).join("; ");
    const flags = group.archived
      ? `<span class="chip" data-tone="neutral" title="Grades certified ${escape(shortCertified(group.archived))}: nothing here needs you; the charts are unchanged">Grades certified</span>`
      : `${flagged ? `<span class="seating-flag" title="${escape(flaggedRooms)}">${flagged} room${flagged === 1 ? " needs" : "s need"} attention</span>` : ""}${group.seatUpdates ? `<span class="seating-flag seating-flag-updates" title="Students moved after confirmation still need their updated seat cards">${escape(plural(group.seatUpdates, "updated seat card"))} to send</span>` : ""}`;
    const when = group.exam ? `<p class="seating-group-date">Exam ${escape([group.exam.date, group.exam.time, group.exam.relative].filter(Boolean).join(" · "))}</p>` : "";
    const cards = group.cards.recipients ? `<p>Cards ${group.cards.responded.toLocaleString()} of ${group.cards.recipients.toLocaleString()} responded${group.cards.flagged ? ` · ${group.cards.flagged.toLocaleString()} flagged` : ""}</p>` : "";
    return `<a class="seating-card seating-exam-card" href="${hasRoomsPage(group) ? examHref(group) : href(group.charts[0])}" aria-label="Open ${escape(group.title)} seating, ${escape(scope)}, ${escape(plural(rooms, "room"))}"><span class="seating-card-top"><span class="seating-card-icon">${icon("grid")}</span><span class="chip seating-card-status" data-state="${group.past ? "held" : group.confirmed === rooms ? "confirmed" : "open"}"${group.past || group.confirmed === rooms ? "" : ' data-tone="info"'}>${escape(state)}</span></span>${flags}<h3>${escape(group.title)}</h3><p class="seating-exam-scope">${escape(scope)}</p>${when}<p>${escape(group.rooms.join(", "))} · ${sectionsLabel(group.sections)}</p>${group.missing.length ? `<p>Not yet seated: ${sectionsLabel(group.missing)}</p>` : ""}${cards}<div class="room-facts"><span><strong>${group.seated}</strong> seated</span><span><strong>${group.dr}</strong> DR testing</span><span><strong>${group.free ?? "–"}</strong> free seats</span></div></a>`;
  }

  function examGrid(groups) {
    return `<div class="seating-chart-grid seating-exam-grid">${groups.map(examCard).join("")}</div>`;
  }

  function roomsBlock(group) {
    // The exam's own page: its summary line and chips (the header carries its name), Add rooms while a section has none,
    // then a tile for each room.
    const flagged = group.archived ? 0 : group.flagged;
    const when = group.exam ? `<span class="seating-group-date">Exam ${escape([group.exam.date, group.exam.time, group.exam.relative].filter(Boolean).join(" · "))}</span> · ` : "";
    return `<section class="seating-group" aria-label="${escape(group.title)} · ${escape(group.code)} · ${escape(group.term_label)}"><div class="seating-group-heading"><div><p>${when}${escape(plural(group.charts.length, "room"))} · ${group.seated} seated${group.missing.length ? ` · not yet seated: ${sectionsLabel(group.missing)}` : ""}${flagged ? ` · <strong>${flagged} room${flagged === 1 ? "" : "s"} need${flagged === 1 ? "s" : ""} attention</strong>` : ""}</p>${groupChips(group)}</div>${group.missing.length ? `<button type="button" class="button" data-action="add-seating" data-assignment="${escape(group.assignment_id)}">Add rooms</button>` : ""}</div><div class="seating-chart-grid">${group.charts.map(card).join("")}</div></section>`;
  }

  const shortCertified = (archived) => String(archived?.certified_at || "").slice(0, 10);

  function renderList(root) {
    const charts = getCharts();
    // The header carries the one-line summary; how seating works lives in the New seating chart dialog.
    header?.set({ route: location.hash, subtitle: listSummary(charts) });
    if (!charts.length) {
      root.innerHTML = emptyMarkup({ icon: "grid", title: "Start with an exam and a room.", body: "Choose the assignment and a room for each section; linked DR bookings stay separate.", action: { label: "New seating chart", data: { action: "add-seating" } } });
      return;
    }
    const { upcoming, past, archived } = orderGroups(chartGroups(charts));
    const none = !upcoming.length && !past.length ? `<p class="supporting-copy">Every assignment here has certified grades; its charts are under Archived below.</p>` : "";
    root.innerHTML = `${none}${upcoming.length ? examGrid(upcoming) : ""}${past.length ? disclosureMarkup({ label: "Past exams", hint: past.length, body: examGrid(past), key: "seating:past-exams", className: "seating-past" }) : ""}${archived.length ? disclosureMarkup({ label: "Archived · grades certified", hint: archived.length, body: examGrid(archived), key: "seating:archived", className: "seating-past seating-archived" }) : ""}`;
  }

  function renderExam(root, route) {
    // One exam's rooms, from the list rows the page already holds (no new read).
    const group = chartGroups(getCharts()).find((item) => item.assignment_id === route.assignment_id && item.term_id === route.term_id);
    const crumbs = [{ label: "Home", href: "#overview" }, { label: "Seating charts", href: "#seating" }];
    const back = `<nav class="room-breadcrumbs" aria-label="Seating navigation"><a class="text-button" href="#seating">${icon("arrow-left")}All seating charts</a></nav>`;
    if (!group) {
      header?.set({ route: location.hash, title: "Seating charts", subtitle: "", action: null, crumbs: [...crumbs, { label: "Exam" }] });
      root.innerHTML = `${back}${emptyMarkup({ icon: "grid", title: "No seating charts for this exam.", body: "Its rooms may have been removed. New seating chart starts one.", action: { label: "New seating chart", data: { action: "add-seating" } } })}`;
      return;
    }
    header?.set({ route: location.hash, title: group.title, context: `${group.code} · ${group.term_label}`, subtitle: "", action: null, crumbs: [...crumbs, { label: group.title }] });
    root.innerHTML = `${back}${roomsBlock(group)}`;
  }

  function seatButton(seat, mini, attributes) {
    const conditions = conditionsOf(seat);
    const student = seat.student;
    if (versionView) {
      const conflict = conflictSeats.has(seat.label);
      const letter = seat.exam_version || "—";
      const classes = ["seat", mini ? "seat-mini" : "", ...conditions, student ? "seated version-seat" : conditions.length ? "" : "free", student && !seat.exam_version ? "version-missing" : "", conflict ? "version-conflict" : ""].filter(Boolean).join(" ");
      const label = `Seat ${seat.label}${student ? `: ${seat.exam_version ? `exam version ${letter}` : "version not assigned"}, ${student.name}` : ", empty"}${conflict ? ", version conflict" : ""}`;
      return `<button type="button" class="${classes}" data-seat="${seat.id}"${student && seat.exam_version ? ` data-exam-version="${escape(seat.exam_version)}"` : ""}${attributes} aria-label="${escape(label)}" title="${escape(label)}">${student ? `<span class="seat-exam-version">${escape(letter)}</span>` : ""}<span class="seat-tag">${escape(seat.label)}</span>${seatFrame}</button>`;
    }
    const card = student?.card;
    const classes = ["seat", mini ? "seat-mini" : "", ...conditions, student ? "seated" : conditions.length ? "" : "free", student?.crowded ? "crowded" : "", card ? `card-${card.state}` : ""].filter(Boolean).join(" ");
    const title = student ? `${student.name} · Section ${student.section_number ?? "?"} · ${seat.label}${card ? ` · ${card.summary}` : ""}` : `${seat.label}${conditions.map((item) => ` · ${item}`).join("")}`;
    const label = student ? `Seat ${seat.label}: ${student.name}${card ? `, ${card.summary}` : ""}` : `Seat ${seat.label}${conditions.length ? `, ${conditions.join(", ")}` : ", free"}`;
    return `<button type="button" class="${classes}" data-seat="${seat.id}"${attributes}${card ? ` data-card-state="${card.state}"` : ""} aria-label="${escape(label)}" title="${escape([title, seat.notes].filter(Boolean).join(" · "))}">${student ? `<span class="seat-name">${escape(student.name)}</span>` : ""}<span class="seat-tag">${escape(seat.label)}</span>${seatFrame}</button>`;
  }

  function tableTitle(group, size) {
    return `<span class="seat-table-title" title="${escape(group)} · ${size} seats">${escape(group.replace(/^\S+\s+/, ""))}</span>`;
  }

  function cardCell(item) {
    // The status opens the full card details; the note names the specific problem, not just its heading.
    if (!item.card) return '<span class="seating-card-state none">—</span>';
    const note = (item.card.problems?.length ? item.card.problems : (item.card.detail || []).slice(0, 1)).join(" ");
    return `<button type="button" class="text-button seating-card-button" data-card-details="${escape(item.enrollment_id)}" aria-label="Response card details for ${escape(item.name)}" title="Show card details"><span class="seating-card-state ${item.card.state}">${escape(item.card.summary)}</span></button>${note ? `<span class="seating-card-note">${escape(note)}</span>` : ""}`;
  }

  let outsideClick = null;
  function closeSeatPopover(root) {
    root.querySelector(".seat-popover")?.remove();
    root.querySelectorAll('.seat[aria-expanded="true"]').forEach((button) => button.removeAttribute("aria-expanded"));
  }

  function toggleSeatPopover(root, button, seat) {
    // The tile expands into a card beneath the seat: status, details, and the move action.
    const open = button.getAttribute("aria-expanded") === "true";
    closeSeatPopover(root);
    if (open) return;
    const card = seat.student.card;
    const holder = root.querySelector(".seat-chart");
    const pop = document.createElement("div");
    pop.className = `seat-popover ${card.state}`;
    pop.tabIndex = -1;
    pop.setAttribute("role", "dialog");
    pop.setAttribute("aria-label", `${seat.student.name}, seat ${seat.label}: ${card.summary}`);
    pop.innerHTML = `<div class="seat-popover-head"><strong>${escape(seat.student.name)}</strong><span>Seat ${escape(seat.label)} · Section ${seat.student.section_number ?? "?"}</span><button type="button" class="text-button" data-pop-close aria-label="Close">×</button></div>${cardBody(card)}${cardActions(card, '<button type="button" class="text-button" data-pop-move>Move or swap</button>')}`;
    holder.appendChild(pop);
    button.setAttribute("aria-expanded", "true");
    const box = holder.getBoundingClientRect(), at = button.getBoundingClientRect();
    pop.style.left = `${Math.max(0, Math.min(at.left - box.left - holder.clientLeft + holder.scrollLeft, holder.scrollWidth - pop.offsetWidth - 4))}px`;
    pop.style.top = `${at.bottom - box.top - holder.clientTop + holder.scrollTop + 6}px`;
    pop.querySelector("[data-pop-close]").onclick = () => { closeSeatPopover(root); button.focus(); };
    pop.querySelector("[data-pop-move]").onclick = () => {
      closeSeatPopover(root);
      openMove(chart.students.find((item) => item.enrollment_id === seat.student.enrollment_id) || { enrollment_id: seat.student.enrollment_id, name: seat.student.name, seat: seat.label });
    };
    bindCardActions(pop, seat.student, `Seat ${seat.label}`, card, () => closeSeatPopover(root));
    pop.focus();
  }

  // The seat popover and the student table's details dialog share one card panel:
  // status, every detail line (problems first), the link to hand over, and the actions.
  function cardBody(card) {
    return `<p><span class="seating-card-state ${card.state}">${escape(card.summary)}</span></p><ul class="seat-popover-detail">${(card.detail || []).map((line) => `<li>${escape(line)}</li>`).join("") || "<li>No details recorded.</li>"}</ul>${card.link_available ? '<p class="seat-popover-link"><span class="seat-popover-link-label">Card link</span><code data-pop-link-text>Loading…</code><button type="button" class="text-button" data-pop-copy disabled>Copy</button></p>' : ""}`;
  }

  function cardActions(card, extra = "") {
    return `<div class="seat-popover-actions">${extra}${card.can_resend ? `<button type="button" class="text-button" data-pop-resend>${resendLabel(card)}</button>` : ""}${card.issue_id ? `<a href="#response-cards/issue/${encodeURIComponent(card.issue_id)}">Open in Response Cards</a>` : ""}</div>`;
  }

  function bindCardActions(container, student, where, card, dismiss) {
    container.querySelector("[data-pop-resend]")?.addEventListener("click", () => {
      dismiss();
      openResend(student, where, card);
    });
    if (card.link_available) loadCardLink(container, student, card);
  }

  function whereIs(student) {
    return student.status === "dr_testing" ? "DR testing, not seated" : student.seat ? `Seat ${student.seat}` : "No seat";
  }

  function openCardDetails(student) {
    // DR students have no seat tile to click, so the table opens the same panel in a dialog.
    if (!student?.card) return;
    const card = student.card;
    showDialog(`<div id="seating-card-details">${dialogHeader(escape(student.name), escape(`${whereIs(student)} · Section ${student.section_number ?? "?"}`))}<div class="dialog-body seat-card-details">${cardBody(card)}${cardActions(card)}</div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Close</button></footer></div>`);
    bindCardActions(document.querySelector("#seating-card-details"), student, whereIs(student), card, () => document.querySelector("#dialog").close());
  }

  function loadCardLink(pop, student, card) {
    // The chart payload never carries card tokens; the link is fetched for the one open seat and copied from there.
    const text = pop.querySelector("[data-pop-link-text]");
    const copy = pop.querySelector("[data-pop-copy]");
    api(`/api/response-cards/cards/${encodeURIComponent(card.card_id)}/link`).then((result) => {
      if (!pop.isConnected) return;
      text.textContent = result.link;
      copy.disabled = false;
      copy.onclick = async () => {
        try {
          await navigator.clipboard.writeText(result.link);
          copy.textContent = "Copied";
          toast(`Card link for ${student.name} copied.`);
          setTimeout(() => { if (copy.isConnected) copy.textContent = "Copy"; }, 2000);
        } catch {
          const range = document.createRange();
          range.selectNodeContents(text);
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          toast("The browser refused clipboard access; the link is selected, so copy it with Ctrl+C or ⌘C.", true);
        }
      };
    }).catch((cause) => {
      if (pop.isConnected) text.textContent = cause.message;
    });
  }

  function resendLabel(card) {
    return ["sent", "failed", "unconfirmed"].includes(card.delivery) ? "Resend email" : "Email the link";
  }

  function defaultMailLabel(mail) {
    return mail.provider === "resend" ? "Resend (default)" : "Faculty email (default)";
  }

  function gmailLabel(mail) {
    const address = mail.gmail?.email || "";
    return `${address.toLowerCase().endsWith("@tamu.edu") ? "TAMU Gmail" : "Gmail"} — ${address}`;
  }

  async function openResend(student, where, card) {
    // Only a published card still awaiting a response is offered; the server re-checks before queueing.
    const intent = captureDialogIntent(document.querySelector("#dialog"));
    let mail;
    try {
      mail = await api("/api/response-cards/mail");
    } catch (cause) {
      if (intent()) toast(cause.message, true);
      return;
    }
    if (!intent()) return;
    const again = resendLabel(card) === "Resend email";
    showDialog(`<form id="seating-resend">${dialogHeader(again ? `Resend the card email to ${escape(student.name)}?` : `Email the card link to ${escape(student.name)}?`, `${escape(where)} · Section ${student.section_number ?? "?"}`)}<div class="dialog-body"><p>${again ? "A second copy of the invitation goes to the student's roster address with the same personal link; the message says it is a copy of an earlier email." : "The invitation with the student's personal link goes to their roster address."} The Inbox worker sends it within about a minute, and this chart shows the delivery on refresh.</p>${card.delivery === "unconfirmed" ? '<p class="room-import-note">The previous email’s delivery could not be confirmed. Check with the student before sending another copy.</p>' : ""}<label class="field">Send from<select name="mail_provider" aria-label="Send from"><option value="default">${escape(defaultMailLabel(mail))}</option>${mail.gmail?.connected ? `<option value="gmail">${escape(gmailLabel(mail))}</option>` : ""}</select></label><p class="field-hint">This choice applies to this email only.</p><p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">${again ? "Resend email" : "Send email"}</button></footer></form>`);
    const form = document.querySelector("#seating-resend");
    const active = dialogSession(form, document.querySelector("#dialog"));
    form.onsubmit = (event) => {
      event.preventDefault();
      perform(form, async () => {
        await api(`/api/response-cards/cards/${encodeURIComponent(card.card_id)}/resend`, "POST", { mail_provider: form.elements.mail_provider.value });
        if (active()) document.querySelector("#dialog").close();
        toast(`Email to ${student.name} queued to send.`);
        await render(true);
      });
    };
  }

  function printExports(current) {
    // Jobs from one confirmation share a request prefix; keep the newest export first, sections in order within it.
    const groups = new Map();
    current.print_batches.forEach((batch) => {
      if (!groups.has(batch.export)) groups.set(batch.export, []);
      groups.get(batch.export).push(batch);
    });
    return [...groups.values()].map((batches) => batches.sort((a, b) => a.section_number - b.section_number));
  }

  function exportBlock(batches) {
    // One export as a table: the whole room in seat order, then each section's own job, with the same stamps on both.
    // The files come from print-files.js, the renderer the print job dialog, the Print jobs list and the assignment
    // window use, so every page names the files and label media the same way (and remembers the media last used).
    const first = batches[0];
    const jobs = batches.map((batch) => ({ ...batch, stamps_only: isStampsOnly({ label: batch.version_label }) }));
    const current = jobs.every(downloadable);
    const superseded = jobs.every((batch) => batch.superseded_at);
    const copies = jobs.reduce((sum, batch) => sum + batch.quantity, 0);
    const roomStatus = current ? "Ready to print" : superseded ? "Superseded" : "Waiting for every section";
    const roomFiles = current ? exportFiles(chart.id, jobs, { compact: true }) : "—";
    const roomRow = `<tr class="seating-export-room"><th scope="row">Whole room <span class="seating-export-note">seat order</span></th><td>${roomStatus}</td><td>${copies.toLocaleString()}</td><td class="seating-export-files">${roomFiles}</td></tr>`;
    const sectionRows = jobs.map((batch) => {
      const status = stateLabel(batch.superseded_at ? "superseded" : batch.status);
      const toggle = ["pending", "failed"].includes(batch.status) ? ` <button type="button" class="text-button" data-seat-marks-batch="${batch.id}" data-enabled="${batch.seat_marks ? 1 : 0}">${batch.seat_marks ? "Remove seat numbers" : "Add seat numbers beside stamps"}</button>` : "";
      return `<tr class="seating-export-section" data-section="${batch.section_number}"><th scope="row">Section ${batch.section_number}</th><td>${escape(status)}${batch.seat_marks ? " · seat numbers beside stamps" : ""}${toggle}</td><td>${batch.quantity.toLocaleString()}</td><td class="seating-export-files">${jobFiles(batch, { compact: true }) || "—"}</td></tr>`;
    }).join("");
    return `<div class="seating-export"><p class="seating-export-head"><strong>Exported ${escape(when(first.created_at))}</strong> · version ${escape(first.version_label)}${first.seat_marks ? " · seat numbers beside stamps" : ""}</p><div class="seating-export-scroll" tabindex="0" role="region" aria-label="Print files exported ${escape(when(first.created_at))}"><table class="seating-export-table"><thead><tr><th scope="col">Files</th><th scope="col">Status</th><th scope="col">Copies</th><th scope="col">Download</th></tr></thead><tbody>${roomRow}${sectionRows}</tbody></table></div></div>`;
  }

  function printJobsMarkup(current) {
    const exports = printExports(current);
    if (!exports.length) return "";
    const [latest, ...earlier] = exports;
    return `<section class="course-panel seating-print-jobs" id="seating-print-jobs" tabindex="-1"><h3>Print jobs from this chart</h3><p class="field-hint seating-export-caution"><strong>Print either the whole-room files or the section files, never both:</strong> they carry the same stamps.</p>${exportBlock(latest)}${earlier.length ? disclosureMarkup({ label: `Earlier exports (${earlier.length})`, body: earlier.map(exportBlock).join(""), className: "seating-earlier-exports" }) : ""}</section>`;
  }

  function actionButton(skip, action, label, className = "button") {
    // The header carries the chart's next step; the same action is not repeated in the groups below.
    return action === skip ? "" : `<button type="button" class="${className}" data-seating-action="${action}">${label}</button>`;
  }

  function actionGroup(key, title, body, level = "h3") {
    return body ? `<section class="seating-action-group" data-group="${key}" aria-labelledby="seating-actions-${key}"><${level} id="seating-actions-${key}">${title}</${level}>${body}</section>` : "";
  }

  function actionsMarkup(next) {
    const skip = next.action;
    const confirmed = Boolean(chart.confirmed_at);
    const seats = `<div class="seating-action-buttons">${actionButton(skip, "update", "Update seating")}${chart.exam_version_count ? "" : actionButton(skip, "assign-versions", "Assign exam versions")}<button type="button" class="danger-button seating-regenerate" data-seating-action="regenerate">Regenerate</button></div>`;
    // The stamped papers lead the Print group once the latest export's files are ready (the room in seat order, the same files as
    // "Print jobs from this chart" below); until then the way to them, the assignment window at this chart. The exam-day packet
    // holds the chart and the roster; the room handouts follow on their own row.
    const latest = chart.print_batches?.length ? printExports(chart)[0] : null;
    const papersReady = Boolean(latest?.length) && latest.every(downloadable);
    const wizardLink = `<a class="link-arrow" href="#documents?chart=${encodeURIComponent(chart.id)}">Print exams for this room</a>`;
    const row = (label, body, className = "") => body ? `<div class="seating-action-row${className ? ` ${className}` : ""}">${label ? `<span class="seating-action-label">${label}</span>` : ""}<div class="seating-action-buttons">${body}</div></div>` : "";
    const jobs = papersReady ? latest.map((batch) => ({ ...batch, stamps_only: isStampsOnly({ label: batch.version_label }) })) : [];
    // The same files as the room row under "Print jobs from this chart": print-files.js names their "Other formats" menu by the
    // first job, so this copy takes its own popover id (two menus with one id would open the first, far above the room row's).
    const papers = papersReady
      ? row(jobs.every((batch) => batch.stamps_only) ? "Stamp labels for this room" : "Stamped papers for this room",
        `${exportFiles(chart.id, jobs, { compact: true }).replaceAll('"files-room-', '"files-print-room-')}<button type="button" class="text-button" data-print-jobs-jump>All print files ↓</button>`, "seating-print-papers")
      : row("", wizardLink, "seating-print-papers");
    const handouts = `${actionButton(skip, "print-roster", "Print room roster")}${chart.exam_version_count ? actionButton(skip, "print-versions", "Print version list") : ""}`;
    const print = `${papers}${row("", `${confirmed ? "" : actionButton(skip, "confirm", "Confirm for printing")}${actionButton(skip, "print", `${icon("download")}Print chart`)}${actionButton(skip, "print-packet", "Print exam-day packet")}`)}${row("Room handouts", handouts)}${confirmed ? `<div class="seating-action-reexport">${actionButton("", "confirm", "Export again as new print jobs")}<span class="field-hint">New stamps for every copy</span>${papersReady ? wizardLink : ""}</div>` : ""}`;
    return `<div class="seating-actions" aria-label="Chart actions">${actionGroup("seats", "Seats", seats)}${actionGroup("print", "Print", print)}</div>`;
  }

  function emailGroup(next, hasSeatUpdates) {
    // The email actions sit with the notices they send (response cards, personal PDFs, updated seat cards).
    const skip = next.action;
    const confirmed = Boolean(chart.confirmed_at);
    const pending = chart.seat_updates?.pending || 0;
    const row = (label, body) => `<div class="seating-action-row"><span class="seating-action-label">${label}</span><div class="seating-action-buttons">${body}</div></div>`;
    const email = [
      row("Response-card invitations", confirmed ? `${actionButton(skip, "gmail-send", "Send with Gmail")}${actionButton(skip, "mail-merge", "Gmail mail merge")}` : '<span class="field-hint">After confirmation</span>'),
      row("Personal seat charts (PDF)", actionButton(skip, "student-email", "Email student charts")),
      confirmed ? row("Updated seat cards", hasSeatUpdates ? (skip === "seat-updates" ? '<span class="field-hint">Your next step, above</span>' : `<button type="button" class="button" data-seating-action="seat-updates">Send updated seat cards${pending ? ` (${pending})` : ""}</button>`) : '<span class="field-hint">None waiting</span>') : "",
    ].join("");
    return actionGroup("email", "Email students", email, "h4");
  }

  function noticeRow(kind, item) {
    // Counts per notice kind; answers flagged for review filter the student table (card issues).
    const flagged = item.flagged ? ` · <button type="button" class="text-button seating-flagged-filter" data-notice-filter="issue" title="Show these students in the table">${item.flagged} flagged for review</button>` : "";
    return `<li><strong>${kind === "seat" ? "Seat assignment notices" : "DR testing notices"}</strong> · ${item.recipients} card${item.recipients === 1 ? "" : "s"} · ${item.published ? `${item.published} published` : "not published"}${item.emailed ? ` · ${item.emailed} emailed` : ""}${item.queued ? ` · ${item.queued} email${item.queued === 1 ? "" : "s"} waiting to send` : ""}${item.responded ? ` · ${item.responded} responded` : ""}${flagged} · <a href="#response-cards/issue/${encodeURIComponent(item.issue_id)}">Open in Response Cards</a></li>`;
  }

  function noticesMarkup(next, hasSeatUpdates, exam) {
    const cards = chart.response_cards;
    const prepared = cards && (cards.seat || cards.dr || cards.errors?.length);
    const state = !prepared ? "" : cards.emailed ? "Published and emailed to each student." : cards.published ? "Published to the response service; send the invitations below." : "Cards stay in Faculty until you publish them from Response Cards; nothing has been sent to students.";
    const help = cards?.emailed
      ? "<p>Card status shows on the seating chart and in the student table. A confirmation email goes back to each student when they complete their card.</p>"
      : "<p>Send invitations with Gmail, use Gmail mail merge, or share each student's link from Response Cards. Faculty tracks direct Gmail delivery; mail sent manually from a spreadsheet is not tracked.</p>";
    const summary = prepared
      ? `<div class="seating-cards"><p class="seating-cards-lead">Prepared ${escape(when(cards.prepared_at))}${exam ? ` for ${escape(exam.date)}${exam.time ? ` at ${escape(exam.time)}` : ""}` : ""}. ${state}</p><ul class="seating-notice-counts">${["seat", "dr"].filter((kind) => cards[kind]).map((kind) => noticeRow(kind, cards[kind])).join("")}</ul>${(cards.errors || []).map((item) => `<p class="room-import-note">${escape(item)}</p>`).join("")}${disclosureMarkup({ label: "How delivery works", body: `${help}<p>Personal seat charts are separate PDFs with the student's own seat marked; they need no response card. Updated seat cards replace the card of a student moved after confirmation.</p>`, className: "seating-delivery-help" })}</div>`
      : '<p class="field-hint seating-cards-lead">Response cards are prepared when you confirm the chart for printing.</p>';
    return `<section class="course-panel seating-notices-panel" aria-labelledby="seating-notices-title"><h3 id="seating-notices-title">Notices to students</h3>${summary}${emailGroup(next, hasSeatUpdates)}</section>`;
  }

  function statTile(filter, className, count, label) {
    // A problem count is a filter for the student table and the seats (browser memory only).
    return `<button type="button" class="room-stat ${className}" data-student-filter="${filter}" aria-pressed="${studentFilter === filter}" title="Show only these students"><strong>${count}</strong> ${label}</button>`;
  }

  function applyStudentFilter(root) {
    // Hide table rows outside the filter or the search; outline (never fill) the seats of the students shown by a filter.
    const query = (root.querySelector("#seating-search")?.value || "").trim().toLowerCase();
    const byId = new Map(chart.students.map((item) => [item.enrollment_id, item]));
    let visible = 0;
    root.querySelectorAll(".seating-table tbody tr").forEach((row) => {
      const student = byId.get(row.dataset.student);
      row.hidden = !(student && matchesFilter(student, studentFilter)) || (Boolean(query) && !row.dataset.search.includes(query));
      if (!row.hidden) visible += 1;
    });
    root.querySelectorAll(".seat-highlight").forEach((seat) => seat.classList.remove("seat-highlight"));
    if (studentFilter !== "all") {
      chart.students.filter((item) => item.seat_id && matchesFilter(item, studentFilter)).forEach((item) => {
        root.querySelector(`.seat-chart [data-seat="${CSS.escape(item.seat_id)}"]`)?.classList.add("seat-highlight");
      });
    }
    root.querySelectorAll("[data-student-filter]").forEach((tile) => tile.setAttribute("aria-pressed", String(tile.dataset.studentFilter === studentFilter)));
    root.querySelectorAll('.seating-student-filters [data-filter]').forEach((chip) => chip.setAttribute("aria-pressed", String(chip.dataset.filter === studentFilter)));
    const empty = root.querySelector(".seating-filter-empty");
    if (empty) empty.hidden = visible > 0;
    return { query, visible };
  }

  function setStudentFilter(root, value) {
    studentFilter = value || "all";
    applyStudentFilter(root);
  }

  function locateSearchedSeat(root, query, visible) {
    // "Where is X sitting?": when the search leaves exactly one seated student, bring their seat into view and pulse it.
    const row = visible === 1 && query ? [...root.querySelectorAll(".seating-table tbody tr")].find((item) => !item.hidden) : null;
    const student = row && chart.students.find((item) => item.enrollment_id === row.dataset.student);
    const seatId = student?.seat_id && student.status !== "dr_testing" ? student.seat_id : "";
    if (!seatId || seatId === foundSeat) { if (!seatId) foundSeat = ""; return; }
    foundSeat = seatId;
    const seat = root.querySelector(`.seat-chart [data-seat="${CSS.escape(seatId)}"]`);
    if (!seat) return;
    seat.scrollIntoView({ block: "nearest", inline: "center", behavior: scrollBehavior() });
    seat.classList.remove("seat-found");
    void seat.offsetWidth;
    seat.classList.add("seat-found");
    setTimeout(() => seat.classList.remove("seat-found"), 2400);
  }

  // Chart ↔ exam hub: the exam record of this assignment and term when one exists, else the assessment's hub. Read with the
  // chart's link (GET /api/exams?summary=0, a fresh read each time, the result kept a minute per chart in examDetails); the list
  // names the main sitting's date as main_date, only to note a difference.
  const examDetails = new Map();

  function examLinkMarkup(current) {
    const found = examDetails.get(`${current.assignment_id}|${current.term_id}`);
    if (!found) return "";
    const date = String(current.exam_date || "").slice(0, 10);
    const differs = found.date && date && found.date !== date;
    return ` · <a class="link-arrow" href="${escape(found.href)}">Exam: ${escape(found.title)}</a>${differs ? ` <span class="seating-exam-mismatch" title="The exam record's main sitting and this chart's response-card date differ.">(exam record says ${escape(wallDate(found.date))})</span>` : ""}`;
  }

  async function showExamLink(root, current) {
    const key = `${current.assignment_id}|${current.term_id}`;
    const cached = examDetails.get(key);
    if (cached && Date.now() - cached.at < 60000) return;
    const exams = await api("/api/exams?summary=0").then((result) => result.exams || []).catch(() => null);
    if (!exams) return;
    const event = exams.find((item) => item.assignment_id === current.assignment_id && item.term_id === current.term_id);
    const date = String(event?.main_date || "").slice(0, 10);
    examDetails.set(key, {
      at: Date.now(), date, title: event?.title || current.title,
      href: event ? `#exams/${encodeURIComponent(event.id)}` : `#exams/assessment/${encodeURIComponent(current.assignment_id)}/${encodeURIComponent(current.term_id)}`,
    });
    const host = root.querySelector(".seating-exam-link");
    if (host && chart?.id === current.id) host.innerHTML = examLinkMarkup(chart);
  }

  function runNextAction(root, next) {
    if (interactions.moveRequest || interactions.versionRequest) return;
    clearMoveSelection();
    clearVersionSelection();
    if (next.action === "room") {
      location.hash = `#rooms/${encodeURIComponent(chart.room_id)}`;
      return;
    }
    chartAction(root, next.action).catch((cause) => toast(cause.message, true));
  }

  function renderDetail(root) {
    const oldSearch = root.querySelector("#seating-search");
    const keepSearch = filterChart === chart.id;
    const query = keepSearch ? oldSearch?.value || "" : "";
    const focused = keepSearch && document.activeElement === oldSearch;
    const selection = focused ? [oldSearch.selectionStart, oldSearch.selectionEnd] : null;
    conflictSeats = new Set((chart.exam_versions?.conflicts || []).flatMap((item) => item.seats));
    clearMoveSelection();
    clearVersionSelection();
    root.classList.toggle("seating-version-mode", versionView);
    if (filterChart !== chart.id) { filterChart = chart.id; studentFilter = "all"; foundSeat = ""; }
    const live = chart.live_counts;
    const { roomReview, rosterIssues } = chartProblems(chart);
    const cardCounts = { complete: 0, pending: 0, issue: 0, update_pending: 0 };
    chart.students.forEach((item) => { if (item.card) cardCounts[item.card.state] += 1; });
    const hasCards = Object.values(cardCounts).some((count) => count > 0);
    const unseated = chart.students.filter((item) => item.status === "unseated");
    const hasSeatUpdates = Boolean(chart.confirmed_at && (chart.seat_updates?.pending || seatUpdateRequests.has(chart.id)));
    const next = nextAction(chart, { seatUpdateRequested: seatUpdateRequests.has(chart.id) });
    const exam = examTiming(chart);
    const others = drOthers(chart);
    const name = `${chart.title} · ${chart.room_name}`;
    header?.set({
      route: location.hash, title: name, context: scope(chart), subtitle: "",
      crumbs: [{ label: "Home", href: "#overview" }, { label: "Seating charts", href: "#seating" }, { label: name }],
      action: { label: next.label, icon: next.icon, run: () => runNextAction(document.querySelector("#seating-section"), next) },
    });
    const filterOptions = studentFilterOptions(chart);
    if (!filterOptions.some((option) => option.value === studentFilter)) studentFilter = "all";
    const drElsewhere = others.rest.length ? `<br><span class="seating-dr-elsewhere">DR bookings for other exams in these sections: ${others.rest.map((item) => `${escape(item.title)} ${item.students}`).join(" · ")}</span>` : "";
    const drCallout = others.auto.length ? calloutMarkup({ tone: "warning", className: "seating-dr-callout", title: `DR bookings may belong to ${chart.title}`,
      body: `${others.auto.map((item) => `${item.title} (created from the DR export) · ${plural(item.students, "student")}`).join("; ")}. If it is the same exam, link those bookings so the students are not seated.`,
      action: { label: "Link in Chart settings", data: { seatingSettings: "" } } }) : "";
    const hint = versionView ? "Click a seat to set its version, or a second seat to swap their versions."
      : hasCards ? "Click a seat for its card details and Move or swap, then click the destination seat."
        : "Click a student, then a seat to move or swap them; click a free seat to seat someone without one.";
    // Back to where the chart was opened from: the exam's rooms when its tile opens them, else the list.
    const rooms = chartGroups(getCharts().filter((item) => item.assignment_id === chart.assignment_id && item.term_id === chart.term_id))[0];
    const back = rooms && hasRoomsPage(rooms) ? `<a class="text-button" href="${examHref(chart)}">${icon("arrow-left")}All rooms for ${escape(chart.title)}</a>` : `<a class="text-button" href="#seating">${icon("arrow-left")}All seating charts</a>`;
    root.innerHTML = `<nav class="room-breadcrumbs" aria-label="Seating navigation">${back}</nav>
      <section class="seating-workflow${next.attention ? " needs-attention" : ""}" aria-labelledby="seating-next-title"><div><p class="seating-workflow-scope"><a href="#rooms/${encodeURIComponent(chart.room_id)}">${escape(chart.room_name)}</a>${exam ? ` · <span class="seating-exam-date">Exam ${escape(exam.short)}</span>` : ""}<span class="seating-exam-link">${examLinkMarkup(chart)}</span></p><h2 id="seating-next-title">${escape(next.title)}</h2><p class="supporting-copy">${escape(next.detail)}</p></div><div class="seating-workflow-action"><button type="button" class="text-button" data-seating-settings>${icon("edit")}Chart settings</button></div></section>
      <section class="room-summary" aria-label="Seating summary"><div class="room-stat"><strong>${live.seated}</strong>seated</div>${statTile("dr", "left", live.excluded_dr, "DR testing")}${statTile("unseated", live.unseated ? "broken" : "", live.unseated, "without a seat")}<div class="room-stat"><strong>${live.free_seats}</strong>free of ${live.usable_seats} usable</div><div class="room-stat ${chart.spacing.crowded ? "blocked" : ""}"><strong>${chart.spacing.spaced}</strong>with an empty seat beside</div>${hasCards ? `<div class="room-stat card-complete"><strong>${cardCounts.complete}</strong>${cardCounts.complete === 1 ? "card" : "cards"} completed</div>${statTile("pending", "card-pending", cardCounts.pending, cardCounts.pending === 1 ? "card pending" : "cards pending")}${cardCounts.update_pending ? statTile("update_pending", "card-update-pending", cardCounts.update_pending, cardCounts.update_pending === 1 ? "seat update not sent" : "seat updates not sent") : ""}${statTile("issue", cardCounts.issue ? "broken" : "", cardCounts.issue, cardCounts.issue === 1 ? "card issue" : "card issues")}` : ""}<details class="seating-provenance"><summary>Chart details and generation checks</summary><div class="room-source"><strong>${escape(chart.room_name)}</strong> · ${escape(scope(chart))}${chart.pool_rooms?.length ? `<br>These sections' students are mixed with ${escape(chart.pool_rooms.join(", "))}; this chart lists only the students seated here${chart.pool_dr_room ? `, and the ${escape(chart.pool_dr_room)} chart lists the DR students` : ""}.` : ""}<br>DR exam bookings for <strong>${escape(chart.title)}</strong>: ${chart.dr.excluded_students} student${chart.dr.excluded_students === 1 ? "" : "s"} not seated · generated ${escape(when(chart.generated_at))}${drElsewhere}${chart.confirmed_at ? `<br><strong>Confirmed for printing ${escape(when(chart.confirmed_at))}</strong> (${chart.confirmed_mode === "stamps_only" ? "stamps only" : "exam PDF"}). Printed stamps will not follow later seat changes; removing this chart needs typed confirmation.` : ""}<br>Roster check at generation${chart.checked_at ? ` (${escape(when(chart.checked_at))})` : ""}: ${!chart.checked?.counts ? "not recorded — Regenerate or Update seating to record one." : chart.checked.ok ? `passed — ${chart.checked.students} roster students, ${chart.checked.excluded_dr} with DR bookings kept out, everyone else seated.` : `<strong>${escape(flagText(chart.checked.counts))}</strong>`}<br>${chart.spacing.crowded ? `${chart.spacing.crowded} student${chart.spacing.crowded === 1 ? " sits" : "s sit"} beside another student; this room can space at most ${chart.spacing.spaced_capacity}.` : "Every seated student has an empty seat on either side."}${chart.print_jobs ? `<br>${chart.print_jobs} print job${chart.print_jobs === 1 ? "" : "s"} with page stamps exist${chart.print_jobs === 1 ? "s" : ""} for this exam; removing the chart needs typed confirmation.` : ""}${chart.notes ? `<br>${escape(chart.notes)}` : ""}</div></details></section>
      ${drCallout}
      ${chart.room.notes ? calloutMarkup({ tone: "info", className: "seating-room-note", title: `About ${chart.room_name}`, body: chart.room.notes }) : ""}
      ${roomReview.length ? `<section class="room-review" role="alert"><strong>Check this room before exam seating</strong><ul>${roomReview.map((reason) => `<li>${escape(reason)}</li>`).join("")}</ul><a class="text-button" href="#rooms/${encodeURIComponent(chart.room_id)}">Open room to review seat labels and chair locations</a></section>` : ""}
      ${rosterIssues.length ? `<section class="callout seating-issues" data-tone="warning" role="alert"><strong class="callout-title">What the roster check found</strong><ul>${rosterIssues.map((item) => `<li>${escape(item)}</li>`).join("")}</ul><button type="button" class="button" data-seating-action="update">Update seating</button></section>` : ""}
      ${actionsMarkup(next)}
      ${versionStatusMarkup()}<section class="course-panel room-chart-panel seating-view${versionView ? " version-view" : ""}" aria-labelledby="seating-chart-title"><div class="seating-chart-heading"><h3 id="seating-chart-title">Seating plan<span class="sr-only"> · ${escape(name)}</span></h3><button type="button" class="button" data-seating-action="version-view" aria-pressed="${versionView}">${versionView ? "Student view" : "Version view"}</button></div><p class="field-hint seating-print-note">${escape(hint)}</p><div class="seat-legend" aria-label="Legend">${legendStates(chart).map(([className, label]) => `<span><i${className ? ` class="${className}"` : ""}></i>${label}</span>`).join("")}</div><div class="seat-chart">${renderSeatGrid(chart.room, { escape, seatButton, tableTitle, label: `${chart.title} seating chart` })}</div></section>
      <section class="course-panel seating-students"><h3>Students <span class="count-badge">${chart.students.length}</span></h3><div class="seating-students-tools">${filterChipsMarkup("seating-students", filterOptions, studentFilter, { label: "Show students" }).replace('class="filter-chips"', 'class="filter-chips seating-student-filters"')}<label class="field roster-search">Find a student<input type="search" id="seating-search" data-page-search placeholder="Name or seat" autocomplete="off"></label></div><div class="roster-table-scroll" tabindex="0" role="region" aria-label="Seat assignments"><table class="roster-table seating-table"><thead><tr><th scope="col">Student</th><th scope="col">Section</th><th scope="col">Seat</th><th scope="col">Status</th>${hasCards ? '<th scope="col">Response card</th>' : ""}<th scope="col" aria-label="Actions"></th></tr></thead><tbody>${chart.students.map((item) => `<tr data-student="${escape(item.enrollment_id)}" data-search="${escape(`${item.name} ${item.seat || ""} ${STATUS[item.status]} ${item.card?.summary || ""}`.toLowerCase())}"><td>${escape(item.name)}</td><td>${item.section_number}</td><td>${escape(item.seat || "—")}</td><td><span class="seating-status ${item.status}">${STATUS[item.status]}</span></td>${hasCards ? `<td>${cardCell(item)}</td>` : ""}<td>${item.status === "dr_testing" ? "" : `<button type="button" class="text-button" data-move="${escape(item.enrollment_id)}">${item.seat ? "Move" : "Seat"}</button>`}</td></tr>`).join("")}</tbody></table></div><p class="seating-filter-empty field-hint" hidden>No students match. <button type="button" class="text-button" data-clear-student-filter>Show all students</button></p>${unseated.length ? `<p class="field-hint">${unseated.length} student${unseated.length === 1 ? " has" : "s have"} no seat yet. Use Update seating or seat them by hand.</p>` : ""}</section>
      ${noticesMarkup(next, hasSeatUpdates, exam)}
      ${chart.print_batches?.length ? printJobsMarkup(chart) : ""}
      <section class="course-panel seating-history"><h3>History</h3><div class="room-imports">${chart.events.map((event) => `<p><strong>${escape(when(event.created_at))}</strong> · ${escape(describe(event))}</p>`).join("") || "<p>No changes recorded.</p>"}</div></section>`;
    void showExamLink(root, chart);
    root.querySelectorAll("[data-seating-settings]").forEach((button) => {
      button.onclick = () => {
        if (interactions.moveRequest || interactions.versionRequest) return;
        void openSettings();
      };
    });
    const workspace = document.createElement("div");
    workspace.className = "seating-version-workspace";
    const seatChart = root.querySelector(".seat-chart");
    seatChart.before(workspace);
    workspace.appendChild(seatChart);
    placeSeatGrid(root, chart.room);
    const moveNotice = document.createElement("div");
    moveNotice.className = "seating-move-selection";
    moveNotice.hidden = true;
    moveNotice.innerHTML = '<p id="seating-move-instructions" role="status"></p><button type="button" class="button" data-cancel-move>Cancel move</button>';
    root.querySelector(".seat-chart").before(moveNotice);
    moveNotice.querySelector("[data-cancel-move]").onclick = () => {
      if (!interactions.moveRequest) clearMoveSelection(true);
    };
    root.querySelectorAll("[data-seating-action]").forEach((button) => {
      button.onclick = () => {
        if (interactions.moveRequest || interactions.versionRequest) return;
        clearMoveSelection();
        clearVersionSelection();
        chartAction(root, button.dataset.seatingAction).catch((cause) => toast(cause.message, true));
      };
    });
    root.querySelector(".seat-grid").addEventListener("click", (event) => {
      const button = event.target.closest("[data-seat]");
      if (!button) return;
      const seat = chart.room.seats.find((item) => item.id === button.dataset.seat);
      if (!seat) return;
      if (interactions.moveRequest || interactions.versionRequest) return;
      if (versionView) {
        selectVersionSeat(root, button, seat);
        return;
      }
      if (interactions.moveSelection) void moveToSeat(seat);
      else if (seat.student?.card) toggleSeatPopover(root, button, seat);
      else if (seat.student) openMove(chart.students.find((item) => item.enrollment_id === seat.student.enrollment_id) || { enrollment_id: seat.student.enrollment_id, name: seat.student.name, seat: seat.label });
      else if (!seat.broken && !seat.blocked) openSeatHere(seat);
      else toast(`Seat ${seat.label} is ${seat.broken ? "broken" : "blocked"}.`, true);
    });
    root.querySelectorAll("[data-move]").forEach((button) => {
      button.onclick = () => openMove(chart.students.find((item) => item.enrollment_id === button.dataset.move));
    });
    root.querySelectorAll("[data-card-details]").forEach((button) => {
      button.onclick = () => {
        clearMoveSelection();
        openCardDetails(chart.students.find((item) => item.enrollment_id === button.dataset.cardDetails));
      };
    });
    root.querySelectorAll("[data-seat-marks-batch]").forEach((button) => {
      // A job still in the print queue can gain or lose the visible seat label before the worker renders it.
      button.onclick = async () => {
        setBusy(button, true);
        try {
          const enabled = button.dataset.enabled !== "1";
          await api(`/api/documents/batches/${encodeURIComponent(button.dataset.seatMarksBatch)}/seat-marks`, "POST", { seat_marks: enabled });
          toast(enabled ? "Seat numbers will be printed beside the stamps when the job renders." : "Seat numbers will not be printed beside the stamps.");
          await render(true);
        } catch (cause) {
          setBusy(button, false);
          toast(cause.message, true);
        }
      };
    });
    root.querySelectorAll("[data-student-filter]").forEach((tile) => {
      tile.onclick = () => setStudentFilter(root, studentFilter === tile.dataset.studentFilter ? "all" : tile.dataset.studentFilter);
    });
    root.querySelector(".seating-student-filters")?.addEventListener("click", (event) => {
      const chip = event.target.closest("[data-filter]");
      if (chip) setStudentFilter(root, chip.dataset.filter);
    });
    root.querySelector("[data-print-jobs-jump]")?.addEventListener("click", () => {
      const jobs = root.querySelector("#seating-print-jobs");
      jobs?.scrollIntoView({ block: "start", behavior: scrollBehavior() });
      jobs?.focus({ preventScroll: true });
    });
    root.querySelectorAll("[data-notice-filter]").forEach((button) => {
      button.onclick = () => {
        setStudentFilter(root, button.dataset.noticeFilter);
        const table = root.querySelector(".seating-students");
        table?.scrollIntoView({ block: "start", behavior: scrollBehavior() });
      };
    });
    root.querySelector("[data-clear-student-filter]")?.addEventListener("click", () => {
      const field = root.querySelector("#seating-search");
      if (field) field.value = "";
      setStudentFilter(root, "all");
    });
    if (outsideClick) document.removeEventListener("click", outsideClick);
    outsideClick = (event) => {
      if (!event.target.closest(".seat-popover, .seat")) {
        closeSeatPopover(root);
        if (!interactions.versionRequest) clearVersionSelection();
      }
    };
    document.addEventListener("click", outsideClick);
    root.onkeydown = (event) => {
      if (event.key !== "Escape") return;
      if (interactions.versionSelection) {
        if (!interactions.versionRequest) clearVersionSelection(true);
        event.preventDefault();
        event.stopPropagation();
      } else if (interactions.moveSelection) {
        if (!interactions.moveRequest) clearMoveSelection(true);
        event.preventDefault();
        event.stopPropagation();
      } else if (root.querySelector(".seat-popover")) {
        const button = root.querySelector('.seat[aria-expanded="true"]');
        closeSeatPopover(root);
        button?.focus({ preventScroll: true });
        event.stopPropagation();
      }
    };
    if (interactions.moveRequest || interactions.versionRequest) setMoveBusy(root, true);
    const search = root.querySelector("#seating-search");
    search.value = query;
    search.oninput = () => {
      const { query, visible } = applyStudentFilter(root);
      locateSearchedSeat(root, query, visible);
    };
    applyStudentFilter(root);
    if (focused) {
      search.focus({ preventScroll: true });
      search.setSelectionRange(...selection);
    }
    if (!chart.students.length) root.querySelector(".seating-students").hidden = true;
  }

  function describe(event) {
    const detail = event.detail || {};
    if (event.kind === "exam_versions_assigned") return `Assigned exam versions${detail.count ? ` A–${String.fromCharCode(64 + detail.count)}` : ""} to occupied seats.`;
    if (event.kind === "exam_version_changed") return "Changed exam versions at the selected seats.";
    if (event.kind === "seat_update_send") return `Queued ${detail.receipt?.queued ?? 0} updated seat email${detail.receipt?.queued === 1 ? "" : "s"}.${detail.receipt?.errors?.length ? ` ${detail.receipt.errors.length} issue${detail.receipt.errors.length === 1 ? " needs" : "s need"} attention.` : ""}`;
    if (event.kind === "gmail_send") return `Queued ${detail.receipt?.queued ?? 0} response-card invitation${detail.receipt?.queued === 1 ? "" : "s"} through Gmail (${detail.receipt?.sender || "connected account"}).`;
    if (event.kind === "student_chart_email") return `Queued ${detail.receipt?.queued ?? 0} personal seating chart email${detail.receipt?.queued === 1 ? "" : "s"}.`;
    if (event.kind === "regenerate") return `Generated: ${detail.newly_seated} seated at random${detail.counts?.adjacent_pairs ? `, ${detail.counts.spaced} with an empty seat beside them` : " with an empty seat beside each"}, ${detail.excluded_dr?.length || 0} left out for DR testing.${detail.check ? ` Roster check ${detail.check.ok ? "passed" : "flagged " + flagText(detail.check.counts)}.` : ""}`;
    if (event.kind === "update") return `Updated: ${detail.kept} kept their seats, ${detail.newly_seated} newly seated, ${detail.removed?.length || 0} removed${detail.removed?.length ? ` (${[...new Set(detail.removed.map((item) => item.reason))].join("; ")})` : ""}.${detail.check ? ` Roster check ${detail.check.ok ? "passed" : "flagged " + flagText(detail.check.counts)}.` : ""}`;
    if (event.kind === "move") return `Moved a student to ${detail.seat}${detail.swapped_with ? ", swapping with the seat's occupant" : ""}.`;
    if (event.kind === "confirmed") return `Confirmed and exported to printing (${detail.mode === "stamps_only" ? "stamps only" : "exam PDF"}): ${(detail.batches || []).map((item) => `Section ${item.section_number} · ${item.quantity} copies`).join(", ")}${detail.acknowledged_issues?.length ? ` — ${detail.acknowledged_issues.length} issue${detail.acknowledged_issues.length === 1 ? "" : "s"} acknowledged` : ""}.${detail.response_cards?.seat || detail.response_cards?.dr ? ` Response cards prepared: ${[detail.response_cards.seat ? `${detail.response_cards.seat.recipients} seat` : "", detail.response_cards.dr ? `${detail.response_cards.dr.recipients} DR` : ""].filter(Boolean).join(", ")} (${detail.response_cards.emailed ? "published and emailed" : detail.response_cards.published ? "published" : "not published"}).` : ""}`;
    if (event.kind === "relink") return `Linked ${detail.moved?.reduce((sum, item) => sum + item.bookings, 0) || 0} DR booking(s) from other exams to this exam.`;
    if (event.kind === "exam_changed") return `Attached to ${detail.to_title} (was ${detail.from_title}); ${detail.relinked_bookings || 0} DR booking(s) followed.`;
    return event.kind;
  }

  async function chartAction(root, action) {
    if (action === "version-view") {
      versionView = !versionView;
      renderDetail(root);
      return;
    }
    if (action === "assign-versions") {
      await openVersionAssign();
      return;
    }
    if (action === "print-roster") {
      await openRoomRosterPrint();
      return;
    }
    if (action === "print-versions") {
      await openVersionPrint();
      return;
    }
    if (action === "print-packet") {
      await openPacketPrint();
      return;
    }
    if (action === "seat-updates") {
      await openSeatUpdates(chart);
      return;
    }
    if (action === "gmail-send") {
      await openGmailSend(chart);
      return;
    }
    if (action === "student-email") {
      await openStudentChartMail(chart);
      return;
    }
    if (action === "mail-merge") {
      await openMailMerge(chart);
      return;
    }
    if (action === "print") {
      openPrint();
      return;
    }
    if (action === "confirm") {
      await openConfirm(root);
      return;
    }
    if (action === "regenerate") {
      const current = chart;
      const impact = reseatImpact(chart);
      // On a confirmed chart, say what reseating everyone changes; the server applies the same rules as for any move.
      const warning = impact ? calloutMarkup({ tone: "warning", className: "seating-regenerate-impact", title: "This chart is confirmed for printing",
        body: [impact.cards ? `Up to ${plural(impact.cards, "response card")} will be revoked, and each moved student then needs an updated seat card.` : "",
          impact.markedJobs ? `Seat numbers printed beside the stamps of ${plural(impact.markedJobs, "print job")} will no longer match.` : "",
          "Printed room charts will be out of date."].filter(Boolean).join(" ") }) : "";
      showDialog(`<form id="seating-regenerate">${dialogHeader("Regenerate the seating chart?", escape(chart.title))}<div class="dialog-body"><p>Every student gets a new random seat. Manual moves are discarded. Students with DR exam bookings stay unseated.</p>${warning}<p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Keep current seats</button><button type="submit" class="button danger">Regenerate</button></footer></form>`);
      const form = document.querySelector("#seating-regenerate");
      const active = dialogSession(form, document.querySelector("#dialog"));
      form.onsubmit = (event) => {
        event.preventDefault();
        perform(form, async () => {
          const saved = await api(`/api/seating/${encodeURIComponent(current.id)}/regenerate`, "POST", { version: current.version });
          if (active()) document.querySelector("#dialog").close();
          toast(saved.check?.ok ? "Seating regenerated. Roster check passed." : "Seating regenerated, but the roster check found problems.", !saved.check?.ok);
          adoptChart(saved, root);
          void refresh();
        });
      };
      return;
    }
    root.querySelectorAll("[data-seating-action]").forEach((button) => { setBusy(button, true); });
    const current = chart;
    try {
      const before = new Set((current.seat_updates?.students || []).map((item) => item.enrollment_id));
      const saved = await api(`/api/seating/${encodeURIComponent(current.id)}/update`, "POST", { version: current.version });
      // Name the students whose response cards the update made outdated (confirmed charts only).
      const outdated = (saved.seat_updates?.students || []).filter((item) => !before.has(item.enrollment_id)).map((item) => item.name);
      const cards = outdated.length ? ` Updated seat cards are now needed for ${outdated.length > 3 ? `${outdated.slice(0, 3).join("; ")} and ${outdated.length - 3} more` : outdated.join("; ")}.` : "";
      toast(`${saved.issues.length ? "Seating updated, but the roster check still finds problems." : "Seating updated. Roster check passed."}${cards}`, Boolean(saved.issues.length));
      adoptChart(saved, root);
    } finally {
      if (root.isConnected && chartId() === current.id) root.querySelectorAll("[data-seating-action]").forEach((button) => setBusy(button, false));
    }
    void refresh();
  }

  async function openSeatUpdates(current) {
    const chartPath = `/api/seating/${encodeURIComponent(current.id)}`;
    showDialog(`<div id="seating-seat-updates">${dialogHeader("Send updated seat cards", escape(`${current.title} · ${current.room_name}`))}<div class="dialog-body"><p>Review students whose seats changed after confirmation. Each replacement card shows their new seat and asks them to enter their UIN. It replaces their earlier card.</p><p class="field-hint">A moved seat stays orange until sending is confirmed. Queued emails are still orange. If an old link still needs revocation, Faculty must finish that before queueing its replacement.</p><div data-seat-update-preview aria-live="polite"></div><label class="field">Send from<select name="mail_provider" aria-label="Send from" disabled><option value="default">Faculty email (default)</option></select></label><p class="field-hint" data-seat-update-provider>This choice applies to these updated notices only.</p><section class="seating-merge-instructions" data-seat-update-card-section aria-labelledby="seating-update-message-title" hidden><h3 id="seating-update-message-title">Updated card preview</h3><div data-seat-update-card-preview></div><p class="field-hint">The email retains the UPDATED notice, a generic “Hello,” greeting and the student's new personal link, with no student name. Names in this review are not included in the email.</p></section><p class="form-error" role="alert" hidden></p><p class="field-hint" data-seat-update-result role="status"></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Close</button><button type="button" class="button" data-seat-update-refresh>Refresh preview</button><button type="button" class="button primary" data-seat-update-retry hidden>Retry request</button><button type="button" class="button primary" data-seat-update-send disabled>Send updated emails</button></footer></div>`);
    const host = document.querySelector("#seating-seat-updates"), dialog = document.querySelector("#dialog");
    const previewHost = host.querySelector("[data-seat-update-preview]"), provider = host.querySelector('[name="mail_provider"]');
    const providerHint = host.querySelector("[data-seat-update-provider]"), error = host.querySelector('[role="alert"]');
    const result = host.querySelector("[data-seat-update-result]"), send = host.querySelector("[data-seat-update-send]");
    const refreshPreview = host.querySelector("[data-seat-update-refresh]"), retry = host.querySelector("[data-seat-update-retry]");
    const cardSection = host.querySelector("[data-seat-update-card-section]"), cardPreview = host.querySelector("[data-seat-update-card-preview]");
    let preview = null, mail = null, requestId = "", loading = false, sending = false, previewGeneration = 0, queuedReceipt = null;
    const active = dialogSession(host, dialog);
    const waiting = (item) => ["queued", "sending"].includes(item.delivery);
    const readyToQueue = (item) => !waiting(item) && item.delivery !== "unconfirmed";
    function clearCardPreview() {
      cardSection.hidden = true;
      cardPreview.replaceChildren();
    }
    function drawCardPreview(data, students) {
      if (!students.length) { clearCardPreview(); return; }
      const selected = cardPreview.querySelector("select")?.value;
      cardPreview.innerHTML = `<label class="field">Preview updated card for<select aria-label="Preview updated card for">${students.map((item) => `<option value="${escape(item.enrollment_id)}">${escape(item.name)} · Seat ${escape(item.seat)} · Section ${escape(String(item.section_number))}</option>`).join("")}</select></label><div class="seating-update-message" aria-live="polite"></div>`;
      const select = cardPreview.querySelector("select"), message = cardPreview.querySelector(".seating-update-message");
      if (students.some((item) => item.enrollment_id === selected)) select.value = selected;
      const showCard = () => {
        const student = students.find((item) => item.enrollment_id === select.value);
        message.innerHTML = student ? `<p>Your seat for ${escape(data.title)} in ${escape(data.room_name)} has been updated to ${escape(student.seat)}.</p><p>Enter your UIN.</p>` : "";
      };
      select.onchange = showCard;
      showCard();
      cardSection.hidden = false;
    }
    function drawPreview(data) {
      const students = data.seat_updates?.students || [];
      drawCardPreview(data, students);
      const status = (item) => item.revocation_pending ? "Old link revocation pending" : ({queued: "Queued; sending not confirmed", sending: "Sending; confirmation pending", failed: "Sending failed", unconfirmed: "Sending unconfirmed", no_email: "No email address"}[item.delivery] || "Updated notice not sent");
      previewHost.innerHTML = `<div class="seating-merge-counts"><strong>${students.length} seat update${students.length === 1 ? "" : "s"} not sent</strong><span>${students.filter(waiting).length} already waiting to send</span></div>${students.some((item) => item.delivery === "unconfirmed") ? '<p class="room-import-note">For an unconfirmed delivery, check whether the student received it. If another copy is needed, open that student’s card details and choose Resend email.</p>' : ""}${students.length ? `<div class="data-table" tabindex="0" role="region" aria-label="Updated seat card recipients"><table><thead><tr><th scope="col">Student</th><th scope="col">Section</th><th scope="col">New seat</th><th scope="col">Status</th></tr></thead><tbody>${students.map((item) => `<tr><td>${escape(item.name)}</td><td>${escape(String(item.section_number))}</td><td>${escape(item.seat)}</td><td>${escape(status(item))}</td></tr>`).join("")}</tbody></table></div>` : '<p class="seating-merge-empty">No updated seat cards are waiting to be sent.</p>'}`;
    }
    function drawProviders(selected = "default") {
      provider.innerHTML = `<option value="default">${escape(defaultMailLabel(mail))}</option>${mail.gmail?.connected ? `<option value="gmail">${escape(gmailLabel(mail))}</option>` : selected === "gmail" ? '<option value="gmail">Gmail — disconnected</option>' : ""}`;
      provider.value = selected;
    }
    function controls() {
      const unresolved = seatUpdateRequests.has(current.id);
      provider.disabled = sending || loading || unresolved || !mail;
      refreshPreview.disabled = sending || loading || unresolved;
      retry.hidden = !unresolved;
      retry.disabled = sending;
      const available = provider.value === "gmail" ? mail?.gmail?.connected : Boolean(mail?.provider);
      send.disabled = sending || loading || unresolved || !available || !preview?.seat_updates?.students?.some(readyToQueue);
      send.textContent = sending ? "Queueing…" : queuedReceipt > 0 ? "Emails queued" : "Send updated emails";
      send.setAttribute("aria-busy", String(sending));
      retry.setAttribute("aria-busy", String(sending));
      refreshPreview.setAttribute("aria-busy", String(loading));
      providerHint.textContent = !mail ? "This choice applies to these updated notices only." : available ? "This choice applies to these updated notices only; your default email provider stays unchanged." : provider.value === "gmail" ? "Gmail is disconnected. Connect it in Response Cards, then refresh this preview, or choose the default provider." : "The default email provider is unavailable. Choose connected Gmail or configure email in Response Cards.";
    }
    function uncertain() {
      error.textContent = "Faculty could not confirm the result of this request. Retry this request to retrieve its result without queueing duplicate emails.";
      error.hidden = false;
      controls();
    }
    async function loadPreview() {
      if (sending || seatUpdateRequests.has(current.id)) return;
      const token = ++previewGeneration, selected = provider.value;
      preview = null; requestId = ""; queuedReceipt = null; loading = true; controls(); error.hidden = true; result.textContent = "";
      clearCardPreview();
      previewHost.innerHTML = '<p class="loading-state">Checking changed seats…</p>';
      previewHost.setAttribute("aria-busy", "true");
      try {
        const [data, status] = await Promise.all([api(chartPath), api("/api/response-cards/mail")]);
        if (!active() || token !== previewGeneration) return;
        preview = data; mail = status; requestId = crypto.randomUUID();
        drawPreview(data); drawProviders(selected);
        if (chartId() === current.id) {
          chart = data;
          renderDetail(document.querySelector("#seating-section"));
        }
      } catch (cause) {
        if (!active() || token !== previewGeneration) return;
        previewHost.innerHTML = ""; error.textContent = cause.message; error.hidden = false;
      } finally {
        if (active() && token === previewGeneration) { loading = false; previewHost.removeAttribute("aria-busy"); controls(); }
      }
    }
    async function queueUpdates(record) {
      if (sending) return;
      sending = true; seatUpdateRequests.set(current.id, record);
      const clearRequest = () => { if (seatUpdateRequests.get(current.id)?.payload.request_id === record.payload.request_id) seatUpdateRequests.delete(current.id); };
      let confirmed = false;
      preview = null; queuedReceipt = null; controls(); error.hidden = true; result.textContent = "Preparing updated cards and queueing emails…";
      try {
        const response = await fetch(`${chartPath}/seat-updates`, {method: "POST", cache: "no-store", signal: AbortSignal.timeout(60000),
          headers: {"Content-Type": "application/json", "X-Teaching-Request": "1"}, body: JSON.stringify(record.payload)});
        let data;
        try { data = await response.json(); } catch { throw new Error("The send result could not be read."); }
        if (!response.ok) {
          const unavailableBeforeSend = response.status === 503 && [
            "Connect Gmail in Response Cards before choosing Gmail delivery.",
            "Email sending is not configured, so cards cannot be emailed.",
            "The workspace email provider is not configured correctly.",
          ].includes(data.error);
          if (([400, 401, 403, 404, 409, 422].includes(response.status) || unavailableBeforeSend) && typeof data.error === "string") {
            clearRequest();
            if (active()) {
              clearCardPreview();
              error.textContent = `${data.error} Refresh the preview and review it before sending.`;
              error.hidden = false;
            }
            return;
          }
          throw new Error("The send result could not be confirmed.");
        }
        const receipt = data.seat_update_send;
        if (data.id !== current.id || !Number.isInteger(receipt?.queued) || receipt.queued < 0 || !Array.isArray(receipt.errors) || receipt.errors.some((item) => typeof item !== "string")) throw new Error("The send result could not be confirmed.");
        clearRequest(); confirmed = true; queuedReceipt = receipt.queued;
        const message = `${receipt.queued} updated seat email${receipt.queued === 1 ? "" : "s"} queued.`;
        if (active()) {
          drawPreview(data);
          result.textContent = `${message} Queueing is complete. Seats stay orange until sending is confirmed. Refresh the preview to check delivery or review any remaining updates.`;
          error.textContent = receipt.errors.join(" "); error.hidden = !receipt.errors.length;
        }
        toast(message, Boolean(receipt.errors.length));
      } catch {
        if (active()) uncertain();
      } finally {
        sending = false;
        if (active()) {
          if (result.textContent === "Preparing updated cards and queueing emails…") result.textContent = "";
          controls();
        }
      }
      if (confirmed) {
        try { if (chartId() === current.id) await render(true); await refresh(); }
        catch { toast("The send result was recorded, but the chart could not be refreshed. Refresh the page to check delivery.", true); }
      }
    }
    provider.onchange = controls;
    refreshPreview.onclick = () => { void loadPreview(); };
    send.onclick = () => {
      if (send.disabled || !preview || !requestId) return;
      void queueUpdates({payload: {version: preview.version, request_id: requestId, mail_provider: provider.value}, preview, mail});
    };
    retry.onclick = () => { const previous = seatUpdateRequests.get(current.id); if (previous) void queueUpdates(previous); };
    const previous = seatUpdateRequests.get(current.id);
    if (previous) {
      mail = previous.mail;
      drawPreview(previous.preview); drawProviders(previous.payload.mail_provider); uncertain();
    } else await loadPreview();
  }

  // The Create assignment window runs this same confirmation for a chart it names, away from the chart page.
  // `wizard` is { chartId, versionId, mode, onDone(result, info), onBack() }; the chart page passes none.
  function wizardBack(form, wizard) {
    if (!wizard) return;
    const cancel = form.querySelector('.dialog-footer [data-action="close"]');
    cancel.removeAttribute("data-action");
    cancel.textContent = "Back";
    cancel.onclick = () => wizard.onBack();
  }

  async function openConfirm(root, wizard = null) {
    const intent = captureDialogIntent(document.querySelector("#dialog"));
    const id = wizard?.chartId || chart.id;
    let options, current;
    try {
      current = await api(`/api/seating/${encodeURIComponent(id)}`);
      if (!intent()) return;
      if (!wizard) adoptChart(current, root);
      options = await api(`/api/seating/${encodeURIComponent(current.id)}/print-options`);
    } catch (cause) {
      if (intent()) toast(cause.message, true);
      return;
    }
    if (!intent()) return;
    const requestId = crypto.randomUUID();
    const proceed = (acknowledged) => {
      if (!options.versions.length || wizard?.mode === "stamps_only") stampsOnlyDialog(root, current, options, requestId, acknowledged, wizard);
      else exportDialog(root, current, options, requestId, acknowledged, wizard);
    };
    if (options.issues.length) {
      showDialog(`<form id="seating-confirm-issues">${dialogHeader("Unresolved issues", escape(`${current.title} · ${current.room_name}`))}<div class="dialog-body"><p class="dialog-step">Before confirming · Unresolved issues</p><div class="callout seating-delete-warning" data-tone="warning" role="alert"><p><strong>The roster check found ${options.issues.length} issue${options.issues.length === 1 ? "" : "s"}.</strong> Printed stamps will reflect the seats as they are now.</p></div><ul class="seating-issue-list">${options.issues.map((item) => `<li>${escape(item)}</li>`).join("")}</ul><p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Go back and resolve</button><button type="submit" class="button danger">Continue anyway</button></footer></form>`);
      const form = document.querySelector("#seating-confirm-issues");
      wizardBack(form, wizard);
      form.onsubmit = (event) => { event.preventDefault(); proceed(true); };
      return;
    }
    proceed(false);
  }

  function reexportNote(current) {
    // Every export is a new request, so every section gets NEW print jobs with new stamps; the earlier jobs are not superseded.
    const earlier = (current.print_batches || []).filter((batch) => !batch.superseded_at && batch.status !== "cancelled");
    if (!current.confirmed_at && !earlier.length) return "";
    return `<div class="callout seating-delete-warning" data-tone="warning" role="alert"><p><strong>This creates new print jobs.</strong> Each section gets a new job, with new stamps on a new copy for every seated student.${earlier.length ? ` The ${earlier.length} earlier print job${earlier.length === 1 ? "" : "s"} from this chart stay valid.` : ""} Print only one set: if these replace the earlier jobs, supersede or cancel those under Assignments › All print jobs.</p></div>`;
  }

  function sheetLabelOption() {
    // DOCUMENTS "SCANTRON LABELS": one more label per student, which names their bubble sheet and is no page of the paper.
    return '<label class="checkbox-field"><input type="checkbox" name="sheet_labels">Also print a Scantron label for each student (identifies their bubble sheet; it is not a page of the exam)</label>';
  }

  function cardsNote(options) {
    // The response card step is opt-in from here: ticked by default, it opens the review window next.
    return options.response_cards?.available
      ? '<label class="checkbox-field seating-cards-option"><input type="checkbox" name="prepare_cards" checked>Prepare response cards and email each student their link, or export for Gmail (review cards and choose delivery next)</label>'
      : "";
  }

  function watchCardsOption(form, withCards, withoutCards) {
    const box = form.elements.prepare_cards;
    const submit = form.querySelector('button[type="submit"]');
    const step = form.querySelector("[data-dialog-step]");
    const cardsLine = form.querySelector("[data-cards-line]");
    const update = () => {
      const cards = Boolean(box?.checked);
      if (box) submit.textContent = cards ? withCards : withoutCards;
      if (step) step.textContent = `Step 1 of ${cards ? 2 : 1} · Print jobs`;
      if (cardsLine) cardsLine.textContent = cards ? "Response cards are reviewed in the next step, before anything is sent." : "No response cards are prepared this time.";
    };
    box?.addEventListener("change", update);
    update();
  }

  function whatHappens(first) {
    // The confirmation chain in three lines; the step caption and the last line follow the response-card option.
    return `<p class="dialog-step" data-dialog-step>Step 1 of 2 · Print jobs</p><ul class="seating-what-happens" aria-label="What happens"><li>${first}</li><li>Printed stamps keep the seats as they are now; students moved later need updated seat cards.</li><li data-cards-line></li></ul>`;
  }

  async function nextStep(root, current, options, payload, wizard) {
    if (options.response_cards?.available && payload.prepare_cards) await cardsWindow(root, current, payload, wizard);
    else await submitConfirm(root, current, { ...payload, prepare_cards: false }, wizard);
  }

  const ITEM_KINDS = { short_text: "Response box", acknowledgment: "Checkbox", date: "Date", time: "Time", static: "Information", long_text: "Long response", confirmation: "Confirmation" };
  const ANSWER_KINDS = ["short_text", "long_text", "confirmation", "date", "time"];
  let customItems = 0;

  function editorState(schema) {
    return {
      title: schema.title || "", description: schema.description || "", confirmation_message: schema.confirmation_message || "",
      fields: (schema.fields || []).filter((field) => field.id !== "confirm_uin" && field.verification?.mode !== "uin-digest").map((field) => ({
        id: field.id, type: field.type, label: field.label || "",
        expected: typeof field.verification?.expected === "string" ? field.verification.expected : "",
        policy: field.verification ? (field.verification.on_mismatch === "BLOCK_SUBMISSION" ? "block" : field.verification.on_mismatch === "ALLOW" ? "none" : "flag") : "none",
      })),
    };
  }

  function toSchema(state) {
    const fields = state.fields.map((field, index) => {
      const item = { id: field.id || `custom_${index + 1}`, type: field.type, label: field.label, required: field.type !== "static" };
      if (ANSWER_KINDS.includes(field.type) && field.expected.trim() && field.policy !== "none") {
        item.verification = { expected: field.expected.trim(), mode: ["date", "time"].includes(field.type) ? "exact" : "compact", on_mismatch: field.policy === "block" ? "BLOCK_SUBMISSION" : "ALLOW_AND_FLAG" };
      }
      return item;
    });
    return { title: state.title, description: state.description, confirmation_message: state.confirmation_message || "Thank you. Your response has been recorded.", fields };
  }

  function fieldRows(kind, state) {
    const who = kind === "seat" ? "Seat card" : "DR card";
    return state.fields.map((field, index) => `<div class="sc-field" data-index="${index}"><span class="sc-type">${ITEM_KINDS[field.type] || escape(field.type)}</span><button type="button" class="text-button sc-remove" aria-label="Remove ${who} item ${index + 1}">Remove</button><input class="sc-label" value="${escape(field.label)}" placeholder="Prompt shown to the student" aria-label="${who} item ${index + 1} prompt" maxlength="2000">${ANSWER_KINDS.includes(field.type) ? `<input class="sc-expected" value="${escape(field.expected)}" placeholder="Expected answer; may use {{variables}} such as {{seat.number}}" aria-label="${who} item ${index + 1} expected answer" maxlength="2000"><select class="sc-policy" aria-label="${who} item ${index + 1} check"><option value="block" ${field.policy === "block" ? "selected" : ""}>Reject other answers (spacing and capitalization may differ)</option><option value="flag" ${field.policy === "flag" ? "selected" : ""}>Accept, but flag for my review</option><option value="none" ${field.policy === "none" ? "selected" : ""}>No check</option></select>` : ""}</div>`).join("") || '<p class="field-hint">No response items yet.</p>';
  }

  function columnMarkup(kind, state, heading, hint) {
    const who = kind === "seat" ? "Seat card" : "DR card";
    return `<section class="sc-column" data-column="${kind}"><h3>${heading}</h3><p class="field-hint">${hint}</p><label class="field">Card title<input class="sc-title" value="${escape(state.title)}" aria-label="${who} title" maxlength="200" required></label><label class="field">Statement<textarea class="sc-description" aria-label="${who} statement" maxlength="10000">${escape(state.description)}</textarea></label><div class="sc-fields">${fieldRows(kind, state)}</div><div class="sc-add"><button type="button" class="button" data-add="short_text">Add response box</button><button type="button" class="button" data-add="acknowledgment">Add response checkbox</button></div><p class="sc-uin"><strong>Always last:</strong> “Type your UIN to confirm that you have read and understood all of the above.” Only the student's own UIN is accepted (digits only; spaces and dashes are ignored), and the UIN itself is never stored in the public service.</p></section>`;
  }

  function cardDeliveryMarkup(templates) {
    const mail = templates.mail || {};
    return `<div class="sc-delivery"><label class="checkbox-field seating-cards-option"><input type="checkbox" name="publish_cards" ${templates.configured ? "checked" : "disabled"}><span data-publish-card-label>Publish the cards and email each student their link now${templates.configured ? "" : " (response service not configured)"}</span></label><label class="field">Invitation delivery<select name="card_delivery" aria-label="Invitation delivery" ${templates.configured ? "" : "disabled"}><option value="faculty">${escape(defaultMailLabel(mail))}</option>${mail.gmail?.connected ? `<option value="gmail_direct">${escape(gmailLabel(mail))}</option>` : ""}<option value="gmail">Gmail mail merge</option></select></label><p class="field-hint" data-card-delivery-note></p></div>`;
  }

  function bindCardDelivery(form, templates, current) {
    const publish = form.elements.publish_cards, delivery = form.elements.card_delivery;
    const label = form.querySelector("[data-publish-card-label]"), note = form.querySelector("[data-card-delivery-note]");
    const update = () => {
      delivery.disabled = !templates.configured || !publish.checked;
      if (!templates.configured) {
        label.textContent = "Publish the cards (response service not configured)";
        note.textContent = "These cards stay in Faculty until the response service is configured and you publish them.";
      } else if (!publish.checked) {
        label.textContent = "Publish the cards now";
        note.textContent = "Keep the cards in Faculty only. You can publish them later from Response Cards; no invitations are sent.";
      } else if (delivery.value === "gmail") {
        label.textContent = "Publish the cards for Gmail mail merge";
        note.textContent = "Publish personal response links without queuing invitations in Faculty. After confirmation, review and download the Gmail spreadsheet, then import it into Google Sheets and send from Gmail.";
      } else if (delivery.value === "gmail_direct") {
        label.textContent = "Publish the cards and email each student their link now";
        note.textContent = `Faculty sends these invitations directly from ${templates.mail.gmail.email}. No spreadsheet is needed. This choice applies to these invitations only; automatic confirmation emails keep using the default provider.`;
      } else {
        label.textContent = "Publish the cards and email each student their link now";
        note.textContent = templates.mail_configured
          ? `Each student gets an email with the subject “IMPORTANT: Information about your upcoming ${current.title} for ${current.code || ""}” and their personal link. When they complete the card, a confirmation email goes back to them automatically. Confirming again adds cards for newly seated students and replaces cards for re-seated students. Untick to keep cards in Faculty only.`
          : "Email sending is not configured. Published cards will not be emailed by Faculty; choose Gmail mail merge to prepare a spreadsheet, or share each link from Response Cards. Untick to keep cards in Faculty only.";
      }
    };
    publish.addEventListener("change", update); delivery.addEventListener("change", update); update();
  }

  async function cardsWindow(root, current, payload, wizard) {
    const intent = captureDialogIntent(document.querySelector("#dialog"));
    let templates;
    try {
      templates = await api(`/api/seating/${encodeURIComponent(current.id)}/card-templates`);
    } catch (cause) {
      if (intent()) toast(cause.message, true);
      return;
    }
    if (!intent()) return;
    // An exam's date and time not recorded yet start from the class schedule's planned row this assignment follows, for this
    // chart's sections (SCHEDULING "CLASS SCHEDULE": each section's own final, a regular exam's shared time). Still typed over freely.
    if (!templates.exam_date || !templates.exam_time) {
      try {
        const planned = await api(`/api/scheduling/terms/${encodeURIComponent(current.term_id)}/class-schedule`);
        const row = (planned.items || []).find((item) => item.link?.assignment_id === current.assignment_id);
        if (row) {
          const own = (field) => dateFor(row, current.section_ids || [], field);
          templates = { ...templates, exam_date: templates.exam_date || own("date"), exam_time: templates.exam_time || own("time") };
        }
      } catch { /* the fields stay empty, as before */ }
    }
    if (!intent()) return;
    const editors = { seat: editorState(templates.seat), dr: editorState(templates.dr) };
    const counts = templates.counts || {};
    const ambiguous = counts.dr_ambiguous?.length ? `<div class="callout seating-delete-warning" data-tone="warning" role="alert"><p>${counts.dr_ambiguous.length} DR-booked student${counts.dr_ambiguous.length === 1 ? " has" : "s have"} more than one booking linked to this exam and will get no DR card: ${escape(counts.dr_ambiguous.join(", "))}. Contact them directly.</p></div>` : "";
    showDialog(`<form id="seating-cards">${dialogHeader("Response cards for this exam", escape(`${current.title} · ${current.room_name}`))}<div class="dialog-body"><p class="dialog-step">Step 2 of 2 · Response cards</p><p>Each student gets a personalized card. Variables such as {{seat.number}}, {{exam.room}}, {{exam.date}}, {{exam.time}}, {{dr_exam.date}} and {{dr_exam.time}} are filled in per student when the cards are created.</p><div class="sc-exam"><label class="field">Exam date<input type="date" name="exam_date" value="${escape(templates.exam_date || "")}" aria-label="Exam date" required></label><label class="field">Exam time<input name="exam_time" value="${escape(templates.exam_time || "")}" placeholder="e.g. 7:00 PM" aria-label="Exam time" maxlength="40" required></label></div>${ambiguous}<div class="sc-columns">${columnMarkup("seat", editors.seat, `Students taking the exam in ${escape(current.room_name)} (${counts.seated ?? 0})`, "Seated students. Their card states the time, room and seat, then asks them to confirm the room and seat.")}${columnMarkup("dr", editors.dr, `Disability Resource Center students (${counts.dr ?? 0})`, "DR-booked students. Their card does not state the time or place; their answers are checked against the current booking and mismatches are flagged for you.")}</div>${cardDeliveryMarkup(templates)}<p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="button" class="button" data-cards-skip>Skip response cards</button><button type="submit" class="button primary">Confirm, export, and prepare cards</button></footer></form>`);
    const dialog = document.querySelector("#dialog");
    dialog.classList.add("dialog-wide");
    dialog.addEventListener("close", () => dialog.classList.remove("dialog-wide"), { once: true });
    const form = document.querySelector("#seating-cards");
    wizardBack(form, wizard);
    bindCardDelivery(form, templates, current);
    function column(kind) { return form.querySelector(`[data-column="${kind}"]`); }
    function redraw(kind) { column(kind).querySelector(".sc-fields").innerHTML = fieldRows(kind, editors[kind]); }
    form.addEventListener("input", (event) => {
      const section = event.target.closest("[data-column]");
      if (!section) return;
      const state = editors[section.dataset.column];
      if (event.target.classList.contains("sc-title")) state.title = event.target.value;
      if (event.target.classList.contains("sc-description")) state.description = event.target.value;
      const row = event.target.closest(".sc-field");
      if (!row) return;
      const field = state.fields[Number(row.dataset.index)];
      if (event.target.classList.contains("sc-label")) field.label = event.target.value;
      if (event.target.classList.contains("sc-expected")) field.expected = event.target.value;
      if (event.target.classList.contains("sc-policy")) field.policy = event.target.value;
    });
    form.addEventListener("click", (event) => {
      const section = event.target.closest("[data-column]");
      if (!section) return;
      const kind = section.dataset.column;
      const add = event.target.closest("[data-add]");
      if (add) {
        customItems += 1;
        editors[kind].fields.push({ id: `custom_${customItems}_${Date.now().toString(36)}`, type: add.dataset.add, label: "", expected: "", policy: add.dataset.add === "acknowledgment" ? "none" : "block" });
        redraw(kind);
        column(kind).querySelector(".sc-field:last-child .sc-label")?.focus();
        return;
      }
      const remove = event.target.closest(".sc-remove");
      if (remove) {
        editors[kind].fields.splice(Number(remove.closest(".sc-field").dataset.index), 1);
        redraw(kind);
      }
    });
    form.querySelector("[data-cards-skip]").onclick = () => {
      perform(form, async () => { await submitConfirm(root, current, { ...payload, prepare_cards: false }, wizard); });
    };
    form.onsubmit = (event) => {
      event.preventDefault();
      perform(form, async () => {
        const data = new FormData(form);
        await submitConfirm(root, current, { ...payload, prepare_cards: true, publish_cards: data.get("publish_cards") === "on",
          email_cards: data.get("publish_cards") === "on" && data.get("card_delivery") !== "gmail",
          mail_provider: data.get("card_delivery") === "gmail_direct" ? "gmail" : "default",
          exam_date: data.get("exam_date"), exam_time: data.get("exam_time"), cards: { seat: toSchema(editors.seat), dr: toSchema(editors.dr) } }, wizard);
      });
    };
  }

  function exportDialog(root, current, options, requestId, acknowledged, wizard) {
    const latest = options.versions.find((item) => item.id === wizard?.versionId) || options.versions[options.versions.length - 1];
    showDialog(`<form id="seating-export">${dialogHeader("Confirm and export to printing", escape(`${current.title} · ${current.room_name}`))}<div class="dialog-body">${whatHappens("One print job per section, with one stamped copy per seated student; peel-off labels once the jobs are ready.")}${reexportNote(current)}${current.exam_version_count > 1 ? calloutMarkup({ tone: "warning", className: "seating-export-versions",
      title: "Every copy prints from the one PDF chosen here",
      body: `The chart gives seats Versions A–${String.fromCharCode(64 + current.exam_version_count)}: to hand out each seat’s own version, print the versions elsewhere and label them (${wizard ? "go Back and choose Printed elsewhere" : "Print exams for this room, then Printed elsewhere"}), or set the chart to one version.` }) : ""}<label class="field">Exam PDF version<select name="version_id" aria-label="Exam PDF version">${options.versions.map((item) => `<option value="${escape(item.id)}" ${item.id === latest.id ? "selected" : ""}>${escape(item.label)} · ${item.page_count} page${item.page_count === 1 ? "" : "s"}</option>`).join("")}</select></label><label class="field">Extra unassigned copies per section<input name="extras" type="number" min="0" max="100" value="0"></label><label class="checkbox-field"><input type="checkbox" name="seat_marks" checked>Print each student's seat number beside the stamp (bold, top-aligned with the square)</label>${sheetLabelOption()}<ul class="seating-forecast">${options.sections.map((item) => `<li>Section ${item.number}: ${item.seated} seated cop${item.seated === 1 ? "y" : "ies"}${item.seated ? "" : " — nothing to print"}</li>`).join("")}</ul>${cardsNote(options)}${acknowledged ? '<p class="field-hint">Continuing despite unresolved issues; they will be recorded with the confirmation.</p>' : ""}<p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Confirm and export</button></footer></form>`);
    const form = document.querySelector("#seating-export");
    wizardBack(form, wizard);
    watchCardsOption(form, "Continue to response cards", "Confirm and export");
    // A stamps-only version prints labels for papers printed elsewhere, which is the advice itself: the warning steps aside.
    const versionWarning = form.querySelector(".seating-export-versions");
    const versionSelect = form.querySelector('[name="version_id"]');
    const showVersionWarning = () => {
      if (versionWarning) versionWarning.hidden = isStampsOnly(options.versions.find((item) => item.id === versionSelect.value));
    };
    showVersionWarning();
    versionSelect.addEventListener("change", showVersionWarning);
    form.onsubmit = (event) => {
      event.preventDefault();
      perform(form, async () => {
        const data = new FormData(form);
        await nextStep(root, current, options, { mode: "print", version_id: data.get("version_id"), extras: Number(data.get("extras") || 0), seat_marks: data.get("seat_marks") === "on", sheet_labels: data.get("sheet_labels") === "on", prepare_cards: data.get("prepare_cards") === "on", request_id: requestId, acknowledge_issues: acknowledged }, wizard);
      });
    };
  }

  function stampsOnlyDialog(root, current, options, requestId, acknowledged, wizard) {
    // From the assignment window the instructor may choose stamps only although a PDF version exists.
    const chosen = Boolean(options.versions.length);
    showDialog(`<form id="seating-stamps-only">${dialogHeader(chosen ? "Export stamps only" : "No exam PDF is attached", escape(`${current.title} · ${current.room_name}`))}<div class="dialog-body">${whatHappens("One print job per section: peel-off labels and stamped blank pages, one stamp per page of every seated student's paper.")}${reexportNote(current)}${chosen ? "" : `<div class="callout seating-delete-warning" data-tone="warning" role="alert"><p><strong>${escape(current.title)} has no registered PDF version to stamp.</strong> Add the paper in its assignment’s Create to print stamped exams, or export stamps only.</p></div>`}<p class="field-hint">Labels print on the Brother QL-800 (29 × 90 mm DK-1201 or 62 × 29 mm rolls) or Avery 5160 sheets. Each label belongs to one page, numbered from the front. Enter 1 to label only the front page: Faculty takes the other pages from the scan in order.</p><label class="field">Labeled pages per paper exam<input name="page_count" type="number" min="1" max="100" value="1" required></label>${sheetLabelOption()}<label class="field">Extra unassigned copies per section<input name="extras" type="number" min="0" max="100" value="0"></label><label class="checkbox-field"><input type="checkbox" name="seat_marks" checked>Print each student's seat number beside the stamp on the stamped pages (labels already show it)</label><ul class="seating-forecast">${options.sections.map((item) => `<li>Section ${item.number}: ${item.seated} seated cop${item.seated === 1 ? "y" : "ies"}</li>`).join("")}</ul>${cardsNote(options)}${acknowledged ? '<p class="field-hint">Continuing despite unresolved issues; they will be recorded with the confirmation.</p>' : ""}<p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button>${wizard ? "" : `<a class="button" href="#documents?definition=${encodeURIComponent(current.definition_id || "")}">Register a PDF first</a>`}<button type="submit" class="button primary">Export stamps only</button></footer></form>`);
    const form = document.querySelector("#seating-stamps-only");
    wizardBack(form, wizard);
    watchCardsOption(form, "Export stamps only, then response cards", "Export stamps only");
    form.onsubmit = (event) => {
      event.preventDefault();
      perform(form, async () => {
        const data = new FormData(form);
        await nextStep(root, current, options, { mode: "stamps_only", page_count: Number(data.get("page_count")), extras: Number(data.get("extras") || 0), seat_marks: data.get("seat_marks") === "on", sheet_labels: data.get("sheet_labels") === "on", prepare_cards: data.get("prepare_cards") === "on", request_id: requestId, acknowledge_issues: acknowledged }, wizard);
      });
    };
  }

  async function submitConfirm(root, current, payload, wizard) {
    // Confirming publishes and emails every seated student's card; allow well over the default 15 s.
    let result;
    const dialog = document.querySelector("#dialog");
    const active = dialogSession(dialog.firstElementChild, dialog);
    try {
      result = await api(`/api/seating/${encodeURIComponent(current.id)}/confirm`, "POST", { version: current.version, ...payload }, {}, 300000);
    } catch (cause) {
      // The chart changed while this window was open. Take its current version and show its current seats, then leave the decision
      // to the instructor: nothing is resent by itself, and the server checks the chart and its issues again on the next submit.
      const fresh = cause?.status === 409 ? await api(`/api/seating/${encodeURIComponent(current.id)}`).catch(() => null) : null;
      if (!fresh || fresh.version === current.version) throw cause;
      current.version = fresh.version;
      if (!wizard) adoptChart(fresh, root);
      throw new Error("The seating chart changed while this window was open. It now shows its current seats: check them, then submit again to confirm the chart as it is now.");
    }
    const continueFlow = active();
    if (!wizard) {
      if (active()) dialog.close();
      adoptChart(result, root);
    }
    const jobs = result.exported_batches.length;
    const cards = result.response_cards || {};
    const prepared = [cards.seat ? `${cards.seat.recipients} seat notice${cards.seat.recipients === 1 ? "" : "s"}` : "", cards.dr ? `${cards.dr.recipients} DR notice${cards.dr.recipients === 1 ? "" : "s"}` : ""].filter(Boolean).join(" and ");
    toast(`Seating chart confirmed. ${jobs} print job${jobs === 1 ? "" : "s"} queued${payload.mode === "stamps_only" ? " for stamps only" : ""}.${prepared ? ` ${prepared} prepared${cards.emailed ? " and emailed." : cards.published ? " and published." : ", not published."}` : ""}${cards.errors?.length ? ` ${cards.errors.length} notice problem${cards.errors.length === 1 ? "" : "s"} listed on the chart.` : ""}`, Boolean(cards.errors?.length));
    void refresh();
    const mailMerge = Boolean(payload.prepare_cards && payload.publish_cards && payload.email_cards === false);
    // The assignment window may have more rooms to confirm, so it offers the Gmail spreadsheet itself.
    if (wizard && continueFlow) wizard.onDone(result, { mailMerge });
    else if (!wizard && continueFlow && mailMerge && chartId() === current.id) await openMailMerge(result);
  }

  window.addEventListener("hashchange", () => {
    printing.reset();
    clearVersionSelection();
    versionView = false;
    clearMoveSelection();
    const root = document.querySelector("#seating-section");
    if (root) closeSeatPopover(root);
    ++generation;
  });

  // The chart read under way (its chart and generation). The shell renders again when its own state read returns; that render
  // joins this read instead of starting another. Cleared however the read ends, and a navigation starts a new generation.
  let inflight = null;
  async function render(force = false) {
    const root = document.querySelector("#seating-section");
    const id = chartId();
    if (!id) {
      clearMoveSelection();
      ++generation;
      route = location.hash;
      chart = null;
      const exam = examRoute(location.hash);
      if (exam) renderExam(root, exam); else renderList(root);
      return;
    }
    const summary = getCharts().find((item) => item.id === id);
    if (interactions.moveRequest?.chartId === id || interactions.versionRequest?.chartId === id) {
      if (chart?.id !== id) root.innerHTML = `<p class="loading-state">${interactions.versionRequest?.chartId === id ? "Saving exam versions…" : "Saving the seating change…"}</p>`;
      return;
    }
    if (!force && route === location.hash && chart?.id === id && (!summary || summary.version === chart.version)) return;
    if (!force && route === location.hash && inflight?.id === id && inflight.generation === generation) return;
    if (chart?.id !== id) {
      root.innerHTML = '<p class="loading-state">Loading the seating chart…</p>';
      // No header action until the chart is here: its next step depends on the loaded chart.
      header?.set({ route: location.hash, action: null, subtitle: "", ...(summary ? { title: `${summary.title} · ${summary.room_name}`, context: scope(summary) } : {}) });
    }
    route = location.hash;
    const current = ++generation;
    const reading = (inflight = { id, generation: current });
    try {
      const loaded = await api(`/api/seating/${encodeURIComponent(id)}`);
      if (current !== generation || root.hidden) return;
      chart = loaded;
      renderDetail(root);
      openRequestedPacket();
      // A chart changed after this read began, as the shell's newer summary says: read it again, as the joined render would have.
      const latest = getCharts().find((item) => item.id === id);
      if (latest && Number(latest.version) > Number(loaded.version)) void render(true);
    } catch (cause) {
      if (current !== generation) return;
      chart = { id, version: summary?.version, missing: true };
      header?.set({ route: location.hash, title: "Seating chart", action: null, subtitle: "" });
      root.innerHTML = `<div class="empty-state">${icon("grid")}<h3>This seating chart isn’t available.</h3><p>${escape(cause.message)}</p><a class="button" href="#seating">All seating charts</a></div>`;
    } finally {
      if (inflight === reading) inflight = null;
    }
  }

  // #seating/<id>?packet=1 (the exam-day plan's Print packet links) opens the room's packet once: the query is dropped as it is read
  // (FRONTEND.md "Deep links that open a control"), and the page keeps the chart it just drew.
  function openRequestedPacket() {
    if (new URLSearchParams(location.hash.split("?")[1] || "").get("packet") !== "1") return;
    history.replaceState(history.state, "", location.hash.split("?")[0]);
    route = location.hash;
    openPacketPrint().catch((cause) => toast(cause.message, true));
  }

  function relinkField(dr, title) {
    if (!dr.others.length) return "";
    return `<fieldset class="seating-sections seating-relink"><legend>DR bookings linked to other exams</legend><p class="field-hint">These students' DR bookings are linked to a different exam of this course, so they would still be seated. Tick any that are really ${escape(title)}; their bookings will be linked to it.</p>${dr.others.map((item) => `<label class="checkbox-field"><input type="checkbox" name="relink_assignment_ids" value="${escape(item.assignment_id)}">${escape(item.title)} · ${item.students} student${item.students === 1 ? "" : "s"}, ${item.bookings} booking${item.bookings === 1 ? "" : "s"}${item.auto_created ? " · created from the DR export" : ""}</label>`).join("")}</fieldset>`;
  }

  async function openSettings() {
    clearMoveSelection();
    if (!chart || chart.missing) {
      toast("Open a seating chart first.", true);
      return;
    }
    const current = chart;
    const intent = captureDialogIntent(document.querySelector("#dialog"));
    let options;
    try {
      options = await api(`/api/seating/options?assignment_id=${encodeURIComponent(current.assignment_id)}&sections=${current.section_details.map((item) => encodeURIComponent(item.id)).join(",")}`);
    } catch (cause) {
      if (intent()) toast(cause.message, true);
      return;
    }
    if (!intent()) return;
    showDialog(`<form id="seating-settings">${dialogHeader("Seating chart settings", escape(`${current.title} · ${current.room_name}`))}<div class="dialog-body"><p class="field-hint">${options.dr.excluded_students} student${options.dr.excluded_students === 1 ? "" : "s"} in these sections ${options.dr.excluded_students === 1 ? "has" : "have"} DR bookings linked to ${escape(current.title)} and ${options.dr.excluded_students === 1 ? "is" : "are"} not seated.</p>${relinkField(options.dr, current.title)}<label class="field">Notes<span class="field-optional">optional</span><input name="notes" maxlength="280" value="${escape(current.notes)}"></label><p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="danger-button" data-seating-delete>${icon("trash")}Remove chart</button><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Save changes</button></footer></form>`);
    const form = document.querySelector("#seating-settings");
    const active = dialogSession(form, document.querySelector("#dialog"));
    form.onsubmit = (event) => {
      event.preventDefault();
      perform(form, async () => {
        const data = new FormData(form);
        const saved = await api(`/api/seating/${encodeURIComponent(current.id)}`, "PATCH", { version: current.version, relink_assignment_ids: data.getAll("relink_assignment_ids"), notes: data.get("notes") || "" });
        if (active()) document.querySelector("#dialog").close();
        toast(saved.issues.length ? "Settings saved. Use Update seating to apply the DR exclusions." : "Settings saved.");
        adoptChart(saved);
        await refresh(true);
      });
    };
    form.querySelector("[data-seating-delete]").onclick = () => {
      const jobs = current.print_jobs || 0;
      const typed = current.delete_confirmation === "typed";
      showDialog(`<form id="seating-delete">${dialogHeader("Remove this seating chart?", escape(current.title))}<div class="dialog-body"><p>The seat assignments for ${escape(scope(current))} in ${escape(current.room_name)} will be removed. The room, roster, and DR bookings are untouched.</p>${typed ? `<div class="callout seating-delete-warning" data-tone="warning" role="alert"><p><strong>${jobs} print job${jobs === 1 ? "" : "s"} with Data Matrix page stamps already exist${jobs === 1 ? "s" : ""} for ${escape(current.title)} this term.</strong> Paper for this exam may already be printed. Removing the seating chart cannot be undone.</p></div><label class="field field-plain">Type <strong>delete</strong> to confirm<input name="confirmation" autocomplete="off" spellcheck="false" placeholder="delete" aria-label="Type delete to confirm"></label>` : ""}<p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Keep chart</button><button type="submit" class="button danger" ${typed ? "disabled" : ""}>Remove chart</button></footer></form>`);
      const confirmation = document.querySelector("#seating-delete");
      const active = dialogSession(confirmation, document.querySelector("#dialog"));
      if (typed) {
        const input = confirmation.elements.confirmation;
        const submit = confirmation.querySelector('button[type="submit"]');
        input.oninput = () => { submit.disabled = input.value.trim().toLowerCase() !== "delete"; };
        input.focus();
      }
      confirmation.onsubmit = (event) => {
        event.preventDefault();
        if (typed && confirmation.elements.confirmation.value.trim().toLowerCase() !== "delete") return;
        perform(confirmation, async () => {
          await api(`/api/seating/${encodeURIComponent(current.id)}`, "DELETE", { version: current.version, confirmation: typed ? "delete" : undefined });
          if (active()) document.querySelector("#dialog").close();
          toast("Seating chart removed.");
          if (chartId() === current.id) { chart = null; ++generation; location.hash = "seating"; }
          await refresh(true);
        }).then(() => {
          if (typed && confirmation.isConnected) confirmation.querySelector('button[type="submit"]').disabled = confirmation.elements.confirmation.value.trim().toLowerCase() !== "delete";
        });
      };
    };
  }

  return { render, openCreate, openSettings, openMailMerge, confirmChart: (wizard) => openConfirm(null, wizard) };
}
