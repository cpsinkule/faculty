import {env} from 'cloudflare:workers';
import {applyD1Migrations} from 'cloudflare:test';
import {beforeAll,afterEach,expect,it,vi} from 'vitest';
import worker,{hash} from '../src/index';
import {sha256Hex, UIN_MARK,validateAnswers,validateSchema} from '../src/schema';

const origin='https://respond.example.com';
beforeAll(async()=>{await applyD1Migrations(env.DB,env.TEST_MIGRATIONS);});
afterEach(()=>vi.restoreAllMocks());
function request(path:string,method='GET',body?:unknown,faculty=false,headers:Record<string,string>={}) {
  return worker.fetch(new Request(origin+path,{method,headers:{...(body?{'Content-Type':'application/json','Origin':origin}:{}),...(faculty?{Authorization:'Bearer '+env.FACULTY_API_SECRET}:{}),...headers},body:body?JSON.stringify(body):undefined}),env);
}
function schema() {return {title:'Exam confirmation',fields:[
  {id:'info',type:'static',label:'Please confirm your seat.'},
  {id:'ack',type:'acknowledgment',label:'I read the instructions',required:true},
  {id:'seat',type:'confirmation',label:'Enter your seat',required:true,verification:{expected:'C17',mode:'exact',on_mismatch:'ALLOW_AND_FLAG'}},
  {id:'attend',type:'boolean',label:'Will you attend?',required:true},
  {id:'reason',type:'long_text',label:'Explain why',required:true,show_if:{field:'attend',equals:false}},
]};}
async function publish(extra:Record<string,unknown>={}) {
  const id=crypto.randomUUID(),token=crypto.randomUUID().replaceAll('-','')+crypto.randomUUID().replaceAll('-','');
  const snapshot={id,issue_id:'issue-test',token_hash:await hash(token),schema:schema(),policy:'once',due_at:null,expires_at:null,...extra};
  const response=await request('/faculty-api/cards/'+id,'PUT',snapshot,true);expect(response.status).toBe(200);
  return {id,token,snapshot,path:'/api/cards/'+token};
}
function protect(result:Record<string,unknown>={}) {return vi.spyOn(globalThis,'fetch').mockImplementation(async()=>Response.json({success:true,hostname:'respond.example.com',action:'response_card_submit',...result}));}
const good={ack:true,seat:'C17',attend:true};
function send(path:string,answers:unknown=good,request_id=crypto.randomUUID()) {return request(path+'/submit','POST',{request_id,answers,turnstile_token:'test-token'});}

