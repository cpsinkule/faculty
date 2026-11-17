// Run with Node 22.12+, after npm run build. This test uses the actual bundled
// Worker and local D1. Only Cloudflare's challenge UI/Siteverify are mocked.
import assert from 'node:assert/strict';
import {createHash, randomBytes} from 'node:crypto';
import {createRequire} from 'node:module';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {readD1Migrations} from '@cloudflare/vitest-plugin';
const require=createRequire(import.meta.url);
const wranglerRequire=createRequire(require.resolve('wrangler/package.json'));
const {Miniflare,convertV4MiniflareOptions}=wranglerRequire('miniflare');
const {chromium}=await import(process.env.RESPONSE_CARDS_PLAYWRIGHT_MODULE||'playwright');
const origin='https://respond.example.com';
const secret='browser-test-only-secret-0000000000000000';
let verifications=0;
const mf=new Miniflare(convertV4MiniflareOptions({workers:[{
  name:'response-cards-test',
  modules:true,scriptPath:'dist/index.js',compatibilityDate:'2026-09-14',compatibilityFlags:['nodejs_compat'],
  d1Databases:['DB'],bindings:{PUBLIC_ORIGIN:origin,FACULTY_API_SECRET:secret,TURNSTILE_SECRET:'test-only',TURNSTILE_SITE_KEY:'test-only',TURNSTILE_HOSTNAME:'respond.example.com'},
  outboundService:async(request)=>{
    assert.equal(request.url,'https://challenges.cloudflare.com/turnstile/v0/siteverify');
    verifications++;
    return Response.json({success:true,hostname:'respond.example.com',action:'response_card_submit'});
  },
}]}));
let browser;
try {
  const db=await mf.getD1Database('DB');
  for(const migration of await readD1Migrations('./migrations'))await db.batch(migration.queries.map(q=>db.prepare(q)));
  const token=randomBytes(32).toString('base64url'),id=crypto.randomUUID();
  const snapshot={id,issue_id:'browser-test',token_hash:createHash('sha256').update(token).digest('hex'),policy:'unlimited',schema:{title:'Exam instructions confirmation',description:'Review your instructions and confirm your details.',fields:[
    {id:'info',type:'static',label:'Bring your calculator and student ID.'},
    {id:'ack',type:'acknowledgment',label:'I have read the exam instructions.',required:true},
    {id:'seat',type:'confirmation',label:'Enter your assigned seat.',required:true,verification:{expected:'C17',mode:'normalized',on_mismatch:'ALLOW_AND_FLAG'}},
    {id:'attend',type:'boolean',label:'Will you attend?',required:true},
    {id:'reason',type:'long_text',label:'Describe the reason.',required:true,show_if:{field:'attend',equals:false}},
  ]}};
  const published=await mf.dispatchFetch(origin+'/faculty-api/cards/'+id,{method:'PUT',headers:{Authorization:'Bearer '+secret,'Content-Type':'application/json'},body:JSON.stringify(snapshot)});
  assert.equal(published.status,200);
  browser=await chromium.launch({headless:true,executablePath:process.env.RESPONSE_CARDS_CHROMIUM_PATH});
  const context=await browser.newContext(),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await context.route('**/*',async route=>{
    const req=route.request();
    if(req.url().startsWith('https://challenges.cloudflare.com/turnstile/v0/api.js')) {
      return route.fulfill({contentType:'text/javascript',body:"window.turnstile={render:function(id,o){this.o=o;setTimeout(()=>o.callback('test-only-token'),10);return 'test-widget';},reset:function(){setTimeout(()=>this.o.callback('test-only-token'),10);}}"});
    }
    assert.ok(req.url().startsWith(origin+'/'),'Unexpected browser network destination');
    const response=await mf.dispatchFetch(req.url(),{method:req.method(),headers:req.headers(),body:req.postData()||undefined});
    await route.fulfill({status:response.status,headers:Object.fromEntries(response.headers),body:Buffer.from(await response.arrayBuffer())});
  });
  await page.goto(origin+'/r/'+token);
  await page.getByRole('heading',{name:snapshot.schema.title}).waitFor();
  assert.equal(await page.getByLabel('Describe the reason.').isVisible(),false);
  await page.getByLabel('I acknowledge').check();
  await page.getByLabel('Enter your assigned seat.').fill('C17');
  await page.getByRole('radio',{name:'No',exact:true}).check();
  await page.getByLabel('Describe the reason.').fill('Scheduling conflict');
  await page.getByText('Progress saved',{exact:true}).waitFor();
  await page.reload();
  await page.getByText('Saved progress restored',{exact:true}).waitFor();
  assert.equal(await page.getByLabel('Enter your assigned seat.').inputValue(),'C17');
  assert.equal(await page.getByLabel('Describe the reason.').inputValue(),'Scheduling conflict');
  await page.getByRole('radio',{name:'Yes',exact:true}).check();
  assert.equal(await page.getByLabel('Describe the reason.').isVisible(),false);
  await page.getByRole('radio',{name:'No',exact:true}).check();
  assert.equal(await page.getByLabel('Describe the reason.').inputValue(),'');
  await page.getByLabel('Describe the reason.').fill('Changed reason after reopening this field');
  await page.getByRole('radio',{name:'Yes',exact:true}).check();
  await page.getByRole('button',{name:'Submit response',exact:true}).click();
  await page.getByText('Thank you. Your response has been recorded.',{exact:true}).waitFor();
  let rows=await db.prepare('SELECT * FROM submissions WHERE card_id=?').bind(id).all();
  assert.equal(rows.results.length,1);assert.equal(rows.results[0].status,'verified');assert.equal(JSON.parse(rows.results[0].answers_json).reason,undefined);
  await page.getByLabel('Enter your assigned seat.').fill('C71');
  await page.getByRole('button',{name:'Submit another response',exact:true}).click();
  await page.getByText('2 saved responses',{exact:true}).waitFor();
  rows=await db.prepare('SELECT * FROM submissions WHERE card_id=? ORDER BY rowid').bind(id).all();
  assert.equal(rows.results.length,2);assert.equal(rows.results[1].status,'needs_review');assert.equal(verifications,2);assert.deepEqual(errors,[]);
  await page.screenshot({path:process.env.RESPONSE_CARDS_SCREENSHOT||join(tmpdir(),'faculty-response-card-browser.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  console.log('Browser smoke passed: real Worker/D1, conditional fields, restored draft, verified submission, immutable flagged revision, desktop/mobile layout.');
} finally {if(browser)await browser.close();await mf.dispose();}
