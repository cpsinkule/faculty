import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../static/grading-model-picker.js", import.meta.url), "utf8");
const standalone = source.replace(/^import .*$/m, "const dialogSession = () => {};");
const {recentModels, rememberModel, savedModels, connectionFor, incompatibility, modelProvider, catalogUrl, hostsUrl} = await import(`data:text/javascript;base64,${Buffer.from(standalone).toString("base64")}`);
const settings = () => ({recent_models: [], nebius_model: 'Qwen3-VL', dedicated_enabled: false,
  endpoints: 'abcdefg'.split('').map(name => ({name, model: `custom/${name}`, base_url: 'https://custom.example', kind: 'hosted', enabled: true, modality: 'vision', lineage: name})),
  tiers: {standard: {reader: 'a', primary: 'a', second: 'b', verifier: ''}}, yardstick: 'c'});

test('initial recent choices use assigned models without adding unused presets or mutating settings', () => {
  const s = settings(), before = structuredClone(s);
  assert.deepEqual(recentModels(s, 'a'), ['a', 'b', 'c']);
  assert.deepEqual(s, before);
});

test('selections promote unique choices, cap visible history and keep an older selection visible', () => {
  const s = settings();
  for (const name of ['d', 'e', 'f', 'g', 'd']) rememberModel(s, name);
  assert.deepEqual(s.recent_models, ['d', 'g', 'f', 'e', 'a', 'b', 'c']);
  assert.deepEqual(recentModels(s), ['d', 'g', 'f', 'e', 'a']);
  assert.deepEqual(recentModels(s, 'b'), ['d', 'g', 'f', 'e', 'b']);
  const history = [...s.recent_models];
  rememberModel(s, '');
  assert.deepEqual(s.recent_models, history); // None is a control, not a model.
  s.endpoints = s.endpoints.filter(e => e.name !== 'd');
  assert.deepEqual(recentModels(s), ['g', 'f', 'e', 'a', 'b']);
});

test('saved custom connections and built-in remain accessible outside the recent list', () => {
  const s = settings(), rows = savedModels(s, 'reader');
  const unused = rows.find(row => row.saved_name === 'g');
  assert.equal(connectionFor(unused, s, ''), s.endpoints.at(-1));
  assert.ok(rows.some(row => row.saved_name === 'nebius'));
  assert.ok(!savedModels(s, 'yardstick').some(row => row.saved_name === 'nebius'));
  s.endpoints.at(-1).enabled = false;
  assert.match(incompatibility(savedModels(s, 'reader').find(row => row.saved_name === 'g'), s, 'reader'), /disabled/);
  s.tiers.standard.primary = 'nebius';
  s.endpoints[1].lineage = 'qwen3';
  assert.equal(incompatibility(savedModels(s, 'second')[1], s, 'second'), '');
});

test('provider lists and reuse are scoped by gateway even for identical model IDs', () => {
  const s = settings();
  s.endpoints[0] = {...s.endpoints[0], model: 'anthropic/claude-test', base_url: 'https://openrouter.ai/api/v1'};
  const remote = {id: 'anthropic/claude-test', endpoint: {...s.endpoints[0], base_url: 'https://ai-gateway.vercel.sh/v1', key_env: 'AI_GATEWAY_API_KEY'}};
  const vercel = connectionFor(remote, s, '');
  assert.notEqual(vercel.name, 'a');
  assert.equal(vercel.key_env, 'AI_GATEWAY_API_KEY');
  s.endpoints.push(vercel);
  rememberModel(s, vercel.name);
  assert.equal(connectionFor(remote, s, ''), vercel);
  assert.deepEqual(recentModels(s, 'a', 'reader', 'vercel'), [vercel.name]);
  assert.deepEqual(recentModels(s, vercel.name, 'reader', 'openrouter'), ['a']);
  assert.deepEqual(savedModels(s, 'reader', 'vercel').map(m => m.saved_name), [vercel.name]);
  assert.ok(savedModels(s, 'reader', 'custom').some(m => m.saved_name === 'nebius'));
  assert.equal(modelProvider(vercel), 'vercel');
  assert.equal(catalogUrl('vercel', true), '/api/grading/model-catalog?provider=vercel&refresh=1');
  assert.equal(catalogUrl(), '/api/grading/model-catalog');
  assert.equal(hostsUrl('openrouter', 'xiaomi/mimo-v2.6-pro'), '/api/grading/model-catalog/hosts?provider=openrouter&model=xiaomi%2Fmimo-v2.6-pro');
});