it('requires Faculty authentication and refuses cross-origin public writes',async()=>{
  expect((await request('/faculty-api/events')).status).toBe(401);
  const c=await publish();
  expect((await request(c.path+'/draft','PUT',{answers:good},false,{Origin:'https://elsewhere.test'})).status).toBe(403);
  expect((await request(c.path+'/draft','PUT',{answers:good},false,{Origin:''})).status).toBe(403);
});
it('publishes once, deduplicates identical snapshots and rejects changed ones',async()=>{
  const c=await publish();
  expect((await request('/faculty-api/cards/'+c.id,'PUT',c.snapshot,true)).status).toBe(200);
  expect((await request('/faculty-api/cards/'+c.id,'PUT',{...c.snapshot,policy:'unlimited'},true)).status).toBe(409);
  const stored=await env.DB.prepare('SELECT * FROM public_cards WHERE id=?').bind(c.id).first();
  expect(JSON.stringify(stored)).not.toContain(c.token);
  const other={...c.snapshot,id:crypto.randomUUID()};
  expect((await request('/faculty-api/cards/'+other.id,'PUT',other,true)).status).toBe(409);
});
it('strips expected answers and retains restored drafts and status events',async()=>{
  const c=await publish(),loaded=await (await request(c.path)).json();
  expect(loaded.status).toBe('opened');expect(JSON.stringify(loaded)).not.toContain('C17');expect(loaded.schema.fields[2].verification).toBeUndefined();
  expect((await request(c.path+'/draft','PUT',{answers:{seat:'B2',attend:false}})).status).toBe(200);
  const saved=await (await request(c.path)).json();expect(saved.draft).toEqual({seat:'B2',attend:false});expect(saved.status).toBe('started');
});
it('records immutable verified submissions and retries without a second Turnstile call',async()=>{
  const c=await publish(),verify=protect(),key=crypto.randomUUID();
  const first=await (await send(c.path,good,key)).json();expect(first.submission.status).toBe('verified');
  const repeated=await (await send(c.path,good,key)).json();expect(repeated.submission.id).toBe(first.submission.id);expect(verify).toHaveBeenCalledOnce();
  expect((await send(c.path,{...good,seat:'A2'},key)).status).toBe(409);
  expect((await send(c.path)).status).toBe(409);
  expect((await request(c.path+'/draft','PUT',{answers:good})).status).toBe(409);
  await expect(env.DB.prepare('UPDATE submissions SET status=? WHERE id=?').bind('submitted',first.submission.id).run()).rejects.toThrow('immutable');
  await expect(env.DB.prepare('DELETE FROM submissions WHERE id=?').bind(first.submission.id).run()).rejects.toThrow('immutable');
});
it('atomically permits only one concurrent one-shot submission',async()=>{
  const c=await publish();protect();
  const responses=await Promise.all(Array.from({length:6},()=>send(c.path)));
  expect(responses.filter(r=>r.status===200)).toHaveLength(1);expect(responses.filter(r=>r.status===409)).toHaveLength(5);
  const count=await env.DB.prepare('SELECT count(*) AS n FROM submissions WHERE card_id=?').bind(c.id).first();expect(count.n).toBe(1);
});
it('accepts flagged mismatches and blocks required, conditional and blocking mismatches',async()=>{
  const c=await publish({policy:'unlimited'});protect();
  const mismatch=await (await send(c.path,{...good,seat:'C71'})).json();expect(mismatch.submission.status).toBe('needs_review');
  expect((await send(c.path,{...good,ack:false})).status).toBe(422);
  expect((await send(c.path,{...good,attend:false})).status).toBe(422);
  expect((await send(c.path,{...good,attend:false,reason:'Other appointment'})).status).toBe(200);
  const s=schema();s.fields[2].verification!.on_mismatch='BLOCK_SUBMISSION';const blocked=await publish({schema:s});
  expect((await send(blocked.path,{...good,seat:'C71'})).status).toBe(422);
  expect((await send(blocked.path)).status).toBe(200);
});
it.each([{success:false},{hostname:'wrong.test'},{action:'login'},{hostname:undefined},{action:undefined}])('fails closed for Siteverify response %j',async result=>{
  const c=await publish();protect(result);expect((await send(c.path)).status).toBe(403);
  expect((await env.DB.prepare('SELECT count(*) AS n FROM submissions WHERE card_id=?').bind(c.id).first()).n).toBe(0);
});
it('fails closed on unavailable or missing Turnstile configuration',async()=>{
  const c=await publish();vi.spyOn(globalThis,'fetch').mockRejectedValue(new Error('offline'));expect((await send(c.path)).status).toBe(403);
  const r=new Request(origin+c.path+'/submit',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify({request_id:'test',answers:good,turnstile_token:'token'})});
  expect((await worker.fetch(r,{...env,TURNSTILE_SECRET:''})).status).toBe(503);
});
it('enforces deadlines and revocation on server routes',async()=>{
  const past='2020-01-01T00:00:00Z';const expired=await publish({expires_at:past});expect((await request(expired.path)).status).toBe(410);
  const due=await publish({policy:'until_due',due_at:past});expect((await request(due.path+'/draft','PUT',{answers:good})).status).toBe(410);
  const c=await publish();expect((await request('/faculty-api/cards/'+c.id+'/control','POST',{request_id:'revoke-1',action:'revoke'},true)).status).toBe(200);expect((await request(c.path)).status).toBe(410);
  expect((await request('/faculty-api/cards/'+c.id+'/control','POST',{request_id:'unlock-1',action:'unlock'},true)).status).toBe(409);
});
it('retains unlimited history and unlocks one additional submission idempotently',async()=>{
  const c=await publish({policy:'instructor_unlock'});protect();expect((await send(c.path)).status).toBe(200);
  const path='/faculty-api/cards/'+c.id+'/control',body={request_id:'unlock-1',action:'unlock'};
  expect((await request(path,'POST',body,true)).status).toBe(200);expect((await send(c.path)).status).toBe(200);
  expect((await request(path,'POST',body,true)).status).toBe(200);expect((await send(c.path)).status).toBe(409);
  const unlimited=await publish({policy:'unlimited'});expect((await send(unlimited.path)).status).toBe(200);expect((await send(unlimited.path,{...good,seat:'B2'})).status).toBe(200);
  const loaded=await (await request(unlimited.path)).json();expect(loaded.history).toHaveLength(2);expect(loaded.submission_count).toBe(2);
});
it('syncs ordered, resumable events with a status and complete submission object',async()=>{
  const c=await publish();protect();await request(c.path);await send(c.path);
  await request('/faculty-api/cards/'+c.id+'/control','POST',{request_id:'unlock',action:'unlock'},true);
  const events=[];let cursor=0,more=true;while(more){const page=await(await request('/faculty-api/events?after='+cursor+'&limit=3','GET',undefined,true)).json();events.push(...page.events);expect(page.cursor).toBeGreaterThanOrEqual(cursor);cursor=page.cursor;more=page.has_more;}
  const own=events.filter(e=>e.card_id===c.id);expect(own.every(e=>typeof e.data.status==='string')).toBe(true);expect(own.find(e=>e.type==='submission').data.submission.answers).toEqual(good);
  expect((await(await request('/faculty-api/events?after='+cursor,'GET',undefined,true)).json()).events).toHaveLength(0);
});
it('bounds request streams and gives the student page privacy headers',async()=>{
  const c=await publish();const response=await request(c.path+'/draft','PUT',{answers:{seat:'x'.repeat(140000)}});expect(response.status).toBe(413);
  const page=await request('/r/'+c.token);expect(page.headers.get('referrer-policy')).toBe('no-referrer');expect(page.headers.get('cache-control')).toContain('no-store');expect(page.headers.get('content-security-policy')).toContain("frame-ancestors 'none'");expect(await page.text()).not.toContain(c.token);
});
it('rejects deeply nested answer JSON before hashing can exhaust the Worker stack',async()=>{
  const c=await publish(),verify=protect();
  const body='{"request_id":"nested","answers":{"seat":'+'['.repeat(6000)+'"C17"'+']'.repeat(6000)+'},"turnstile_token":"test-token"}';
  const response=await worker.fetch(new Request(origin+c.path+'/submit',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body}),env);
  expect(response.status).toBe(400);
  expect(await response.json()).toEqual({error:'JSON nesting is too deep.'});
  expect(verify).not.toHaveBeenCalled();
  expect((await env.DB.prepare('SELECT count(*) AS n FROM submissions WHERE card_id=?').bind(c.id).first()).n).toBe(0);
});
it('ignores hidden field injection and validates typed choices and dates',()=>{
  const s=validateSchema(schema());expect(validateAnswers(s,{...good,reason:'hidden malicious answer'}).answers.reason).toBeUndefined();
  const numeric=validateSchema({title:'Types',fields:[{id:'n',type:'integer',label:'Number',required:true},{id:'d',type:'date',label:'Date'},{id:'m',type:'multi_select',label:'Options',options:['a','b']}]});
  expect(()=>validateAnswers(numeric,{n:'1'})).toThrow();expect(()=>validateAnswers(numeric,{n:1,d:'2026-02-30'})).toThrow();expect(()=>validateAnswers(numeric,{n:1,m:['c']})).toThrow();expect(validateAnswers(numeric,{n:1,d:'2026-02-28',m:['a']}).answers.n).toBe(1);
});
it('rechecks revocation after Turnstile and commits no submission when revoked concurrently',async()=>{
  const c=await publish();
  vi.spyOn(globalThis,'fetch').mockImplementation(async()=>{
    await request('/faculty-api/cards/'+c.id+'/control','POST',{request_id:'race-revoke',action:'revoke'},true);
    return Response.json({success:true,hostname:'respond.example.com',action:'response_card_submit'});
  });
  expect((await send(c.path)).status).toBe(409);
  expect((await env.DB.prepare('SELECT count(*) AS n FROM submissions WHERE card_id=?').bind(c.id).first()).n).toBe(0);
});
it('bounds large event pages without dropping any immutable submissions',async()=>{
  const before=await env.DB.prepare('SELECT max(cursor) AS n FROM card_events').first(),start=before.n??0;
  const fields=Array.from({length:20},(_,i)=>({id:'field'+i,type:'long_text',label:'Detail '+i}));
  const c=await publish({schema:{title:'Large response',fields},policy:'unlimited'}),answers=Object.fromEntries(fields.map(f=>[f.id,'x'.repeat(6000)]));protect();
  for(let i=0;i<19;i++)expect((await send(c.path,answers)).status).toBe(200);
  const response=await request('/faculty-api/events?after='+start,'GET',undefined,true),body=await response.text(),first=JSON.parse(body);
  expect(new TextEncoder().encode(body).length).toBeLessThan(2000100);expect(first.has_more).toBe(true);
  const second=await(await request('/faculty-api/events?after='+first.cursor,'GET',undefined,true)).json();
  const all=[...first.events,...second.events];expect(all.filter(e=>e.type==='submission'&&e.card_id===c.id)).toHaveLength(19);expect(new Set(all.map(e=>e.cursor)).size).toBe(all.length);expect(second.has_more).toBe(false);
});
it('supports numeric, normalized and typed one-of verification modes',()=>{
  const s=validateSchema({title:'Checks',fields:[
    {id:'n',type:'decimal',label:'Number',verification:{mode:'numeric',expected:'1.50'}},
    {id:'name',type:'short_text',label:'Name',verification:{mode:'normalized',expected:'Jane Smith'}},
    {id:'yes',type:'boolean',label:'Confirm',verification:{mode:'one-of',expected:[true,1]}},
  ]});
  expect(validateAnswers(s,{n:1.5,name:'  JANE   Smith ',yes:true}).status).toBe('verified');
  expect(validateAnswers(s,{n:2,name:'Jane Smith',yes:false}).status).toBe('needs_review');
});
it('evaluates empty multiple-choice conditions consistently for drafts and submissions',()=>{
  const s=validateSchema({title:'Choices',fields:[
    {id:'options',type:'multi_select',label:'Pick options',options:['a','b']},
    {id:'reason',type:'short_text',label:'Why none?',required:true,show_if:{field:'options',equals:[]}},
  ]});
  expect(validateAnswers(s,{options:[],reason:'No suitable option'}).answers).toEqual({options:[],reason:'No suitable option'});
  expect(validateAnswers(s,{options:[],reason:'Draft'},true).answers.reason).toBe('Draft');
  expect(()=>validateAnswers(s,{options:[]})).toThrow();
  expect(validateAnswers(s,{options:['a'],reason:'Hidden'}).answers.reason).toBeUndefined();
});

