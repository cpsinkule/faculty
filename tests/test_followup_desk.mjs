import assert from 'node:assert/strict';
import test from 'node:test';
import {deskTasks, deskTitle, nextDeskAction, actionFinished, commitmentsMarkup} from '../static/followup-desk.js';

const task = (id, fields={}) => ({id,title:id,kind:'work',state:'open',waiting_on:'you',...fields});
const action = (id, endpoint, fields={}) => ({id,endpoint,pending:true,status:'proposed',body:{},effect:{title:id},...fields});
const esc = s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');

test('TEACHING-WORKFLOWS: work precedes mail and pending prerequisites remain explicit',()=>{
  const item={title:'ER',suggestions:{state:'ready',actions:[action('Reply','gmail_mail.send'),action('Prepare alternative','absences.revise')]}};
  const rows=deskTasks(item);
  assert.equal(rows[0].title,'Prepare alternative');
  assert.equal(rows[1].blocked,true);
  assert.equal(deskTitle(item),'Prepare alternative');
  assert.equal(nextDeskAction(item).target,'Prepare alternative');
});
test('TEACHING-WORKFLOWS: reply completion leaves a future check-back and separate work open',()=>{
  const item={commitments:[task('work'),task('later',{kind:'check_back',waiting_on:'date',check_back:'2026-10-12'})],suggestions:{state:'settled',actions:[action('Reply','gmail_mail.send',{pending:false,status:'succeeded',events:[{detail:{result:{status:'accepted'}}}]})]}};
  assert.equal(deskTasks(item).filter(t=>t.state==='open').length,2);
  assert.equal(nextDeskAction(item).title,'work');
  assert.equal(actionFinished(action('Uncertain','gmail_mail.send',{status:'succeeded',events:[{detail:{result:{status:'uncertain'}}}]})),false);
});
test('TEACHING-WORKFLOWS: explicit work prerequisite blocks a reply, stale proposals are not ready',()=>{
  const item={commitments:[task('Prepare',{blocks_reply:true})],suggestions:{state:'ready',actions:[action('Reply','gmail_mail.send')]}};
  assert.equal(deskTasks(item).find(t=>t.id==='Reply').blocked,true);
  assert.equal(deskTasks(item)[0].title,'Prepare');
  assert.equal(nextDeskAction(item).target,'tasks');
  item.suggestions.state='superseded';
  assert.equal(deskTasks(item).some(t=>t.kind==='action'),false);
});
test('TEACHING-WORKFLOWS: counts separate conversations from tasks and escape text',()=>{
  const item={commitments:[task('<unsafe>'),task('other')],student:{name:'Example'}};
  const markup=commitmentsMarkup([{item,index:0}],esc);
  assert.match(markup,/2 tasks · 1 conversations/);
  assert.match(markup,/&lt;unsafe>/);
  assert.doesNotMatch(markup,/<unsafe>/);
});

test('TEACHING-WORKFLOWS: all tasks complete offers explicit resolution but not for a new unanswered message',()=>{
  const item={state:'todo',commitments:[task('done',{state:'completed'})]};
  assert.equal(nextDeskAction(item).target,'resolve');
  assert.notEqual(nextDeskAction({...item,state:'needs_reply'}).target,'resolve');
});

test('TEACHING-WORKFLOWS ACTION DESK (requirement, 2026-11-06): a succeeded card whose outcome is not succeeded is not listed as completed',()=>{
  const card=(outcome,fields={})=>action('Card-'+outcome,'canvas.content_push',{pending:false,status:'succeeded',outcome,events:[{detail:{result:{status:'running'}}}],...fields});
  const rowFor=outcome=>deskTasks({suggestions:{state:'settled',actions:[card(outcome)]}}).filter(t=>t.kind==='action');
  for (const outcome of ['partial','failed','uncertain']) {
    const rows=rowFor(outcome);
    assert.equal(rows.length,1,outcome);
    assert.equal(rows[0].state,'failed',outcome);
    assert.equal(rows[0].reason,'Partly done / Needs a look',outcome);
    assert.equal(actionFinished(card(outcome)),false,outcome);
  }
  const started=rowFor('started');
  assert.equal(started.length,1);
  assert.equal(started[0].state,'open');
  assert.equal(started[0].reason,'Running…');
  assert.equal(actionFinished(card('started')),false);
  const done=rowFor('succeeded');
  assert.equal(done.length,1);
  assert.equal(done[0].state,'completed');
  assert.equal(done[0].reason,'Confirmed action receipt');
  assert.equal(actionFinished(card('succeeded')),true);
  assert.equal(rowFor('cancelled').length,0);
  assert.equal(actionFinished(card('cancelled')),false);
  const open=deskTasks({state:'todo',suggestions:{state:'settled',actions:[card('partial')]}});
  assert.equal(open.some(t=>t.state==='failed'),true);
});
