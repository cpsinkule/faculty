"""Jev 1.14 and Jev's opt-in "danger zone" features (requirement, 2026-10-15): one bounded request, no redirect,
proxy or automatic retry.

Two sandbox modes beside `jev` (the app's own client, pinned `jev-1.13.0`, text only, default limits):
- `jev114`: `jev-1.14.0`, the new release (`jev-preview` until `jev-latest` moves to it).
- `jev113x`: `jev-1.13.0`, the model the app pins, with the opt-in features: on its text answers it is the app's Jev.
Both send `TypeSafe-Danger-Zone-Images: true` with a request that carries images (Jev's `assets` map) and
`TypeSafe-Danger-Zone-Long-Context: true` with one too long for the default 64K-token request (262K tokens with it), and
neither otherwise, so a short text request is the app's own. The key is the app's `TYPESAFE_API_KEY`.
"""
from jev import client as jev_client

from . import systemone
from .decisions_client import DecisionError  # noqa: F401 - what a caller catches

URL = jev_client.URL
TIMEOUT = 120  # a long-context request is slower than the app's
MODELS = {'jev114': 'jev-1.14.0', 'jev113x': 'jev-1.13.0'}
MAX_IMAGES = 8
# Body bytes past which the long-context header is sent. The default allows 32K tokens per question, and the invented notes run
# about 3 characters a token (measured 2026-10-15: 101,437 characters were 33,169 tokens and answered; 101,418 were refused).
LONG = 60_000


def headers(body, images):
    found = {}
    if images:
        found['TypeSafe-Danger-Zone-Images'] = 'true'
    if len(body) > LONG:
        found['TypeSafe-Danger-Zone-Long-Context'] = 'true'
    return found


def prepare(picture):
    """The image as Jev takes it (docs.typesafe.ai/images "Limits", read 2026-10-15): each side at most 1568 pixels and 78,400 to
    2,458,624 pixels in all. The guide says a side may be 256, but measured the same day 600 × 256 and 1200 × 256 are refused
    ("Invalid image dimensions") while 280 × 280 and 600 × 300 are answered, so the shortest side is padded to 280. A larger
    image is scaled down whole; a smaller one (a bubble row, a narrow crop) is padded with white, never stretched, so the marks
    keep their size. One that already fits is sent as it is."""
    import io
    import math
    from PIL import Image
    image = Image.open(io.BytesIO(picture))
    width, height = image.size
    if 280 <= width <= 1568 and 280 <= height <= 1568 and 78_400 <= width * height <= 2_458_624:
        return picture
    scale = min(1.0, 1568 / max(width, height), math.sqrt(2_458_624 / (width * height)))
    if scale < 1:
        image = image.resize((max(1, min(1568, round(width * scale))), max(1, min(1568, round(height * scale)))), Image.LANCZOS)
    width, height = image.size
    padded = (max(width, 280), max(height, 280))
    if padded[0] * padded[1] < 78_400:
        padded = (padded[0], max(padded[1], math.ceil(78_400 / padded[0])))
    if padded != (width, height):
        canvas = Image.new('RGB' if image.mode not in ('L', '1') else 'L', padded, 'white')
        canvas.paste(image.convert(canvas.mode), (0, 0))
        image = canvas
    out = io.BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()


def open_request(request):
    return systemone.open_request(request, TIMEOUT)


HOSTS = {mode: systemone.Host(f'Jev ({model}{", opt-in features" if mode == "jev113x" else ""})', URL, model, 'TYPESAFE_API_KEY',
                              lambda request: open_request(request), max_images=MAX_IMAGES, assets=True, headers=headers,
                              prepare=prepare, max_request=30 * 1024 * 1024)
         for mode, model in MODELS.items()}

