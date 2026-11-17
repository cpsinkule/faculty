"""Made-up recordings, clips and documents for the checks of what a model reads beside text and pictures (Chris, 2026-11-11, of
Gemini Decision 1: "it has more modalities than Jev"). Everything is synthesized here from invented words: a voice by ffmpeg's
flite, a clip by its drawtext, a document by reportlab or drawn as a crooked, grainy scan. Scratch goes through `tempfile`
(the run folder's `tmp`, on /mnt)."""
import io
import random
import subprocess
import tempfile
from pathlib import Path

FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
BOLD = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'


def _ffmpeg(*args):
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', *args], check=True, timeout=120)


def speech(text, noise=0.0):
    """WAV bytes (16 kHz mono) of a synthetic voice saying `text`; `noise` mixes in pink noise of that amplitude (0 to 1)."""
    with tempfile.TemporaryDirectory() as temp:
        words, out = Path(temp) / 'words.txt', Path(temp) / 'voice.wav'
        words.write_text(text)
        source = ['-f', 'lavfi', '-i', f'flite=textfile={words}:voice=slt']
        if noise:
            _ffmpeg(*source, '-f', 'lavfi', '-i', f'anoisesrc=color=pink:amplitude={noise}:seed=7', '-filter_complex',
                    'amix=inputs=2:duration=first:normalize=0', '-ar', '16000', '-ac', '1', str(out))
        else:
            _ffmpeg(*source, '-ar', '16000', '-ac', '1', str(out))
        return out.read_bytes()


def clip(captions, spoken='', seconds=3):
    """MP4 bytes: each caption shown for `seconds` on a white frame, with a synthetic voice saying `spoken` when given."""
    with tempfile.TemporaryDirectory() as temp:
        out = Path(temp) / 'clip.mp4'
        draw = ','.join(f"drawtext=fontfile={FONT}:text='{caption}':fontsize=34:fontcolor=black:x=40:y=100:"
                        f"enable='between(t,{i * seconds},{(i + 1) * seconds})'" for i, caption in enumerate(captions))
        picture = ['-f', 'lavfi', '-i', f'color=c=white:s=640x240:d={seconds * len(captions)}:r=10']
        if spoken:
            voice = Path(temp) / 'voice.wav'
            voice.write_bytes(speech(spoken))
            _ffmpeg(*picture, '-i', str(voice), '-vf', draw, '-pix_fmt', 'yuv420p', str(out))
        else:
            _ffmpeg(*picture, '-vf', draw, '-pix_fmt', 'yuv420p', str(out))
        return out.read_bytes()


def document(heading, lines):
    """A one-page typed PDF (its text can be selected)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    out = io.BytesIO()
    page = canvas.Canvas(out, pagesize=letter, invariant=1)
    page.setFont('Helvetica-Bold', 16)
    page.drawString(72, 720, heading)
    page.setFont('Helvetica', 12)
    for row, line in enumerate(lines):
        page.drawString(72, 680 - 22 * row, line)
    page.showPage()
    page.save()
    return out.getvalue()


def page(heading, lines, seed=0):
    """The same document as a phone picture of a printout would look: a grayscale page, a little crooked, soft and grainy."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    rng = random.Random(seed)
    sheet = Image.new('L', (1275, 1650), 250)  # letter at 150 dpi
    draw = ImageDraw.Draw(sheet)
    draw.text((150, 150), heading, font=ImageFont.truetype(BOLD, 34), fill=25)
    body = ImageFont.truetype(FONT, 25)
    for row, line in enumerate(lines):
        draw.text((150, 235 + 46 * row), line, font=body, fill=35)
    sheet = sheet.rotate(rng.uniform(-2.5, 2.5), resample=Image.BICUBIC, fillcolor=235).filter(ImageFilter.GaussianBlur(.8))
    return Image.blend(sheet, Image.effect_noise(sheet.size, 40), .12)


def page_png(heading, lines, seed=0):
    out = io.BytesIO()
    page(heading, lines, seed).save(out, format='PNG', optimize=True)
    return out.getvalue()


def scanned(heading, lines, seed=0):
    """A one-page PDF that is only that picture (no text to select), as a scanner or a phone's scan app saves it."""
    out = io.BytesIO()
    page(heading, lines, seed).save(out, format='PDF', resolution=150)
    return out.getvalue()
