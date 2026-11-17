// CANVAS: exact preview evidence, never an inferred historical base or a write to Canvas.
// `typed` maps a difference's index to the text the instructor typed (kept across a preview reload) and `chosen` holds the
// indices whose text waits to go into the draft; either opens the comparison.
export function conflictMarkup(entry, escape, canResolve, direct = false, {typed = {}, chosen = []} = {}) {
  if (!entry.differences?.length) return '';
  const show = value => typeof value === 'string' ? value : JSON.stringify(value);
  const open = Object.keys(typed).length || chosen.length;
  return `<details class="disclosure"${open ? ' open' : ''}><summary>Compare changes</summary><div class="disclosure-body">
    <p>Compare the current versions below. Earlier wording is unavailable.</p>
    ${entry.differences.map((diff, i) => `<section><h5>${escape(diff.field.replaceAll('_', ' '))}</h5>
      <p><strong>Faculty</strong></p><pre class="cc-diff-text">${escape(show(diff.faculty))}</pre>
      <p><strong>Canvas</strong></p><pre class="cc-diff-text">${escape(show(diff.canvas))}</pre>
      ${diff.truncated ? '<p>Long content shortened. Open the editor to inspect it in full.</p>' : ''}
      ${canResolve && !diff.truncated && ['body', 'description', 'title', 'name'].includes(diff.field) ?
        `<label class="cc-conflict-edit">Text to keep<textarea data-conflict-value="${i}" rows="4">${escape(i in typed ? typed[i] : show(diff.canvas))}</textarea></label>
         ${chosen.includes(i) ? `<button type="button" class="button" data-conflict-undo="${i}">Don’t use this text</button>`
           : `<button type="button" class="button" data-conflict-keep="${i}">Use this text in ${direct ? 'this section’s' : 'the shared term'} draft</button>`}` : ''}</section>`).join('')}
    </div></details>`;
}

export function conflictPrompt(entry) {
  return `Help resolve the Canvas conflict for ${entry.label}.`;
}

export function conflictContext(previewPath, entry) {
  const evidence = {key: entry.key, label: String(entry.label).slice(0, 300), verdict: entry.verdict,
    differences: (entry.differences || []).slice(0, 12).map(d => ({...d,
      faculty: typeof d.faculty === 'string' ? d.faculty.slice(0, 4000) : d.faculty,
      canvas: typeof d.canvas === 'string' ? d.canvas.slice(0, 4000) : d.canvas,
      truncated: d.truncated || String(d.faculty).length > 4000 || String(d.canvas).length > 4000})), omitted_fields: 0};
  while (JSON.stringify(evidence).length > 16000 && evidence.differences.length) evidence.differences.pop();
  evidence.omitted_fields = (entry.differences || []).length - evidence.differences.length;
  return {kind: 'canvas-conflict', preview_path: previewPath, evidence};
}
