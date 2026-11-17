import assert from 'node:assert/strict';
import test from 'node:test';
import {selectionRect, imageCrop, intersect} from '../static/assistant-selection.js';
import {registerShortcut, dispatchShortcut, dispatchShortcutTap, resetShortcutTaps, listShortcuts, noteShortcutPointer} from '../static/ui.js';

test('ASSISTANT: original image crops do not depend on Retina scale or browser chrome',()=>{
  const selection={x:150,y:125,width:50,height:25};
  const result=imageCrop(selection,{x:100,y:100,width:200,height:100},800,400);
  assert.deepEqual([result.x,result.y,result.width,result.height],[200,100,200,100]);
  assert.deepEqual(imageCrop({x:300,y:250,width:100,height:50},{x:200,y:200,width:400,height:200},800,400).area,{x:300,y:250,width:100,height:50});
});
test('ASSISTANT: image letterboxing, object positions and clipping never include unselected pixels',()=>{
  const bounds={x:100,y:100,width:200,height:200};
  assert.equal(imageCrop({x:100,y:100,width:200,height:40},bounds,400,200,'contain'),null);
  assert.deepEqual(imageCrop({x:125,y:160,width:50,height:20},bounds,400,200,'contain').area,{x:125,y:160,width:50,height:20});
  const clipped=imageCrop({x:80,y:80,width:80,height:100},bounds,400,200,'cover');
  assert.deepEqual([clipped.x,clipped.y,clipped.width,clipped.height],[100,0,60,80]);
  assert.equal(intersect(bounds,{x:300,y:100,width:10,height:10}),null);
});
test('ASSISTANT: reversed and off-screen drags stay in the viewport',()=>{
  assert.deepEqual(selectionRect({x:400,y:300},{x:200,y:100},800,600),{x:200,y:100,width:200,height:200});
  assert.deepEqual(selectionRect({x:-10,y:-20},{x:900,y:700},800,600),{x:0,y:0,width:800,height:600});
});
const metaEvent=(type,extra={})=>({type,key:'Meta',code:'MetaLeft',altKey:false,shiftKey:false,ctrlKey:false,metaKey:type==='keydown',repeat:false,target:{tagName:'INPUT',closest:()=>true},preventDefault(){this.defaultPrevented=true;},...extra});
const dialogDoc={querySelector:()=>true};
function tap(at,doc=dialogDoc,extra={}) {
  dispatchShortcutTap(metaEvent('keydown',extra),doc,at);
  return dispatchShortcutTap(metaEvent('keyup',extra),doc,at+60);
}
test('FRONTEND: floating chat opts into double Command in fields, grids and dialogs',()=>{
  let count=0;
  const off=registerShortcut({key:'Meta Meta',label:'Select area',allowInInput:true,allowInGrid:true,allowInDialog:true,run:()=>count++});
  resetShortcutTaps();assert.equal(tap(0),false);assert.equal(tap(200),true);assert.equal(count,1);
  assert.ok(listShortcuts().some(item=>item.key==='⌘ ⌘'));off();
  const normal=registerShortcut({key:'Meta Meta',label:'Normal',allowInInput:true,run:()=>count++});
  resetShortcutTaps();tap(1000);assert.equal(tap(1200),false);assert.equal(count,1);normal();
});
test('FRONTEND: holding Command starts selection once and its release submits without opening chat',t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const events=[];
  const hold=registerShortcut({key:'Hold Meta',label:'Draw area',allowInInput:true,allowInGrid:true,allowInDialog:true,
    run:()=>events.push('start'),release:()=>events.push('send'),cancel:()=>events.push('cancel')});
  const chat=registerShortcut({key:'Meta Meta',label:'Floating chat',allowInInput:true,allowInGrid:true,allowInDialog:true,run:()=>events.push('chat')});
  try {
    resetShortcutTaps();dispatchShortcutTap(metaEvent('keydown'),dialogDoc,0);
    t.mock.timers.tick(599);assert.deepEqual(events,[]);
    t.mock.timers.tick(1);assert.deepEqual(events,['start']);
    dispatchShortcutTap(metaEvent('keydown',{repeat:true}),dialogDoc,650);
    assert.equal(dispatchShortcutTap(metaEvent('keyup'),dialogDoc,900),true);
    assert.deepEqual(events,['start','send']);
    tap(1000);assert.deepEqual(events,['start','send']);
    assert.ok(listShortcuts().some(item=>item.key==='Hold ⌘'));
  } finally {resetShortcutTaps();hold();chat();}
});
test('FRONTEND: chords, Escape, blur and unregister cancel held selection without submitting',t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const events=[];
  const off=registerShortcut({key:'Hold Meta',label:'Draw area',allowInInput:true,allowInGrid:true,allowInDialog:true,
    run:()=>events.push('start'),release:()=>events.push('send'),cancel:()=>events.push('cancel')});
  try {
    resetShortcutTaps();dispatchShortcutTap(metaEvent('keydown'),dialogDoc,0);
    dispatchShortcutTap(metaEvent('keydown',{key:'c',code:'KeyC'}),dialogDoc,100);
    t.mock.timers.tick(700);assert.deepEqual(events,[]);
    for(const key of ['c','Escape',null]) {
      dispatchShortcutTap(metaEvent('keydown'),dialogDoc,1000);t.mock.timers.tick(600);
      if(key) {
        const event=metaEvent('keydown',{key,code:key==='c'?'KeyC':'Escape',stopImmediatePropagation(){this.stopped=true;}});
        dispatchShortcutTap(event,dialogDoc,1700);
        assert.equal(Boolean(event.defaultPrevented),key==='Escape');
        assert.equal(Boolean(event.stopped),key==='Escape');
      } else resetShortcutTaps();
      dispatchShortcutTap(metaEvent('keyup'),dialogDoc,1800);
    }
    assert.deepEqual(events,['start','cancel','start','cancel','start','cancel']);
    dispatchShortcutTap(metaEvent('keydown'),dialogDoc,2000);t.mock.timers.tick(600);off();
    dispatchShortcutTap(metaEvent('keyup'),dialogDoc,2800);
    assert.deepEqual(events.slice(-2),['start','cancel']);
    assert.ok(!events.includes('send'));
  } finally {resetShortcutTaps();off();}
});
test('FRONTEND: a released or modified Command never starts a delayed selector',t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  let starts=0;
  const off=registerShortcut({key:'Hold Meta',label:'Draw area',allowInInput:true,allowInGrid:true,allowInDialog:true,run:()=>starts++});
  try {
    resetShortcutTaps();tap(0);t.mock.timers.tick(1000);assert.equal(starts,0);
    dispatchShortcutTap(metaEvent('keydown',{shiftKey:true}),dialogDoc,2000);t.mock.timers.tick(1000);assert.equal(starts,0);
    dispatchShortcutTap(metaEvent('keydown'),dialogDoc,4000);off();t.mock.timers.tick(1000);assert.equal(starts,0);
  } finally {resetShortcutTaps();off();}
});
test('FRONTEND: Command chords, mouse activity, held keys, repeats and slow taps do not select',()=>{
  let count=0;
  const off=registerShortcut({key:'Meta Meta',label:'Select area',allowInInput:true,allowInGrid:true,allowInDialog:true,run:()=>count++});
  resetShortcutTaps();tap(0);
  dispatchShortcutTap(metaEvent('keydown',{key:'c',code:'KeyC'}),dialogDoc,100);
  tap(200);assert.equal(count,0);
  resetShortcutTaps();tap(1000);resetShortcutTaps();tap(1200);assert.equal(count,0);
  resetShortcutTaps();dispatchShortcutTap(metaEvent('keydown'),dialogDoc,2000);dispatchShortcutTap(metaEvent('keyup'),dialogDoc,2700);tap(2800);assert.equal(count,0);
  resetShortcutTaps();dispatchShortcutTap(metaEvent('keydown'),dialogDoc,3000);dispatchShortcutTap(metaEvent('keydown',{repeat:true}),dialogDoc,3100);dispatchShortcutTap(metaEvent('keyup'),dialogDoc,3150);tap(3250);assert.equal(count,0);
  resetShortcutTaps();tap(4000);tap(5000);assert.equal(count,0);
  resetShortcutTaps();tap(6000,{querySelector:()=>false},{shiftKey:true});tap(6200);assert.equal(count,0);
  resetShortcutTaps();tap(7000);tap(7200);tap(7350);assert.equal(count,1);off();resetShortcutTaps();
});
test('FRONTEND: Command copy still dispatches normally and interrupts a partial double tap',()=>{
  const doc={querySelector:()=>false};let copies=0,selections=0;
  const area=registerShortcut({key:'Meta Meta',label:'Select area',allowInInput:true,allowInGrid:true,allowInDialog:true,run:()=>selections++});
  const copy=registerShortcut({key:'Mod+C',label:'Copy',allowInInput:true,allowInGrid:true,run:()=>copies++});
  resetShortcutTaps();tap(0,doc);
  const chord=metaEvent('keydown',{key:'c',code:'KeyC'});
  dispatchShortcutTap(chord,doc,100);assert.equal(dispatchShortcut(chord,doc),true);
  dispatchShortcutTap(metaEvent('keyup'),doc,130);tap(200,doc);
  assert.equal(copies,1);assert.equal(selections,0);copy();area();resetShortcutTaps();
});
test('FRONTEND: natural Command taps and physical key codes do not require synthetic-test speed',()=>{
  let count=0;
  const off=registerShortcut({key:'Meta Meta',label:'Select area',allowInInput:true,allowInGrid:true,allowInDialog:true,run:()=>count++});
  resetShortcutTaps();
  dispatchShortcutTap(metaEvent('keydown'),dialogDoc,0);
  assert.equal(dispatchShortcutTap(metaEvent('keyup'),dialogDoc,350),false);
  dispatchShortcutTap(metaEvent('keydown',{key:'Unidentified',code:'MetaRight'}),dialogDoc,650);
  assert.equal(dispatchShortcutTap(metaEvent('keyup',{key:'Unidentified',code:'MetaRight'}),dialogDoc,1000),true);
  assert.equal(count,1);off();resetShortcutTaps();
});
test('FRONTEND: a hold arms only while the pointer is still, so a slow Command+click keeps its meaning',t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  let starts=0;
  const off=registerShortcut({key:'Hold Meta',label:'Draw area',allowInInput:true,allowInGrid:true,allowInDialog:true,run:()=>starts++});
  try {
    resetShortcutTaps();noteShortcutPointer({clientX:100,clientY:100});
    dispatchShortcutTap(metaEvent('keydown'),dialogDoc,0);
    noteShortcutPointer({clientX:102,clientY:102});t.mock.timers.tick(600);assert.equal(starts,1,'small jitter keeps the hold');
    dispatchShortcutTap(metaEvent('keyup'),dialogDoc,700);
    // Reaching for a link with Command down drops the pending hold; the click that follows is an ordinary Command+click.
    dispatchShortcutTap(metaEvent('keydown'),dialogDoc,2000);
    noteShortcutPointer({clientX:110,clientY:100});t.mock.timers.tick(700);assert.equal(starts,1);
    dispatchShortcutTap(metaEvent('keyup'),dialogDoc,2900);
    // Drawing during an armed hold does not cancel it.
    dispatchShortcutTap(metaEvent('keydown'),dialogDoc,4000);t.mock.timers.tick(600);assert.equal(starts,2);
    noteShortcutPointer({clientX:300,clientY:300});
    assert.equal(dispatchShortcutTap(metaEvent('keyup'),dialogDoc,4900),true);
  } finally {resetShortcutTaps();off();}
});
test('ASSISTANT: a slow Command chord typed in the open chat never arms the hold',t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const events=[];let typingInChat=true;
  const off=registerShortcut({key:'Hold Meta',label:'Draw area',allowInInput:true,allowInGrid:true,allowInDialog:true,when:()=>!typingInChat,
    run:()=>events.push('start'),release:()=>events.push('send'),cancel:()=>events.push('cancel')});
  try {
    resetShortcutTaps();dispatchShortcutTap(metaEvent('keydown'),dialogDoc,0);t.mock.timers.tick(800);
    dispatchShortcutTap(metaEvent('keydown',{key:'v',code:'KeyV'}),dialogDoc,800);dispatchShortcutTap(metaEvent('keyup'),dialogDoc,900);
    assert.deepEqual(events,[]);
    // Focus moved away between keydown and the 600 ms mark: the hold still does not start.
    typingInChat=false;dispatchShortcutTap(metaEvent('keydown'),dialogDoc,2000);typingInChat=true;t.mock.timers.tick(700);
    dispatchShortcutTap(metaEvent('keyup'),dialogDoc,2800);assert.deepEqual(events,[]);
    typingInChat=false;dispatchShortcutTap(metaEvent('keydown'),dialogDoc,4000);t.mock.timers.tick(600);
    dispatchShortcutTap(metaEvent('keydown',{key:'c',code:'KeyC'}),dialogDoc,4700);
    assert.deepEqual(events,['start','cancel']);
  } finally {resetShortcutTaps();off();}
});
