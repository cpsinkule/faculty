// Workspace preferences: edit a draft, then save through the existing versioned API.
import {runFormAction, scrollBehavior} from "./ui.js";
import {SECTIONS, TIERS, ROLES, FALLBACK_ROLES, BLANK, NUMBERS, FLAGS, SUBSCRIPTION_NUMBERS, settingsMarkup, endpointEditor, modelChoices} from "./grading-settings-layout.js";
import {rememberModel, openModelPicker, modelScanWarning, modelPricing, MODEL_PROVIDERS, modelProvider, selectedProvider, catalogUrl, hostsUrl, subscriptionEndpoint, isSubscription, planName, firstModel} from "./grading-model-picker.js";
import {reasoningMarkup, hostMarkup, bundleMarkup, apiFallbackMarkup, teamSummaryMarkup, availabilityMarkup} from "./grading-settings-layout.js";

// Roles outside the team: one model each, chosen by name, with their own reasoning key where they have one. The final
// arbitrator is no longer among them: it is the tier's own additional reviewer, chosen with the teams above.
// The Faculty assistant's model is one of them too (AGENTS.md "Faculty assistant"): any connection, its own reasoning key.
const SOLO = ['yardstick', 'rubric_reviewer', 'assistant_model', 'coding_model'], SOLO_REASONING = {rubric_reviewer: 'rubric_reviewer_reasoning', assistant_model: 'assistant_reasoning', coding_model: 'coding_reasoning'};

