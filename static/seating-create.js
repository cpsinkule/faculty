import { captureDialogIntent, dialogSession, disclosureMarkup, runFormAction } from "./ui.js";
import { examHref, plural, roomOptions } from "./seating-state.js";

// How seating is assigned: shown where the choices are made (moved here from the seating list).
const HOW_SEATING = `<p><strong>Start with the room.</strong> Pick a room for each section taking the exam, or mix sections across rooms: choose the rooms and the sections, and their students are shuffled together and spread over the rooms by their spaced seats. Each room gets its own chart.</p><p><strong>Keep DR bookings separate.</strong> Students with an imported DR booking linked to this assignment are left out and listed separately. “Test 1” and “Exam 1” count as the same exam.</p><ul><li><strong>Room to work.</strong> Students get an empty seat on either side (one per table) whenever space allows; otherwise the least crowded seats are used.</li><li><strong>Usable seats only.</strong> Each roster student receives a random usable seat, except those with linked DR bookings. Broken and blocked seats are never used, and a section is seated once per exam.</li><li><strong>Keep up with changes.</strong> The chart flags changes to rosters, rooms, and DR bookings. <strong>Update seating</strong> fixes only the affected seats.</li><li><strong>Adjust when needed.</strong> Move a student, swap two students, or print the room’s chart.</li></ul>`;

/**
 * Create charts through the existing catalog/options/plan workflow. The wizard
 * supplies its selected assignment and permanent sections; it owns no records.
 */
