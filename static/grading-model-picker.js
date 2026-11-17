import {dialogSession} from "./ui.js";

export const MODEL_PROVIDERS = {
  openrouter: {label: 'OpenRouter', url: 'https://openrouter.ai/api/v1', key: 'OPENROUTER_API_KEY'},
  vercel: {label: 'Vercel AI Gateway', url: 'https://ai-gateway.vercel.sh/v1', key: 'AI_GATEWAY_API_KEY'},
  // The instructor's Claude subscription through Claude Code on the server (`grading/providers/agent_sdk.py`): no URL, no key.
  claude: {label: 'Claude subscription', url: '', key: '', kind: 'agent_sdk'},
  // The instructor's ChatGPT subscription through Codex on the server (`grading/providers/codex_sdk.py`): likewise.
  chatgpt: {label: 'ChatGPT subscription', url: '', key: '', kind: 'codex_sdk'},
  // The instructor's Google AI (Gemini) subscription through Google's Antigravity CLI on the server (`grading/providers/antigravity.py`).
  gemini: {label: 'Gemini subscription', url: '', key: '', kind: 'antigravity'},
  custom: {label: 'Other connections', url: '', key: ''},
};
export const SUBSCRIPTION_KINDS = ['agent_sdk', 'codex_sdk', 'antigravity'];
export const isSubscription = endpoint => SUBSCRIPTION_KINDS.includes(endpoint?.kind);
// Each plan as the pages name it: the plan, the program on the server that runs it, and what its model list holds.
export const PLANS = {agent_sdk: {plan: 'Claude', program: 'Claude Code', listed: 'Claude models on your subscription'},
  codex_sdk: {plan: 'ChatGPT', program: 'Codex', listed: 'ChatGPT models on your subscription'},
  antigravity: {plan: 'Gemini', program: 'Antigravity', listed: 'Models on your Gemini subscription'}};
export const planName = endpoint => `${(PLANS[endpoint?.kind] || PLANS.agent_sdk).plan} subscription`;
// The coding agent works through Claude Code or Codex (`config.TOOL_KINDS`), so a Gemini subscription connection is not offered
// for it. Every other role takes one, the Assistant included (`assistant/engines.py`).
export const TOOL_ROLES = ['coding_model'];
export const runsRole = (endpoint, role) => !(TOOL_ROLES.includes(role) && endpoint?.kind === 'antigravity');
export const providersFor = role => providerChoices.filter(([key]) => runsRole(MODEL_PROVIDERS[key], role));
// Browse asks the server which models each plan's installed program offers now (`model_catalog.subscription_listing`). These lists
// stand in when that cannot be read, and name a new connection's model; the server's `providers.codex_sdk.MODELS`,
// `providers.agent_sdk.MODELS` and `providers.antigravity.MODELS` are the same lists. The ChatGPT and Google plans' models come
// with the reasoning levels each offers; the Google plan serves Claude and GPT-OSS models too (GPT-OSS reads no pictures).
export const CODEX_MODELS = [['gpt-6-astra', 'GPT-6-Astra', ['low', 'medium', 'high', 'xhigh', 'max'], 'medium'], ['gpt-6.1-sol', 'GPT-6.1-Sol', ['low', 'medium', 'high', 'xhigh', 'max'], 'low'],
  ['gpt-6-sol', 'GPT-6-Sol', ['low', 'medium', 'high', 'xhigh', 'max'], 'medium'], ['gpt-6-luna', 'GPT-6-Luna', ['low', 'medium', 'high', 'xhigh', 'max'], 'medium'],
  ['gpt-5.6-sol', 'GPT-5.6-Sol', ['low', 'medium', 'high', 'xhigh', 'max'], 'low'],
  ['gpt-5.6-terra', 'GPT-5.6-Terra', ['low', 'medium', 'high', 'xhigh', 'max'], 'medium'], ['gpt-5.6-luna', 'GPT-5.6-Luna', ['low', 'medium', 'high', 'xhigh', 'max'], 'medium'],
  ['gpt-5.5', 'GPT-5.5', ['low', 'medium', 'high', 'xhigh'], 'medium']];
export const CLAUDE_MODELS = [['claude-opus-5-5', 'Claude Opus 5.5'], ['claude-sonnet-5-5', 'Claude Sonnet 5.5'], ['claude-sonnet-5', 'Claude Sonnet 5'],
  ['claude-haiku-4-5-20251001', 'Claude Haiku 4.5'], ['claude-fable-5-1', 'Claude Fable 5.1']];
