// RUBRIC CHOICES (requirement, 2026-10-24). The same few questions came back on assignment after assignment; answered once here, before the rubrics are
// drafted, the writers follow them and the rubric check applies them instead of asking. "Ask me" leaves a question to the check, as
// before. The server owns the choices offered (`grading/rubric_choices.py`); this draws and reads them, nothing more.

// One fieldset per choice: its question, "Ask me", then its options; `current` ({key: option}) preselects.
export function choicesMarkup(options, current, escape) {
  return `<div class="rubric-choices" data-rubric-choices>${options.map(choice => `<fieldset class="quality-policy-choices rubric-choice"><legend>${escape(choice.topic)}</legend>
    <p class="supporting-copy">${escape(choice.question)}</p>
    ${[{value: '', label: 'Ask me'}, ...choice.options].map(option => `<label class="quality-policy-choice"><input type="radio" name="rubric-choice-${escape(choice.key)}" value="${escape(option.value)}" ${(current?.[choice.key] || '') === option.value ? 'checked' : ''}><span>${escape(option.label)}</span></label>`).join('')}
  </fieldset>`).join('')}</div>`;
}

// {key: option} as the form holds it; "Ask me" is left out.
export function readChoices(form, options) {
  const found = {};
  for (const choice of options) {
    const value = form.querySelector(`[name="rubric-choice-${CSS.escape(choice.key)}"]:checked`)?.value || '';
    if (value) found[choice.key] = value;
  }
  return found;
}

// The chosen answers in a few words each ("Work expected · Exact values only"), or '' when every question is left to the check.
export function choicesSummary(options, choices) {
  return options.filter(choice => choices?.[choice.key]).map(choice => choice.options.find(option => option.value === choices[choice.key])?.label).filter(Boolean).join(' · ');
}

// The plan's choices, edited on their own (Key & Rubric Finalization): read by the next draft and the next rubric check only.
export async function editPlanChoices(aid, h, saved) {
  const base = `/api/grading/assignments/${aid}/rubric-choices`;
  const read = await h.api(base);
  const current = read.saved ? read.choices : read.course;
  const form = h.dialog('Your rubric choices', 'For this assignment. The next draft and rubric check follow them and stop asking about them.',
    `${read.saved ? '' : Object.keys(read.course || {}).length ? '<p class="supporting-copy" data-choices-from-course>Started from your answers on this course’s last assignment.</p>' : ''}
    ${choicesMarkup(read.options, current, h.e)}
    <p class="supporting-copy">A rubric check that already ran keeps what it asked. Run the check again for it to use these.</p>`,
    'Save choices', async (_, node, live) => {
      const answer = await h.api(base, 'POST', {choices: readChoices(node, read.options)});
      if (live()) h.close();
      saved?.(answer);
    });
  return form;
}

// RUBRIC CHOICES ON IMPORTED RUBRICS (requirement, 2026-10-29: Exam 2's rubrics came from Gradescope, were never drafted, and so its
// check ran with no choices at all; of its 36 questions about five were one of these). The import and upload dialogs ask them too,
// started from the plan's own answers, else the course's latest; the dialog's own button saves them (`save`), before it imports.
// Nothing is saved when the plan has answers and they are unchanged, and "Ask me" stays every question's way to leave it to the check.
export function choicesFold(host, aid, h) {
  const base = `/api/grading/assignments/${aid}/rubric-choices`;
  let read = null;
  host.innerHTML = '<p class="loading-state" role="status">Reading your rubric choices…</p>';
  const ready = h.api(base).then(found => {
    read = found;
    const current = found.saved ? found.choices : found.course;
    host.innerHTML = `<details class="grading-tools rubric-choices-fold" ${found.saved ? '' : 'open'}><summary>Quick choices for the rubric check</summary><p class="supporting-copy">The rubric check applies your answers instead of asking. “Ask me” leaves a question to the check.${!found.saved && Object.keys(found.course || {}).length ? ' Started from your answers on this course’s last assignment.' : ''}</p>${choicesMarkup(found.options, current, h.e)}</details>`;
  }).catch(() => { host.innerHTML = ''; });
  return {
    async save() {
      await ready;
      if (!read || !host.isConnected) return;
      const chosen = readChoices(host, read.options);
      const same = read.saved && JSON.stringify(chosen) === JSON.stringify(Object.fromEntries(read.options.filter(c => read.choices[c.key]).map(c => [c.key, read.choices[c.key]])));
      if (same) return;
      read = {...read, ...(await h.api(base, 'POST', {choices: chosen}))};
    },
  };
}
