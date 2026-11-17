"""web_server.py: a restart the instructor's pages never see (FRONTEND "A RESTART NOBODY SEES")."""
import http.client
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import web_server  # noqa: E402

# What app.py's main does with the port, around an application with one slow answer. Started as systemd starts it, with the kept socket.
PROCESS = """
import os, signal, sys, time
sys.path.insert(0, sys.argv[1])
os.dup2(int(sys.argv[5]), 3)
os.environ.update(LISTEN_PID=str(os.getpid()), LISTEN_FDS="1")
from waitress import create_server
from canvas.stopping import Writes, stop_handler
from web_server import drain, listening_socket
def application(environ, start_response):
    if environ["PATH_INFO"] == "/slow":
        time.sleep(float(sys.argv[3]))
    body = str(os.getpid()).encode() * 2000
    start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))])
    return [body]
server = create_server(application, sockets=[listening_socket("127.0.0.1", int(sys.argv[2]))], threads=4)
signal.signal(signal.SIGTERM, stop_handler(Writes(), previous=signal.SIG_DFL, drain=lambda: drain(server, wait=float(sys.argv[4]))))
print("up", flush=True)
server.run()
"""


def ask(port, path="/", conn=None):
    connection = conn or http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        if not conn:
            connection.close()


class ListeningSocketTests(unittest.TestCase):
    def setUp(self):
        saved = {name: os.environ.pop(name, None) for name in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES", "NOTIFY_SOCKET")}
        self.addCleanup(lambda: [os.environ.pop(name, None) or (value is not None and os.environ.__setitem__(name, value)) for name, value in saved.items()])
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.notify = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(self.notify.close)
        self.notify.settimeout(2)
        self.notify.bind(str(Path(self.scratch.name) / "notify"))

    def _free_port(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    def test_with_no_systemd_it_binds_its_port_and_tells_nobody(self):
        port = self._free_port()
        with web_server.listening_socket("127.0.0.1", port) as sock:
            self.assertEqual(sock.getsockname(), ("127.0.0.1", port))
            socket.create_connection(("127.0.0.1", port), timeout=2).close()  # it is listening

    def test_a_new_socket_is_left_with_systemd(self):
        os.environ["NOTIFY_SOCKET"] = self.notify.getsockname()
        port = self._free_port()
        with web_server.listening_socket("127.0.0.1", port):
            message, fds, _flags, _addr = socket.recv_fds(self.notify, 200, 2)
            self.assertEqual(message, b"FDSTORE=1\nFDNAME=web")
            self.assertEqual(len(fds), 1)
            with socket.socket(fileno=fds[0]) as stored:
                self.assertEqual(stored.getsockname(), ("127.0.0.1", port))

    def test_the_kept_socket_is_used_and_the_processes_it_starts_are_not_told(self):
        original = socket.create_server(("127.0.0.1", 0))
        host, port = original.getsockname()
        os.environ.update(LISTEN_PID=str(os.getpid()), LISTEN_FDS="1", LISTEN_FDNAMES="web", NOTIFY_SOCKET=self.notify.getsockname())
        with patch.object(web_server, "FIRST_FD", original.fileno()):
            kept = web_server.listening_socket(host, port)
        original.detach()
        with kept:
            self.assertEqual(kept.getsockname(), (host, port))
            self.assertFalse(kept.get_inheritable())
            for name in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
                self.assertNotIn(name, os.environ)
            self.notify.settimeout(0.1)
            with self.assertRaises(TimeoutError):  # nothing new was stored
                self.notify.recv(200)

    def test_a_kept_socket_of_another_address_is_dropped_for_the_new_one(self):
        original = socket.create_server(("127.0.0.1", 0))
        port = self._free_port()
        os.environ.update(LISTEN_PID=str(os.getpid()), LISTEN_FDS="1", NOTIFY_SOCKET=self.notify.getsockname())
        with patch.object(web_server, "FIRST_FD", original.fileno()):
            made = web_server.listening_socket("127.0.0.1", port)
        original.detach()
        with made:
            self.assertEqual(made.getsockname(), ("127.0.0.1", port))
            self.assertEqual(self.notify.recv(200), b"FDSTOREREMOVE=1\nFDNAME=web")
            message, fds, _flags, _addr = socket.recv_fds(self.notify, 200, 2)
            self.assertEqual(message, b"FDSTORE=1\nFDNAME=web")
            os.close(fds[0])

    def test_a_systemd_that_refuses_never_stops_the_start(self):
        os.environ["NOTIFY_SOCKET"] = str(Path(self.scratch.name) / "nobody-listens")
        with web_server.listening_socket("127.0.0.1", self._free_port()) as sock:
            self.assertGreater(sock.fileno(), 0)

    def test_someone_elses_passed_sockets_are_left_alone(self):
        os.environ.update(LISTEN_PID="1", LISTEN_FDS="1")
        with web_server.listening_socket("127.0.0.1", self._free_port()) as sock:
            self.assertGreater(sock.fileno(), 2)


class RestartTests(unittest.TestCase):
    """Two real processes, this test holding the socket as systemd's store does."""

    def setUp(self):
        self.held = socket.create_server(("127.0.0.1", 0), backlog=128)
        self.addCleanup(self.held.close)
        self.port = self.held.getsockname()[1]
        self.processes = []
        self.addCleanup(self._end)

    def _end(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()

    def _start(self, slow=1.0, wait=5.0):
        fd = self.held.fileno()
        env = {key: value for key, value in os.environ.items() if key != "NOTIFY_SOCKET"}
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", PROCESS, str(ROOT), str(self.port), str(slow), str(wait), str(fd)],
            stdout=subprocess.PIPE, text=True, pass_fds=(fd,), env=env)
        self.processes.append(process)
        self.assertEqual(process.stdout.readline().strip(), "up")
        return process

    def test_a_restart_finishes_what_it_is_answering_and_the_next_request_waits_for_the_new_process(self):
        first = self._start()
        self.assertEqual(ask(self.port, "/")[0], 200)
        answers = {}
        slow = threading.Thread(target=lambda: answers.update(slow=ask(self.port, "/slow")))
        slow.start()
        time.sleep(0.3)
        first.send_signal(signal.SIGTERM)
        time.sleep(0.2)
        # Arrives while the first process drains and no process takes connections: never refused, answered by the next one.
        waiting = threading.Thread(target=lambda: answers.update(waiting=ask(self.port, "/")))
        waiting.start()
        slow.join(20)
        self.assertEqual(first.wait(10), -signal.SIGTERM)
        self.assertEqual(answers["slow"][0], 200)
        self.assertEqual(answers["slow"][1], str(first.pid).encode() * 2000)  # all of it, from the old process
        second = self._start()
        waiting.join(20)
        self.assertEqual(answers["waiting"], (200, str(second.pid).encode() * 2000))
        self.assertEqual(ask(self.port, "/")[1], str(second.pid).encode() * 2000)

    def test_a_kept_connection_is_closed_so_the_next_request_goes_to_the_new_process(self):
        first = self._start()
        kept = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        self.assertEqual(ask(self.port, "/", kept)[0], 200)
        first.send_signal(signal.SIGTERM)
        self.assertEqual(first.wait(4), -signal.SIGTERM)  # an idle connection holds nothing up
        kept.close()

    def test_an_answer_past_the_wait_does_not_hold_the_stop_up(self):
        first = self._start(slow=30.0, wait=0.3)
        answers = []
        def asked():
            try:
                answers.append(ask(self.port, "/slow"))
            except OSError as error:
                answers.append(error)
        slow = threading.Thread(target=asked)
        slow.start()
        time.sleep(0.3)
        began = time.monotonic()
        first.send_signal(signal.SIGTERM)
        self.assertEqual(first.wait(10), -signal.SIGTERM)
        self.assertLess(time.monotonic() - began, 5)
        slow.join(10)
        self.assertIsInstance(answers[0], Exception)


class UnitTests(unittest.TestCase):
    def test_the_drop_in_lets_the_main_process_leave_one_socket_with_systemd(self):
        conf = (ROOT / "deploy" / "faculty.service.d" / "keep-port.conf").read_text(encoding="utf-8")
        self.assertIn("NotifyAccess=main", conf)
        self.assertIn("FileDescriptorStoreMax=1", conf)
        self.assertLess(web_server.DRAIN_WAIT, 60)  # static/request.js: a request waiting for the next process gives up after 60 s


if __name__ == "__main__":
    unittest.main()
