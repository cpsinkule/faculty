"""Offline template conversion; output is a reference, never qualified paper.

Original uploads are retained separately from a visually flattened PDF and its
300-DPI page images. PDF/Office parsers run only inside the existing Bubblewrap
boundary. LaTeX uses the existing bounded compiler. No catalog or owner is read.
"""
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import resource
import subprocess
import tempfile

from .process import sandbox_command

MAX_INPUT = 25 * 1024**2
MAX_PAGES = 100
MAX_OUTPUT = 256 * 1024**2
MAX_PIXELS = 30_000_000
MAX_TEXT = 2_000_000
DPI = 300
OFFICE_ROOT = Path("/mnt/nvme/Teaching/runtime/libreoffice-v2")


def _sources(files, entrypoint):
    if not isinstance(files, dict) or not 1 <= len(files) <= 100:
        raise ValueError("Upload one PDF or DOCX, or at most 100 LaTeX source files.")
    allowed = {".pdf", ".docx", ".tex", ".latex", ".png", ".jpg", ".jpeg"}
    for name, content in files.items():
        if (not isinstance(name, str) or not name or len(name) > 200 or "\\" in name or "\x00" in name
                or PurePosixPath(name).is_absolute() or str(PurePosixPath(name)) != name
                or any(part in ("", ".", "..") for part in name.split("/"))
                or PurePosixPath(name).suffix.lower() not in allowed or not isinstance(content, bytes)):
            raise ValueError("Use safe relative PDF, DOCX, LaTeX, PNG, or JPEG source filenames.")
    if not 0 < sum(map(len, files.values())) <= MAX_INPUT:
        raise ValueError("Template source files must total at most 25 MiB.")
    tex = [name for name in files if PurePosixPath(name).suffix.lower() in (".tex", ".latex")]
    if entrypoint is not None and (not isinstance(entrypoint, str) or entrypoint not in files):
        raise ValueError("Choose an uploaded source file as the entrypoint.")
    if tex:
        if any(PurePosixPath(name).suffix.lower() == ".docx" for name in files):
            raise ValueError("Upload DOCX separately from LaTeX sources.")
        if entrypoint is None:
            if len(tex) != 1:
                raise ValueError("Choose the LaTeX entrypoint when several source files are uploaded.")
            entrypoint = tex[0]
        if entrypoint not in tex:
            raise ValueError("The LaTeX entrypoint must be a .tex or .latex file.")
        return "latex", entrypoint
    if len(files) != 1:
        raise ValueError("Upload one PDF or DOCX template at a time.")
    name = next(iter(files))
    kind = PurePosixPath(name).suffix.lower().removeprefix(".")
    if kind not in ("pdf", "docx"):
        raise ValueError("Choose a PDF, DOCX, or LaTeX template.")
    return kind, name


def _office_root():
    configured = os.environ.get("FACULTY_LIBREOFFICE_ROOT")
    path = Path(configured) if configured else OFFICE_ROOT
    if (path / "usr/lib/libreoffice/program/soffice.bin").is_file():
        return path.resolve()
    if configured:
        raise ValueError("The configured DOCX converter is unavailable.")
    if Path("/usr/lib/libreoffice/program/soffice.bin").is_file():
        return None
    raise ValueError("DOCX conversion is unavailable. Run manage_template_converter.py or upload an exported PDF.")


def _command(root, office=None):
    command = sandbox_command(root, ["/venv/bin/python", "-m", "document_identity.template_conversion", "--child"])
    if office:
        # Package data has absolute links. Expose only the extracted runtime,
        # its Office data, and fonts; never the wider /mnt teaching directory.
        # /usr itself is read-only in the shared sandbox. Build its lib/share
        # mount points before binding their children so absent package paths
        # can be added without changing any host directory.
        additions = {"lib": {"libreoffice"}, "share": {"libreoffice", "liblangtag", "libexttextcat"}}
        usr = ["--dir", "/usr"]
        for child in sorted(Path("/usr").iterdir()):
            if child.name not in additions:
                usr += ["--ro-bind", str(child), str(child)]
                continue
            usr += ["--dir", str(child)]
            for item in sorted(child.iterdir()):
                if item.name not in additions[child.name]:
                    usr += ["--ro-bind", str(item), str(item)]
        at = command.index("/usr") - 1
        command[at:at + 3] = usr
        extra = ["--ro-bind", str(office), "/office"]
        for relative in ("usr/lib/libreoffice", "usr/share/libreoffice", "etc/libreoffice", "usr/share/liblangtag", "usr/share/libexttextcat"):
            if (office / relative).exists():
                extra += ["--ro-bind", str(office / relative), "/" + relative]
        # Debian's maintainer scripts normally publish these packaged defaults
        # into /etc. Bind them directly; never run scripts against the host.
        registry = office / "usr/lib/libreoffice/share/.registry"
        if registry.is_dir():
            extra += ["--ro-bind", str(registry), "/etc/libreoffice/registry"]
        command[command.index("--"):command.index("--")] = extra
    return command


