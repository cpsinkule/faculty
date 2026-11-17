// Browser-only navigation. Panels stay mounted so changing questions cannot
// discard an answer-key or finalization draft. No selection is a saved approval.
import {registerShortcut} from "./ui.js";

const remembered = new Map();
// The page's '[' and ']' belong to the one navigator on screen: a newer page navigator replaces the older registration.
let pageShortcuts = [];

// Items: {id, label, detail, attention, group?, unsaved?}. `group` (a version heading such as "Version A") is drawn above the first
// item of each run of the same group, so callers pass items already in that order. `shortcuts` gives the page's navigator the
// '[' and ']' keys (never a dialog's; the shared registry ignores keys typed in fields and while a dialog is open). `find: false`
// leaves out the search and the filter for a short list, where they only add controls (the rubric finalization, 2026-10-06).
export function mountQuestionWorkspace(host, initialItems, h, {key, initial = '', shortcuts = false, find = true} = {}) {
  let items = initialItems, selected = initial || remembered.get(key) || items.find(i => i.attention)?.id || items[0]?.id;
  const panels = new Map([...host.querySelectorAll('[data-question-panel]')].map(node => [node.dataset.questionPanel, node]));
  host.classList.add('grading-question-workspace');
  const rail = document.createElement('aside'), work = document.createElement('div');
  rail.className = 'grading-question-rail'; work.className = 'grading-question-work';
  const nav = document.createElement('nav');
  nav.setAttribute('aria-label', 'Questions');
  rail.innerHTML = `<h4>Questions</h4><div class="grading-question-find" ${find ? '' : 'hidden'}>${h.field('Find a question', 'question-search', '', 'search', 'placeholder="Question or version…"')}${h.select('Questions to show', 'question-filter', [['all', 'All questions'], ['attention', 'Needs attention']])}</div><p class="supporting-copy" data-question-count></p>`;
  rail.append(nav);
  // The pager stays in view (sticky) while a long question scrolls, so it needs no second copy at the foot.
  work.innerHTML = `<div class="grading-question-pager"><span data-question-position></span><div><button type="button" class="button" data-question-previous>← Previous question</button><button type="button" class="button" data-question-next>Next question →</button><button type="button" class="text-button" data-question-attention>Next needing attention →</button></div></div><p class="empty-state" data-no-questions hidden>No questions match. Change the search or show all questions.</p>`;
  for (const panel of panels.values()) work.append(panel);
  host.replaceChildren(rail, work);
  const search = rail.querySelector('input'), filter = rail.querySelector('select');

  // The list itself: drawn again only when the questions or their groups change, never on a selection.
  let drawnShape = '';
  function drawList() {
    const shape = items.map(i => `${i.id}\u001f${i.group || ''}`).join('\u001e');
    if (shape === drawnShape) return;
    drawnShape = shape;
    const counts = new Map();
    for (const item of items) if (item.group) counts.set(item.group, (counts.get(item.group) || 0) + 1);
    let previous = null;
    nav.innerHTML = items.map(i => {
      const heading = i.group && i.group !== previous ? `<p class="grading-question-group" data-question-group="${h.e(i.group)}">${h.e(i.group)} <span>${h.e(counts.get(i.group))}</span></p>` : '';
      previous = i.group || null;
      return `${heading}<button type="button" class="grading-question-link" data-question-choice="${h.e(i.id)}"><strong><span data-question-label></span><span class="grading-question-unsaved" data-question-unsaved></span></strong><small></small></button>`;
    }).join('');
    nav.querySelectorAll('[data-question-choice]').forEach(button => button.onclick = () => { selected = button.dataset.questionChoice; draw(); });
  }
  const matches = () => items.filter(i => (filter.value !== 'attention' || i.attention) && `${i.label} ${i.detail} ${i.group || ''}`.toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase()));
  // The next question needing attention after the one shown, in list order and wrapping.
  function nextAttention() {
    const start = items.findIndex(i => i.id === selected);
    for (let step = 1; step <= items.length; step += 1) {
      const item = items[(Math.max(start, 0) + step) % items.length];
      if (item.attention && item.id !== selected) return item;
    }
    return null;
  }
  function draw() {
    drawList();
    const visible = matches();
    if (!visible.some(i => i.id === selected)) selected = visible[0]?.id;
    if (selected && key) remembered.set(key, selected);
    for (const [id, panel] of panels) {
      panel.hidden = id !== selected;
      if (panel instanceof HTMLDetailsElement && !panel.hidden) panel.open = true;
    }
    for (const button of nav.querySelectorAll('[data-question-choice]')) {
      const item = items.find(i => i.id === button.dataset.questionChoice);
      button.hidden = !visible.includes(item);
      button.setAttribute('aria-pressed', String(item.id === selected));
      button.querySelector('[data-question-label]').textContent = item.label;
      button.querySelector('[data-question-unsaved]').textContent = item.unsaved ? ' · unsaved' : '';
      button.querySelector('small').textContent = item.detail;
      button.classList.toggle('needs-attention', Boolean(item.attention));
    }
    for (const heading of nav.querySelectorAll('[data-question-group]')) {
      const members = items.filter(i => i.group === heading.dataset.questionGroup);
      heading.hidden = !members.some(i => visible.includes(i));
      const waiting = members.filter(i => i.attention).length;
      heading.querySelector('span').textContent = waiting ? `${members.length} · ${waiting} need${waiting === 1 ? 's' : ''} attention` : String(members.length);
    }
    const waiting = items.filter(i => i.attention).length, count = rail.querySelector('[data-question-count]');
    count.textContent = find ? `${visible.length} of ${items.length} questions · ${waiting} need attention` : waiting ? `${waiting} need${waiting === 1 ? 's' : ''} attention` : '';
    count.hidden = !count.textContent;
    const index = visible.findIndex(i => i.id === selected);
    work.querySelector('[data-question-position]').textContent = selected ? `${index + 1} of ${visible.length}` : '';
    work.querySelector('[data-question-previous]').disabled = index <= 0;
    work.querySelector('[data-question-next]').disabled = index < 0 || index === visible.length - 1;
    work.querySelector('[data-question-attention]').hidden = !nextAttention();
    work.querySelector('[data-no-questions]').hidden = Boolean(selected);
  }
  const choose = id => { selected = id; draw(); };
  const move = delta => {
    const visible = matches(), index = visible.findIndex(i => i.id === selected), target = visible[index + delta];
    if (target) choose(target.id);
  };
  search.addEventListener('input', draw); filter.addEventListener('change', draw);
  work.querySelector('[data-question-previous]').onclick = () => move(-1);
  work.querySelector('[data-question-next]').onclick = () => move(1);
  work.querySelector('[data-question-attention]').onclick = () => {
    const target = nextAttention();
    if (!target) return;
    if (!matches().includes(target)) { filter.value = 'all'; search.value = ''; }
    choose(target.id);
    if (work.getBoundingClientRect().top < 0) work.scrollIntoView({block: 'start'});
  };
  if (shortcuts) {
    pageShortcuts.forEach(off => off());
    const shown = () => host.isConnected && !host.closest('[hidden]');
    pageShortcuts = [
      registerShortcut({key: '[', label: 'Previous question', when: shown, run: () => move(-1)}),
      registerShortcut({key: ']', label: 'Next question', when: shown, run: () => move(1)}),
    ];
  }
  draw();
  return {update: updated => { items = updated; draw(); }, select: id => { filter.value = 'all'; search.value = ''; choose(id); }};
}
