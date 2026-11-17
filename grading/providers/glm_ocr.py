from ..models import plain_transcript
from .base import ProviderError


class GlmOcrProvider:
    def health(self):
        from faculty_ocr import available
        return {"status": "available" if available() else "not_installed", "provider": "glm"}

    def recognize(self, image):
        from faculty_ocr import recognize, OcrError
        try:
            raw = recognize(image, task="text")
        except (OcrError, ValueError, OSError):
            raise ProviderError("Local GLM-OCR failed. Check its isolated runtime and GPU availability.") from None
        return self._reading(raw)

    def recognize_many(self, images):
        """Several whole pages in one model load, each the reading `recognize` gives it (a blank exam's pages)."""
        from faculty_ocr import recognize_pages, OcrError
        try:
            raws = recognize_pages(images)
        except (OcrError, ValueError, OSError):
            raise ProviderError("Local GLM-OCR failed. Check its isolated runtime and GPU availability.") from None
        return [self._reading(raw) for raw in raws]

    @staticmethod
    def _reading(raw):
        result = plain_transcript(raw["text"], ambiguities=["OCR output was truncated."] if raw.get("truncated") else [])
        return {"provider": "glm", "transcript": result, "raw": raw,
                "metadata": {key: value for key, value in raw.items() if key not in ("text", "html")}}