def convert_template(files, entrypoint=None):
    """Return immutable sources, normalized PDF, ordered page images and evidence."""
    kind, selected = _sources(files, entrypoint)
    retained = dict(files)
    details = {"source_format": kind, "entrypoint": selected, "dpi": DPI,
               "reference_only": True, "physical_layout_qualified": False,
               "normalization": "visible-crop-and-rotation-raster-v1",
               "source_sha256": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}}
    office = _office_root() if kind == "docx" else None
    with tempfile.TemporaryDirectory(prefix="faculty-template-") as temporary:
        root = Path(temporary)
        source = files[selected]
        if kind == "latex":
            from .compiler import compile_sources
            toolchain = os.environ.get("FACULTY_TEXLIVE_ROOT")
            if not toolchain:
                local = Path(__file__).resolve().parents[1] / ".tools/texlive"
                if (local / "usr/bin/pdflatex").is_file():
                    toolchain = str(local)
            source, compiled, result = compile_sources(files, entrypoint=selected, toolchain_root=toolchain)
            details["compiler"] = result
            retained["_conversion/build.log"] = compiled["build.log"]
            retained["_conversion/facultyassignment.cls"] = compiled["facultyassignment.cls"]
        (root / ("input.docx" if kind == "docx" else "input.pdf")).write_bytes(source)
        (root / "request.json").write_text(json.dumps({"kind": "docx" if kind == "docx" else "pdf"}))
        env = _environment()
        env.update(HOME="/work/profile", XDG_CACHE_HOME="/work/cache", TMPDIR="/tmp")
        if office:
            env.update(LD_LIBRARY_PATH="/usr/lib/libreoffice/program:/office/usr/lib/x86_64-linux-gnu",
                       URE_BOOTSTRAP="vnd.sun.star.pathname:/usr/lib/libreoffice/program/fundamentalrc")
        _run(_command(root, office), env, 180)
        result = _result(root / "result.json")
        pages = [{**page, "png": (root / f"page-{page['number']}.png").read_bytes()} for page in result["pages"]]
        pdf = (root / "normalized.pdf").read_bytes()
        if len(pdf) + sum(len(page["png"]) for page in pages) > MAX_OUTPUT:
            raise ValueError("Converted template exceeds the 256 MiB output limit.")
        details.update(result["conversion"], page_count=len(pages), pdf_sha256=hashlib.sha256(pdf).hexdigest())
        # A reflowed template puts every answer area on the wrong page of the students' paper, and aligns badly
        # with their scans. Word's own page count is recorded in the file, so an unfaithful conversion is refused.
        word_pages = details.get("word_page_count")
        if kind == "docx" and word_pages and word_pages != len(pages):
            raise ValueError(f"Word laid this document out on {word_pages} page{'' if word_pages == 1 else 's'}, but converting it here produced {len(pages)}, "
                             "so its page breaks moved and answer areas would land on the wrong pages of the students’ papers. "
                             "In Word choose File → Save As → PDF and upload that PDF, or scan one blank copy and upload the scan.")
        return {"pdf": pdf, "sources": retained, "pages": pages, "conversion": details}