export function createSeatingCreation({ api, escape, toast, showDialog, dialogHeader, getCharts, refresh, relinkField, wizardBack, href }) {
  // `wizard` is { assignmentId, sectionIds, onCreated(result), onBack() } from the Create assignment window:
  // the exam and its sections are already chosen there, and the window continues instead of opening the chart.
  async function openCreate(preselect = "", wizard = null) {
    const intended = captureDialogIntent(document.querySelector("#dialog"));
    let catalog;
    try {
      catalog = await api("/api/seating/catalog");
      if (!intended()) return;
    } catch (cause) {
      toast(cause.message, true);
      return;
    }
    if (!catalog.assignments.length) {
      toast("Add the exam under Assignments first, then create its seating chart.", true);
      return;
    }
    if (!catalog.rooms.length) {
      toast("Add a room with its seating chart first.", true);
      return;
    }
    // Ready rooms first, so the first real option is always a room that can be chosen: the rooms an earlier chart used
    // (the latest first; the same few rooms serve most exams), then the rest, most usable seats first.
    const rooms = roomOptions(catalog.rooms, getCharts());
    const option = (room, note, disabled = false) => `<option value="${escape(room.id)}"${disabled ? " disabled" : ""}>${escape(room.name)} — ${escape(note)}</option>`;
    const seats = (room) => `${room.usable_count} usable · ${room.spaced_count} spaced`;
    const roomChoices = [
      rooms.used.length ? `<optgroup label="Used before">${rooms.used.map((room) => option(room, `${seats(room)} · ${room.used_for}`)).join("")}</optgroup>` : "",
      rooms.others.length ? `<optgroup label="${rooms.used.length ? "Other ready rooms" : "Ready rooms"}">${rooms.others.map((room) => option(room, seats(room))).join("")}</optgroup>` : "",
      rooms.review.length ? `<optgroup label="Needs review">${rooms.review.map((room) => option(room, "review needed", true)).join("")}</optgroup>` : "",
      rooms.empty.length ? `<optgroup label="No assignable seats">${rooms.empty.map((room) => option(room, "0 usable", true)).join("")}</optgroup>` : "",
    ].join("");
    // A mix's rooms: one aligned row each, the rooms used before above a hairline, the list scrolling inside its own box.
    const mixRoom = (room) => `<label class="checkbox-field seating-room-row" data-room-name="${escape(room.name)}"><input type="checkbox" name="mix_room" value="${escape(room.id)}"><span class="seating-room-name">${escape(room.name)}</span> <span class="seating-room-seats">${room.usable_count} <span>usable</span></span> <span class="seating-room-seats">${room.spaced_count} <span>spaced</span></span> <span class="seating-room-used">${room.used_for ? `Used for ${escape(room.used_for)}` : ""}</span></label>`;
    const mixRoomList = `${rooms.used.length ? `<div class="seating-room-group">${rooms.used.map(mixRoom).join("")}</div>` : ""}${rooms.others.length ? `<div class="seating-room-group">${rooms.others.map(mixRoom).join("")}</div>` : ""}<p class="field-hint" data-room-none hidden>No room has that name.</p>`;
    if (wizard) preselect = wizard.assignmentId;
    const assignments = wizard ? catalog.assignments.filter((item) => item.id === wizard.assignmentId) : catalog.assignments;
    if (!assignments.length) {
      toast("That assignment no longer exists.", true);
      return;
    }
    showDialog(`<form id="seating-create">${dialogHeader("New seating chart", "Choose the exam. Give each section a room, or mix sections across rooms.")}<div class="dialog-body"><section class="detail-group" aria-labelledby="seating-create-rooms-title"><h3 id="seating-create-rooms-title">Place your sections</h3><label class="field">Exam assignment<select name="assignment_id" aria-label="Exam assignment" required>${assignments.map((item) => `<option value="${escape(item.id)}" ${item.id === preselect ? "selected" : ""}>${escape(item.code)} · ${escape(item.title)}</option>`).join("")}</select></label><fieldset class="seating-mode" aria-label="How to seat the sections"><label class="checkbox-field"><input type="radio" name="seating_mode" value="rooms" checked>Give each section a room</label><label class="checkbox-field"><input type="radio" name="seating_mode" value="mix">Mix sections across rooms</label></fieldset><fieldset class="seating-sections" data-mode="rooms"><legend>Room for each section</legend><p class="supporting-copy">Sections can share a room; each room gets its own chart. Leave a section out with “Not in this exam”.</p><div id="seating-section-rooms"></div></fieldset><div data-mode="mix" hidden><fieldset class="seating-sections"><legend>Rooms</legend><p class="supporting-copy">Choose two or more rooms. Each room seats a share of the students in proportion to its spaced seats, and gets its own chart.</p>${rooms.ready.length > 8 ? '<label class="field seating-room-find">Find a room<input type="search" data-room-find placeholder="Building or room number" autocomplete="off"></label>' : ""}<div id="seating-mix-rooms" class="seating-room-list">${mixRoomList}</div><p id="seating-mix-tally" class="field-hint seating-room-tally" role="status"></p></fieldset><fieldset class="seating-sections"><legend>Sections to mix</legend><p class="supporting-copy">Their students are shuffled together and seated at random across the rooms.</p><div id="seating-mix-sections" class="seating-mix-list"></div></fieldset></div><div id="seating-dr"></div><div id="seating-forecast" class="seating-forecast" role="status"></div>${disclosureMarkup({ label: "How seating is assigned", body: HOW_SEATING, className: "seating-create-guide" })}</section><section class="detail-group" aria-labelledby="seating-create-versions-title"><h3 id="seating-create-versions-title">Plan the exam versions</h3><label class="field">Exam versions<span class="field-optional">optional</span><input type="number" name="exam_version_count" aria-label="Exam versions" min="1" max="26" step="1" placeholder="Leave blank to assign later" aria-describedby="seating-create-version-help"></label><p class="supporting-copy" id="seating-create-version-help">Enter 1–26 and Faculty will assign A, B, C, and so on around the room. Empty seats do not interrupt the alternating sequence. One version gives everyone A.</p><label class="checkbox-field"><input type="checkbox" name="allow_unequal_versions">Allow unequal version counts</label><p class="supporting-copy">Automatic assignment normally keeps totals equal or differing by one. Check this to favor fewer matching versions in nearby rows when assigning versions.</p></section><label class="field">Notes<span class="field-optional">optional</span><input name="notes" maxlength="280" placeholder="e.g. Bring photo ID"></label><p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">${wizard ? "Create seating chart and continue" : "Create seating chart"}</button></footer></form>`);
    const form = document.querySelector("#seating-create");
    const active = dialogSession(form, document.querySelector("#dialog"));
    wizardBack(form, wizard);
    const rows = form.querySelector("#seating-section-rooms");
    const mixSections = form.querySelector("#seating-mix-sections");
    const mixRooms = form.querySelector("#seating-mix-rooms");
    const mixing = () => form.elements.seating_mode.value === "mix";
    const drHost = form.querySelector("#seating-dr");
    const forecast = form.querySelector("#seating-forecast");
    let request = 0;
    function sectionRows() {
      request += 1; // Changing the assignment invalidates any earlier DR preview.
      const assignment = catalog.assignments.find((item) => item.id === form.elements.assignment_id.value);
      const sections = catalog.sections.filter((item) => item.definition_id === assignment?.definition_id && (!wizard || wizard.sectionIds.includes(item.id)));
      const taken = new Map();
      getCharts().filter((item) => item.assignment_id === assignment?.id).forEach((item) => (item.section_ids || []).forEach((id) => taken.set(id, item.room_name)));
      // The same sections, as checkboxes for a mix: the current term's free sections start ticked.
      const free = sections.filter((item) => !taken.has(item.id));
      mixSections.innerHTML = free.length ? free.map((item) => `<label class="checkbox-field"><input type="checkbox" name="mix_section" value="${escape(item.id)}" data-term="${escape(item.term_id)}" data-number="${item.number}"${item.is_current || wizard ? " checked" : ""}>${escape(`${item.term_label} · Section ${item.number}${item.is_current ? "" : " (not current)"}`)}</label>`).join("")
        : `<p class="field-hint">${sections.length ? "Every section is already seated for this exam." : `${escape(assignment?.code || "This course")} has no saved sections yet.`}</p>`;
      rows.innerHTML = sections.length ? sections.map((item) => {
        const name = `${item.term_label} · Section ${item.number}${item.is_current ? "" : " (not current)"}`;
        if (taken.has(item.id)) return `<div class="seating-section-row"><span>${escape(name)}</span><span class="field-hint">Already seated in ${escape(taken.get(item.id))}</span></div>`;
        return `<label class="seating-section-row"><span>${escape(name)}</span><select name="room:${escape(item.id)}" data-section="${escape(item.id)}" data-term="${escape(item.term_id)}" data-number="${item.number}" aria-label="Room for Section ${item.number}"><option value="">Not in this exam</option>${roomChoices}</select></label>`;
      }).join("") : `<p class="field-hint">${escape(assignment?.code || "This course")} has no saved sections yet.</p>`;
      // A room that still needs review cannot be chosen; say where to review it, in a new tab so this form keeps its choices.
      const review = rooms.review;
      if (sections.length && review.length) rows.insertAdjacentHTML("beforeend", `<p class="field-hint" data-rooms-need-review>${plural(review.length, "room needs", "rooms need")} review before ${review.length === 1 ? "it" : "they"} can be chosen · <a href="#rooms?filter=review" target="_blank" rel="noopener">Review rooms</a> (a new tab; start this form again afterwards)</p>`);
      drHost.innerHTML = "";
      forecast.innerHTML = "";
    }
    function chosen() {
      return [...form.querySelectorAll("select[data-section]")].filter((select) => select.value);
    }
    // A mix's sections as the per-section rows give theirs: {dataset: {section, term, number}}.
    const mixChosen = () => [...mixSections.querySelectorAll("input:checked")].map((input) => ({ dataset: { section: input.value, term: input.dataset.term, number: input.dataset.number } }));
    const mixRoomIds = () => [...mixRooms.querySelectorAll("input:checked")].map((input) => input.value);
    // What is ticked stays in view while the list is scrolled or narrowed: the chosen rooms and the seats they hold together.
    function mixTally() {
      const picked = mixRoomIds().map((id) => rooms.ready.find((room) => room.id === id)).filter(Boolean);
      const sum = (key) => picked.reduce((total, room) => total + room[key], 0);
      form.querySelector("#seating-mix-tally").textContent = picked.length
        ? `${plural(picked.length, "room")} chosen: ${picked.map((room) => room.name).join(", ")} · ${sum("usable_count")} usable · ${sum("spaced_count")} spaced` : "";
    }
    // Words typed narrow the list to the rooms whose names hold them all (any order, case ignored); a ticked room stays ticked.
    function findRooms(typed) {
      const words = typed.toLowerCase().split(/\s+/).filter(Boolean);
      mixRooms.querySelectorAll(".seating-room-row").forEach((row) => { row.hidden = !words.every((word) => row.dataset.roomName.toLowerCase().includes(word)); });
      mixRooms.querySelectorAll(".seating-room-group").forEach((group) => { group.hidden = !group.querySelector(".seating-room-row:not([hidden])"); });
      mixRooms.querySelector("[data-room-none]").hidden = Boolean(mixRooms.querySelector(".seating-room-row:not([hidden])"));
    }
    async function loadOptions() {
      const current = ++request;
      const selects = mixing() ? mixChosen() : chosen();
      if (new Set(selects.map((select) => select.dataset.term)).size > 1) {
        forecast.innerHTML = '<p class="field-hint">Choose sections from one term only.</p>';
        drHost.innerHTML = "";
        return;
      }
      if (!selects.length) {
        forecast.innerHTML = "";
        drHost.innerHTML = "";
        return;
      }
      const roomIds = mixing() ? mixRoomIds() : [];
      try {
        const options = await api(`/api/seating/options?assignment_id=${encodeURIComponent(form.elements.assignment_id.value)}&sections=${selects.map((select) => encodeURIComponent(select.dataset.section)).join(",")}${roomIds.length > 1 ? `&rooms=${roomIds.map(encodeURIComponent).join(",")}` : ""}`);
        if (current !== request || !active()) return;
        drHost.innerHTML = relinkField(options.dr, options.assignment_title);
        if (mixing()) {
          forecast.innerHTML = mixForecast(options, selects);
          return;
        }
        const byRoom = new Map();
        selects.forEach((select) => {
          const stats = options.per_section[select.dataset.section] || { students: 0, excluded: 0 };
          const entry = byRoom.get(select.value) || { room: catalog.rooms.find((room) => room.id === select.value), students: 0, excluded: 0, sections: [] };
          entry.students += stats.students;
          entry.excluded += stats.excluded;
          entry.sections.push(select.dataset.number);
          byRoom.set(select.value, entry);
        });
        // One row per room: who is seated there, against the room's usable and spaced seats.
        forecast.innerHTML = `<table class="seating-forecast-table"><caption>Forecast · DR bookings for ${escape(options.assignment_title)} are not seated</caption><thead><tr><th scope="col">Room</th><th scope="col">Sections</th><th scope="col">Students</th><th scope="col">DR</th><th scope="col">To seat</th><th scope="col">Usable</th><th scope="col">Spaced</th></tr></thead><tbody>${[...byRoom.values()].map((entry) => {
          const toSeat = entry.students - entry.excluded;
          const note = entry.room && toSeat > entry.room.usable_count ? "Too many for this room" : entry.room && toSeat > entry.room.spaced_count ? "Not everyone can be spaced" : "";
          return `<tr${note ? ' class="seating-forecast-warning"' : ""}><th scope="row">${escape(entry.room?.name || "Room")}</th><td>${entry.sections.join(", ")}</td><td>${entry.students}</td><td>${entry.excluded}</td><td>${toSeat}</td><td>${entry.room?.usable_count ?? "?"}</td><td>${entry.room?.spaced_count ?? "?"}</td></tr>${note ? `<tr class="seating-forecast-note"><td colspan="7">${note}</td></tr>` : ""}`;
        }).join("")}</tbody></table>`;
      } catch (cause) {
        if (current === request && active()) forecast.innerHTML = `<p class="form-error">${escape(cause.message)}</p>`;
      }
    }
    // One row per room of the mix: the share it seats, against its usable and spaced seats.
    function mixForecast(options, selects) {
      const students = selects.reduce((sum, select) => sum + (options.per_section[select.dataset.section]?.students || 0), 0);
      const excluded = selects.reduce((sum, select) => sum + (options.per_section[select.dataset.section]?.excluded || 0), 0);
      const summary = `<p class="field-hint">${plural(students - excluded, "student")} from Section${selects.length === 1 ? "" : "s"} ${selects.map((select) => select.dataset.number).join(", ")} to mix · ${plural(excluded, "DR booking")} not seated</p>`;
      if (!options.pool) return `${summary}<p class="field-hint">Choose at least two rooms.</p>`;
      if (options.pool_error) return `${summary}<p class="form-error">${escape(options.pool_error)}</p>`;
      return `${summary}<table class="seating-forecast-table"><caption>Forecast · each room's share of the mix</caption><thead><tr><th scope="col">Room</th><th scope="col">To seat</th><th scope="col">Usable</th><th scope="col">Spaced</th></tr></thead><tbody>${options.pool.map((entry) => {
        const room = catalog.rooms.find((item) => item.id === entry.room_id);
        const note = room && entry.to_seat > room.spaced_count ? "Not everyone can be spaced" : "";
        return `<tr${note ? ' class="seating-forecast-warning"' : ""}><th scope="row">${escape(room?.name || "Room")}</th><td>${entry.to_seat}</td><td>${room?.usable_count ?? "?"}</td><td>${room?.spaced_count ?? "?"}</td></tr>${note ? `<tr class="seating-forecast-note"><td colspan="4">${note}</td></tr>` : ""}`;
      }).join("")}</tbody></table>`;
    }
    form.elements.assignment_id.onchange = () => { sectionRows(); };
    rows.addEventListener("change", () => loadOptions());
    form.querySelector('[data-mode="mix"]').addEventListener("change", (event) => {
      if (event.target.matches("[data-room-find]")) return;
      mixTally();
      loadOptions();
    });
    const find = form.querySelector("[data-room-find]");
    if (find) {
      find.addEventListener("input", () => findRooms(find.value));
      // Enter in the box is not Create seating chart.
      find.addEventListener("keydown", (event) => { if (event.key === "Enter") event.preventDefault(); });
    }
    form.querySelectorAll('input[name="seating_mode"]').forEach((input) => input.addEventListener("change", () => {
      form.querySelectorAll("[data-mode]").forEach((node) => { node.hidden = node.dataset.mode !== form.elements.seating_mode.value; });
      loadOptions();
    }));
    sectionRows();
    form.onsubmit = (event) => {
      event.preventDefault();
      runFormAction(form, async () => {
        const data = new FormData(form);
        const allocations = new Map();
        chosen().forEach((select) => {
          if (!allocations.has(select.value)) allocations.set(select.value, []);
          allocations.get(select.value).push(select.dataset.section);
        });
        const placement = mixing() ? { pool: { room_ids: mixRoomIds(), section_ids: mixChosen().map((select) => select.dataset.section) } }
          : { allocations: [...allocations].map(([room_id, section_ids]) => ({ room_id, section_ids })) };
        const result = await api("/api/seating/plan", "POST", {
          assignment_id: data.get("assignment_id"),
          exam_version_count: data.get("exam_version_count") ? Number(data.get("exam_version_count")) : null,
          allow_unequal_versions: data.get("allow_unequal_versions") === "on",
          ...placement,
          relink_assignment_ids: data.getAll("relink_assignment_ids"), notes: data.get("notes") || "",
        });
        const visible = active();
        if (!wizard && visible) document.querySelector("#dialog").close();
        const seated = result.charts.reduce((sum, item) => sum + item.counts.seated, 0);
        const excluded = result.charts.reduce((sum, item) => sum + item.counts.excluded_dr, 0);
        const failed = result.charts.filter((item) => !item.check?.ok).length;
        toast(`${result.charts.length === 1 ? "Seating chart" : `${result.charts.length} seating charts`} created: ${seated} seated, ${excluded} left out for DR testing. ${failed ? `Roster check flagged ${failed} chart${failed === 1 ? "" : "s"}.` : "Roster check passed."}`, Boolean(failed));
        // One room opens its chart; several open the exam's page of rooms (SEATING "One tile per exam").
        if (!wizard && visible) location.hash = result.charts.length === 1 ? href(result.charts[0]) : examHref(result.charts[0]);
        await refresh(true);
        if (wizard && active()) wizard.onCreated(result);
      });
    };
  }

  return { openCreate };
}