export const GEMINI_MODELS = [['gemini-4-argon', 'Gemini 4 Argon', ['low', 'medium', 'high'], 'medium'], ['gemini-3.8-flash', 'Gemini 3.8 Flash', ['low', 'medium', 'high'], 'medium'],
  ['claude-opus-5-5', 'Claude Opus 5.5', ['low', 'medium', 'high'], 'medium'], ['claude-sonnet-5-5', 'Claude Sonnet 5.5', ['low', 'medium', 'high'], 'medium'],
  ['gpt-oss-120b', 'GPT-OSS 120B', ['medium'], 'medium']];
export const SUBSCRIPTION_FACTS = {supported: true, mandatory: true, efforts: ['low', 'medium', 'high', 'xhigh', 'max'], default_effort: 'medium'};
const BUILT_IN = {agent_sdk: CLAUDE_MODELS, codex_sdk: CODEX_MODELS, antigravity: GEMINI_MODELS};
// A plan's built-in list as the server's listing gives a model; a new connection of a plan starts on its first.
export const builtInModels = kind => BUILT_IN[kind].map(([id, name]) => ({id, name, ...(kind === 'antigravity' && id.startsWith('gpt-oss') ? {vision: false} : {})}));
export const firstModel = kind => kind === 'agent_sdk' ? 'claude-sonnet-5' : BUILT_IN[kind][0][0];
const family = model => ['gemini', 'claude', 'gpt'].find(name => String(model).startsWith(name)) || '';
// `listed`: what the plan's program said of this model (its reasoning facts and whether it reads images), else the lists above decide.
export function subscriptionEndpoint(model = 'claude-sonnet-5', name = '', kind = 'agent_sdk', listed = null) {
  const own = kind !== 'agent_sdk' && BUILT_IN[kind].find(m => m[0] === model);
  const facts = listed?.reasoning_options ? {...listed.reasoning_options, efforts: [...listed.reasoning_options.efforts]}
    : own ? {supported: true, mandatory: true, efforts: [...own[2]], default_effort: own[3]}
    : kind === 'antigravity' ? {supported: true, mandatory: true, efforts: ['low', 'medium', 'high'], default_effort: 'medium'} : {...SUBSCRIPTION_FACTS, efforts: [...SUBSCRIPTION_FACTS.efforts]};
  return {name, kind, base_url: '', key_env: '', model, lineage: kind === 'codex_sdk' ? 'gpt' : kind === 'antigravity' ? family(model) : 'claude', modality: listed?.vision === false ? 'text' : 'vision', structured: 'json_schema', reasoning: true,
    reasoning_tokens: 8192, max_tokens: 8192, price_in: 0, price_out: 0, upstream: '', aggregator: false, batch_api: false, max_inflight: 12, profile: '',
    enabled: true, notes: '', reasoning_options: facts, bundle_size: 6, sessions: 2};
}
export const providerChoices = Object.entries(MODEL_PROVIDERS).map(([key, value]) => [key, value.label]);
export function modelProvider(endpoint) {
  if (endpoint?.kind === 'agent_sdk') return 'claude';
  if (endpoint?.kind === 'codex_sdk') return 'chatgpt';
  if (endpoint?.kind === 'antigravity') return 'gemini';
  const url = (endpoint?.base_url || '').replace(/\/$/, '');
  return Object.keys(MODEL_PROVIDERS).find(key => MODEL_PROVIDERS[key].url && MODEL_PROVIDERS[key].url === url) || 'custom';
}
export function selectedProvider(settings, current, role = '') {
  const name = current || (role === 'rubric_reviewer' ? settings.tiers.cloud_standard.primary : '');
  return name ? modelProvider(settings.endpoints.find(e => e.name === name)) : 'openrouter';
}
export function catalogUrl(provider = 'openrouter', refresh = false) {
  const params = new URLSearchParams();
  if (provider !== 'openrouter') params.set('provider', provider);
  if (refresh) params.set('refresh', '1');
  return `/api/grading/model-catalog${params.size ? '?' + params : ''}`;
}
export const hostsUrl = (provider, model) => `/api/grading/model-catalog/hosts?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}`;
export const RECENT_MODEL_LIMIT = 5;
const assignedModels = settings => [...Object.values(settings.tiers).flatMap(team => Object.values(team)), settings.yardstick, settings.rubric_reviewer, settings.assistant_model, settings.coding_model].filter(Boolean);
export function recentModels(settings, current = "", role = "", provider = "") {
  const known = new Set(['nebius', ...settings.endpoints.map(e => e.name)]);
  const recent = [...new Set([...(settings.recent_models || []), ...assignedModels(settings)])]
    .filter(name => known.has(name) && (role !== 'yardstick' || name !== 'nebius') && runsRole(settings.endpoints.find(e => e.name === name), role) &&
      (!provider || modelProvider(settings.endpoints.find(e => e.name === name)) === provider)).slice(0, RECENT_MODEL_LIMIT);
  if (current && !recent.includes(current) && (!provider || modelProvider(settings.endpoints.find(e => e.name === current)) === provider)) return [...recent.slice(0, RECENT_MODEL_LIMIT - 1), current];
  return recent;
}
export function rememberModel(settings, name) {
  if (name) settings.recent_models = [...new Set([name, ...(settings.recent_models || []), ...assignedModels(settings)])].slice(0, 40);
}
export function modelScanWarning(endpoint, role) {
  // The rubric model drafts rubrics from a blank exam's pages as well as reviewing them: only the drafting needs images.
  if (role === 'rubric_reviewer') return endpoint?.modality === 'text' ? 'Warning: This text-only model can review rubrics but cannot draft them from a blank template, which needs an image-capable model.' : '';
  if (endpoint?.modality !== 'text' || !['reader', 'primary', 'second', 'verifier', 'flag_reviewer', 'yardstick'].includes(role)) return '';
  const detail = ['second', 'yardstick'].includes(role) ? 'It can only use the transcript and other supplied text.' : 'This step requires an image-capable model.';
  return `Warning: This text-only model cannot view the scan itself or consider it in its judgment. ${detail}`;
}
function builtin(settings) {
  return {name: 'nebius', model: settings.nebius_model, modality: 'vision', lineage: /qwen/i.test(settings.nebius_model) ? 'qwen3' : 'unrecorded',
    structured: 'json_object', enabled: true, kind: 'hosted', price_in: null, price_out: null};
}
export function savedModels(settings, role = "", provider = "") {
  return [...settings.endpoints, ...(role === 'yardstick' || !role ? [] : [builtin(settings)])]
    .filter(endpoint => !provider || modelProvider(endpoint) === provider).map(endpoint => ({
    id: `@connection/${endpoint.name}`, saved_name: endpoint.name, name: endpoint.name, endpoint, description: 'Saved connection. Your configured provider and limits are preserved; a gateway connection shows the gateway\'s current prices.', context_length: null,
    unavailable: !endpoint.enabled ? 'This connection is disabled. Enable it in Connections first.' :
      endpoint.kind === 'dedicated' && !settings.dedicated_enabled ? 'The dedicated GPU provider is disabled in Advanced settings.' : ''}));
}
export function existingConnection(model, settings) {
  if (model.saved_name) return model.saved_name === 'nebius' ? builtin(settings) : settings.endpoints.find(e => e.name === model.saved_name);
  return settings.endpoints.find(e => e.model === model.id && e.base_url.replace(/\/$/, "") === model.endpoint.base_url.replace(/\/$/, "") && e.kind === (model.endpoint.kind || "hosted") && e.enabled);
}
export function incompatibility(model, settings, role) {
  const endpoint = existingConnection(model, settings) || model.endpoint;
  if (model.unavailable && (model.saved_name || !existingConnection(model, settings))) return model.unavailable;
  if (["reader", "primary", "verifier", "flag_reviewer"].includes(role) && endpoint.modality !== "vision") return "This role needs a model that can see images.";
  if (!runsRole(endpoint, role)) return "The coding agent does not run on the Gemini subscription.";
  return "";
}
export function connectionFor(model, settings) {
  const existing = existingConnection(model, settings);
  if (existing) return existing;
  const base = `${modelProvider(model.endpoint) === 'vercel' ? 'vercel-' : modelProvider(model.endpoint) === 'claude' ? 'subscription-' : modelProvider(model.endpoint) === 'chatgpt' ? 'chatgpt-' : modelProvider(model.endpoint) === 'gemini' ? 'google-' : ''}${model.id.replace(/[^A-Za-z0-9_.:-]/g, "-")}`.slice(0, 52);
  let name = base, suffix = 2;
  while (settings.endpoints.some(e => e.name === name)) name = `${base}-${suffix++}`;
  return {...model.endpoint, name, lineage: model.endpoint.lineage || ""};
}
const money = value => value == null ? "Price unavailable" : `$${Number(value).toLocaleString(undefined, {maximumFractionDigits: 4})}`;
const cents = value => value == null ? null : Math.round(value * 1e6) / 1e6;