def _environment():
    return {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1",
            "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "TZ": "UTC", "LANG": "C.UTF-8"}


def _run(command, env, timeout):
    try:
        result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired as cause:
        raise ValueError("Template conversion exceeded its time limit.") from cause
    except OSError as cause:
        raise ValueError("The isolated template converter could not start.") from cause
    if result.returncode:
        raise ValueError("Isolated template conversion failed or exceeded its resource limits.")


def _result(path):
    if not path.is_file():
        raise ValueError("The isolated converter did not produce a result.")
    value = json.loads(path.read_text())
    if "error" in value:
        raise ValueError(value["error"])
    return value


def _convert_docx(root):
    import zipfile
    from xml.etree import ElementTree
    with zipfile.ZipFile(root / "input.docx") as archive:
        entries = archive.infolist()
        if len(entries) > 2000 or sum(item.file_size for item in entries) > 100 * 1024**2:
            raise ValueError("The DOCX exceeds its unpacked size limit.")
        names = {item.filename for item in entries}
        if not {"[Content_Types].xml", "word/document.xml"} <= names or any("vba" in name.casefold() for name in names):
            raise ValueError("Choose an ordinary DOCX document without macros.")
        document = ElementTree.fromstring(archive.read("word/document.xml"))
        equations = document.findall(".//{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath")
        if equations and not Path("/usr/lib/libreoffice/program/libsmlo.so").is_file():
            raise ValueError("The DOCX converter is missing LibreOffice Math. Rebuild it with manage_template_converter.py; equations must not be dropped.")
        # Word records the page count of its own layout. It is the only evidence here of how the printed paper broke.
        word_pages = None
        if "docProps/app.xml" in names:
            properties = ElementTree.fromstring(archive.read("docProps/app.xml"))
            application = properties.findtext("{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}Application") or ""
            counted = properties.findtext("{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}Pages") or ""
            if "word" in application.casefold() and counted.isdigit() and 1 <= int(counted) <= MAX_PAGES:
                word_pages = int(counted)
    profile = root / "profile"
    (profile / "user").mkdir(parents=True)
    # Highest macro security, no trusted locations. Each conversion gets a
    # disposable profile and a network-free namespace.
    (profile / "user/registrymodifications.xcu").write_text('''<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry"><item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item></oor:items>''')
    fonts = root / "fonts.conf"
    fonts.write_text('''<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig><include ignore_missing="yes">/etc/fonts/fonts.conf</include><dir>/usr/share/fonts</dir><dir>/office/usr/share/fonts</dir><dir>/office/usr/share/texmf/fonts/opentype</dir><cachedir>/work/cache/fontconfig</cachedir></fontconfig>''')
    env = {**os.environ, "SAL_USE_VCLPLUGIN": "svp", "FONTCONFIG_FILE": str(fonts)}
    # The distribution's launcher handles the first-profile restart (exit 81).
    # Invoke the ELF launcher directly; no host shell or profile is needed.
    executable = "/usr/lib/libreoffice/program/oosplash"
    version = subprocess.check_output([executable, "--version"], env=env, timeout=10).decode().strip()
    subprocess.run([executable, "-env:UserInstallation=file:///work/profile", "--headless", "--nologo", "--nodefault", "--norestore",
                    "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(root), str(root / "input.docx")],
                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=90)
    if not (root / "input.pdf").is_file():
        raise ValueError("LibreOffice could not convert this DOCX to PDF.")
    return {"office": version, "office_math": Path("/usr/lib/libreoffice/program/libsmlo.so").is_file(),
            "source_equation_objects": len(equations), "word_page_count": word_pages}


def _normalize(root):
    import io
    from importlib.metadata import version
    import pypdfium2 as pdfium
    from pypdf import PdfReader
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    source = (root / "input.pdf").read_bytes()
    if len(source) > MAX_OUTPUT:
        raise ValueError("The converted PDF exceeds its size limit.")
    reader = PdfReader(io.BytesIO(source), strict=True)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= MAX_PAGES:
        raise ValueError("Use an unencrypted template PDF with 1 to 100 pages.")
    pages, total, characters = [], 0, 0
    output = canvas.Canvas(str(root / "normalized.pdf"), invariant=1, pageCompression=1)
    with pdfium.PdfDocument(source) as document:
        for index in range(len(document)):
            page = document[index]
            try:
                width, height = page.get_size()
                if not all(math.isfinite(value) and value > 0 for value in (width, height)) or width * height * (DPI / 72)**2 > MAX_PIXELS:
                    raise ValueError("A template page exceeds the 30-megapixel raster limit.")
                textpage = page.get_textpage()
                try:
                    if textpage.count_chars() > MAX_TEXT - characters:
                        raise ValueError("Template native text exceeds its size limit.")
                    text = textpage.get_text_bounded(*page.get_bbox())
                    characters += len(text)
                    if characters > MAX_TEXT:
                        raise ValueError("Template native text exceeds its size limit.")
                finally:
                    textpage.close()
                bitmap = page.render(scale=DPI / 72)
                try:
                    image = bitmap.to_pil().convert("RGB")
                finally:
                    bitmap.close()
                image.save(root / f"page-{index + 1}.png", format="PNG")
                total += (root / f"page-{index + 1}.png").stat().st_size
                if total > MAX_OUTPUT:
                    raise ValueError("Template page images exceed the output limit.")
                output.setPageSize((width, height))
                output.drawImage(ImageReader(image), 0, 0, width=width, height=height)
                output.showPage()
                pages.append({"number": index + 1, "width_pt": width, "height_pt": height,
                              "width_px": image.width, "height_px": image.height, "text": text})
                image.close()
            finally:
                page.close()
    output.save()
    if total + (root / "normalized.pdf").stat().st_size > MAX_OUTPUT:
        raise ValueError("Converted template exceeds the 256 MiB output limit.")
    return {"pages": pages, "conversion": {"pdfium": version("pypdfium2"), "reportlab": version("reportlab"),
            "network": False, "native_text_characters": characters, "native_text_truncated": False,
            "limits": {"source_bytes": MAX_INPUT, "pages": MAX_PAGES, "page_pixels": MAX_PIXELS,
                       "text_characters": MAX_TEXT, "output_bytes": MAX_OUTPUT, "wall_seconds": 180}}}


def _child(root):
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (150, 150))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT, MAX_OUTPUT))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    request = json.loads((root / "request.json").read_text())
    office = _convert_docx(root) if request["kind"] == "docx" else None
    result = _normalize(root)
    if office:
        result["conversion"].update(office)
    return result


if __name__ == "__main__":
    root = Path("/work")
    try:
        (root / "result.json").write_text(json.dumps(_child(root)))
    except Exception as error:
        message = str(error) if type(error) is ValueError else "The template could not be converted. Check the document and installed converter."
        (root / "result.json").write_text(json.dumps({"error": message}))
