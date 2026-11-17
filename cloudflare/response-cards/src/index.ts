import {ApiError, Schema, object, publicSchema, stable, validateAnswers, validateSchema} from './schema';
import {page, client, styles} from './public';

interface Card {
  id:string; issue_id:string; token_hash:string; snapshot_json:string; snapshot_hash:string;
  policy:string; due_at:string|null; expires_at:string|null; status:string;
  revoked:number; unlocked:number; submission_count:number; current_submission_id:string|null;
  draft_json:string|null; draft_updated_at:string|null;
}
interface Submission {id:string; request_id:string; request_hash:string; answers_json:string; verification_json:string; status:string; submitted_at:string}
const ID=/^[A-Za-z0-9_-]{1,100}$/;
const TOKEN=/^[A-Za-z0-9_-]{32,128}$/;
const writable=`revoked=0 AND (expires_at IS NULL OR julianday(expires_at)>julianday('now'))
  AND (policy!='until_due' OR due_at IS NULL OR julianday(due_at)>julianday('now'))
  AND (submission_count=0 OR policy IN ('unlimited','until_due') OR unlocked=1)`;
const securityHeaders={
  'Cache-Control':'no-store, private', 'Referrer-Policy':'no-referrer',
  'X-Content-Type-Options':'nosniff', 'X-Frame-Options':'DENY',
  'Permissions-Policy':'camera=(), microphone=(), geolocation=()',
  'Content-Security-Policy':"default-src 'none'; script-src 'self' https://challenges.cloudflare.com; style-src 'self'; connect-src 'self' https://challenges.cloudflare.com; frame-src https://challenges.cloudflare.com; img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
};
export async function hash(value:string):Promise<string> {
  const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest),v=>v.toString(16).padStart(2,'0')).join('');
}
async function authorized(request:Request,env:Env) {
  if (!env.FACULTY_API_SECRET || env.FACULTY_API_SECRET.length<32) throw new ApiError(503,'Faculty API is not configured.');
  const supplied=request.headers.get('Authorization')||'';
  const [a,b]=await Promise.all([crypto.subtle.digest('SHA-256',new TextEncoder().encode(supplied)),crypto.subtle.digest('SHA-256',new TextEncoder().encode('Bearer '+env.FACULTY_API_SECRET))]);
  if (!crypto.subtle.timingSafeEqual(a,b)) throw new ApiError(401,'Unauthorized.');
}
async function boundedJson(request:Request|Response,max=131072):Promise<Record<string,unknown>> {
  if (!request.body) throw new ApiError(400,'A JSON body is required.');
  if (Number(request.headers.get('Content-Length'))>max) throw new ApiError(413,'Request is too large.');
  const reader=request.body.getReader(), chunks:Uint8Array[]=[];let length=0;
  while (true) {const {done,value}=await reader.read();if(done)break;length+=value.length;if(length>max){await reader.cancel();throw new ApiError(413,'Request is too large.');}chunks.push(value);}
  const bytes=new Uint8Array(length);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
  try {return object(JSON.parse(new TextDecoder('utf-8',{fatal:true,ignoreBOM:false}).decode(bytes)));} catch(error) {if(error instanceof ApiError)throw error;throw new ApiError(400,'Invalid JSON.');}
}
function sameOrigin(request:Request,env:Env) {
  if (request.headers.get('Origin')!==env.PUBLIC_ORIGIN || new URL(request.url).origin!==env.PUBLIC_ORIGIN) throw new ApiError(403,'Same-origin request required.');
  if (!request.headers.get('Content-Type')?.toLowerCase().startsWith('application/json')) throw new ApiError(415,'JSON is required.');
}
function date(value:unknown):string|null {
  if(value===null || value===undefined)return null;
  if(typeof value!=='string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value) || !Number.isFinite(Date.parse(value))) throw new ApiError(400,'Dates must be UTC ISO timestamps.');
  return new Date(value).toISOString();
}
function submission(row:Submission) {return {id:row.id,request_id:row.request_id,answers:JSON.parse(row.answers_json),verification:JSON.parse(row.verification_json),status:row.status,submitted_at:row.submitted_at};}
function isWritable(card:Card) {const now=Date.now();return !card.revoked && (!card.expires_at || Date.parse(card.expires_at)>now) && (card.policy!=='until_due' || !card.due_at || Date.parse(card.due_at)>now) && (!card.submission_count || ['unlimited','until_due'].includes(card.policy) || !!card.unlocked);}
export async function expire(db:D1Database) {
  await db.prepare(`UPDATE public_cards SET status='expired' WHERE revoked=0 AND status!='expired' AND
    ((expires_at IS NOT NULL AND julianday(expires_at)<=julianday('now')) OR
    (policy='until_due' AND due_at IS NOT NULL AND julianday(due_at)<=julianday('now')))` ).run();
}
async function findCard(token:string,env:Env):Promise<Card> {
  if(!TOKEN.test(token))throw new ApiError(404,'Card not found.');
  const card=await env.DB.prepare('SELECT * FROM public_cards WHERE token_hash=?').bind(await hash(token)).first<Card>();
  if(!card)throw new ApiError(404,'Card not found.');
  if(card.revoked)throw new ApiError(410,'This card has been revoked.');
  if((card.expires_at && Date.parse(card.expires_at)<=Date.now()) || (card.policy==='until_due' && card.due_at && Date.parse(card.due_at)<=Date.now())) {
    await env.DB.prepare("UPDATE public_cards SET status='expired' WHERE id=? AND revoked=0 AND status!='expired'").bind(card.id).run();
    throw new ApiError(410,'This card has expired.');
  }
  return card;
}
async function turnstile(request:Request,token:unknown,env:Env) {
  if(!env.TURNSTILE_SECRET || !env.TURNSTILE_HOSTNAME || !env.TURNSTILE_SITE_KEY)throw new ApiError(503,'Submission verification is not configured.');
  if(typeof token!=='string' || !token || token.length>2048)throw new ApiError(403,'Complete the verification before submitting.');
  try {
    const response=await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify',{
      method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},signal:AbortSignal.timeout(10000),
      body:new URLSearchParams({secret:env.TURNSTILE_SECRET,response:token,remoteip:request.headers.get('CF-Connecting-IP')||''}),
    });
    if(!response.ok)throw new Error('Siteverify failed');
    const result=await boundedJson(response,16384);
    if(result.success!==true || result.hostname!==env.TURNSTILE_HOSTNAME || result.action!=='response_card_submit')throw new Error('Invalid verification');
  } catch {throw new ApiError(403,'Verification could not be completed. Please try again.');}
}
async function faculty(request:Request,env:Env,path:string):Promise<Response> {
  await authorized(request,env);
  const publish=path.match(/^\/faculty-api\/cards\/([A-Za-z0-9_-]{1,100})$/);
  if(publish && request.method==='PUT') {
    const input=await boundedJson(request,524288), id=publish[1];
    if(input.id!==id || typeof input.issue_id!=='string' || !ID.test(input.issue_id) || typeof input.token_hash!=='string' || !/^[a-f0-9]{64}$/.test(input.token_hash) || !['once','until_due','unlimited','instructor_unlock'].includes(String(input.policy)))throw new ApiError(400,'Invalid card snapshot.');
    const snapshot={id,issue_id:input.issue_id,token_hash:input.token_hash,schema:validateSchema(input.schema),policy:input.policy,due_at:date(input.due_at),expires_at:date(input.expires_at)};
    if(snapshot.policy==='until_due' && !snapshot.due_at)throw new ApiError(400,'A deadline is required for until_due cards.');
    const encoded=stable(snapshot), digest=await hash(encoded);
    const rows=await env.DB.batch([
      env.DB.prepare(`INSERT OR IGNORE INTO public_cards(id,issue_id,token_hash,snapshot_json,snapshot_hash,policy,due_at,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?)`).bind(id,snapshot.issue_id,snapshot.token_hash,encoded,digest,snapshot.policy,snapshot.due_at,snapshot.expires_at,new Date().toISOString()),
      env.DB.prepare('SELECT id,snapshot_hash FROM public_cards WHERE id=?').bind(id),
    ]);
    const saved=rows[1].results[0] as {snapshot_hash:string}|undefined;
    if(!saved || saved.snapshot_hash!==digest)throw new ApiError(409,'This card ID or access token already has a different immutable snapshot.');
    return Response.json({id,snapshot_hash:digest,published:true});
  }
  const control=path.match(/^\/faculty-api\/cards\/([A-Za-z0-9_-]{1,100})\/control$/);
  if(control && request.method==='POST') {
    const input=await boundedJson(request,4096),id=control[1];
    if(typeof input.request_id!=='string' || !ID.test(input.request_id) || !['revoke','unlock'].includes(String(input.action)))throw new ApiError(400,'Invalid control request.');
    const prior=await env.DB.prepare('SELECT action FROM card_controls WHERE card_id=? AND request_id=?').bind(id,input.request_id).first<{action:string}>();
    if(prior) {if(prior.action!==input.action)throw new ApiError(409,'Request ID already used.');return Response.json({id,action:prior.action,applied:true});}
    const card=await env.DB.prepare('SELECT * FROM public_cards WHERE id=?').bind(id).first<Card>();
    if(!card)throw new ApiError(404,'Card not found.');
    if(input.action==='unlock' && card.revoked)throw new ApiError(409,'Revoked cards cannot be unlocked.');
    await env.DB.batch([
      env.DB.prepare('INSERT OR IGNORE INTO card_controls(card_id,request_id,action,created_at) VALUES(?,?,?,?)').bind(id,input.request_id,input.action,new Date().toISOString()),
      env.DB.prepare(input.action==='revoke'?"UPDATE public_cards SET revoked=1,status='revoked',draft_json=NULL WHERE id=? AND changes()>0":"UPDATE public_cards SET unlocked=1 WHERE id=? AND revoked=0 AND changes()>0").bind(id),
      env.DB.prepare("INSERT INTO card_events(card_id,type,created_at,data_json) SELECT id,'control',?,json_object('status',status,'action',?,'request_id',?,'submission_count',submission_count) FROM public_cards WHERE id=? AND changes()>0").bind(new Date().toISOString(),input.action,input.request_id,id),
    ]);
    const saved=await env.DB.prepare('SELECT action FROM card_controls WHERE card_id=? AND request_id=?').bind(id,input.request_id).first<{action:string}>();
    if(saved?.action!==input.action)throw new ApiError(409,'Request ID already used.');
    return Response.json({id,action:input.action,applied:true});
  }
  if(path==='/faculty-api/events' && request.method==='GET') {
    const params=new URL(request.url).searchParams, after=Number(params.get('after')??0),limit=Number(params.get('limit')??100);
    if(!Number.isSafeInteger(after) || after<0 || !Number.isSafeInteger(limit) || limit<1 || limit>100)throw new ApiError(400,'Invalid event cursor or limit.');
    await expire(env.DB);
    const rows=await env.DB.prepare('SELECT * FROM card_events WHERE cursor>? ORDER BY cursor LIMIT ?').bind(after,limit+1).all<{cursor:number;card_id:string;type:string;created_at:string;data_json:string}>();
    const events=[];let bytes=0;
    for(const {data_json,...event} of rows.results.slice(0,limit)) {
      const entry={...event,data:JSON.parse(data_json)}, size=new TextEncoder().encode(JSON.stringify(entry)).length;
      if(events.length && bytes+size>2000000)break;
      events.push(entry);bytes+=size;
    }
    return Response.json({events,cursor:events.at(-1)?.cursor??after,has_more:rows.results.length>events.length});
  }
  throw new ApiError(404,'Endpoint not found.');
}
async function publicApi(request:Request,env:Env,token:string,action:string|undefined):Promise<Response> {
  if(request.method!=='GET')sameOrigin(request,env);
  const card=await findCard(token,env), schema=JSON.parse(card.snapshot_json).schema as Schema;
  if(!action && request.method==='GET') {
    const rows=await env.DB.batch([
      env.DB.prepare("UPDATE public_cards SET status='opened' WHERE id=? AND status='issued' AND revoked=0").bind(card.id),
      env.DB.prepare('SELECT * FROM public_cards WHERE id=?').bind(card.id),
      env.DB.prepare('SELECT * FROM submissions WHERE card_id=? ORDER BY submitted_at DESC, rowid DESC LIMIT 20').bind(card.id),
    ]);
    const current=rows[1].results[0] as unknown as Card;
    if(current.revoked)throw new ApiError(410,'This card has been revoked.');
    const history=(rows[2].results as unknown as Submission[]).map(submission);
    // Verification expected values remain inside the private published snapshot.
    return Response.json({id:card.id,schema:publicSchema(schema),status:current.status,policy:current.policy,due_at:current.due_at,expires_at:current.expires_at,can_submit:isWritable(current),submission_count:current.submission_count,draft:current.draft_json?JSON.parse(current.draft_json):null,history,turnstile_site_key:env.TURNSTILE_SITE_KEY});
  }
  if(action==='draft' && request.method==='PUT') {
    const body=await boundedJson(request), checked=validateAnswers(schema,body.answers,true);
    const result=await env.DB.prepare(`UPDATE public_cards SET draft_json=?,draft_updated_at=?,status=CASE WHEN submission_count=0 THEN 'started' ELSE status END WHERE id=? AND ${writable}`).bind(JSON.stringify(checked.answers),new Date().toISOString(),card.id).run();
    if(!result.meta.changes)throw new ApiError(409,'This card no longer accepts changes.');
    return Response.json({saved:true});
  }
  if(action==='submit' && request.method==='POST') {
    const body=await boundedJson(request);
    if(typeof body.request_id!=='string' || !ID.test(body.request_id))throw new ApiError(400,'A valid request ID is required.');
    const requestHash=await hash(stable(object(body.answers)));
    const prior=await env.DB.prepare('SELECT * FROM submissions WHERE card_id=? AND request_id=?').bind(card.id,body.request_id).first<Submission>();
    if(prior){if(prior.request_hash!==requestHash)throw new ApiError(409,'Request ID already used with different answers.');return Response.json({submission:submission(prior),confirmation_message:schema.confirmation_message});}
    if(!isWritable(card))throw new ApiError(409,'This card no longer accepts submissions.');
    await turnstile(request,body.turnstile_token,env);
    const checked=validateAnswers(schema,body.answers),id=crypto.randomUUID(),now=new Date().toISOString();
    const rows=await env.DB.batch([
      env.DB.prepare(`INSERT OR IGNORE INTO submissions(id,card_id,request_id,request_hash,answers_json,verification_json,status,submitted_at)
        SELECT ?,id,?,?,?,?,?,? FROM public_cards WHERE id=? AND ${writable}`).bind(id,body.request_id,requestHash,JSON.stringify(checked.answers),JSON.stringify(checked.verification),checked.status,now,card.id),
      env.DB.prepare('SELECT * FROM submissions WHERE card_id=? AND request_id=?').bind(card.id,body.request_id),
    ]);
    const saved=rows[1].results[0] as unknown as Submission|undefined;
    if(!saved)throw new ApiError(409,'This card no longer accepts submissions.');
    if(saved.request_hash!==requestHash)throw new ApiError(409,'Request ID already used with different answers.');
    return Response.json({submission:submission(saved),confirmation_message:schema.confirmation_message});
  }
  throw new ApiError(404,'Endpoint not found.');
}
export default {
  async fetch(request:Request,env:Env):Promise<Response> {
    let response:Response;
    try {
      const url=new URL(request.url);
      if(url.origin!==env.PUBLIC_ORIGIN)throw new ApiError(404,'Endpoint not found.');
      if(url.pathname.startsWith('/faculty-api/'))response=await faculty(request,env,url.pathname);
      else if(request.method==='GET' && url.pathname==='/assets/card.js')response=new Response(client,{headers:{'Content-Type':'text/javascript; charset=utf-8'}});
      else if(request.method==='GET' && url.pathname==='/assets/card.css')response=new Response(styles,{headers:{'Content-Type':'text/css; charset=utf-8'}});
      else if(request.method==='GET' && /^\/r\/[A-Za-z0-9_-]{32,128}$/.test(url.pathname))response=new Response(page,{headers:{'Content-Type':'text/html; charset=utf-8'}});
      else {
        const route=url.pathname.match(/^\/api\/cards\/([A-Za-z0-9_-]{32,128})(?:\/(draft|submit))?$/);
        if(!route)throw new ApiError(404,'Card not found.');
        response=await publicApi(request,env,route[1],route[2]);
      }
    } catch(error) {
      if(error instanceof ApiError)response=Response.json({error:error.message,...(error.fields?{fields:error.fields}:{})},{status:error.status});
      else {console.error(JSON.stringify({event:'response_card_request_failed'}));response=Response.json({error:'The card service is temporarily unavailable. Please try again.'},{status:503});}
    }
    for(const [name,value] of Object.entries(securityHeaders))response.headers.set(name,value);
    return response;
  },
  async scheduled(_event:ScheduledController,env:Env):Promise<void> {await expire(env.DB);},
} satisfies ExportedHandler<Env>;