it('verifies compact codes and salted UIN digests without storing the UIN',()=>{
  expect(sha256Hex('abc')).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  expect(sha256Hex('0123456789abcdef001234567')).toBe('16a0df6add80ce8e71b02453534f0255a2641787d4ffa8a774964f9ac9523e4b');
  const s=validateSchema({title:'Seat check',fields:[
    {id:'room',type:'short_text',label:'Room',required:true,verification:{expected:'HECC 105',mode:'compact',on_mismatch:'BLOCK_SUBMISSION'}},
    {id:'uin',type:'short_text',label:'UIN',required:true,verification:{expected:'0123456789abcdef:16a0df6add80ce8e71b02453534f0255a2641787d4ffa8a774964f9ac9523e4b',mode:'uin-digest',on_mismatch:'BLOCK_SUBMISSION'}}]});
  for (const room of ['HECC 105','hecc105',' Hecc - 105 ','HECC\u00a0105']) expect(validateAnswers(s,{room,uin:'001234567'}).status).toBe('verified');
  for (const uin of ['001234567','001-234-567',' 001 234 567 ','UIN 001234567']) expect(validateAnswers(s,{room:'HECC 105',uin}).status).toBe('verified');
  expect(()=>validateAnswers(s,{room:'HECC 106',uin:'001234567'})).toThrow();
  expect(()=>validateAnswers(s,{room:'HECC 105',uin:'001234568'})).toThrow();
  expect(()=>validateAnswers(s,{room:'HECC 105',uin:'1234567'})).toThrow();
  expect(JSON.stringify(s)).not.toContain('001234567');
  expect(()=>validateSchema({title:'Bad',fields:[{id:'uin',type:'short_text',label:'UIN',verification:{expected:'001234567',mode:'uin-digest'}}]})).toThrow();
  // The typed UIN never reaches storage: submissions keep a marker, drafts drop it, and the error names the field plainly.
  const checked=validateAnswers(s,{room:'HECC 105',uin:'001-234-567'});
  expect(checked.answers).toEqual({room:'HECC 105',uin:UIN_MARK});
  expect(JSON.stringify(checked)).not.toContain('001234567');
  expect(validateAnswers(s,{room:'HECC 105',uin:'001234567'},true).answers).toEqual({room:'HECC 105'});
  let failure:unknown=null;
  try {validateAnswers(s,{room:'HECC 105',uin:'001234568'});} catch (error) {failure=error;}
  expect((failure as {fields?:Record<string,string>}).fields).toEqual({uin:'Enter your own 9-digit UIN.'});
});
