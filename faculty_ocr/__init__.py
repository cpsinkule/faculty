"""Local, optional OCR tooling. Importing this package never loads a model."""
from .client import BUSY, OcrError, available, recognize, recognize_aliases, recognize_artifact, recognize_bands, recognize_covers, recognize_fields, recognize_pages, status

__all__ = ["BUSY", "OcrError", "available", "recognize", "recognize_aliases", "recognize_artifact", "recognize_bands", "recognize_covers", "recognize_fields", "recognize_pages", "status"]
