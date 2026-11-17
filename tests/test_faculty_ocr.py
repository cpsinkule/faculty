"""OCR boundaries without model downloads, GPU requirements, or teaching writes."""
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

from faculty_ocr import BUSY, OcrError, recognize, recognize_artifact
from faculty_ocr.client import _exclusive, _manifest, _prepare_command, _result, _run, recognize_covers, recognize_pages
from faculty_ocr.config import MODEL_ID, MODEL_REVISION, MAX_INPUT_BYTES
from faculty_ocr.__main__ import _local_path, main
from document_identity.process import sandbox_command


# Page shapes (pixels) the prepare cases run over: Letter and A4 at 200 dpi, and a page small enough to need no scaling down.
LETTER, A4, SMALL = (1700, 2200), (1654, 2339), (800, 1000)
COVERS = {'inputs': 3, 'title_box': [0.1, 0.3, 0.9, 0.4], 'first': 5, 'operation': 'covers'}
# name: (request, page shapes). The expected output of each is tests/fixtures/faculty_ocr/prepare.json.
PREPARE_CASES = {
    'aliases-letter': ({'operation': 'aliases'}, [LETTER]),
    'aliases-a4': ({'operation': 'aliases'}, [A4]),
    'bands-top': ({'operation': 'bands', 'band': 'top'}, [LETTER]),
    'bands-bottom': ({'operation': 'bands', 'band': 'bottom'}, [LETTER]),
    'bands-full': ({'operation': 'bands', 'band': 'full'}, [LETTER]),
    'bands-default-small': ({'operation': 'bands'}, [SMALL]),
    'bands-full-small': ({'operation': 'bands', 'band': 'full'}, [SMALL]),
    'fields': ({'operation': 'fields', 'boxes': [[10, 10, 300, 200], [1500, 2000, 1900, 2400], [0, 0, 1700, 2200]]}, [LETTER]),
    'fields-small': ({'operation': 'fields', 'boxes': [[0, 0, 800, 1000], [100, 100, 500, 400]]}, [SMALL]),
    'fields-outside': ({'operation': 'fields', 'boxes': [[10, 10, 300, 200], [1699, 2199, 1800, 2300]]}, [LETTER]),
    'covers': ({**COVERS, 'blank_corners': [False, True, 'small']}, [LETTER, A4, SMALL]),
    'covers-plain': ({**COVERS, 'inputs': 2}, [LETTER, SMALL]),
    'page-small': ({}, [SMALL]),
    'page-large': ({}, [LETTER]),
}
PREPARE_EXPECTED = Path(__file__).resolve().parent / 'fixtures' / 'faculty_ocr' / 'prepare.json'


