"""Workspace-wide model assets; these do not own teaching records."""
from pathlib import Path
import os

MODEL_ID = "zai-org/GLM-OCR"
MODEL_REVISION = "2e85a62840ccac27daa451df36c736c4636b8628"
ROOT = Path(__file__).resolve().parents[1]
TASKS = {"text": "Text Recognition:", "formula": "Formula Recognition:", "table": "Table Recognition:"}
MAX_INPUT_BYTES = 25 * 1024**2
MAX_TOKENS = 4096


def runtime_root():
    return Path(os.environ.get("FACULTY_OCR_RUNTIME", "/mnt/nvme/Teaching/runtime/glm-ocr")).expanduser().resolve()  # STORAGE: on NVMe, beside the embedding runtime
