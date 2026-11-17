"""EMBEDDING (requirement, 2026-10-10): the local worker's protocol, cache and limits, the
client that never waits long, the jail's argv, the model lock, and the Assistant's memory using it with keywords as the
fallback; and the worker lending the GPU ("LENDS THE GPU", requirement, 2026-10-11). A stand-in
encoder and a stand-in card throughout: no model is loaded, no GPU is touched and the live worker is never reached."""
import base64
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from faculty_embedding import client, config, launch, setup, worker
from test_assistant_memory import MemoryBase

ROOT = Path(__file__).resolve().parents[1]


def encoder(calls=None):
    """Unit vectors with one coordinate per word stem (its first six letters), so "regraded" is near "regrade" where the
    keyword rule, which wants the whole word, sees nothing in common."""
    words = {}

    def encode(texts, role):
        if calls is not None:
            calls.append((role, list(texts)))
        matrix = np.zeros((len(texts), config.DIMENSION), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in text.lower().replace('.', ' ').replace('?', ' ').split():
                if len(word) >= 4:
                    matrix[row, words.setdefault(word[:6], len(words))] += 1
            norm = np.linalg.norm(matrix[row])
            if norm:
                matrix[row] /= norm
        return matrix
    return encode


def picture_encoder(images):
    """A unit vector from a picture's mean colour and size: enough to tell two test pictures apart."""
    matrix = np.zeros((len(images), config.DIMENSION), dtype=np.float32)
    for row, image in enumerate(images):
        red, green, blue = image.resize((1, 1)).getpixel((0, 0))
        matrix[row, :4] = (red + 1, green + 1, blue + 1, max(image.size))
        matrix[row] /= np.linalg.norm(matrix[row])
    return matrix


def picture(colour, kind='PNG', size=(40, 30)):
    import io
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', size, colour).save(out, kind)
    return out.getvalue()


class Card:
    """A stand-in for the model's place on the GPU: it records the moves, and `full` looks find the card without room."""
    def __init__(self, full=0):
        self.moves, self.full = [], full

    def leave(self):
        self.moves.append('leave')

    def enter(self):
        if self.full:
            self.full -= 1
            raise worker.Busy('no room')
        self.moves.append('enter')


class Worker:
    """A worker on a socket of its own, with the stand-in encoder."""
    def __init__(self, test, limit=config.CACHE, pictures=True, gpu=None):
        self.calls = []
        self.folder = tempfile.TemporaryDirectory()
        test.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'embedding.sock'
        self.server = worker.Server(self.path, encoder(self.calls), {'model_id': config.MODEL_ID, 'revision': config.MODEL_REVISION,
                                                                    'device': 'test', 'dimension': config.DIMENSION}, limit,
                                    encode_pictures=picture_encoder if pictures else None, gpu=gpu)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)


