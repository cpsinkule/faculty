import { captureDialogIntent, dialogSession, runFormAction } from "./ui.js";

/**
 * Seat moves and exam-version editing for the existing chart controller. The
 * selections and in-flight guards are browser state only; each write retains its
 * existing revision check and recovery read, with no automatic move/swap retry.
 */
export function createSeatingInteractions({ api, escape, toast, showDialog, dialogHeader, getChart, setChart, isVersionView, chartId, invalidateRender, renderDetail, render, refresh, closeSeatPopover }) {
  let moveSelection = null;
  let moveRequest = null;
  let versionSelection = null;
  let versionRequest = null;

  function versionStatusMarkup() {
    const chart = getChart();
    const versionView = isVersionView();
    const versions = chart.exam_versions;
    if (!chart.exam_version_count) return versionView
      ? '<section class="seating-version-status"><p>Choose Assign exam versions to show their letters on the seats.</p></section>' : "";
    const conflicts = versions?.conflicts || [], missing = versions?.unassigned || [];
    const counts = (versions?.counts || []).map((item) => item.count);
    const imbalanced = !chart.allow_unequal_versions && counts.length && Math.max(...counts) - Math.min(...counts) > 1;
    return `<section class="seating-version-status${conflicts.length || missing.length || imbalanced ? " needs-attention" : ""}" aria-label="Exam versions"><div><strong>${chart.exam_version_count} exam version${chart.exam_version_count === 1 ? "" : "s"}</strong><p>${(versions?.counts || []).map((item) => `<span class="seating-version-total"><span class="seating-version-key" data-exam-version="${escape(item.version)}">${escape(item.version)}</span>: ${item.count}</span>`).join(" ")}</p><p class="field-hint">${chart.allow_unequal_versions ? "Unequal version counts allowed." : "Automatic assignment uses equal counts, differing by at most one."}</p>${imbalanced ? '<p class="seating-version-warning" role="status">Saved totals now differ by more than one. Review your available exam copies, swap or edit versions, or reassign to balance the counts.</p>' : ""}${missing.length ? `<p class="seating-version-warning" role="status">Version needed at ${escape(missing.join(", "))}. Choose a letter in Version view or reassign versions.</p>` : ""}${conflicts.length ? `<p class="seating-version-warning" role="status">Same-version neighbors: ${conflicts.map((item) => `${escape(item.row)} · ${escape(item.seats.join(" / "))} (${escape(item.version)})`).join("; ")}. Review these seats in Version view.</p>` : ""}${versionView ? '<p class="field-hint">Letters stay with seats. Changing versions does not move students or send notices.</p>' : ""}</div><button type="button" class="button" data-seating-action="assign-versions">Reassign versions</button></section>`;
  }

  function clearVersionSelection(restoreFocus = false) {
    if (!versionSelection) return;
    const { root, button } = versionSelection;
    versionSelection = null;
    root.querySelector("#seating-version-editor")?.remove();
    root.querySelector(".seating-version-workspace")?.classList.remove("editing-versions");
    button?.classList.remove("version-source");
    button?.removeAttribute("aria-pressed");
    button?.removeAttribute("aria-expanded");
    if (restoreFocus) button?.focus({ preventScroll: true });
  }

  function selectVersionSeat(root, button, seat) {
    const chart = getChart();
    if (versionRequest || !seat.student || seat.broken || seat.blocked) return;
    if (!chart.exam_version_count) {
      openVersionAssign().catch((cause) => toast(cause.message, true));
      return;
    }
    if (versionSelection) {
      if (versionSelection.seat.id === seat.id) clearVersionSelection(true);
      else if (!versionSelection.seat.exam_version || !seat.exam_version) toast("Choose a version for each seat before swapping their letters.", true);
      else void saveVersionEdit({ target_seat_id: seat.id });
      return;
    }
    closeSeatPopover(root);
    const holder = root.querySelector(".seating-version-workspace");
    const pop = document.createElement("div");
    pop.id = "seating-version-editor";
    pop.className = "seat-popover seat-version-popover";
    pop.tabIndex = -1;
    pop.setAttribute("role", "dialog");
    pop.setAttribute("aria-label", `Exam version for seat ${seat.label}`);
    pop.innerHTML = `<div class="seat-popover-head"><strong>Seat ${escape(seat.label)} · ${seat.exam_version ? `Version ${escape(seat.exam_version)}` : "No version yet"}</strong><span>${escape(seat.student.name)}</span><button type="button" class="text-button" data-cancel-version aria-label="Cancel version selection">×</button></div><p data-version-instructions>Click another occupied seat to swap versions, or choose a letter here.</p><div class="seating-version-choices" aria-label="Choose exam version">${Array.from({ length: chart.exam_version_count }, (_, i) => String.fromCharCode(65 + i)).map((letter) => `<button type="button" class="button" data-set-exam-version="${letter}" data-exam-version="${letter}" aria-pressed="${letter === seat.exam_version}">${letter}</button>`).join("")}</div><button type="button" class="text-button" data-cancel-version>Cancel</button>`;
    holder.appendChild(pop);
    holder.classList.add("editing-versions");
    versionSelection = { root, button, seat, chartId: chart.id, version: chart.version };
    button.classList.add("version-source");
    button.setAttribute("aria-pressed", "true");
    button.setAttribute("aria-expanded", "true");
    pop.querySelectorAll("[data-cancel-version]").forEach((item) => { item.onclick = () => { if (!versionRequest) clearVersionSelection(true); }; });
    pop.querySelectorAll("[data-set-exam-version]").forEach((item) => { item.onclick = () => { void saveVersionEdit({ exam_version: item.dataset.setExamVersion }); }; });
    pop.focus({ preventScroll: true });
    pop.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  async function saveVersionEdit(change) {
    const chart = getChart();
    const selected = versionSelection;
    if (!selected || versionRequest) return;
    const { root, seat } = selected;
    if (selected.chartId !== chartId() || selected.version !== chart?.version || root.hidden) {
      clearVersionSelection();
      toast("The chart changed. Select the seat again before changing its version.", true);
      return;
    }
    versionRequest = selected;
    invalidateRender();
    setMoveBusy(root, true);
    root.querySelector("[data-version-instructions]").textContent = "Saving exam versions…";
    let reload = false;
    try {
      const result = await api(`/api/seating/${encodeURIComponent(selected.chartId)}/exam-versions/edit`, "POST", { version: selected.version, seat_id: seat.id, ...change });
      if (versionSelection === selected && chartId() === selected.chartId && !root.hidden) {
        versionRequest = null;
        setChart(result);
        renderDetail(root);
        root.querySelector(`.seat-chart [data-seat="${CSS.escape(seat.id)}"]`)?.focus({ preventScroll: true });
      } else reload = true;
      toast(change.target_seat_id ? "Exam versions swapped." : `Seat ${seat.label} now has version ${change.exam_version}.`);
      void refresh(true);
    } catch (cause) {
      clearVersionSelection();
      toast(`${cause.message} Review the chart before trying again.`, true);
      reload = true;
    } finally {
      versionRequest = null;
      setMoveBusy(root, false);
    }
    // A lost response may follow a committed edit. Read it back; never repeat a
    // version swap automatically or let a delayed result replace another chart.
    if (reload && chartId() === selected.chartId && !root.hidden) await render(true);
  }

  async function openVersionAssign() {
    const intended = captureDialogIntent(document.querySelector("#dialog"));
    const chart = getChart();
    const id = chart.id;
    const current = await api(`/api/seating/${encodeURIComponent(id)}`);
    if (chartId() !== id || !intended()) return;
    showDialog(`<form id="seating-version-assign">${dialogHeader(current.exam_version_count ? "Reassign exam versions" : "Assign exam versions", escape(`${current.title} · ${current.room_name}`))}<div class="dialog-body"><label class="field">Number of exam versions<input type="number" name="version_count" min="1" max="26" step="1" value="${current.exam_version_count || 2}" required></label><p class="field-hint">Versions are named A, B, C, and so on. With two or more, successive students in a row receive different letters, even across empty seats. One version gives everyone A.</p><label class="checkbox-field"><input type="checkbox" name="allow_unequal_versions" ${current.allow_unequal_versions ? "checked" : ""}>Allow unequal version counts</label><p class="field-hint">By default, totals are equal or differ by one. Allow unequal counts to favor fewer matching versions in nearby rows.</p><p>Save a version for every occupied seat. Versions stay with their seats when students move.</p>${current.exam_version_count ? '<p class="seating-version-warning">Reassigning replaces all saved version choices in this room, including manual edits.</p>' : ""}<p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Assign versions</button></footer></form>`);
    const form = document.querySelector("#seating-version-assign");
    const active = dialogSession(form, document.querySelector("#dialog"));
    form.onsubmit = (event) => {
      event.preventDefault();
      runFormAction(form, async () => {
        if (!active() || chartId() !== id) return;
        const result = await api(`/api/seating/${encodeURIComponent(id)}/exam-versions`, "POST", { version: current.version, count: form.elements.version_count.valueAsNumber, allow_unequal_versions: form.elements.allow_unequal_versions.checked });
        if (chartId() === id && active()) {
          document.querySelector("#dialog").close();
          setChart(result);
          renderDetail(document.querySelector("#seating-section"));
        }
        toast("Exam versions assigned and saved.");
        void refresh(true);
      });
    };
  }

  function openMove(student) {
    const chart = getChart();
    const versionView = isVersionView();
    if (!student || moveRequest || versionRequest || versionView) return;
    clearMoveSelection();
    const root = document.querySelector("#seating-section");
    closeSeatPopover(root);
    const source = chart.room.seats.find((seat) => seat.student?.enrollment_id === student.enrollment_id);
    const button = source && root.querySelector(`.seat-chart [data-seat="${CSS.escape(source.id)}"]`);
    moveSelection = { root, chartId: chart.id, version: chart.version, student, source, button };
    button?.classList.add("move-source");
    button?.setAttribute("aria-pressed", "true");
    button?.setAttribute("aria-describedby", "seating-move-instructions");
    const notice = root.querySelector(".seating-move-selection");
    notice.hidden = false;
    // On a confirmed chart a move replaces the student's response card (the server revokes it at the move); say so up front.
    const card = (chart.students || []).find((item) => item.enrollment_id === student.enrollment_id)?.card || student.card;
    const marked = (chart.print_batches || []).some((batch) => batch.seat_marks && !batch.superseded_at && batch.status !== "cancelled");
    const consequences = [card ? "their response card is revoked and they need an updated seat card" : "",
      source && marked ? "the seat number printed beside their stamps will no longer match" : ""].filter(Boolean);
    const confirmed = chart.confirmed_at && consequences.length ? ` This chart is confirmed: if they move, ${consequences.join(", and ")}.` : "";
    notice.querySelector("p").textContent = source
      ? `${student.name} selected at ${source.label}.${confirmed} Click an empty seat to move them, or an occupied seat to swap students. Press Escape to cancel.`
      : `${student.name} selected.${confirmed} Click an empty seat to seat them. Press Escape to cancel.`;
    const focus = button || root.querySelector('.seat-chart .seat.free');
    focus?.focus({ preventScroll: true });
    (focus || notice).scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  function clearMoveSelection(restoreFocus = false) {
    if (!moveSelection) return;
    const { root, button } = moveSelection;
    moveSelection = null;
    button?.classList.remove("move-source");
    button?.removeAttribute("aria-pressed");
    button?.removeAttribute("aria-describedby");
    const notice = root.querySelector(".seating-move-selection");
    if (notice) notice.hidden = true;
    if (restoreFocus) button?.focus({ preventScroll: true });
  }

  function setMoveBusy(root, busy) {
    root.querySelector(".seat-grid")?.setAttribute("aria-busy", String(busy));
    if (busy) {
      root.querySelectorAll('.seat-chart [data-seat], [data-move], [data-seating-action], [data-cancel-move], [data-set-exam-version], [data-cancel-version]').forEach((button) => {
        if (!button.disabled) {
          button.dataset.moveDisabled = "true";
          button.disabled = true;
        }
      });
    } else {
      root.querySelectorAll('[data-move-disabled]').forEach((button) => {
        button.disabled = false;
        delete button.dataset.moveDisabled;
      });
    }
  }

  async function moveToSeat(seat) {
    const chart = getChart();
    const selected = moveSelection;
    if (!selected || moveRequest) return;
    const { root, student, source } = selected;
    if (selected.chartId !== chartId() || selected.version !== chart?.version || root.hidden) {
      clearMoveSelection();
      toast("The chart changed. Select the student again before moving them.", true);
      return;
    }
    if (seat.id === source?.id) {
      clearMoveSelection(true);
      return;
    }
    if (seat.broken || seat.blocked) {
      toast(`Seat ${seat.label} is ${seat.broken ? "broken" : "blocked"}. Choose another seat.`, true);
      return;
    }
    if (seat.student && (!source || source.broken || source.blocked)) {
      toast(source ? "Choose an empty seat so the other student is not swapped into an unusable seat." : "Choose an empty seat for a student who does not have a seat yet.", true);
      return;
    }
    // Capture the exact chart revision and suppress more clicks until this one
    // request finishes. No automatic retry: a lost response may follow a saved move.
    moveRequest = selected;
    invalidateRender();
    setMoveBusy(root, true);
    root.querySelector(".seating-move-selection p").textContent = `Moving ${student.name} to ${seat.label}…`;
    let reload = false;
    let moved = false;
    try {
      const result = await api(`/api/seating/${encodeURIComponent(selected.chartId)}/move`, "POST", {
        version: selected.version, enrollment_id: student.enrollment_id, seat_id: seat.id,
      });
      if (moveSelection === selected && chartId() === selected.chartId && !root.hidden) {
        moveRequest = null;
        setChart(result);
        renderDetail(root);
        root.querySelector(`.seat-chart [data-seat="${CSS.escape(seat.id)}"]`)?.focus({ preventScroll: true });
      } else {
        // Navigation may have replaced this chart with a saving placeholder.
        // Restore the active chart directly once the request guard is released.
        reload = true;
      }
      toast(seat.student ? `${student.name} and ${seat.student.name} swapped seats.` : `${student.name} seated at ${seat.label}.`);
      moved = true;
    } catch (cause) {
      if (moveSelection === selected) clearMoveSelection();
      toast(`${cause.message} Review the chart before trying again.`, true);
      reload = true;
    } finally {
      moveRequest = null;
      setMoveBusy(root, false);
    }
    if (reload && chartId() === selected.chartId && !root.hidden) await render(true);
    if (moved) void refresh(true);
  }

  function openSeatHere(seat) {
    const chart = getChart();
    const id = chart.id;
    const unseated = chart.students.filter((item) => item.status === "unseated");
    if (!unseated.length) {
      toast(`Seat ${seat.label} is free. Every student already has a seat; use Move on a student to bring them here.`);
      return;
    }
    showDialog(`<form id="seating-fill">${dialogHeader(`Seat someone at ${escape(seat.label)}`, "Students without a seat")}<div class="dialog-body"><label class="field">Student<select name="enrollment_id" aria-label="Student" required>${unseated.map((item) => `<option value="${escape(item.enrollment_id)}">${escape(item.name)} · Section ${item.section_number}</option>`).join("")}</select></label><p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Seat here</button></footer></form>`);
    const form = document.querySelector("#seating-fill");
    const active = dialogSession(form, document.querySelector("#dialog"));
    form.onsubmit = (event) => {
      event.preventDefault();
      runFormAction(form, async () => {
        if (!active() || chartId() !== id) return;
        const result = await api(`/api/seating/${encodeURIComponent(id)}/move`, "POST", { version: chart.version, enrollment_id: form.elements.enrollment_id.value, seat_id: seat.id });
        if (chartId() === id && active()) {
          setChart(result);
          document.querySelector("#dialog").close();
          renderDetail(document.querySelector("#seating-section"));
        }
        toast(`Seated at ${seat.label}.`);
        void refresh();
      });
    };
  }

  return {
    versionStatusMarkup, clearVersionSelection, selectVersionSeat, openVersionAssign,
    openMove, clearMoveSelection, setMoveBusy, moveToSeat, openSeatHere,
    get moveSelection() { return moveSelection; },
    get moveRequest() { return moveRequest; },
    get versionSelection() { return versionSelection; },
    get versionRequest() { return versionRequest; },
  };
}
