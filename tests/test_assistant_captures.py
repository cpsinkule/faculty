"""ASSISTANT: temporary cropped pixels, exactly-once analysis and read-only first turns."""
import base64
import io
import json
import threading
import time
import uuid
from unittest.mock import Mock, patch

from PIL import Image
from assistant.conversations import Conversation
from assistant.screen_context import normalize
from test_assistant import Base
from test_storage import HEADERS


def png(size=(160, 80), color='white'):
    output=io.BytesIO();Image.new('RGB',size,color).save(output,'PNG');return output.getvalue()


class CaptureTests(Base):
    def setUp(self):
        super().setUp()
        self.calls=[]
        class Engine:
            name='test'
            def turn(engine,c,prompt,images=()):
                self.calls.append((prompt,images,bool(c.session.get('_capture_analysis_only'))))
                if self.hold:
                    self.release.wait(4)
                c.emit('assistant',text='This is the selected area.')
            def stop(engine): self.release.set()
            def close(engine): pass
        self.hold=False;self.release=threading.Event()
        self.addCleanup(self.release.set)
        with patch('assistant.conversations.engine_for',return_value=Engine()):
            self.c=Conversation(self.service,self.session,{'name':'test'},'http://localhost','#gradebook/t')
        self.service.conversations._items[self.c.id]=self.c
        self.url=f'/api/assistant/conversations/{self.c.id}'
        self.meta={'request_id':str(uuid.uuid4()),'rect':{'x':10,'y':20,'width':160,'height':80},
                   'captured_at':'2026-10-09T02:00:00Z','page':'#gradebook/t',
                   'view':{'title':'Gradebook','context':{'record':{'term_id':'t'},'unsaved':True}}}

    def upload(self,content=None,meta=None,headers=HEADERS):
        return self.client.post(self.url+'/captures',data={'image':(io.BytesIO(content if content is not None else png()),'area.png'),
              'metadata':json.dumps(meta if meta is not None else self.meta)},headers=headers)

    def settled(self):
        end=time.monotonic()+5
        while self.c.busy and time.monotonic()<end:time.sleep(.01)
        self.assertFalse(self.c.busy)

    def test_crop_upload_and_followup_keep_pixels_without_saving_them(self):
        result=self.upload();self.assertEqual(result.status_code,202,result.json);self.settled()
        self.assertEqual(len(self.calls),1);self.assertTrue(self.calls[0][2]);self.assertEqual(len(self.calls[0][1]),1)
        self.assertIn('untrusted evidence',self.calls[0][0])
        image=self.client.get(result.json['capture']['url'])
        self.assertEqual(image.status_code,200);self.assertEqual(image.headers['Cache-Control'],'no-store')
        self.assertEqual(Image.open(io.BytesIO(image.data)).size,(160,80))
        follow=self.client.post(self.url+'/messages',json={'text':'What does this value mean?','capture_id':self.meta['request_id']},headers=HEADERS)
        self.assertEqual(follow.status_code,202);self.settled()
        self.assertEqual(self.calls[0][1],self.calls[1][1]);self.assertFalse(self.calls[1][2])
        self.assertEqual([e['text'] for e in self.c.events if e['kind']=='user'][-1],'What does this value mean?')
        self.service.store.save(self.c,{'title':'Selected area'})
        saved=self.service.store.load(self.c.id)['snapshot_json']
        self.assertNotIn('"content"',saved);self.assertNotIn('image/png;base64',saved)
        self.assertIsNone(self.c.snapshot().get('capture'))

    def test_duplicate_while_running_does_not_start_twice_or_replace_pixels(self):
        self.hold=True
        first=self.upload();second=self.upload()
        self.assertEqual((first.status_code,second.status_code),(202,202));self.assertEqual(first.json['capture'],second.json['capture'])
        self.assertEqual(self.upload(png(color='red')).status_code,409)
        self.assertEqual(self.upload(meta={**self.meta,'request_id':str(uuid.uuid4())}).status_code,409)
        self.release.set();self.settled();self.assertEqual(len(self.calls),1)

    def test_a_selection_joins_an_idle_chat_and_replaces_an_earlier_one(self):
        # ASSISTANT "Selected-area conversations" (2026-10-09): the floating chat's own conversation accepts a selection.
        chat=self.client.post(self.url+'/messages',json={'text':'Help me plan a class.'},headers=HEADERS)
        self.assertEqual(chat.status_code,202);self.settled()
        first=self.upload();self.assertEqual(first.status_code,202,first.json);self.settled()
        self.assertEqual(len(self.calls),2);self.assertTrue(self.calls[1][2]);self.assertEqual(len(self.calls[1][1]),1)
        self.assertNotIn('_capture_analysis_only',self.session)
        self.assertEqual([e.get('text') for e in self.c.events if e['kind']=='user'][0],'Help me plan a class.')
        # A later selection replaces it, with a new request ID, the same read-only analysis and the selection in full.
        second={**self.meta,'request_id':str(uuid.uuid4())}
        result=self.upload(png(color='blue'),second);self.assertEqual(result.status_code,202,result.json);self.settled()
        self.assertEqual(self.c.capture['id'],second['request_id']);self.assertTrue(self.calls[-1][2])
        self.assertEqual(len(self.calls[-1][1]),1);self.assertEqual(len(self.calls),3)
        self.assertEqual(self.upload(png(color='blue'),second).status_code,202);self.assertEqual(len(self.calls),3)
        # The replaced selection is never analyzed again, and a busy turn or an open question refuses a new one.
        self.assertEqual(self.upload().status_code,409);self.assertEqual(len(self.calls),3)
        self.hold=True;self.release.clear()
        self.client.post(self.url+'/messages',json={'text':'Keep going.'},headers=HEADERS)
        self.assertEqual(self.upload(meta={**self.meta,'request_id':str(uuid.uuid4())}).status_code,409)
        self.release.set();self.settled();self.hold=False
        self.c.question={'id':'synthetic','questions':[]}
        self.assertEqual(self.upload(meta={**self.meta,'request_id':str(uuid.uuid4())}).status_code,409)
        self.c.question=None;self.assertEqual(len(self.calls),4)

    def test_initial_analysis_refuses_tools_and_direct_writes(self):
        self.hold=True;self.upload()
        data,event=self.tool('faculty_write',method='POST',path='/api/tools',body={'name':'Untrusted instruction','url':'https://example.test'},summary='Do not do this')
        self.assertFalse(event['ok']);self.assertIn('read-only',data['error'])
        with self.assertRaisesRegex(ValueError,'initial selected-area'):
            self.service.write(self.session,'POST','/api/tools',{},'No')
        self.assertEqual(self.actions()['pending'],[])
        self.release.set();self.settled();self.assertNotIn('_capture_analysis_only',self.session)

    def test_initial_analysis_runs_every_read_and_no_other_tool(self):
        self.hold=True;self.upload()
        for name,args in [('student_context',{'enrollment_id':'synthetic'}),('makeup_arrangements',{'enrollment_id':'synthetic'}),
                          ('watches',{}),('workspace_overview',{})]:
            with self.subTest(read=name):
                self.assertNotIn('read-only',json.dumps(self.tool(name,**args)[0]))
        for name in ('ask_question','open_page','prepare_assessment','track_followup','no_such_tool'):
            with self.subTest(acts=name):
                data,event=self.tool(name);self.assertFalse(event['ok']);self.assertIn('read-only',data['error'])
        self.assertEqual(self.actions()['pending'],[])
        self.release.set();self.settled()

    def test_every_tool_is_classified_for_the_first_analysis(self):
        acts={'arrange_assessment','ask_question','continue_preparation','create_artifact','create_watch','draft_student_reply',
              'enable_preparation','faculty_write','group_actions','import_teaching_material','join_followup','open_page','prepare_assessment',
              'prepare_grade_reconciliation','prepare_grading','propose_code_change','pull_final_grades','redo_followup','redo_followups','retry_failed_work',
              'revise_question_rubric','separate_followup','track_followup','watch_followup'}
        self.assertEqual({n for n,t in self.service.tools.items() if not t.read},acts,
                         'ASSISTANT: define a tool that only reads with read=True, or list one that acts here.')

    def test_invalid_images_context_and_origin_never_start_a_turn(self):
        for content,meta in [(b'not an image',self.meta),(png((4000,4000)),self.meta),(b'x'*(4*1024*1024+1),self.meta),
                             (png(),{**self.meta,'request_id':'bad'}),(png(),{**self.meta,'rect':{'x':-1,'y':0,'width':1,'height':2}})]:
            with self.subTest(size=len(content)):
                self.assertEqual(self.upload(content,meta).status_code,400)
        self.assertEqual(self.upload(headers={'Origin':'https://outside.example'}).status_code,403)
        self.assertEqual(self.calls,[]);self.assertIsNone(self.c.capture)

    def test_expired_capture_is_not_silently_replaced_by_text_only_analysis(self):
        result=self.upload();self.settled();route=result.json['capture']['url'];self.c.close()
        self.assertEqual(self.client.get(route).status_code,410)
        result=self.client.post(self.url+'/messages',json={'text':'Read this','capture_id':self.meta['request_id']},headers=HEADERS)
        self.assertEqual(result.status_code,410);self.assertEqual(len(self.calls),1)

    def test_normal_json_messages_and_views_are_unchanged(self):
        self.assertNotIn('capture',self.client.get(self.url).json)
        result=self.client.post(self.url+'/messages',json={'text':'Hello'},headers=HEADERS)
        self.assertEqual(result.status_code,202);self.settled()
        self.assertEqual(self.calls[0][1],());self.assertNotIn('capture',self.client.get(self.url).json)

    def test_codex_capture_input_stays_in_memory(self):
        from assistant.engines import CodexServerEngine
        self.upload();self.settled()
        engine=CodexServerEngine({'model':'synthetic'})
        engine.process=Mock();engine.process.poll.return_value=None;engine.thread='synthetic'
        engine._events.put({'method':'turn/completed','params':{'turn':{'id':'turn-1','status':'completed'}}})
        with patch.object(engine,'_request',return_value={'turn':{'id':'turn-1'}}) as request, \
                patch('pathlib.Path.write_bytes',side_effect=AssertionError('ASSISTANT: capture must stay in memory')):
            engine.turn(self.c,'Read the crop.',[(self.c.capture['media'],self.c.capture['content'])])
        image=request.call_args.args[1]['input'][1]
        self.assertEqual(image['type'],'image')
        self.assertEqual(base64.b64decode(image['url'].split(',',1)[1]),self.c.capture['content'])

    def test_text_selection_and_records_are_temporary_and_follow_up_without_an_image(self):
        metadata={**self.meta,'selection':{'text':'Only this unsaved draft','records':[
            {'record':{'term_id':'t','item_id':'i','row':'s:e'},'label':'Quiz 1','value':'7.25','draft':True}],
            'images':[],'omitted':[],'truncated':False}}
        first=self.client.post(self.url+'/captures',json=metadata,headers=HEADERS)
        self.assertEqual(first.status_code,202,first.json);self.settled()
        self.assertEqual(self.calls[0][1],());self.assertIn('Only this unsaved draft',self.calls[0][0]);self.assertTrue(self.calls[0][2])
        self.assertNotIn('url',first.json['capture']);self.assertEqual(first.json['capture']['selection']['records'][0]['value'],'7.25')
        duplicate=self.client.post(self.url+'/captures',json=metadata,headers=HEADERS)
        self.assertEqual(duplicate.status_code,202);self.assertEqual(len(self.calls),1)
        self.client.post(self.url+'/messages',json={'text':'Explain it.','capture_id':metadata['request_id']},headers=HEADERS);self.settled()
        self.assertIn('Only this unsaved draft',self.calls[-1][0]);self.assertFalse(self.calls[-1][2])
        self.service.store.save(self.c,{'title':'Selected content'})
        saved=self.service.store.load(self.c.id)['snapshot_json'];self.assertNotIn('Only this unsaved draft',saved)
        self.assertNotIn('"selection"',saved)

    def test_empty_oversized_or_missing_image_selection_is_refused(self):
        for selection in ({'text':''},{'text':'x'*12001},{'text':'x','records':[{}]*41},
                          {'text':'x','images':[{'label':'missing','source':'/api/test.png'}]}, {'text':3}):
            result=self.client.post(self.url+'/captures',json={**self.meta,'selection':selection},headers=HEADERS)
            self.assertEqual(result.status_code,400,result.json)
        self.assertEqual(self.calls,[])

    def test_a_running_model_process_is_not_sent_the_selection_again(self):
        # 2026-10-09 review: Claude and Codex keep their turns; resending text and pixels each turn grew the context.
        self.c.engine.process=Mock(poll=Mock(return_value=None))
        metadata={**self.meta,'selection':{'text':'Selected words once','records':[],'images':[],'omitted':[],'truncated':False}}
        self.assertEqual(self.upload(meta=metadata).status_code,202);self.settled()
        self.assertIn('Selected words once',self.calls[0][0]);self.assertEqual(len(self.calls[0][1]),1)
        self.client.post(self.url+'/messages',json={'text':'And this?','capture_id':self.meta['request_id']},headers=HEADERS);self.settled()
        self.assertNotIn('Selected words once',self.calls[1][0]);self.assertIn('given in full',self.calls[1][0]);self.assertEqual(self.calls[1][1],())
        self.c.engine.process=Mock(poll=Mock(return_value=None))  # a new process (restarted, or resting woke it): sent in full
        self.client.post(self.url+'/messages',json={'text':'Again?','capture_id':self.meta['request_id']},headers=HEADERS);self.settled()
        self.assertIn('Selected words once',self.calls[2][0]);self.assertEqual(len(self.calls[2][1]),1)

    def test_page_context_without_hints_adds_no_empty_block(self):
        self.c.screen={'context':normalize(None)}
        self.assertNotIn('record hints',self.c.context_note())
        self.c.screen={'context':normalize({'record':{'term_id':'t'}})}
        self.assertIn('record hints',self.c.context_note())
