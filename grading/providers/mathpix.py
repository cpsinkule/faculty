import base64
import os

from .base import ProviderError, request_json
from ..models import transcript


class MathpixProvider:
    def health(self):
        return {"provider": "mathpix", "status": "configured" if os.environ.get("MATHPIX_APP_ID") and os.environ.get("MATHPIX_APP_KEY") else "not_configured",
                "note": "Configuration only; no billable image request is made by health checks."}

    def recognize(self, image):
        if self.health()["status"] != "configured":
            raise ProviderError("Mathpix is not configured.")
        raw = request_json("https://api.mathpix.com/v3/text", headers={"app_id": os.environ["MATHPIX_APP_ID"],
            "app_key": os.environ["MATHPIX_APP_KEY"]}, body={"src": "data:image/png;base64," + base64.b64encode(image).decode(),
            "formats": ["text", "data"], "data_options": {"include_latex": True}, "include_line_data": True,
            "metadata": {"improve_mathpix": False}})
        if raw.get("error"):
            # The identifier (never the message, which can quote the image) says whether Mathpix was busy or the image is the problem.
            # Seen live: a quarter of 880 parallel requests were refused and then succeeded when asked again, with nothing recorded to say why.
            info = raw.get("error_info") if isinstance(raw.get("error_info"), dict) else {}
            ident = str(info.get("id") or "")[:60]
            status = {"http_max_requests": 429, "sys_exception": 503}.get(ident)
            raise ProviderError("Mathpix is rate limited or busy." if status else "Mathpix could not read this response. Review or retry explicitly.",
                                confirmed=True, status=status, confirmation={"mathpix_error": ident, **({"status": status} if status else {})})
        width, height = raw.get("image_width", 0), raw.get("image_height", 0)
        lines = []
        for index, line in enumerate(raw.get("line_data", [])):
            geometry, polygon = None, line.get("cnt")
            if polygon and width and height:
                xs, ys = [p[0] / width for p in polygon], [p[1] / height for p in polygon]
                if min(xs) < max(xs) and min(ys) < max(ys):
                    geometry = [max(0, min(xs)), max(0, min(ys)), min(1, max(xs)), min(1, max(ys))]
            latex = next((d.get("value", "") for d in line.get("data", []) if d.get("type") == "latex"), line.get("text", ""))
            lines.append({"id": f"line-{index+1}", "elements": [{"type": "math", "text": line.get("text", ""),
                "latex": latex, "bbox": geometry, "confidence": line.get("confidence")} ]})
        # The page's own reading, NOT the first entry of `data`: that array holds one snippet per detected formula, so taking
        # its first item made the whole-page `latex` a single symbol beside hundreds of characters of text (measured 2026-09-21:
        # median 4 characters against 528, on 201 of 240 responses). Mathpix's `text` format already writes mathematics inline
        # as \( ... \), so it is the page's LaTeX. A stub here is not harmless: `consensus.align` prefers `latex`, so every
        # reader-versus-OCR comparison diffed the real reading against that one symbol.
        latex = raw.get("text", "")
        return {"provider": "mathpix", "raw": raw, "metadata": {"request_id": raw.get("request_id"),
            "version": raw.get("version"), "geometry": "crop-relative"},
            "transcript": transcript({"plain_text": raw.get("text", ""), "latex": latex, "lines": lines,
                "confidence": raw.get("confidence"), "ambiguities": []})}