class ProtocolTests(unittest.TestCase):
    def test_the_nearest_texts_come_first_and_a_kept_text_is_embedded_once(self):
        found = Worker(self)
        texts = ['Seminar speakers get a thank-you note.', 'Regrade requests come through Gradescope only.', 'Quizzes are fifteen minutes.']
        order = client.nearest('How do students request a regrade?', texts, path=found.path)
        self.assertEqual(order[0], 1)
        self.assertEqual(sorted(order), [0, 1, 2])
        client.nearest('How long are quizzes?', texts, path=found.path)
        documents = [call for call in found.calls if call[0] == 'document']
        self.assertEqual(len(documents), 1)  # the three kept texts, once; the second request embedded only its query
        self.assertEqual(sum(call[0] == 'query' for call in found.calls), 2)
        self.assertEqual(client.status(path=found.path), {'model_id': config.MODEL_ID, 'revision': config.MODEL_REVISION, 'device': 'test',
                                                         'dimension': config.DIMENSION, 'cached': 3, 'lent': False})
        self.assertEqual(self.path_mode(found.path), 0o600)
        self.assertEqual(client.nearest('anything', [], path=found.path), [])

    @staticmethod
    def path_mode(path):
        return path.stat().st_mode & 0o777

    def test_vectors_of_texts_and_pictures_come_back_whole_and_are_never_cached(self):
        found = Worker(self)
        texts = client.text_vectors(['How do students request a regrade?', 'Quizzes are short.'], 'query', path=found.path)
        self.assertEqual((texts.shape, str(texts.dtype)), ((2, config.DIMENSION), 'float32'))
        np.testing.assert_allclose(np.linalg.norm(texts, axis=1), 1, rtol=1e-5)
        pictures = client.picture_vectors([picture('red'), picture('blue', 'JPEG'), picture('red')], path=found.path)
        self.assertEqual(pictures.shape, (3, config.DIMENSION))
        np.testing.assert_allclose(pictures[0], pictures[2], rtol=1e-5)
        self.assertGreater(float(np.abs(pictures[0] - pictures[1]).max()), .01)
        self.assertEqual(client.status(path=found.path)['cached'], 0)  # neither a request's text nor a picture is kept
        big = client.picture_vectors([picture('green', size=(4000, 3000))], path=found.path)  # scaled down before it is embedded
        self.assertAlmostEqual(float(big[0, 3] / big[0, 1]), config.PICTURE_SIDE / (128 + 1), places=1)

    def test_pictures_are_seen_at_560_pieces_and_a_worker_on_another_setting_is_not_taken(self):
        # EMBEDDING "PICTURES AT 560 PIECES" (requirement, 2026-10-11).
        self.assertEqual((config.PICTURE_PIECES, config.PICTURES), (560, f'{config.MODEL_ID}@{config.MODEL_REVISION}#560'))
        # The worker's own cap must not undercut the setting: a Letter page at 560 pieces of 48 by 48 pixels is about 1,292 tall.
        self.assertGreaterEqual(config.PICTURE_SIDE, (config.PICTURE_PIECES * 48 * 48 * 11 / 8.5) ** .5)
        source = (ROOT / 'faculty_embedding' / 'worker.py').read_text()
        self.assertIn('processing_kwargs={"image": {"max_soft_tokens": PICTURE_PIECES}}', source)
        self.assertIn('"picture_pieces": PICTURE_PIECES', source)
        found = Worker(self)
        reply = client.ask({'op': 'picture_vectors', 'pictures': [base64.b64encode(picture('red')).decode()]}, path=found.path)
        self.assertEqual(reply['pieces'], 560)  # how finely they were seen goes back with the vectors
        # A worker started before the setting changed: its vectors cannot be compared with the kept ones, so none is taken,
        # and the refusal is its own kind, which blames no picture (PICTURE-SEARCH waits, as for a lent card).
        with patch.object(worker, 'PICTURE_PIECES', 280):
            with self.assertRaisesRegex(client.OtherSetting, 'another setting') as caught:
                client.picture_vectors([picture('red')], path=found.path)
        self.assertIsInstance(caught.exception, client.EmbeddingUnavailable)
        self.assertNotIsInstance(caught.exception, client.PictureRefused)
        self.assertEqual(client.picture_vectors([picture('red')], path=found.path).shape, (1, config.DIMENSION))

    def test_a_picture_that_is_not_a_png_or_jpeg_or_is_too_much_is_refused(self):
        found = Worker(self)
        for pictures in ([b'%PDF-1.4 not a picture'], [picture('red', 'GIF')], [picture('red')] * (config.MAX_PICTURES + 1), []):
            with self.assertRaises(client.EmbeddingUnavailable):
                client.picture_vectors(pictures, path=found.path)
        for payload in ({'op': 'picture_vectors', 'pictures': ['not base64!']}, {'op': 'vectors', 'role': 'poem', 'texts': ['x']},
                        {'op': 'vectors', 'role': 'query', 'texts': ['x'] * (config.MAX_VECTOR_TEXTS + 1)}, {'op': 'vectors', 'role': 'query', 'texts': ['']}):
            with self.assertRaises(client.EmbeddingUnavailable):
                client.ask(payload, path=found.path)
        plain = Worker(self, pictures=False)
        with self.assertRaisesRegex(client.EmbeddingUnavailable, 'no pictures'):
            client.picture_vectors([picture('red')], path=plain.path)
        # PICTURE-SEARCH sets a picture aside only when the picture itself is the trouble; anything else is a wait.
        for pictures in ([b'%PDF-1.4 not a picture'], [picture('red', 'GIF')], [b'x' * (config.PICTURE_BYTES + 1)]):
            with self.assertRaises(client.PictureRefused):
                client.picture_vectors(pictures, path=found.path)
        for raised in (lambda: client.picture_vectors([picture('red')] * (config.MAX_PICTURES + 1), path=found.path),
                       lambda: client.picture_vectors([picture('red')], path=plain.path),
                       lambda: client.picture_vectors([picture('red')], path=Path(found.folder.name) / 'no-worker.sock')):
            with self.assertRaises(client.EmbeddingUnavailable) as caught:
                raised()
            self.assertNotIsInstance(caught.exception, client.PictureRefused)
        self.assertEqual(client.picture_vectors([picture('red')], path=found.path).shape, (1, config.DIMENSION))  # still serving

    def test_the_cache_is_bounded_and_drops_the_oldest(self):
        found = Worker(self, limit=2)
        client.nearest('first', ['alpha text', 'beta text', 'gamma text'], path=found.path)
        self.assertEqual(client.status(path=found.path)['cached'], 2)

    def test_a_request_the_worker_refuses_or_cannot_read_is_unavailable_and_it_keeps_serving(self):
        found = Worker(self)
        for payload in ({'op': 'explode'}, {'op': 'nearest', 'query': '', 'texts': ['x']}, {'op': 'nearest', 'query': 'q', 'texts': 'x'},
                        {'op': 'nearest', 'query': 'q', 'texts': ['x'] * (config.MAX_TEXTS + 1)}, ['not', 'an', 'object']):
            with self.assertRaises(client.EmbeddingUnavailable):
                client.ask(payload, path=found.path)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as link:
            link.connect(str(found.path))
            link.sendall(b'this is not json\n')
            self.assertIn('error', json.loads(link.recv(65536)))
        with self.assertRaisesRegex(client.EmbeddingUnavailable, 'Too many'):
            client.nearest('q', ['x'] * (config.MAX_TEXTS + 1), path=found.path)
        self.assertEqual(client.nearest('quizzes', ['Quizzes are short.'], path=found.path), [0])
        # A refusal never echoes what it was sent.
        self.assertNotIn('secret words', json.dumps(worker.answer(found.server.index, {'op': 'nearest', 'query': 'secret words', 'texts': 5},
                                                                  found.server.about) if False else {'error': 'Send a query and a list of texts.'}))

    def test_no_worker_a_slow_worker_and_a_wrong_answer_are_all_unavailable_at_once(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(client.EmbeddingUnavailable):
                client.nearest('q', ['x'], path=Path(folder) / 'nothing.sock')
            path = Path(folder) / 'slow.sock'
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(path))
                server.listen(1)
                with self.assertRaises(client.EmbeddingUnavailable):
                    client.nearest('q', ['x'], timeout=.2, path=path)  # connected, never answered
        for order in ([0, 0], [0, None], [False, True]):
            with self.subTest(order=order), patch.object(client, 'ask', return_value={'order': order}), self.assertRaisesRegex(client.EmbeddingUnavailable, 'incomplete'):
                client.nearest('q', ['x', 'y'])

    def test_vectors_of_another_dimension_are_refused(self):
        reply = worker.packed(np.ones((1, 4), dtype=np.float32))
        with patch.object(client, 'ask', return_value=reply), self.assertRaisesRegex(client.EmbeddingUnavailable, 'incomplete'):
            client.text_vectors(['query'])

    def test_pixel_limit_is_checked_before_decoding(self):
        source = Mock(format='PNG', width=10, height=15)
        with patch('PIL.Image.open') as opened, patch.object(worker, 'PICTURE_PIXELS', 100), patch('PIL.Image.MAX_IMAGE_PIXELS'):
            opened.return_value.__enter__.return_value = source
            with self.assertRaises(worker.BadPicture):
                worker.decoded(b'picture')
        source.convert.assert_not_called()

    def test_faculty_asks_only_when_the_service_names_the_socket(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('FACULTY_EMBEDDING_SOCKET', None)
            self.assertFalse(client.configured())
            self.assertEqual(config.socket_path(), config.runtime_root() / 'run' / 'embedding.sock')
        with patch.dict(os.environ, {'FACULTY_EMBEDDING_SOCKET': '/somewhere/embedding.sock'}):
            self.assertTrue(client.configured())
            self.assertEqual(config.socket_path(), Path('/somewhere/embedding.sock'))
        self.assertEqual(config.runtime_root().parts[:2], ('/', 'mnt'))
        drop_in = (ROOT / 'deploy' / 'faculty.service.d' / 'embedding.conf').read_text()
        self.assertIn('FACULTY_EMBEDDING_SOCKET=/mnt/nvme/Teaching/runtime/embedding/run/embedding.sock', drop_in)


class LendingTests(unittest.TestCase):
    """EMBEDDING "LENDS THE GPU" (requirement, 2026-10-11)."""
    TEXTS = ['Quizzes are short.']

    def near(self, found):
        return client.nearest('quizzes', self.TEXTS, path=found.path)

    def test_a_lent_card_refuses_every_request_until_it_is_taken_back(self):
        card = Card()
        found = Worker(self, gpu=card)
        self.assertEqual(self.near(found), [0])
        self.assertIs(client.status(path=found.path)['lent'], False)
        self.assertTrue(client.lend(60, path=found.path))
        self.assertEqual(card.moves, ['leave'])
        self.assertIs(client.status(path=found.path)['lent'], True)
        # Even a text whose vector is kept: the request itself is new. A refused picture is not a bad picture.
        for ask in (lambda: self.near(found), lambda: client.text_vectors(['a text'], path=found.path),
                    lambda: client.picture_vectors([picture('red')], path=found.path)):
            with self.assertRaisesRegex(client.CardLent, 'lent') as refused:  # marked: nobody's fault, and no picture's
                ask()
            self.assertNotIsInstance(refused.exception, client.PictureRefused)
        self.assertTrue(client.lend(90, path=found.path))  # a second loan only moves the end
        self.assertEqual(card.moves, ['leave'])
        client.take_back(path=found.path)
        self.assertEqual(card.moves, ['leave', 'enter'])
        self.assertIs(client.status(path=found.path)['lent'], False)
        self.assertEqual(self.near(found), [0])
        client.take_back(path=found.path)  # nothing is lent: nothing moves
        self.assertEqual(card.moves, ['leave', 'enter'])

    def test_a_loan_nobody_returns_ends_by_itself(self):
        card = Card()
        found = Worker(self, gpu=card)
        client.lend(60, path=found.path)
        found.server.index.lent_until = time.monotonic() - 1  # the borrower stopped and the loan ran out
        self.assertEqual(self.near(found), [0])
        self.assertEqual(card.moves, ['leave', 'enter'])

    def test_a_card_still_full_keeps_the_worker_off_it_until_there_is_room(self):
        card = Card(full=2)
        found = Worker(self, gpu=card)
        client.lend(60, path=found.path)
        with patch.object(worker, 'RETURN_STEP', .01):
            client.take_back(path=found.path)  # the borrower is still unloading: the third look finds room
        self.assertEqual((card.moves, card.full), (['leave', 'enter'], 0))
        # No room within the wait: the worker says so, stays off the card, and does not even look again at once.
        client.lend(60, path=found.path)
        card.full = 5
        with patch.object(worker, 'RETURN_WAIT', 0), self.assertRaisesRegex(client.EmbeddingUnavailable, 'still in use'):
            client.take_back(path=found.path)
        self.assertEqual((card.moves, card.full), (['leave', 'enter', 'leave'], 4))
        self.assertIs(client.status(path=found.path)['lent'], True)
        with self.assertRaisesRegex(client.CardLent, 'lent'):
            self.near(found)
        self.assertEqual(card.full, 4)
        # Later it looks once a request: still full, then room.
        found.server.index.lent_until = time.monotonic() - 1
        with self.assertRaisesRegex(client.CardLent, 'still in use'):
            self.near(found)
        self.assertEqual(card.full, 3)
        card.full = 0
        found.server.index.lent_until = time.monotonic() - 1
        self.assertEqual(self.near(found), [0])
        self.assertEqual(card.moves, ['leave', 'enter', 'leave', 'enter'])

    def test_a_request_that_waits_is_held_for_a_lent_card_and_answered_when_it_is_back(self):
        # 
        card = Card()
        found = Worker(self, gpu=card)
        client.lend(60, path=found.path)
        answers = []
        held = threading.Thread(target=lambda: answers.append(client.nearest('quizzes', self.TEXTS, path=found.path, wait=30)), daemon=True)
        began = time.monotonic()
        held.start()
        held.join(.3)
        self.assertTrue(held.is_alive())  # held, not refused
        self.assertIs(client.status(path=found.path)['lent'], True)  # and the worker still answers a status request meanwhile
        client.take_back(path=found.path)
        held.join(5)
        self.assertEqual((held.is_alive(), answers), (False, [[0]]))
        self.assertLess(time.monotonic() - began, 5)  # answered as the card came back, not when its wait ran out
        # A wait that runs out is a lent card, as for a request that never waited.
        client.lend(60, path=found.path)
        began = time.monotonic()
        with self.assertRaisesRegex(client.CardLent, 'lent'):
            client.nearest('quizzes', self.TEXTS, path=found.path, wait=.3)
        self.assertGreaterEqual(time.monotonic() - began, .3)
        # A loan nobody returns runs out during the wait: the held request ends it. The card is still full at the first look,
        # so it goes on waiting and is answered at the next.
        card.full = 1
        found.server.index.lent_until = time.monotonic() + .2
        with patch.object(worker, 'RETRY', .2):
            self.assertEqual(client.nearest('quizzes', self.TEXTS, path=found.path, wait=30), [0])
        self.assertEqual((card.moves, card.full), (['leave', 'enter', 'leave', 'enter'], 0))
        # Nothing is lent: a wait costs nothing. A wait out of bounds is refused.
        began = time.monotonic()
        self.assertEqual(client.nearest('quizzes', self.TEXTS, path=found.path, wait=30), [0])
        self.assertLess(time.monotonic() - began, 2)
        for wait in (-1, config.MAX_WAIT + 1, 'long', True, None):
            with self.subTest(wait=wait), self.assertRaises(client.EmbeddingUnavailable):
                client.ask({'op': 'nearest', 'query': 'quizzes', 'texts': self.TEXTS, 'wait': wait}, path=found.path)

    def test_a_lookup_in_hand_is_finished_before_the_card_is_given_up(self):
        # The local model is asked only after `lend` has answered (test_frq_providers),
        # and `lend` answers only once nothing is being embedded.
        card, events, plain = Card(), [], encoder()
        found = Worker(self, gpu=card)

        def slow(texts, role):
            events.append('embedding begins')
            time.sleep(.4)
            events.append('embedding ends')
            return plain(texts, role)
        found.server.index.encode = slow
        card.leave = lambda: events.append('card given up')
        card.enter = lambda: events.append('card taken back')
        answers = []
        lookup = threading.Thread(target=lambda: answers.append(client.nearest('quizzes', self.TEXTS, path=found.path, wait=30)), daemon=True)
        lookup.start()
        until = time.monotonic() + 5
        while 'embedding begins' not in events and time.monotonic() < until:
            time.sleep(.01)
        self.assertEqual(events, ['embedding begins'])  # the follow-up's lookup is on the card
        self.assertTrue(client.lend(60, path=found.path))  # the next task asks for the card now, and is made to wait
        given = events.index('card given up')
        self.assertEqual(events[:given].count('embedding begins'), events[:given].count('embedding ends'))
        self.assertGreaterEqual(given, 2)
        self.assertEqual(events.count('embedding begins'), events.count('embedding ends'))  # and nothing is on it as the task starts
        # Whatever of the lookup was left is held, not run beside the task, and answered once the card is back.
        client.take_back(path=found.path)
        lookup.join(5)
        self.assertEqual((lookup.is_alive(), answers), (False, [[0]]))
        self.assertLess(events.index('card given up'), events.index('card taken back'))
        after = events[events.index('card given up') + 1:events.index('card taken back')]
        self.assertEqual(after, [])  # between giving the card up and taking it back, the worker embedded nothing

    def test_a_worker_on_the_cpu_and_a_loan_out_of_bounds_lend_nothing(self):
        found = Worker(self)  # no card
        self.assertFalse(client.lend(60, path=found.path))
        self.assertEqual(self.near(found), [0])
        card = Card()
        other = Worker(self, gpu=card)
        for seconds in (0, -5, config.MAX_LEND + 1, '60', True, None, float('nan'), float('inf')):
            with self.subTest(seconds=seconds), self.assertRaises(client.EmbeddingUnavailable):
                client.lend(seconds, path=other.path)
        self.assertEqual(card.moves, [])
        self.assertGreater(config.MAX_LEND, 600 + 30)  # longer than the longest request of a local model (GRADING's timeout)

    def test_the_model_goes_back_only_where_there_is_room_and_is_never_left_half_moved(self):
        class Model:
            at, fails = 'cuda', False

            def to(self, where):
                if where == 'cuda' and self.fails:
                    self.at = 'half'
                    raise RuntimeError('out of memory')
                self.at = where
        cuda = SimpleNamespace(free=0, emptied=0, memory_allocated=lambda: 1000)
        cuda.mem_get_info = lambda: (cuda.free, 11000)
        cuda.empty_cache = lambda: setattr(cuda, 'emptied', cuda.emptied + 1)
        model = Model()
        gpu = worker.Gpu(model, SimpleNamespace(cuda=cuda), room=500)
        gpu.leave()
        self.assertEqual((model.at, cuda.emptied, gpu.held), ('cpu', 1, 1000))
        cuda.free = 1499  # one byte short of the model and its room: not even tried
        with self.assertRaises(worker.Busy):
            gpu.enter()
        self.assertEqual(model.at, 'cpu')
        cuda.free, model.fails = 1500, True
        with self.assertRaisesRegex(RuntimeError, 'out of memory'):
            gpu.enter()
        self.assertEqual((model.at, cuda.emptied), ('cpu', 2))  # whole in main memory again
        model.fails = False
        gpu.enter()
        self.assertEqual(model.at, 'cuda')

    def test_faculty_borrows_only_from_a_worker_it_was_told_of_and_never_fails_for_it(self):
        card = Card()
        found = Worker(self, gpu=card)
        # No socket named (a check, a script, a second copy of the app): nothing is asked of any worker.
        with patch.dict(os.environ), patch.object(client, 'ask', side_effect=AssertionError('never asked')):
            os.environ.pop('FACULTY_EMBEDDING_SOCKET', None)
            with client.gpu_lent(60) as lent:
                self.assertFalse(lent)
        with patch.dict(os.environ, {'FACULTY_EMBEDDING_SOCKET': str(found.path)}):
            with client.gpu_lent(60) as lent:
                self.assertTrue(lent)
                self.assertEqual(card.moves, ['leave'])
            self.assertEqual(card.moves, ['leave', 'enter'])
            with self.assertRaises(KeyError), client.gpu_lent(60):  # the request failed: the card comes back all the same
                raise KeyError('the model host failed')
            self.assertEqual(card.moves, ['leave', 'enter', 'leave', 'enter'])
            # A worker that cannot take the card back yet: the caller's own work still ends well.
            card.full = 5
            with patch.object(worker, 'RETURN_WAIT', 0), client.gpu_lent(60) as lent:
                self.assertTrue(lent)
            self.assertIs(client.status(path=found.path)['lent'], True)
        with patch.dict(os.environ, {'FACULTY_EMBEDDING_SOCKET': str(Path(found.folder.name) / 'nothing.sock')}):
            with client.gpu_lent(60) as lent:  # no worker there
                self.assertFalse(lent)


class RuntimeTests(unittest.TestCase):
    def test_the_worker_s_jail_has_no_network_home_checkout_catalog_or_key(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model = root / 'model' / config.MODEL_REVISION
            base = root / 'python' / 'cpython-3.12.14-linux-x86_64-gnu'
            for path in (model, base / 'bin', root / 'venv' / 'bin', root / 'run'):
                path.mkdir(parents=True)
            (model / 'model.safetensors').write_bytes(b'x')
            (root / 'venv' / 'bin' / 'python').write_text('')
            (root / 'model-manifest.json').write_text(json.dumps({'model_directory': str(model), 'python_base': str(base)}))
            record, found = launch.manifest(root)
            argv = launch.command(root, record, found, 'auto')
            cpu = launch.command(root, record, found, 'cpu')
            with self.assertRaises(launch.NotInstalled):
                launch.manifest(root / 'nothing')
        self.assertIn('--unshare-all', argv)
        self.assertNotIn('--share-net', argv)
        self.assertIn('--clearenv', argv)
        self.assertEqual(argv[-3:], ['/venv/bin/python', '-m', 'faculty_embedding.worker'])
        bound = [tuple(argv[i:i + 3]) for i, word in enumerate(argv) if word.startswith('--') and word.endswith(('bind', 'bind-try'))]
        # Only the socket's folder is writable, beside the GPU's device nodes.
        self.assertEqual([source for kind, source, _ in bound if not kind.startswith('--ro-') and not Path(source).match('/dev/nvidia*')], [str(root / 'run')])
        package = ('--ro-bind', str(ROOT / 'faculty_embedding'), '/app/faculty_embedding')
        self.assertIn(package, bound)
        kept = (Path.home(), ROOT, Path('/mnt/nvme/Teaching/catalog'), Path.home() / '.config' / 'faculty')
        for kind, source, target in bound:
            path = Path(source).resolve()
            if (kind, source, target) != package:  # this package is all the worker sees of the checkout, wherever that is
                self.assertFalse(source.startswith('/home'), source)
                self.assertFalse(path.is_relative_to(Path.home()) or path.is_relative_to(ROOT), source)
                self.assertNotIn('catalog', source)
            # Never the home folder, the checkout, the live catalog or the saved credentials, nor a folder that holds one of them.
            self.assertFalse([str(folder) for folder in kept if folder.is_relative_to(path)], source)
        self.assertIn(('--ro-bind', str(root / 'python'), str(root / 'python')), bound)
        names = [argv[i + 1] for i, word in enumerate(argv) if word == '--setenv']
        self.assertFalse([name for name in names if name.endswith(('_KEY', '_TOKEN', '_SECRET', '_PASSWORD'))])
        self.assertEqual(argv[argv.index('HF_HUB_OFFLINE') + 1], '1')
        self.assertEqual(argv[argv.index('FACULTY_EMBEDDING_DEVICE') + 1], 'auto')
        self.assertFalse([word for word in cpu if word.startswith('/dev/nvidia')])  # the CPU worker is given no GPU device

    def test_a_model_that_differs_from_the_lock_is_refused(self):
        lock = json.loads((ROOT / 'faculty_embedding' / 'model-lock.json').read_text())
        self.assertEqual((lock['model_id'], lock['revision']), (config.MODEL_ID, config.MODEL_REVISION))
        self.assertEqual(lock['files']['model.safetensors']['size'], 1488915288)
        self.assertEqual({Path(name).suffix for name in lock['files']}, {'.json', '.jinja', '.safetensors', '.model'})  # no code, no pickle
        with tempfile.TemporaryDirectory() as folder:
            small = {'files': {'a.json': {'size': 2, 'sha256': '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a'}}}
            self.assertFalse(worker.verified(folder, small))
            (Path(folder) / 'a.json').write_text('{}')
            self.assertTrue(worker.verified(folder, small))
            (Path(folder) / 'a.json').write_text('[]')
            self.assertFalse(worker.verified(folder, small))
            with patch.dict(os.environ, {'FACULTY_EMBEDDING_MODEL': folder}), self.assertRaisesRegex(SystemExit, 'model-lock.json'):
                worker.main()
            with patch.dict(os.environ, {'FACULTY_EMBEDDING_RUNTIME': folder}):
                self.assertEqual(setup.status(), {'installed': False})

    def test_the_model_runs_in_float32_or_not_at_all(self):
        # 
        import re
        source = (ROOT / 'faculty_embedding' / 'worker.py').read_text()
        code = '\n'.join(line.split('#', 1)[0] for line in source.splitlines())
        self.assertFalse(re.search(r'\.half\(|autocast|float16|bfloat16|quantiz', code))
        self.assertIn('.float().eval()', code)
        self.assertIn('precisions != {"float32"}', code)
        self.assertIn('"precision": "float32"', code)

    def test_the_app_and_the_worker_import_no_model_at_import_time(self):
        import ast
        for name in ('client.py', 'config.py', 'launch.py', 'worker.py', 'setup.py'):
            tree = ast.parse((ROOT / 'faculty_embedding' / name).read_text())
            top = {alias.name.split('.')[0] for node in tree.body if isinstance(node, ast.Import) for alias in node.names}
            top |= {node.module.split('.')[0] for node in tree.body if isinstance(node, ast.ImportFrom) and node.module and not node.level}
            self.assertFalse({'torch', 'sentence_transformers', 'transformers', 'huggingface_hub'} & top, name)


class MemoryUsesItTests(MemoryBase):
    def write_many(self):
        """The memory a request needs, kept first, then twelve newer ones about something else: neither a shared whole word
        nor recency finds it."""
        wanted = self.keep({'content': 'This term regrade requests come through Gradescope only.', 'scope': 'term', 'term_id': self.term})
        for number in range(12):
            self.keep({'content': f'Fact {number} about the seminar room projector and cables. ' + 'x' * 300, 'scope': 'term', 'term_id': self.term})
        return wanted

    def test_the_nearest_memories_come_from_the_worker_joined_with_keyword_hits(self):
        wanted = self.write_many()
        found = Worker(self)

        class Embedder:
            def nearest(self, query, texts):
                return client.nearest(query, texts, path=found.path)
        self.memory.embedder = Embedder()
        chat, _ = self.conversation()
        chat._tool_request = {'request': 'How do students ask to have a quiz regraded?'}
        with patch('jev.client.available', return_value=False):
            told = chat.context_note()
        self.assertIn('Gradescope only', told)  # no whole word in common: only the worker could have put it first
        self.assertTrue(any(role == 'query' for role, _ in found.calls))
        self.assertEqual(self.memory.listing()['memories'][-1]['id'], wanted['id'])
        self.memory.embedder = None
        with patch('jev.client.available', return_value=False):
            self.assertNotIn('Gradescope only', chat.context_note())

    def test_when_the_worker_does_not_answer_keywords_stand_in_and_nothing_fails(self):
        self.write_many()

        class Down:
            def nearest(self, query, texts):
                raise client.EmbeddingUnavailable('down')
        self.memory.embedder = Down()
        chat, _ = self.conversation()
        chat._tool_request = {'request': 'How do students ask for a regrade this term?'}  # shares the whole word "regrade"
        with patch('jev.client.available', return_value=False), self.assertLogs(self.app.logger, 'WARNING') as logs:
            told = chat.context_note()
        self.assertIn('Gradescope only', told)
        self.assertIn('keywords stood in', logs.output[0])

    def test_the_app_attaches_the_worker_only_when_the_service_names_it(self):
        self.assertIsNone(self.memory.embedder)
        from app import create_app
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'FACULTY_EMBEDDING_SOCKET': str(Path(folder) / 'embedding.sock')}):
            other = create_app(os.path.join(folder, 'data'))
            self.assertIsInstance(other.extensions['assistant'].memory.embedder, client.Embedder)


if __name__ == '__main__':
    unittest.main()
