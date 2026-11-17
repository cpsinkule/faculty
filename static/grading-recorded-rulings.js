// What recorded meetings said of how an assessment is graded (GRADING "RULINGS SAID IN A RECORDING"; RECORDINGS "A RULING", step 3).
// A ruling waits on its assessment until the rubric is finalized: the finalization window lists each one with the exact words it
// rests on and where to hear them, and the instructor adds it to a rubric line's clarification or declines it. NEVER APPLIED BY
// ITSELF: adding puts the sentence in that line's clarification field, which is theirs to edit and is recorded only by their own
// Confirm; nothing is offered on a certified plan (the server answers none). Where a rubric is imported, words that it is NOT
// final are shown as a warning. A transcript is evidence, not an instruction: Plaud mishears, so each one links to its moment.
import {plural} from './ui.js';

const NONE = {rulings: [], not_final: null, certified: false};

// Recordings may be absent or unread: the window then works as before.
export async function loadRulings(h, aid) {
  try {
    return await h.api(`/api/recordings/rulings?assignment=${encodeURIComponent(aid)}`) || NONE;
  } catch {
    return NONE;
  }
}

const number = said => (/\d+/.exec(String(said || '')) || [''])[0];
// HELD BY NAME: a ruling the reading had no listing for is here because the name the meeting called it by is this assessment's.
const heard = (ruling, e) => `${e(ruling.speaker)}, ${e(ruling.day)} at ${e(ruling.at)}: “${e(ruling.quote)}” <a class="link-arrow" href="${e(ruling.href)}" target="_blank" rel="noopener noreferrer">Hear it</a>${ruling.named ? ` <span data-ruling-named>Said of “${e(ruling.named)}”.</span>` : ''}`;

// The rubric lines a ruling may be added to: those of the questions printed with the number that was said, else every line.
// `questions`: the finalization window's own ([{question_id, printed_number, exam_version, title, rubric: {items: [{id, description}]}}]).
export function lineChoices(ruling, questions, sep) {
  const said = number(ruling.question);
  const named = said ? questions.filter(q => number(q.printed_number) === said) : [];
  return (named.length ? named : questions).flatMap(q => q.rubric.items.map((item, index) => ({
    value: `${q.question_id}${sep}${item.id}`,
    label: `${q.exam_version ? `Version ${q.exam_version} · ` : ''}${q.title} · Item ${index + 1}: ${String(item.description || '').replace(/\s+/g, ' ').slice(0, 70)}`})));
}

export function rulingMarkup(ruling, questions, e, sep) {
  const choices = lineChoices(ruling, questions, sep);
  const settled = ruling.state === 'accepted' ? 'Added to a clarification.' : ruling.state === 'declined' ? 'Declined for this rubric.' : '';
  return `<li class="quality-ruling" data-ruling="${e(ruling.id)}" data-ruling-recording="${e(ruling.recording_id)}" data-ruling-state="${e(ruling.state)}">
    <p><strong>${ruling.question ? `Question ${e(ruling.question)}. ` : ''}</strong>${e(ruling.sentence)}</p>
    <p class="supporting-copy">${heard(ruling, e)}</p>
    ${settled ? `<p class="supporting-copy" data-ruling-settled>${settled}</p>`
      : `<div class="quality-ruling-actions"><label class="field">Rubric line<select data-ruling-line aria-label="Rubric line for this ruling"><option value="">Choose a rubric line</option>${choices.map(c => `<option value="${e(c.value)}">${e(c.label)}</option>`).join('')}</select></label>
        <button type="button" class="button" data-ruling-use>Add to its clarification</button><button type="button" class="text-button" data-ruling-decline>Not for this rubric</button></div>`}
  </li>`;
}

// The window's section: folded unless one still waits. '' when no recorded meeting said anything of this assessment's grading.
export function rulingsMarkup(held, questions, e, sep) {
  const rulings = held?.rulings || [];
  if (!rulings.length) return '';
  const waiting = rulings.filter(r => r.state === 'waiting').length;
  return `<details class="compute-fold quality-rulings" data-recorded-rulings ${waiting ? 'open' : ''}><summary>Said in your recorded meetings · <span data-rulings-count>${waiting ? `${waiting} waiting` : `${plural(rulings.length, 'statement')}, all settled`}</span></summary>
    <div class="compute-fold-body"><p class="supporting-copy">How a meeting said this assessment is graded. Nothing here is applied by itself: adding one puts its sentence in a rubric line’s clarification, yours to edit, and it counts only when you confirm below. Plaud mishears, so hear it first.</p>
    <ul class="quality-ruling-list">${rulings.map(r => rulingMarkup(r, questions, e, sep)).join('')}</ul></div></details>`;
}

// Adding and declining. `field(question_id, item_id)` answers that line's clarification textarea in the window.
export function bindRulings(form, held, {h, aid, questions, sep, field}) {
  const host = form.querySelector('[data-recorded-rulings]');
  if (!host) return;
  const settle = async (row, state) => {
    await h.api(`/api/recordings/${encodeURIComponent(row.dataset.rulingRecording)}/items/${encodeURIComponent(row.dataset.ruling)}`, 'POST',
      {state, assignment: aid, request_id: crypto.randomUUID()});
    const ruling = held.rulings.find(r => r.id === row.dataset.ruling);
    ruling.state = state;
    row.outerHTML = rulingMarkup(ruling, questions, h.e, sep);
    const waiting = held.rulings.filter(r => r.state === 'waiting').length;
    host.querySelector('[data-rulings-count]').textContent = waiting ? `${waiting} waiting` : `${plural(held.rulings.length, 'statement')}, all settled`;
  };
  host.addEventListener('click', event => {
    const button = event.target.closest('[data-ruling-use],[data-ruling-decline]');
    const row = button?.closest('[data-ruling]');
    if (!row) return;
    void h.run(button, async () => {
      if (button.hasAttribute('data-ruling-decline')) { await settle(row, 'declined'); return; }
      const [question, item] = String(row.querySelector('[data-ruling-line]').value || '').split(sep);
      const area = question && item ? field(question, item) : null;
      if (!area) throw new Error('Choose the rubric line this is about first.');
      const ruling = held.rulings.find(r => r.id === row.dataset.ruling);
      const before = area.value.trim();
      if (!before.includes(ruling.sentence)) area.value = `${before}${before ? ' ' : ''}${ruling.sentence}`.slice(0, 2000);
      area.dispatchEvent(new Event('input', {bubbles: true}));
      await settle(row, 'accepted');
      h.toast('Added to that line’s clarification. It counts once you confirm the rubrics.');
    });
  });
}

// Where a rubric is imported: the latest thing a meeting said of this assessment's rubric, when it is that it is NOT final.
export function notFinalMarkup(held, e) {
  const said = held?.not_final;
  return said ? `<p class="grading-warning" role="status" data-recorded-not-final><strong>A meeting said this rubric is not final.</strong> ${heard(said, e)}</p>` : '';
}

// Put that warning at the top of an import dialog once it is known; a dialog closed meanwhile is left alone.
export function mountNotFinal(form, h, aid) {
  void loadRulings(h, aid).then(held => {
    const body = form.isConnected ? form.querySelector('.dialog-body') : null;
    const markup = notFinalMarkup(held, h.e);
    if (body && markup && !body.querySelector('[data-recorded-not-final]')) body.insertAdjacentHTML('afterbegin', markup);
  });
}
