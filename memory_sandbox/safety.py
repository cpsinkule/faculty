"""Where the memory sandbox may write: the Decisions sandbox's own rules, on a scratch folder of its own."""
from pathlib import Path

from decisions_sandbox import safety as base

SCRATCH = Path('/mnt/nvme/Teaching/tmp/memory-sandbox')
# Reproducible heavy assets (venv, model weights, the pinned Jev-Mem checkout): never in the checkout, never on the root disk.
RUNTIME = Path('/mnt/nvme/Teaching/runtime/memory-sandbox')
KEY_FILES = base.KEY_FILES
scrub_environment = base.scrub_environment


def run_folder(name):
    return base.run_folder(name, scratch=SCRATCH)