def synthetic_page(width, height):
    """A deterministic page of lines and boxes, so every crop of it differs from every other."""
    image = Image.new('RGB', (width, height), 'white')
    draw = ImageDraw.Draw(image)
    for x in range(0, width, 29):
        draw.line([(x, 0), (width - 1 - x // 2, height - 1)], fill=((x * 7) % 256, (x * 3) % 256, (x * 11) % 256), width=3)
    for y in range(0, height, 41):
        draw.rectangle([y % width // 2, y, y % width // 2 + 60, y + 9], fill=(y % 256, 40, 200 - y % 200))
    return image


class OcrBoundaries(unittest.TestCase):
    def test_invalid_requests_never_load_the_model(self):
        with patch('faculty_ocr.client._manifest', side_effect=AssertionError('model load')):
            for content, options in [(b'',{}),('url',{}),(b'x'*(MAX_INPUT_BYTES+1),{}),
                    (b'x',{'task':'grade'}),(b'x',{'task':[]}),(b'x',{'page':True}),(b'x',{'page':0}),
                    (b'x',{'max_new_tokens':4097}),(b'x',{'device':'http://example.test'}),
                    (b'x',{'timeout':601})]:
                with self.subTest(options=options), self.assertRaises(ValueError):
                    recognize(content, **options)

    def test_partial_preparer_start_failure_reaps_every_started_child(self):
        """OCR: failed batch startup must release every process and its resources."""
        gpu, preparer = Mock(), Mock()
        gpu.poll.return_value = preparer.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory, \
                patch('faculty_ocr.client.runtime_root', return_value=Path(directory)), \
                patch('faculty_ocr.client._manifest', return_value=({}, Path(directory))), \
                patch('faculty_ocr.client._gpu_command', return_value=['gpu']), \
                patch('document_identity.process.sandbox_command', return_value=['--', 'prepare']), \
                patch('faculty_ocr.client._start', side_effect=[gpu, preparer, OcrError('cannot start')]):
            with self.assertRaisesRegex(OcrError, 'cannot start'):
                recognize_pages([b'first', b'second'])
        for child in (gpu, preparer):
            child.kill.assert_called_once()
            child.wait.assert_called_once()

    def test_failed_cover_batch_startup_reaps_every_started_child(self):
        """OCR: the first-page batch releases the GPU reader and the preparers already started when a later one cannot start."""
        gpu, preparer = Mock(), Mock()
        gpu.poll.return_value = preparer.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory, \
                patch('faculty_ocr.client.runtime_root', return_value=Path(directory)), \
                patch('faculty_ocr.client._manifest', return_value=({}, Path(directory))), \
                patch('faculty_ocr.client._gpu_command', return_value=['gpu']), \
                patch('document_identity.process.sandbox_command', return_value=['--', 'prepare']), \
                patch('faculty_ocr.client._start', side_effect=[gpu, preparer, OcrError('cannot start')]):
            with self.assertRaisesRegex(OcrError, 'cannot start'):
                recognize_covers([b'first', b'second'], [0.1, 0.3, 0.9, 0.4], ['quiz'])
        for child in (gpu, preparer):
            child.kill.assert_called_once()
            child.wait.assert_called_once()

    def test_prepare_command_mounts_this_package_just_before_the_delimiter(self):
        """OCR: every preparation step runs the sandbox command with faculty_ocr bound at /app/faculty_ocr."""
        folder = Path('/work-folder')
        with patch('document_identity.process.sandbox_command', return_value=['bwrap', '--', '/venv/bin/python']) as built:
            command = _prepare_command(folder)
        built.assert_called_once_with(folder, ['/venv/bin/python', '-m', 'faculty_ocr.prepare'])
        package = Path(__file__).resolve().parents[1] / 'faculty_ocr'
        self.assertEqual(command, ['bwrap', '--ro-bind', str(package), '/app/faculty_ocr', '--', '/venv/bin/python'])

    def test_malformed_runtime_records_raise_ocr_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for value in (None, [], {'files': []}):
                (root/'model-manifest.json').write_text(json.dumps(value))
                with self.assertRaises(OcrError):
                    _manifest(root)
                (root/'result.json').write_text(json.dumps(value))
                if not isinstance(value, dict):
                    with self.assertRaises(OcrError):
                        _result(root/'result.json')

    def test_model_checksums_reject_same_size_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); model=root/'model'/MODEL_REVISION; model.mkdir(parents=True)
            asset=model/'model.safetensors'; asset.write_bytes(b'original')
            record={'model_id':MODEL_ID,'revision':MODEL_REVISION,'files':{
                'model.safetensors':{'size':8,'sha256':hashlib.sha256(b'original').hexdigest()}}}
            (root/'model-manifest.json').write_text(json.dumps(record))
            _manifest(root,verify=True)
            asset.write_bytes(b'tampered')
            with self.assertRaises(OcrError): _manifest(root,verify=True)

    def test_concurrent_inference_is_rejected_and_lock_releases_after_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with self.assertRaisesRegex(RuntimeError,'task failed'):
                with _exclusive(root):
                    with self.assertRaisesRegex(OcrError,'Another local OCR') as busy:
                        with _exclusive(root): self.fail('second task entered')
                    self.assertIn(BUSY,str(busy.exception))
                    raise RuntimeError('task failed')
            with _exclusive(root): pass

    def test_inbox_adapter_uses_verified_reader_without_writes(self):
        inbox=Mock(spec=['read_artifact']); inbox.read_artifact.return_value=('scan.pdf',b'checked bytes')
        with patch('faculty_ocr.client.recognize',return_value={'text':'draft','needs_review':True}) as run:
            result=recognize_artifact(inbox,'artifact-id',page=2,task='text')
        inbox.read_artifact.assert_called_once_with('artifact-id')
        run.assert_called_once_with(b'checked bytes',page=2,task='text')
        self.assertTrue(result['needs_review'])
        self.assertEqual(result['source_artifact_id'],'artifact-id')

    def test_cli_preserves_existing_output_and_rejects_managed_drive_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); config=root/'storage.json'
            config.write_text(json.dumps({'nvme_root':str(root/'managed')}))
            with patch.dict(os.environ,{'FACULTY_STORAGE_CONFIG':str(config)}):
                with self.assertRaises(ValueError): _local_path(root/'managed'/'page.pdf')
                link=root/'alias'; link.symlink_to(root/'managed',target_is_directory=True)
                with self.assertRaises(ValueError): _local_path(link/'page.pdf')
            target=root/'existing.txt'; target.write_text('keep me')
            with (patch('sys.argv',['faculty_ocr','read',str(root/'input.png'),'--output',str(target)]),
                  patch('faculty_ocr.__main__.recognize',side_effect=AssertionError('must not run')),
                  patch('sys.stderr',new=io.StringIO())):
                self.assertEqual(main(),1)
            self.assertEqual(target.read_text(),'keep me')

    @contextmanager
    def sandboxed(self,request,inputs):
        """faculty_ocr.prepare run in the real sandbox over `inputs` ({file name: bytes}); yields the folder it leaves."""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name,content in inputs.items(): (root/name).write_bytes(content)
            (root/'request.json').write_text(json.dumps(request))
            command=sandbox_command(root,['/venv/bin/python','-m','faculty_ocr.prepare'])
            index=command.index('--')
            command[index:index]=['--ro-bind',str(Path(__file__).resolve().parents[1]/'faculty_ocr'),'/app/faculty_ocr']
            _run(command,70)
            yield root

    def prepare(self,content,page=1):
        with self.sandboxed({'page':page,'is_pdf':content.startswith(b'%PDF-')},{'input':content}) as root:
            if (root/'error.json').exists(): return _result(root/'error.json')
            with Image.open(root/'page.png') as image:
                result=_result(root/'prepared.json')
                result['first_pixel']=image.getpixel((0,0))
                result['normalized_pixels']=image.width*image.height
                return result

    def test_isolated_preparation_handles_alpha_and_limits_size(self):
        data=io.BytesIO(); Image.new('RGBA',(2000,2000),(0,0,0,0)).save(data,format='PNG')
        result=self.prepare(data.getvalue())
        self.assertEqual(result['first_pixel'],(255,255,255))
        self.assertLessEqual(result['normalized_pixels'],1_000_000)
        self.assertEqual(result['original_pixels'],[2000,2000])

    def test_isolated_pdf_selection_and_invalid_inputs(self):
        fixture=Path(__file__).resolve().parents[1]/'document_identity/fixtures/letter.pdf'
        result=self.prepare(fixture.read_bytes(),page=2)
        self.assertEqual(result['pdf_render_dpi'],300)
        # PDFium rounds fractional PDF page dimensions up to a whole pixel.
        for actual,expected in zip(result['original_pixels'],[2550,3300]):
            self.assertLessEqual(abs(actual-expected),1)
        for data,page in [(fixture.read_bytes(),4),(b'%PDF-secret invalid input',1),(b'not an image',1)]:
            with self.subTest(page=page),self.assertRaises(OcrError): self.prepare(data,page)


    def pinned(self,name):
        """One PREPARE_CASES run: prepared.json (or error.json) with each image_sha256 checked against the PNG it names, then
        replaced by a digest of that PNG's pixels so the pin does not depend on the PNG encoder."""
        request,shapes=PREPARE_CASES[name]
        inputs={}
        for index,(width,height) in enumerate(shapes):
            data=io.BytesIO(); synthetic_page(width,height).save(data,format='PNG')
            inputs['input-%d'%index if request.get('operation')=='covers' else 'input']=data.getvalue()
        with self.sandboxed({'page':1,'is_pdf':False,**request},inputs) as root:
            if (root/'error.json').exists(): return json.loads((root/'error.json').read_text())
            def settle(node):
                if isinstance(node,list): return [settle(item) for item in node]
                if not isinstance(node,dict): return node
                node={key:settle(value) for key,value in node.items()}
                if 'image_sha256' in node:
                    path=root/node.get('name','page.png')
                    self.assertEqual(node['image_sha256'],hashlib.sha256(path.read_bytes()).hexdigest())
                    with Image.open(path) as png:
                        self.assertEqual(list(png.size),node.get('pixels',node.get('ocr_pixels')))
                        node['image_sha256']=hashlib.sha256(png.tobytes()).hexdigest()[:16]
                return node
            return settle(json.loads((root/'prepared.json').read_text()))

    def test_isolated_preparation_crops_are_pinned_for_every_operation(self):
        """The pixels and hashes the OCR model and the alias and cover receipts rest on: every operation, every scaling branch."""
        expected=json.loads(PREPARE_EXPECTED.read_text())
        self.assertEqual(sorted(expected),sorted(PREPARE_CASES))
        for name in PREPARE_CASES:
            with self.subTest(name):
                actual=self.pinned(name)
                self.assertEqual(actual,expected[name])
                self.assertEqual(json.dumps(actual),json.dumps(expected[name]))  # the order of keys reaches stored reports


class OcrSetup(unittest.TestCase):
    """STORAGE: the installer keeps the runtime and every cache on a /mnt drive, as the embedding installer does. Nothing is installed here:
    uv and the download are replaced."""

    def run_setup(self, runtime):
        from faculty_ocr import setup
        with patch.dict(os.environ, {'FACULTY_OCR_RUNTIME': str(runtime)}), patch.object(setup.sys, 'argv', ['setup']), \
                patch.object(setup.shutil, 'which', return_value='/usr/bin/uv'), patch.object(setup.subprocess, 'run') as run:
            try:
                setup.main()
            except SystemExit as stopped:
                return run, stopped
        return run, None

    def test_a_runtime_outside_mnt_is_refused_before_anything_runs(self):
        run, stopped = self.run_setup(Path('/home/someone/glm-ocr'))
        self.assertIn('/mnt', str(stopped))
        run.assert_not_called()

    def test_every_install_step_keeps_its_caches_under_the_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / 'glm-ocr'
            run, stopped = self.run_setup(root)
            self.assertIsNone(stopped)
            self.assertEqual(len(run.call_args_list), 3)
            for call in run.call_args_list:
                environment = call.kwargs['env']
                self.assertEqual({key: environment[key] for key in ('UV_CACHE_DIR', 'HF_HOME', 'XDG_CACHE_HOME')},
                                 {'UV_CACHE_DIR': str(root / 'uv-cache'), 'HF_HOME': str(root / 'hf-cache'), 'XDG_CACHE_HOME': str(root / 'cache')})


if __name__=='__main__':
    unittest.main()
