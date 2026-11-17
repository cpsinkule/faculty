"""The web process's port, and a restart the instructor's pages never see (FRONTEND "A RESTART NOBODY SEES").

Faculty is restarted several times a day (148 times 2026-10-20..11-03). Each time the port was closed for about a second and a
request being answered was cut off. Tailscale serve answered both with an empty 502, which a page shows as "The server response
could not be read": all 63 such answers in those days fell in a restart (requirement, 2026-11-03).

Two things close that gap. Systemd's file descriptor store (deploy/faculty.service.d/keep-port.conf) keeps the listening socket
while the process is replaced, so a connection arriving in between waits in the socket's queue for the next process instead of being
refused. A store is released on a stop, unlike a socket unit, so a tab's poll can never start a Faculty that was stopped on purpose
(CATALOG). And a stopping process takes no more connections and finishes what it is answering (`drain`, at most DRAIN_WAIT).
Anywhere else, the tests and isolated copies included, there is no store and the process simply binds its port.
"""
import os
import socket
import time

FIRST_FD = 3  # where systemd passes kept sockets (sd_listen_fds)
FD_NAME = "web"
DRAIN_WAIT = 8  # seconds: requests arriving meanwhile wait in the queue, so a restart stays short


def listening_socket(host, port):
    """The socket a previous process left with systemd, or a new one, which is left with systemd for the next process."""
    kept = _inherited()
    if kept is not None:
        if kept.getsockname()[:2] == (host, port):
            return kept
        # The unit's HOST or PORT changed and only a restart followed: the kept socket is the old address.
        kept.close()
        _notify(f"FDSTOREREMOVE=1\nFDNAME={FD_NAME}")
    made = socket.create_server((host, port), backlog=1024)
    _notify(f"FDSTORE=1\nFDNAME={FD_NAME}", made)
    return made


def _inherited():
    try:
        passed = os.environ.get("LISTEN_PID") == str(os.getpid()) and int(os.environ.get("LISTEN_FDS") or 0) >= 1
    except ValueError:
        passed = False
    for name in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
        os.environ.pop(name, None)  # never for the processes this one starts
    if not passed:
        return None
    kept = socket.socket(fileno=FIRST_FD)
    kept.set_inheritable(False)
    return kept


def _notify(message, sock=None):
    """Tells systemd. A refusal only leaves the port closed across the next restart, as it was before: never a reason not to start."""
    target = os.environ.get("NOTIFY_SOCKET")
    if not target:
        return
    address = "\0" + target[1:] if target.startswith("@") else target
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as out:
            out.connect(address)  # socket.send_fds leaves its own address argument unused
            socket.send_fds(out, [message.encode()], [sock.fileno()] if sock else [])
    except OSError as error:
        print(f"Faculty's port will not be kept across a restart: {error}", flush=True)


def drain(server, wait=DRAIN_WAIT):
    """Called from a thread while waitress's loop runs on: stops taking connections and lets what is being answered finish, closing
    each connection once it has nothing left, so that Tailscale's kept connections go to the next process. What runs past `wait` is
    cut off as any stop did before 2026-11-03: a restart check (AGENTS.md CHECKS) comes first for the work that matters."""
    server.accepting = False
    deadline = time.monotonic() + wait
    while server.active_channels and time.monotonic() < deadline:
        for channel in list(server.active_channels.values()):
            if not (channel.requests or channel.request is not None or channel.total_outbufs_len):
                channel.will_close = True  # as waitress's own maintenance closes an idle connection
        server.trigger.pull_trigger()
        time.sleep(0.02)