// Browsing reads the gateway's current prices; a saved connection of that gateway takes them into the draft, so the choice is made
// on today's prices. A connection pinned to one host takes that host's prices (its listing, read only for such connections), any
// other the catalogue's. A price the gateway does not list leaves the saved one. Nothing is saved until compute settings are.
export async function currentPrices(h, settings, provider, models) {
  const listed = new Map(models.map(m => [m.id, m.endpoint])), changes = [];
  const gateway = settings.endpoints.filter(e => e.kind === 'hosted' && modelProvider(e) === provider && listed.has(e.model));
  const hosts = new Map();
  await Promise.all([...new Set(gateway.filter(e => e.upstream).map(e => e.model))].map(async model => {
    try { hosts.set(model, await h.api(hostsUrl(provider, model))); }
    catch { /* The host listing is unavailable: that connection keeps its saved prices. */ }
  }));
  for (const endpoint of gateway) {
    const source = endpoint.upstream ? hosts.get(endpoint.model)?.hosts?.find(item => item.id === endpoint.upstream) : listed.get(endpoint.model);
    const price_in = cents(source?.price_in), price_out = cents(source?.price_out);
    if (price_in == null || price_out == null || (price_in === endpoint.price_in && price_out === endpoint.price_out)) continue;
    changes.push({name: endpoint.name, before: [endpoint.price_in, endpoint.price_out], after: [price_in, price_out]});
    Object.assign(endpoint, {price_in, price_out});
  }
  return changes;
}
export const priceChangeText = changes => changes.length ? ` Current prices taken for ${changes.length} saved connection${changes.length === 1 ? '' : 's'} (save compute settings to keep them): ${
  changes.map(c => `${c.name} ${money(c.before[0])} / ${money(c.before[1])} → ${money(c.after[0])} / ${money(c.after[1])}`).join('; ')}.` : '';