export async function settingsView(host, h, active) {
  const [data, initialSeen] = await Promise.all([h.api("/api/grading/settings"), h.api("/api/grading/endpoints")]);
  if (!active()) return;
  let saved = structuredClone(data.settings), s = structuredClone(saved), seen = initialSeen;
  let selected = "defaults", dirty = false, profiles = JSON.stringify(s.dedicated_profiles, null, 2);
  let opened = new Set(), statusTimer = null, statusRequest = 0;
  // Provider filters are draft UI state; saved role endpoints already retain the actual provider.
  let providers = {}, providerModels = {};
  // Host listings read from a gateway in this visit, by gateway and model: public metadata, kept only while the screen is open.
  const hostLists = new Map(), hostReads = new Map(), hostViews = new WeakMap();
  const hostKey = endpoint => `${modelProvider(endpoint)}|${endpoint.model}`;
  const currentModel = (name, role, tier) => name.startsWith('fallback-') ? s.role_fallbacks?.[tier]?.[role] || '' : tier ? s.tiers[tier][role] : s[role] || '';
  function assignModel(name, role, tier, model) {
    if (name.startsWith('fallback-')) {
      ((s.role_fallbacks ||= {})[tier] ||= {})[role] = model;
    } else if (tier) {
      s.tiers[tier][role] = model; delete s.role_reasoning?.[tier]?.[role];
    } else if (SOLO.includes(role)) {
      s[role] = model;
      if (SOLO_REASONING[role]) s[SOLO_REASONING[role]] = '';
    }
    rememberModel(s, model);
  }
  const live = () => active() && host.isConnected;
  const status = name => seen.endpoints.find(e => e.name === name);
  const persisted = endpoint => saved.endpoints.some(e => [...Object.keys(BLANK), ...SUBSCRIPTION_NUMBERS].every(key => e[key] === endpoint[key]));
  const chip = (key, label) => `<span class="grading-chip grading-chip-${key}">${h.e(label)}</span>`;
  const usage = name => [...TIERS.flatMap(([tier, title]) => ROLES.filter(([role]) => s.tiers[tier][role] === name).map(([, role]) => `${title}: ${role}`)), ...(s.yardstick === name ? ["Benchmark reference"] : []), ...TIERS.flatMap(([tier, title]) => Object.entries((s.role_fallbacks || {})[tier] || {}).filter(([, chosen]) => chosen === name).map(([role]) => `${title}: fallback ${role}`)), ...(s.rubric_reviewer === name ? ['Rubric drafting and review'] : []), ...(s.assistant_model === name ? ['Faculty assistant'] : []), ...(s.coding_model === name ? ['Coding agent'] : [])];
  function setDirty(value = true) {
    dirty = value;
    const form = host.querySelector("form");
    form.querySelector('[data-settings-status]').textContent = dirty ? "Unsaved changes" : "All changes saved";
    form.querySelector('[type="submit"]').disabled = !dirty;
    form.querySelector('[data-grading-action="discard-settings"]').disabled = !dirty;
  }
  // Controls stay mounted across tabs. Capture only controls we own; preserve all other settings.
  function capture(form) {
    const values = new FormData(form);
    for (const key of Object.keys(s)) {
      if (["endpoints", "tiers", "dedicated_profiles", "risk"].includes(key) || !form.elements.namedItem(key)) continue;
      s[key] = typeof s[key] === "boolean" ? values.has(key) : typeof s[key] === "number" ? Number(values.get(key)) : values.get(key);
    }
    for (const [tier] of TIERS) for (const [role] of ROLES) if (form.elements.namedItem(`tier-${tier}-${role}`)) s.tiers[tier][role] = values.get(`tier-${tier}-${role}`) || "";
    // The optional spare for a grader. An empty choice removes it, a tier with none keeps no empty entry, and a tab that is
    // not mounted keeps what was saved rather than losing it.
    const spares = s.role_fallbacks || {}, keptSpares = {};
    for (const [tier] of TIERS) for (const role of FALLBACK_ROLES) {
      const field = form.elements.namedItem(`fallback-${tier}-${role}`);
      const chosen = field ? values.get(`fallback-${tier}-${role}`) : (spares[tier] || {})[role];
      if (chosen) (keptSpares[tier] ??= {})[role] = String(chosen);
    }
    s.role_fallbacks = keptSpares;
    for (const key of Object.keys(s.risk)) {
      if (!form.elements.namedItem(`risk-${key}`)) continue;
      // A risk setting is a tick, a chosen name (the review policy) or a number; only the last may be coerced.
      s.risk[key] = typeof s.risk[key] === "boolean" ? values.has(`risk-${key}`)
        : typeof s.risk[key] === "string" ? String(values.get(`risk-${key}`) ?? s.risk[key]) : Number(values.get(`risk-${key}`));
    }
    profiles = String(values.get("dedicated_profiles"));
    s.endpoints.forEach((endpoint, index) => {
      const answer = form.elements.namedItem(`endpoint-${index}-max_tokens`);
      const reasoning = form.elements.namedItem(`endpoint-${index}-reasoning_tokens`);
      if (answer) endpoint.max_tokens = Number(answer.value);
      if (reasoning) endpoint.reasoning_tokens = Number(reasoning.value);
    });
    opened = new Set([...form.querySelectorAll("details[open][data-compute-fold]")].map(el => el.dataset.computeFold));
  }
  // Availability and costs: read when its section is first opened and on Check again, never on a timer. Kept across redraws of
  // the form while the dialog is open. Cost is the recorded cost of the latest batches the navigation read lists.
  let availability = null, availabilityRead = 0;
  function drawAvailability() {
    const slot = host.querySelector("[data-compute-health]");
    if (!slot) return;
    slot.innerHTML = availabilityMarkup(availability, h);
    const again = slot.querySelector('[data-grading-action="retry-health"]');
    if (again) again.onclick = () => { void loadAvailability(true); };
  }
  async function loadAvailability(again = false) {
    if (availability && !availability.error && !again) { drawAvailability(); return; }
    const read = ++availabilityRead;
    availability = null; drawAvailability();
    try {
      const [health, activity] = await Promise.all([h.api("/api/grading/health"), h.api("/api/grading?view=navigation&health=0")]);
      if (!live() || read !== availabilityRead) return;
      availability = {health, cost: (activity.jobs || []).reduce((sum, job) => sum + (Number(job.cost) || 0), 0)};
    } catch (error) {
      if (!live() || read !== availabilityRead) return;
      availability = {error: error.message};
    }
    drawAvailability();
  }
  function choose(key, focus = false) {
    const changed = selected !== key;
    selected = key;
    if (key === "availability") void loadAvailability();
    host.querySelectorAll("[data-compute-tab]").forEach(button => {
      const current = button.dataset.computeTab === key;
      button.setAttribute("aria-selected", String(current)); button.tabIndex = current ? 0 : -1;
      if (current && focus) button.focus();
    });
    host.querySelectorAll("[data-compute-panel]").forEach(panel => { panel.hidden = panel.dataset.computePanel !== key; });
    if (changed) host.querySelector('.compute-layout').scrollTop = 0;
  }
  function showField(field) {
    const panel = field.closest("[data-compute-panel]");
    if (panel) choose(panel.dataset.computePanel);
    // A field of a scheme that is not the chosen one (an invalid number blocking Save) is revealed where it stands rather
    // than by changing the chosen scheme. The reveal lasts until another scheme is picked.
    const block = field.closest("[data-scheme-for]");
    if (block) { block.dataset.schemeReveal = "1"; block.hidden = false; }
    for (let el = field.parentElement; el; el = el.parentElement) if (el.tagName === "DETAILS") el.open = true;
    field.focus();
  }
  function revealInvalid(form) {
    const field = form.querySelector("input:invalid, select:invalid, textarea:invalid");
    if (!field) return false;
    showField(field); field.reportValidity(); return true;
  }
  function connections() {
    const container = host.querySelector("[data-endpoints]");
    container.innerHTML = s.endpoints.length ? s.endpoints.map((e, i) => {
      const known = persisted(e) ? status(e.name) : null, probe = known?.probe;
      const testing = probe?.current && ["pending", "running"].includes(probe.status);
      const label = !persisted(e) ? "Unsaved" : !e.enabled ? "Disabled" : testing ? "Testing…" : known?.verification === "verified" ? "Test passed" : probe?.current && ["failed", "complete"].includes(probe.status) ? "Test failed" : "Not tested";
      return `<article class="compute-connection" data-endpoint="${i}"><div class="compute-connection-info"><strong>${h.e(e.name)}</strong><p>${h.e(e.model)} · ${isSubscription(e) ? `${planName(e)} · ${h.e(e.bundle_size ?? 6)} responses per call · ${h.e(e.sessions ?? 2)} at once` : e.modality === "vision" ? "Images and text" : "Text only"}</p><small>${h.e(usage(e.name).join(" · ") || "Not assigned to a grading team")}</small>${known?.unusable_reason ? `<p class="supporting-copy" role="note">${h.e(known.unusable_reason)}</p>` : ""}</div><div class="compute-connection-controls"><div class="grading-actions">${chip(label === "Test passed" ? "done" : label === "Test failed" ? "failed" : "todo", label)}${known?.unusable_reason ? chip("failed", "Key not usable for this host") : known && e.key_env && !known.key_present ? chip("attention", "Key not set") : ""}</div><div class="grading-actions">${h.button("edit-endpoint", "Edit", `data-index="${i}" aria-label="Edit ${h.e(e.name)}"`)}${h.button("probe", "Test", `data-index="${i}" aria-label="Test ${h.e(e.name)}" ${!persisted(e) || !e.enabled || testing ? "disabled" : ""}`)}</div></div></article>`;
    }).join("") : '<p class="compute-empty">No model connections yet. Add a connection, then choose its role under Defaults or Other models.</p>';
    container.querySelectorAll('[data-grading-action="edit-endpoint"]').forEach(button => button.onclick = () => editEndpoint(Number(button.dataset.index)));
    container.querySelectorAll('[data-grading-action="probe"]').forEach(button => button.onclick = () => h.run(button, async () => {
      await h.api(`/api/grading/endpoints/${encodeURIComponent(s.endpoints[Number(button.dataset.index)].name)}/probe`, "POST", {});
      if (live()) { h.toast("Connection test queued."); await refreshStatus(); }
    }));
  }
  async function refreshStatus() {
    clearTimeout(statusTimer);
    if (!live() || !host.closest("dialog")?.open) return;
    const request = ++statusRequest;
    try {
      const result = await h.api("/api/grading/endpoints");
      if (!live() || !host.closest("dialog")?.open || request !== statusRequest) return;
      seen = result;
      const form = host.querySelector("form");
      if (form.dataset.saving !== "true") {
        const place = anchor(host.contains(document.activeElement) ? document.activeElement : null);
        capture(form); connections(); drawSummary(form); holdPlace(place);
      }
      if (seen.endpoints.some(e => e.probe?.current && ["pending", "running"].includes(e.probe.status))) statusTimer = setTimeout(() => { if (live()) void refreshStatus(); }, 3000);
    } catch (error) { if (live() && host.closest("dialog")?.open && request === statusRequest) h.toast(error.message, true); }
  }
  function editEndpoint(index = null) {
    capture(host.querySelector("form"));
    const source = MODEL_PROVIDERS[providers.connections || 'openrouter'];
    const endpoint = index === null ? (source.kind ? subscriptionEndpoint(firstModel(source.kind), '', source.kind) : {...BLANK, base_url: source.url, key_env: source.key, aggregator: !!source.url}) : {...s.endpoints[index]}, references = index === null ? [] : usage(endpoint.name);
    const probe = status(endpoint.name)?.probe;
    const receipt = probe ? `<details class="compute-fold"><summary>Last connection test</summary><div class="compute-fold-body"><p>${probe.status === "complete" ? `Probe ${h.e(probe.current && persisted(endpoint) ? "" : "(of an earlier configuration) ")}saw: structured output ${probe.result.structured_output ? "yes" : "no"} · image input ${probe.result.image_input === null ? "not applicable" : probe.result.image_input ? "yes" : "no"} · served by ${h.e(probe.result.upstream || "unreported")} as ${h.e(probe.result.reported_model || "unreported")} · ${h.e(probe.result.seconds ?? "?")} s` : probe.status === "failed" ? `Test failed: ${h.e(probe.error)}` : "Test queued or running."}</p></div></details>` : "";
    const form = h.dialog(index === null ? "Add connection" : `Edit ${endpoint.name}`, "Apply to your draft, then save compute settings.", endpointEditor(endpoint, h) + receipt + (index === null ? "" : `<div class="compute-remove">${h.button("remove-endpoint", "Remove connection", references.length ? "disabled" : "")}<p class="supporting-copy">${references.length ? "Replace this connection under Defaults before removing it." : "Removal takes effect when you save settings."}</p></div>`), "Apply to settings", async (values, _, dialogLive) => {
      if (revealInvalid(form)) return;
      const next = {...endpoint};
      if (isSubscription(endpoint)) {
        // Only what the subscription editor shows; the server fills the rest (`config.subscription`).
        Object.assign(next, {name: String(values.get('name') || '').trim(), model: String(values.get('model') || '').trim(), enabled: values.has('enabled')});
        for (const key of SUBSCRIPTION_NUMBERS) next[key] = Number(values.get(key));
        next.max_inflight = Math.min(256, next.bundle_size * next.sessions);
        if (endpoint.kind === 'agent_sdk' && values.has('api_fallback')) next.api_fallback = true;
        else delete next.api_fallback;
      } else for (const key of Object.keys(BLANK).filter(key => key !== "lineage")) next[key] = FLAGS.includes(key) ? values.has(key) : NUMBERS.includes(key) ? Number(values.get(key)) : String(values.get(key) || "").trim();
      if (!isSubscription(endpoint) && (next.model !== endpoint.model || next.base_url !== endpoint.base_url)) {
        next.lineage = ""; // A manually changed model has no inferred family; existing receipts retain theirs.
        delete next.reasoning_options; delete next.reasoning_controls; delete next.temperature_supported;
      }
      // A host's answer limit belongs to that host: typed another, it is read again rather than carried over.
      if (next.model !== endpoint.model || next.base_url !== endpoint.base_url || next.upstream !== endpoint.upstream) delete next.output_limit;
      if (!/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,59}$/.test(next.name)) throw new Error("Use a connection name starting with a letter or number, followed by letters, numbers, dots, dashes, colons or underscores.");
      if (next.name === "nebius" || s.endpoints.some((e, i) => i !== index && e.name === next.name)) throw new Error("Choose a unique connection name. Nebius is reserved for the built-in connection.");
      if (!live() || !dialogLive()) return;
      if (index === null) s.endpoints.push(next);
      else {
        s.endpoints[index] = next;
        // Keep any historical, hidden references valid when a connection is renamed.
        for (const tier of Object.keys(s.tiers)) for (const role of Object.keys(s.tiers[tier])) if (s.tiers[tier][role] === endpoint.name) s.tiers[tier][role] = next.name;
        if (s.yardstick === endpoint.name) s.yardstick = next.name;
        if (s.rubric_reviewer === endpoint.name) s.rubric_reviewer = next.name;
        if (s.assistant_model === endpoint.name) s.assistant_model = next.name;
        if (s.coding_model === endpoint.name) s.coding_model = next.name;
        for (const tier of Object.keys(s.role_fallbacks || {})) for (const role of Object.keys(s.role_fallbacks[tier])) {
          if (s.role_fallbacks[tier][role] === endpoint.name) s.role_fallbacks[tier][role] = next.name;
        }
        s.recent_models = s.recent_models.map(name => name === endpoint.name ? next.name : name);
      }
      h.close(); dirty = true; draw();
      host.querySelector(`[data-grading-action="edit-endpoint"][data-index="${index ?? s.endpoints.length - 1}"]`)?.focus();
    });
    form.noValidate = true;
    form.querySelector('[data-grading-action="remove-endpoint"]')?.addEventListener("click", () => {
      if (references.length || !live()) return;
      s.endpoints.splice(index, 1);
      s.recent_models = s.recent_models.filter(name => name !== endpoint.name);
      h.close(); dirty = true; draw();
      host.querySelector('[data-grading-action="add-endpoint"]').focus();
    });
  }
  function browse(role = "", tier = "", field = "") {
    capture(host.querySelector("form"));
    field ||= tier ? `tier-${tier}-${role}` : role || 'connections';
    const provider = providers[field] || selectedProvider(s, currentModel(field, role, tier), role);
    const label = role === 'rubric_reviewer' ? 'Rubric drafting and review' : role === 'assistant_model' ? 'Faculty assistant' : role === 'coding_model' ? 'Coding agent' : role === "yardstick" ? "Benchmark reference" : role ? `${TIERS.find(([key]) => key === tier)[1]} · ${ROLES.find(([key]) => key === role)[1]}` : "Add connection";
    openModelPicker(h, s, {role, label: field.startsWith('fallback-') ? `Fallback · ${label}` : label, provider, active: live, apply: endpoint => {
      if (endpoint.name !== 'nebius' && !s.endpoints.some(e => e.name === endpoint.name)) s.endpoints.push(endpoint);
      providers[field] = modelProvider(endpoint);
      (providerModels[field] ||= {})[providers[field]] = endpoint.name;
      if (role) assignModel(field, role, tier, endpoint.name);
      dirty = true; draw();
      host.querySelector(role ? `[name="${field}"]` : '[data-grading-action="browse-models"]')?.focus();
    }, repriced: () => {
      // The catalogue's current prices went into the draft: redraw the prices beside each role and ask for a save.
      if (!live()) return;
      capture(host.querySelector("form")); dirty = true; draw();
    }});
  }
  // "Next Cloud batch uses" follows the draft's Cloud team as it is edited, from what this screen already read (grun:P13).
  function drawSummary(form) {
    const slot = form?.querySelector("[data-team-summary]");
    if (!slot) return;
    const models = Object.fromEntries(ROLES.map(([role]) => [role, form.elements.namedItem(`tier-cloud_standard-${role}`)?.value ?? s.tiers.cloud_standard?.[role] ?? ""]));
    slot.innerHTML = teamSummaryMarkup(models, (name, role) => {
      const endpoint = name === "nebius" ? {name, kind: "hosted", model: s.nebius_model, base_url: "", reasoning: false, max_inflight: s.concurrency, max_tokens: 8192, reasoning_tokens: 0, price_in: null, price_out: null}
        : s.endpoints.find(e => e.name === name), known = persisted(endpoint || {}) ? status(name) : null, probe = known?.probe;
      return {model: name === "nebius" ? s.nebius_model : endpoint?.model || "", unusable: Boolean(known?.unusable_reason),
        key: known && endpoint?.key_env ? Boolean(known.key_present) : undefined,
        test: known?.verification === "verified" ? "passed" : probe?.current && ["failed", "complete"].includes(probe.status) ? "failed" : "",
        // What a batch would use with it: the role's own reasoning choice, the workspace's switches and the role's spare.
        endpoint, reasoning: s.role_reasoning?.cloud_standard?.[role], limits: form.elements.namedItem("token_limits") ? form.elements.namedItem("token_limits").checked : s.token_limits !== false,
        full: form.elements.namedItem("full_concurrency") ? form.elements.namedItem("full_concurrency").checked : Boolean(s.full_concurrency),
        fallback: form.elements.namedItem(`fallback-cloud_standard-${role}`)?.value ?? s.role_fallbacks?.cloud_standard?.[role] ?? ""};
    }, h);
  }
  // KEEP THE INSTRUCTOR'S PLACE (2026-09-28: "whenever I make changes in the compute settings, the page rubberband scrolls back up
  // and I lose my place"). A change redraws parts of the form above the control being edited (the "Next Cloud batch uses" summary,
  // a model's host and bundle rows, the token-limit fields) or the whole form; Safari has no CSS scroll anchoring, so everything
  // below slid. The control being edited is held where it was on screen: its position is read before the change and the layout
  // scrolled by the difference after it, again once asynchronous parts have landed. Chrome's own anchoring makes that a no-op.
  const layout = () => host.querySelector('.compute-layout');
  const keyOf = el => el?.name ? `[name="${CSS.escape(el.name)}"]${el.type === "radio" ? `[value="${CSS.escape(el.value)}"]` : ""}`
    : el?.dataset?.gradingAction ? `[data-grading-action="${CSS.escape(el.dataset.gradingAction)}"]${el.dataset.index ? `[data-index="${CSS.escape(el.dataset.index)}"]` : ""}` : "";
  function anchor(el) {
    const scroller = layout();
    if (!scroller || !el || !scroller.contains(el) || !keyOf(el)) return {scrollTop: scroller?.scrollTop || 0};
    return {scrollTop: scroller.scrollTop, key: keyOf(el), top: el.getBoundingClientRect().top, el};
  }
  function restore(place) {
    const scroller = layout();
    if (!scroller || !place) return;
    const el = place.el?.isConnected && place.el.offsetParent ? place.el : place.key ? [...scroller.querySelectorAll(place.key)].find(node => node.offsetParent) : null;
    if (!el || place.top === undefined) return; // nothing to hold: the scroll position is left to the instructor
    const moved = el.getBoundingClientRect().top - place.top;
    if (Math.abs(moved) <= 1) return;
    // Near the bottom, a change that hides rows shortens the page under the instructor and the browser snaps the view up. Room is
    // added below the form for that (browser memory only) and given back as they scroll up again.
    const wanted = scroller.scrollTop + moved, room = scroller.scrollHeight - scroller.clientHeight;
    if (wanted > room) setSlack(slack + Math.ceil(wanted - room));
    scroller.scrollTop = wanted;
  }
  let slack = 0, basePadding = null, slackBound = null;
  function setSlack(px) {
    const scroller = layout();
    if (!scroller) return;
    if (basePadding === null) basePadding = parseFloat(getComputedStyle(scroller).paddingBottom) || 0;
    slack = Math.max(0, px);
    if (slack) scroller.style.setProperty("padding-bottom", `${basePadding + slack}px`); else scroller.style.removeProperty("padding-bottom");
    if (slackBound !== scroller) {
      slackBound = scroller;
      scroller.addEventListener("scroll", () => {
        if (!slack) return;
        const spare = scroller.scrollHeight - scroller.clientHeight - scroller.scrollTop; // how far above the bottom the view now is
        if (spare > 0) setSlack(slack - Math.min(slack, spare));
      }, {passive: true});
    }
  }
  // A move the instructor asks for (a role opened from the summary) ends any hold still waiting, so it is not scrolled back.
  let holds = 0;
  function holdPlace(place) {
    const hold = ++holds, again = () => { if (hold === holds) restore(place); };
    restore(place);
    requestAnimationFrame(again);
    setTimeout(again, 400); // a host list or reasoning facts arriving after the change
  }
  function draw() {
    const active = document.activeElement;
    const place = anchor(host.contains(active) ? active : null);
    slack = 0; basePadding = null; // the new layout starts without borrowed room
    host.innerHTML = settingsMarkup(s, profiles, selected, h, providers);
    const form = host.querySelector("form");
    form.querySelectorAll("details[data-compute-fold]").forEach(el => { el.open = opened.has(el.dataset.computeFold); });
    form.oninput = form.onchange = event => { syncVisibility(form); setDirty(); drawSummary(form); if (event?.target?.name === "token_limits") drawFacts(); };
    // Read where the edited control stands before any handler changes the form (capture phase), and hold it there afterwards.
    // A click fires input and change together: the place is read once, before the first of them, and held once.
    let pending = null;
    for (const kind of ["input", "change"]) form.addEventListener(kind, event => {
      if (pending) return;
      pending = anchor(event.target);
      queueMicrotask(() => { const place = pending; pending = null; holdPlace(place); });
    }, true);
    const syncVisibility = form => {
      const box = form.querySelector("[data-token-limit-fields]");
      if (box) box.hidden = !form.elements.namedItem("token_limits")?.checked;
      // The picked scheme decides which teams, models and options stand under the picker. Every other scheme's controls stay
      // in the form with their values (hidden, never disabled), so switching back and forth loses nothing.
      const picked = form.elements.namedItem("default_tier")?.value;
      form.querySelectorAll("[data-scheme-for]").forEach(block => {
        block.hidden = !block.dataset.schemeFor.split(" ").includes(picked) && block.dataset.schemeReveal !== "1";
      });
    };
    // Picking a scheme ends any reveal of another scheme's block, so the page shows the choice that was just made.
    form.querySelectorAll('input[name="default_tier"]').forEach(radio => radio.addEventListener("change", () => {
      form.querySelectorAll("[data-scheme-reveal]").forEach(block => { delete block.dataset.schemeReveal; });
    }));
    syncVisibility(form);
    const refreshChoices = () => {
      for (const row of form.querySelectorAll('[data-model-field]')) {
        const {modelField: name, role, tier} = row.dataset, current = currentModel(name, role, tier);
        const provider = providers[name] || selectedProvider(s, current, role);
        const selector = form.elements.namedItem(name);
        if (!selector) continue;
        form.elements.namedItem(`provider-${name}`).value = provider;
        selector.replaceChildren(...modelChoices(s, current, role, provider).map(([value, label]) => new Option(label, value, false, value === current)));
        selector.title = selector.selectedOptions[0]?.textContent || '';
        selector.required = !current && !!providers[name] && !row.dataset.fallback && (['reader', 'primary', 'second'].includes(role) || role === 'rubric_reviewer' && provider !== selectedProvider(s, '', role));
        row.querySelector('button').textContent = `Browse ${MODEL_PROVIDERS[provider].label}`;
        const inheritsPrimary = role === 'rubric_reviewer' && !current && provider === selectedProvider(s, '', role);
        const warning = form.querySelector(`[data-model-scan-warning="${name}"]`);
        // An unset rubric model follows Standard's primary grader: warn about the model that will actually draft.
        warning.textContent = modelScanWarning(s.endpoints.find(e => e.name === (inheritsPrimary ? s.tiers.cloud_standard.primary : current)), role);
        const price = form.querySelector(`[data-model-price="${name}"]`);
        price.textContent = modelPricing(s, current, role === 'rubric_reviewer' && !inheritsPrimary ? '' : role);
        price.hidden = !price.textContent;
        const slot = form.querySelector(`[data-reasoning-slot="${name}"]`);
        if (slot) {
          // Only a choice the instructor makes is stored: a role that was never touched keeps following its connection's
          // default, and saving some other setting must not turn every displayed default into a decision.
          // The rubric reviewer is no team role: its choice has its own key, and an unset reviewer follows Standard's primary grader.
          // The final arbitrator is the same kind of role: its own key, and no model to fall back on.
          const solo = SOLO_REASONING[role], effective = inheritsPrimary ? s.tiers.cloud_standard.primary : current;
          slot.innerHTML = reasoningMarkup(s.endpoints.find(e => e.name === effective), name, solo ? s[solo] : s.role_reasoning?.[tier]?.[role], h);
          slot.querySelector("select")?.addEventListener("change", event => {
            if (solo) s[solo] = event.target.value;
            else ((s.role_reasoning ||= {})[tier] ||= {})[role] = event.target.value;
          });
        }
        const descriptions = [price, warning].filter(el => el.textContent).map(el => el.id).join(' ');
        if (descriptions) selector.setAttribute('aria-describedby', descriptions);
        else selector.removeAttribute('aria-describedby');
        drawHost(row, name, current);
      }
    };
    // Which host serves the chosen gateway model. An inherited choice (the rubric model following the primary grader) shows none:
    // its host is the primary's, chosen there.
    const drawHost = (row, name, current) => {
      const slot = row.querySelector(`[data-host-slot="${name}"]`), endpoint = s.endpoints.find(e => e.name === current);
      if (!slot) return;
      const version = {};
      hostViews.set(slot, version);
      // How many responses of one question and exam version share a request, on every connection a grading role saved (instructor,
      // 2026-09-27). The rubric model's and the benchmark's requests are never bundled. Like a host, it belongs to the connection,
      // so every role using it changes together.
      const bundles = endpoint && !['rubric_reviewer', 'yardstick', 'assistant_model', 'coding_model'].includes(row.dataset.role) && endpoint.kind !== 'dedicated' && !endpoint.batch_api;
      // Fields first, in one row beside the reasoning; then each note, and once which other roles share the connection's settings.
      const gateway = endpoint && ['openrouter', 'vercel'].includes(modelProvider(endpoint));
      const sharedNote = () => {
        const label = slot.dataset.hostLabel, uses = endpoint ? usage(endpoint.name) : [];
        const others = uses.length > 1 ? uses.filter(use => use !== label && !use.endsWith(`: ${label}`)) : [];
        if (!others.length || (!gateway && !bundles)) return '';
        const what = gateway && bundles ? 'its host and responses per call apply' : gateway ? 'its host applies' : 'its responses per call apply';
        return `<p class="supporting-copy compute-host-note compute-shared-note">The connection ${h.e(endpoint.name)} is also used by ${h.e(others.join(', '))}; ${what} there too.</p>`;
      };
      const compose = listing => {
        const host = gateway ? hostMarkup(endpoint, name, slot.dataset.hostLabel, listing, h) : {field: '', notes: ''};
        const bundle = bundles ? bundleMarkup(endpoint, name, slot.dataset.hostLabel, h) : {fields: '', notes: ''};
        const api = ['assistant_model', 'coding_model'].includes(row.dataset.role) ? '' : apiFallbackMarkup(endpoint, `api-fallback-${name}`, h);
        return host.field + bundle.fields + host.notes + bundle.notes + api + sharedNote();
      };
      const bindBundle = () => {
        const apiFallback = slot.querySelector(`[name="api-fallback-${CSS.escape(name)}"]`);
        if (apiFallback) apiFallback.onchange = () => {
          if (apiFallback.checked) endpoint.api_fallback = true;
          else delete endpoint.api_fallback;
          refreshChoices(); drawFacts(); setDirty();
        };
        const control = slot.querySelector('[data-bundle-select]'), sessions = slot.querySelector('[data-sessions-select]');
        if (sessions) sessions.onchange = () => {
          // Never more than the most calls a connection may run (256): a larger number is brought down to it.
          let typed = Number(sessions.value);
          if (typed > Number(sessions.max)) sessions.value = typed = Number(sessions.max);
          if (!Number.isInteger(typed) || typed < 1) { sessions.reportValidity(); return; }
          endpoint.sessions = typed;
          endpoint.max_inflight = Math.min(256, (endpoint.bundle_size ?? 6) * endpoint.sessions);
          drawHost(row, name, current); refreshChoices(); drawFacts(); setDirty();
        };
        if (!control) return;
        control.onchange = () => {
          const size = Number(control.value);
          if (isSubscription(endpoint)) {
            endpoint.bundle_size = size;
            endpoint.max_inflight = Math.min(256, size * (endpoint.sessions ?? 2));
          } else if (size > 1) endpoint.bundle_size = size;
          else delete endpoint.bundle_size;  // One is the default and is not stored, so the connection is unchanged.
          refreshChoices(); drawFacts(); setDirty();
        };
      };
      if (!gateway) { slot.innerHTML = endpoint ? compose() : ''; bindBundle(); return; }
      slot.innerHTML = compose(hostLists.get(hostKey(endpoint)));
      bindBundle();
      const select = slot.querySelector('[data-host-select]');
      const load = async () => {
        const key = hostKey(endpoint);
        if (hostLists.has(key)) return false;
        let pending = hostReads.get(key);
        if (!pending) {
          pending = h.api(hostsUrl(modelProvider(endpoint), endpoint.model));
          hostReads.set(key, pending);
        }
        let listing;
        try { listing = await pending; }
        finally { if (hostReads.get(key) === pending) hostReads.delete(key); }
        if (!live()) return false;
        if (!listing.notice) hostLists.set(key, listing);
        if (!slot.isConnected || hostViews.get(slot) !== version) return false;
        const focused = document.activeElement === select;
        slot.innerHTML = compose(listing);
        bind(slot.querySelector('[data-host-select]')); bindBundle();
        if (focused) slot.querySelector('[data-host-select]').focus();
        return true;
      };
      const bind = control => {
        // A pointer opens the list after it is read (the native list shows what it had when it opened); a keyboard fills it in place.
        control.addEventListener('pointerdown', event => {
          if (hostLists.has(hostKey(endpoint))) return;
          event.preventDefault();
          void load().then(loaded => { const fresh = slot.querySelector('[data-host-select]'); fresh?.focus(); if (loaded) try { fresh?.showPicker(); } catch { /* No user activation left: the list is filled; it opens on the next click. */ } })
            .catch(error => h.toast(error.message, true));
        });
        control.addEventListener('focus', () => { void load().catch(error => h.toast(error.message, true)); }, {once: true});
        control.addEventListener('change', () => {
          endpoint.upstream = control.value;
          // The chosen host's own prices, so the batch estimate and cost cap count what it charges. Any host keeps the prices.
          const listing = hostLists.get(hostKey(endpoint)), chosen = listing?.hosts.find(item => item.id === control.value);
          if (chosen && chosen.price_in != null && chosen.price_out != null) { endpoint.price_in = chosen.price_in; endpoint.price_out = chosen.price_out; }
          // The most this host (for any host: every host) is published to allow, sent when token limits are off. A host that is not
          // listed has no known figure, so nothing is sent for it and its default applies.
          const room = chosen ? chosen.output_limit : control.value === '' ? listing?.any_host_limit : null;
          if (room != null) endpoint.output_limit = room; else delete endpoint.output_limit;
          refreshChoices(); drawFacts(); setDirty();
        });
      };
      bind(select);
    };
    // Connections saved before reasoning options were recorded have none. Nothing is fetched to show this screen, so reading
    // them is one explicit click; the facts are then saved with the connection like its prices.
    const factsSlot = form.querySelector("[data-reasoning-facts]");
    const unknown = () => s.endpoints.filter(e => e.enabled && modelProvider(e) !== 'custom' && !e.reasoning_options);
    // The most a gateway publishes for a connection's host (`output_limit`, sent when token limits are off) is read the same way, from
    // the host listing: the chosen host's figure, or for any host the one every host accepts.
    // Said only while token limits are off (when the figure is used) and for connections a role uses.
    const roomless = () => s.endpoints.filter(e => e.enabled && ['openrouter', 'vercel'].includes(modelProvider(e)) && e.output_limit === undefined && usage(e.name).length);
    const drawFacts = () => {
      const missing = unknown(), unmeasured = form.elements.namedItem("token_limits")?.checked === false ? roomless() : [];
      factsSlot.innerHTML = (missing.length ? `<p class="supporting-copy">Reasoning options are not recorded yet for ${h.e(missing.map(e => e.name).join(", "))}. ${h.button("reasoning-facts", "Read reasoning options from providers")}</p>` : "")
        + (unmeasured.length ? `<p class="supporting-copy" data-answer-limits>Answer limits are not recorded yet for ${h.e(unmeasured.map(e => e.name).join(", "))}; with token limits off their hosts' defaults apply. ${h.button("answer-limits", "Read answer limits from providers")}</p>` : "");
      factsSlot.querySelector('[data-grading-action="answer-limits"]')?.addEventListener("click", event => h.run(event.currentTarget, async () => {
        capture(form);
        const pending = roomless();
        const listings = await Promise.all(pending.map(e => h.api(hostsUrl(modelProvider(e), e.model))));
        if (!live()) return;
        const read = pending.filter((e, index) => {
          const listing = listings[index];
          if (listing.notice) return false;
          hostLists.set(hostKey(e), listing);
          const room = e.upstream ? listing.hosts.find(item => item.id === e.upstream)?.output_limit : listing.any_host_limit;
          if (room == null) return false;
          e.output_limit = room;
          return true;
        }).map(e => e.name);
        refreshChoices(); drawFacts();
        if (read.length) setDirty();
        h.toast(read.length ? `Read for ${read.join(", ")}. Save compute settings to keep them.` : listings.map(listing => listing.notice).filter(Boolean).join(' ') || "No answer limits were published for these hosts.", !read.length);
      }));
      factsSlot.querySelector('[data-grading-action="reasoning-facts"]')?.addEventListener("click", event => h.run(event.currentTarget, async () => {
        capture(form);
        const data = await Promise.all([...new Set(unknown().map(modelProvider))].map(async provider => [provider, await h.api(catalogUrl(provider))]));
        if (!live()) return;
        const catalogues = new Map(data.map(([provider, result]) => [provider, new Map(result.models.map(model => [model.endpoint.model, model.endpoint]))]));
        const read = unknown().filter(e => catalogues.get(modelProvider(e))?.get(e.model)?.reasoning_options).map(e => {
          const facts = catalogues.get(modelProvider(e)).get(e.model);
          for (const key of ['reasoning_options', 'reasoning_controls', 'temperature_supported']) if (facts[key] !== undefined) e[key] = facts[key];
          return e.name;
        });
        refreshChoices(); drawFacts();
        if (read.length) setDirty();
        h.toast(read.length ? `Read for ${read.join(", ")}. Save compute settings to keep them.` : data.map(([, result]) => result.notice).filter(Boolean).join(' ') || "No new reasoning options were available.", !read.length);
      }));
    };
    for (const row of form.querySelectorAll('[data-model-field]')) {
      const {modelField: name, role, tier} = row.dataset;
      const selector = form.elements.namedItem(name), providerSelector = form.elements.namedItem(`provider-${name}`);
      selector.onchange = () => {
        capture(form); assignModel(name, role, tier, selector.value);
        (providerModels[name] ||= {})[providerSelector.value] = selector.value;
        refreshChoices();
      };
      providerSelector.onchange = () => {
        capture(form);
        const previous = currentModel(name, role, tier), nextProvider = providerSelector.value;
        const previousProvider = providers[name] || selectedProvider(s, previous, role);
        (providerModels[name] ||= {})[previousProvider] = previous;
        providers[name] = nextProvider;
        const old = s.endpoints.find(e => e.name === previous);
        const counterpart = s.endpoints.find(e => e.enabled && e.model === old?.model && modelProvider(e) === nextProvider);
        const remembered = providerModels[name][nextProvider];
        const next = remembered === 'nebius' || s.endpoints.some(e => e.name === remembered && e.enabled && modelProvider(e) === nextProvider) ? remembered : counterpart?.name || '';
        assignModel(name, role, tier, next);
        refreshChoices(); setDirty();
      };
    }
    form.querySelectorAll('[data-grading-action="browse-role-models"]').forEach(button => button.onclick = () => browse(button.dataset.role, button.dataset.tier, button.dataset.field));
    form.querySelectorAll("[data-compute-tab]").forEach(button => {
      button.onclick = () => { capture(form); connections(); choose(button.dataset.computeTab); };
      button.onkeydown = event => {
        const keys = SECTIONS.map(([key]) => key), i = keys.indexOf(selected);
        const key = ["ArrowDown", "ArrowRight"].includes(event.key) ? keys[(i + 1) % keys.length] : ["ArrowUp", "ArrowLeft"].includes(event.key) ? keys[(i + keys.length - 1) % keys.length] : event.key === "Home" ? keys[0] : event.key === "End" ? keys.at(-1) : null;
        if (key) { event.preventDefault(); capture(form); connections(); choose(key, true); }
      };
    });
    // A role named in "Next Cloud batch uses" opens its controls: its heading at the top, its model focused. A team standing under
    // another mode than the one picked is revealed where it is, as an invalid field would be.
    form.querySelector("[data-team-summary]").onclick = event => {
      const field = form.elements.namedItem(`tier-cloud_standard-${event.target.closest("[data-jump-role]")?.dataset.jumpRole}`);
      if (!field) return;
      const block = field.closest("[data-scheme-for]");
      if (block?.hidden) { block.dataset.schemeReveal = "1"; block.hidden = false; }
      holds++;
      field.closest(".compute-role").scrollIntoView({block: "start", behavior: scrollBehavior()});
      field.focus({preventScroll: true});
    };
    form.querySelector('[data-grading-action="add-endpoint"]').onclick = () => editEndpoint();
    form.querySelector('[data-grading-action="browse-models"]').onclick = () => browse();
    form.elements.namedItem('provider-connections').onchange = event => {
      providers.connections = event.target.value;
      form.querySelector('[data-grading-action="browse-models"]').textContent = `Browse ${MODEL_PROVIDERS[providers.connections].label}`;
    };
    form.querySelector('[data-grading-action="connection-status"]').onclick = event => h.run(event.currentTarget, refreshStatus);
    form.querySelectorAll('[data-grading-action="local-settings"]').forEach(button => button.onclick = () => { choose("advanced", true); form.querySelector('[data-compute-fold="local"]').open = true; });
    form.querySelector('[data-grading-action="discard-settings"]').onclick = () => { s = structuredClone(saved); providers = {}; providerModels = {}; profiles = JSON.stringify(s.dedicated_profiles, null, 2); dirty = false; draw(); };
    form.onsubmit = event => {
      event.preventDefault();
      if (revealInvalid(form)) return;
      void runFormAction(form, async () => {
        capture(form);
        let parsed;
        try { parsed = JSON.parse(profiles); } catch { showField(form.elements.namedItem("dedicated_profiles")); throw new Error("Resource profiles must be valid JSON."); }
        // Keep a save atomic from the user's perspective: don't lose edits made during the request.
        const controls = [...form.querySelectorAll("input, select, textarea")].filter(el => !el.disabled);
        controls.forEach(el => { el.disabled = true; });
        const submit = form.querySelector('[type="submit"]'), status = form.querySelector('[data-settings-status]');
        submit.textContent = 'Saving…'; status.textContent = 'Saving changes…';
        try {
          const result = await h.api("/api/grading/settings", "PUT", {version: data.version, settings: {...s, dedicated_profiles: parsed}});
          if (!live()) return;
          data.version = result.version; saved = structuredClone(result.settings); s = structuredClone(saved);
          profiles = JSON.stringify(s.dedicated_profiles, null, 2); dirty = false;
          statusRequest++; seen = {endpoints: []}; draw(); h.toast("Compute settings saved."); void refreshStatus();
        } finally {
          controls.forEach(el => { el.disabled = false; });
          submit.textContent = 'Save compute settings';
          status.textContent = dirty ? 'Unsaved changes' : 'All changes saved';
        }
      });
    };
    // Switched off, the confidence values stay and are still saved: read-only, never disabled, so the form keeps sending them.
    const confidence = form.elements.namedItem("risk-confidence_checks");
    const showConfidence = () => form.querySelectorAll("[data-confidence-field]").forEach(input => { input.readOnly = !confidence.checked; input.closest(".field").classList.toggle("compute-field-unused", !confidence.checked); });
    if (confidence) { confidence.addEventListener("change", showConfidence); showConfidence(); }
    refreshChoices(); drawFacts(); connections(); drawSummary(form); setDirty(dirty);
    const scroller = form.querySelector('.compute-layout');
    scroller.scrollTop = place.scrollTop;
    if (place.key) holdPlace(place);
    if (selected === "availability") void loadAvailability();
  }
  draw();
  return {isDirty: () => dirty, resume: () => { void refreshStatus(); }, pause: () => { clearTimeout(statusTimer); statusRequest++; }};
}