export function modelPricing(settings, name, role = '') {
  const inherited = !name && role === 'rubric_reviewer';
  if (inherited) name = settings.tiers.cloud_standard.primary;
  if (!name) return '';
  const endpoint = name === 'nebius' ? builtin(settings) : settings.endpoints.find(e => e.name === name);
  const prefix = inherited ? 'Cloud primary grader · ' : '';
  if (isSubscription(endpoint)) return `${prefix}Included in your ${planName(endpoint)} · ${endpoint.bundle_size ?? 6} responses per call${endpoint.api_fallback ? ' · Grading API fallback billed separately' : ''}`;
  if (endpoint?.price_in == null && endpoint?.price_out == null) return `${prefix}Token pricing unavailable for this connection.`;
  return `${prefix}${money(endpoint.price_in)} input · ${money(endpoint.price_out)} output / 1M tokens (USD estimate)${endpoint.bundle_size > 1 ? ` · ${endpoint.bundle_size} responses per call` : ''}`;
}

export function openModelPicker(h, settings, {role = "", label = "model", provider = "openrouter", active, apply, repriced = () => {}}) {
  // A gateway's catalogue lists every model with its current price, and a model already saved is marked on its row, so saved
  // connections are listed separately only where there is no catalogue: Other connections, or a gateway that cannot be reached.
  const source = MODEL_PROVIDERS[provider], saved = savedModels(settings, role, provider);
  let catalogue = source.url ? [] : [...saved], chosen = null, generation = 0, shown = 40;
  const form = h.dialog("Choose a model", `${label} · ${source.label}. Your choice stays in the draft until you save compute settings.`,
    `<div class="model-picker"><div class="model-picker-tools">${h.field("Search models", "model-search", "", "search", 'placeholder="Name or model ID" autocomplete="off"')}
      ${h.select("Sort models", "model-sort", [["name", "Name"], ["price", "Lowest input price"]])}</div>
      <div class="model-picker-filters">${h.select("Model input", "model-input", [["", "All compatible inputs"], ["vision", "Images and text"], ["text", "Text only"]])}
      ${h.check("Show incompatible models", "model-incompatible")}${h.button("refresh-catalog", "Refresh catalogue")}</div>
      <p class="supporting-copy" data-catalog-status role="status">Loading ${source.label} catalogue…</p>
      <p class="supporting-copy" data-catalog-count aria-live="polite"></p>
      <div class="model-picker-results" role="radiogroup" aria-label="Available models" aria-busy="true"></div>
      ${h.button("more-models", "Show more models", "hidden")}
      <div class="model-picker-selection" hidden></div>
      <p class="grading-model-warning" data-catalog-scan-warning aria-live="polite"></p>
      <p class="supporting-copy">Prices are estimates per million tokens (input / output). Provider charges can vary. Saved connections of this gateway take its current prices when the catalogue loads.</p></div>`,
    "Use model", async (values, _, session) => {
      if (!active() || !session() || !chosen) return;
      const endpoint = connectionFor(chosen, settings);
      const reason = incompatibility({...chosen, endpoint}, settings, role);
      if (reason) throw new Error(reason);
      if (!existingConnection(chosen, settings) && settings.endpoints.length >= 40) throw new Error("You have 40 model connections. Remove an unused connection before adding another.");
      h.close();
      apply(endpoint);
    });
  const session = dialogSession(form, form.closest("dialog")), live = () => active() && session();
  const submit = form.querySelector('[type="submit"]'), list = form.querySelector('.model-picker-results');
  submit.disabled = true;
  const field = name => form.elements.namedItem(name);
  function updateSubmit() {
    submit.disabled = !chosen || !!incompatibility(chosen, settings, role);
  }
  function selection() {
    const box = form.querySelector('.model-picker-selection');
    box.hidden = !chosen;
    form.querySelector('[data-catalog-scan-warning]').textContent = modelScanWarning(chosen && (existingConnection(chosen, settings) || chosen.endpoint), role);
    if (!chosen) { box.innerHTML = ''; updateSubmit(); return; }
    const existing = existingConnection(chosen, settings), endpoint = existing || chosen.endpoint;
    box.innerHTML = `<strong>${h.e(chosen.name)}</strong><p>${h.e(chosen.description)}</p><p class="supporting-copy">${h.e(endpoint.modality === 'vision' ? 'Images and text' : 'Text only')} · ${chosen.context_length ? `${h.e(chosen.context_length.toLocaleString())} token context` : 'Context limit unlisted'}${existing ? ` · Uses your saved ${h.e(existing.name)} connection` : ''}</p>`;
    updateSubmit();
  }
  function render() {
    if (!live()) return;
    const query = String(field('model-search').value).trim().toLowerCase(), input = field('model-input').value;
    let matches = catalogue.filter(m => `${m.name} ${m.id} ${m.endpoint.model}`.toLowerCase().includes(query) && (!input || (existingConnection(m, settings) || m.endpoint).modality === input) &&
      (field('model-incompatible').checked || !incompatibility(m, settings, role)));
    matches.sort((a, b) => field('model-sort').value === 'price' ?
      (a.endpoint.price_in ?? Infinity) - (b.endpoint.price_in ?? Infinity) || a.name.localeCompare(b.name) : a.name.localeCompare(b.name));
    form.querySelector('[data-catalog-count]').textContent = `${matches.length} matching model${matches.length === 1 ? '' : 's'}`;
    list.innerHTML = matches.slice(0, shown).map(m => {
      const reason = incompatibility(m, settings, role), endpoint = existingConnection(m, settings) || m.endpoint;
      return `<label class="model-picker-row"><input type="radio" name="catalog-model" value="${h.e(m.id)}" ${chosen?.id === m.id ? 'checked' : ''} ${reason ? 'disabled' : ''}>
        <span><strong>${h.e(m.name)}</strong><small>${h.e(endpoint.model)}${m.saved_name ? ' · Saved connection' : existingConnection(m, settings) ? ` · Saved as ${h.e(existingConnection(m, settings).name)}` : ''}</small><small>${endpoint.modality === 'vision' ? 'Images + text' : 'Text only'} · ${h.e(endpoint.structured === 'json_schema' ? 'JSON schema' : endpoint.structured === 'json_object' ? 'JSON' : 'Prompt-based JSON')}${reason ? ` · ${h.e(reason)}` : ''}</small></span>
        <span class="model-picker-price">${isSubscription(endpoint) ? 'Subscription' : `${h.e(money(endpoint.price_in))} / ${h.e(money(endpoint.price_out))}`}</span></label>`;
    }).join('') || '<p class="compute-empty">No models match. Try another search or show incompatible models.</p>';
    list.querySelectorAll('input').forEach(radio => radio.onchange = () => { chosen = catalogue.find(m => m.id === radio.value); selection(); });
    form.querySelector('[data-grading-action="more-models"]').hidden = matches.length <= shown;
  }
  async function load(refresh = false) {
    const current = ++generation, button = form.querySelector('[data-grading-action="refresh-catalog"]');
    if (source.kind) {
      // The plan's installed program names its models now, at no per-token price; the built-in list stands in when it cannot.
      const kind = source.kind, {plan, program, listed} = PLANS[kind];
      const usage = 'Usage counts toward your plan’s limits; a reached limit pauses this connection until it resets.';
      const entries = models => models.map(m => ({id: m.id, name: m.name, endpoint: subscriptionEndpoint(m.id, '', kind, m), context_length: null,
        description: `${m.description ? m.description.replace(/\.?$/, '. ') : ''}Runs on your ${plan} subscription through ${program} on this server, with no API billing. Responses to the same question are graded together, several per call.`}));
      button.disabled = true;
      form.querySelector('[data-catalog-status]').textContent = `Asking ${program} which models your ${plan} plan offers…`;
      list.setAttribute('aria-busy', 'true');
      let models, notice;
      try {
        const data = await h.api(catalogUrl(provider, refresh));
        models = data.models;
        notice = data.notice ? `${data.notice} ${usage}` : `${listed}, as ${program} lists them now. ${usage}`;
      } catch (error) {
        models = builtInModels(kind);
        notice = `${error.message} Showing Faculty's built-in list of your ${plan} plan's models. ${usage}`;
      }
      if (!live() || current !== generation) return;
      catalogue = entries(models);
      chosen = catalogue.find(m => m.id === chosen?.id) || null;
      form.querySelector('[data-catalog-status]').textContent = notice;
      button.disabled = false;
      list.setAttribute('aria-busy', 'false');
      selection(); render();
      return;
    }
    if (!source.url) {
      button.hidden = true;
      form.querySelector('[data-catalog-status]').textContent = 'Your saved custom and built-in connections.';
      list.setAttribute('aria-busy', 'false');
      return;
    }
    button.disabled = true;
    form.querySelector('[data-catalog-status]').textContent = refresh ? 'Refreshing catalogue…' : `Loading ${source.label} catalogue…`;
    list.setAttribute('aria-busy', 'true');
    try {
      const data = await h.api(catalogUrl(provider, refresh));
      if (!live() || current !== generation) return;
      // A stale (unreachable) catalogue is the last download, not today's prices: saved connections keep theirs.
      const changes = data.stale ? [] : await currentPrices(h, settings, provider, data.models);
      if (!live() || current !== generation) return;
      if (changes.length) repriced(changes);
      catalogue = data.models.length ? [...data.models] : [...saved];
      const previous = chosen;
      chosen = catalogue.find(m => m.id === previous?.id) || null;
      form.querySelector('[data-catalog-status]').textContent = (data.notice || `Catalogue updated ${new Date(data.checked_at).toLocaleString()}.`) + priceChangeText(changes);
      selection(); render();
    } catch (error) {
      if (live() && current === generation) {
        // Keep a catalogue already on screen; with none, the saved connections are what can be chosen.
        if (!catalogue.length) catalogue = [...saved];
        form.querySelector('[data-catalog-status]').textContent = `${error.message} ${catalogue.some(m => m.saved_name) ? 'Your saved connections are listed instead.' : 'Showing the catalogue loaded earlier.'}`;
        render();
      }
    } finally {
      if (live() && current === generation) { button.disabled = false; list.setAttribute('aria-busy', 'false'); }
    }
  }
  for (const name of ['model-search', 'model-input', 'model-incompatible', 'model-sort']) field(name).addEventListener(name === 'model-search' ? 'input' : 'change', () => { shown = 40; render(); });
  form.querySelector('[data-grading-action="refresh-catalog"]').onclick = () => { void load(true); };
  form.querySelector('[data-grading-action="more-models"]').onclick = () => { shown += 40; render(); };
  // Enter in the search box should search, not unexpectedly apply a previous selection.
  field('model-search').addEventListener('keydown', event => { if (event.key === 'Enter') event.preventDefault(); });
  field('model-search').focus();
  render();
  void load();
  return form;
}
