"""Bound native PDF/image processing in an expendable, resource-limited process."""
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import base64


def sandbox_command(root, command, compiler=False, *, toolchain_root=None):
    """No network or workspace/catalog mounts; only toolchain, code and scratch."""
    args = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session",
            "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev",
            "--size", str(128 * 1024**2), "--tmpfs", "/tmp",
            "--ro-bind", str(Path(__file__).resolve().parent), "/app/document_identity",
            "--ro-bind", sys.prefix, "/venv", "--chdir", "/app"]
    if compiler:
        args.extend(["--ro-bind", str(root), "/inputs", "--size", str(128 * 1024**2), "--tmpfs", "/work",
                     "--bind", str(root / "results"), "/results"])
    else:
        args.extend(["--bind", str(root), "/work"])
    for path in ("/etc/fonts", "/var/lib/texmf", "/etc/texmf"):
        if Path(path).exists():
            args.extend(["--ro-bind", path, path])
    toolchain = toolchain_root if toolchain_root is not None else os.environ.get("FACULTY_TEXLIVE_ROOT")
    if toolchain:
        args.extend(["--ro-bind", str(Path(toolchain).resolve()), "/toolchain"])
    # uv/standalone Python installs live outside /usr; expose only that runtime.
    runtime = Path(sys.base_prefix).resolve()
    if not runtime.is_relative_to("/usr"):
        args.extend(["--ro-bind", str(runtime), str(runtime)])
    return args + ["--", *command]


def run(operation, content, manifest=None, timeout=180, *, layout_profile=None):
    with tempfile.TemporaryDirectory(prefix="faculty-document-") as temporary:
        root = Path(temporary)
        (root / "input.pdf").write_bytes(content)
        (root / "request.json").write_text(json.dumps({"operation": operation, "manifest": manifest, "layout_profile": layout_profile}))
        env = {"PATH": os.defpath, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            completed = subprocess.run(sandbox_command(root, ["/venv/bin/python", "-m", "document_identity.process", "/work"]),
                                       env=env, cwd=Path(__file__).resolve().parent.parent,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise ValueError("Document processing exceeded its time limit.") from error
        if completed.returncode or not (root / "result.json").is_file():
            raise ValueError("Document processing failed or exceeded its resource limits.")
        result = json.loads((root / "result.json").read_text())
        if "error" in result:
            raise ValueError(result["error"])
        if operation in ("stamp", "merge", "labels", "reorder"):
            return (root / "output.pdf").read_bytes(), result
        if operation == "roster_photos":
            return [{**item, "jpeg": (root / f"{item['index']}.jpg").read_bytes()} for item in result["photos"]]
        if operation == "look":
            return {"total": result["total"], "pages": [{"page": n, "png": (root / f"{n}.png").read_bytes()} for n in result["pages"]]}
        if operation == "heif":
            return (root / "picture.png").read_bytes(), tuple(result["size"])
        if operation == "notes_split":
            return [{**item, "pdf": (root / f"{item['position']}.pdf").read_bytes()} for item in result["parts"]]
        if operation == "extract":
            return [{**page, "pdf": (root / f"{page['index']}.pdf").read_bytes(),
                     "png": (root / f"{page['index']}.png").read_bytes()} for page in result["pages"]]
        return result


# A HEIC photo's file-type box brands, as absences.service.document_kind accepts them.
HEIF_BRANDS = (b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis")


def is_heif(content):
    """Whether `content` is a HEIC photo, by its first bytes."""
    return content[4:8] == b"ftyp" and content[8:12] in HEIF_BRANDS


def heif_picture(content, edge):
    """(PNG bytes, upright (width, height)) of a HEIC photo, decoded in the sandbox and scaled to fit `edge`."""
    return run("heif", content, {"edge": edge}, timeout=60)


def merge(items):
    """Join single-page PDFs; an item is page bytes or (bytes, rotation) to turn that page losslessly."""
    items = [item if isinstance(item, tuple) else (item, 0) for item in items]
    if not items or len(items) > 100 or sum(len(content) for content, _ in items) > 100 * 1024**2:
        raise ValueError("Assembly exceeds the PDF processing limit.")
    if any(rotation not in (0, 90, 180, 270) for _, rotation in items):
        raise ValueError("Page rotation must be a right angle.")
    return run("merge", b"", [{"pdf": base64.b64encode(content).decode(), "rotate": rotation} for content, rotation in items])[0]


def merge_pages(items):
    """Join up to a scan's worth of single pages, keeping each native merge bounded.

    DOCUMENTS: a grouped paper may have 200 sides and a sorted scan 1,500.
    Each chunk still goes through the ordinary single-page validation; only its
    verified output is passed to the bounded reordering worker.
    """
    if len(items) <= 100:
        return merge(items)
    if len(items) > 1500:
        raise ValueError("Assembly exceeds the PDF processing limit.")
    chunks = [items[start:start + 100] for start in range(0, len(items), 100)]
    sources = [base64.b64encode(merge(chunk)).decode() for chunk in chunks]
    order = [[index, page] for index, chunk in enumerate(chunks) for page in range(len(chunk))]
    return run("reorder", b"", {"sources": sources, "pages": order}, timeout=300)[0]


def main(root):
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (150, 150))
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024**2, 256 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    from . import media
    content = (root / "input.pdf").read_bytes()
    request = json.loads((root / "request.json").read_text())
    profile = request.get("layout_profile") or media.PROFILE
    try:
        if request["operation"] == "inspect":
            result = media.inspect_canonical(content)
        elif request["operation"] == "stamp":
            output = media.stamp_pdf(content, request["manifest"], profile)
            (root / "output.pdf").write_bytes(output)
            result = media.validate_print(output, request["manifest"], profile)
        elif request["operation"] == "labels":
            pages, header = request["manifest"]["pages"], request["manifest"].get("header") or {}
            label_media = request["manifest"].get("media") or media.DEFAULT_MEDIA
            output = media.labels_pdf(pages, header, profile, label_media)
            (root / "output.pdf").write_bytes(output)
            result = media.validate_labels(output, pages, profile, label_media)
        elif request["operation"] == "extract":
            span = request.get("manifest") or {}
            result = {"pages": media.extract_scan(content, root, span.get("first", 1), span.get("last"))}
        elif request["operation"] == "roster_photos":
            result = {"photos": media.roster_photos(content, root)}
        elif request["operation"] == "notes_outline":
            result = media.notes_outline(content)
        elif request["operation"] == "notes_split":
            result = {"parts": media.notes_split(content, request["manifest"] or {}, root)}
        elif request["operation"] == "look":
            result = media.look_pages(content, request.get("manifest"), root)
        elif request["operation"] == "heif":
            result = media.heif_picture(content, request.get("manifest"), root)
        elif request["operation"] == "text":
            result = media.page_text(content, request.get("manifest"))
        elif request["operation"] == "office_text":
            # The words of a Word or Excel file (ASSISTANT "FILES" › WORD AND EXCEL FILES): a zip of XML, read here only.
            from .office_text import office_text
            result = office_text(content)
        elif request["operation"] == "pages":
            result = {"total": media.scan_page_count(content)}
        elif request["operation"] == "reorder":
            # Already-verified output files, re-cut page by page: nothing is rendered or re-encoded.
            import io
            sources = request["manifest"]["sources"]
            order = request["manifest"]["pages"]
            if not 1 <= len(sources) <= 26 or not 1 <= len(order) <= 5000:
                raise ValueError("Room output exceeds the reordering limit.")
            readers = [media.PdfReader(io.BytesIO(base64.b64decode(content)), strict=True) for content in sources]
            writer = media.PdfWriter()
            for source, index in order:
                writer.add_page(readers[source].pages[index])
            writer.add_metadata({**{key: value for key, value in (readers[0].metadata or {}).items() if str(key).startswith("/Faculty")}, "/FacultyRoomExport": "seat-order"})
            with (root / "output.pdf").open("wb") as handle:
                writer.write(handle)
            result = {"pages": len(writer.pages)}
        elif request["operation"] == "merge":
            import io
            writer = media.PdfWriter()
            for item in request["manifest"]:
                content, rotate = item["pdf"], item.get("rotate", 0)
                if rotate not in (0, 90, 180, 270):
                    raise ValueError("Page rotation must be a right angle.")
                reader = media.PdfReader(io.BytesIO(base64.b64decode(content)), strict=True)
                if len(reader.pages) != 1:
                    raise ValueError("Assembly inputs must each represent one observed page.")
                page = reader.pages[0]
                if rotate:
                    page.rotate(rotate)  # /Rotate only: the scanned content is not re-encoded
                writer.add_page(page)
            with (root / "output.pdf").open("wb") as handle:
                writer.write(handle)
            result = {"pages": len(writer.pages)}
        else:
            raise ValueError("Unknown processing operation.")
    except Exception as error:
        # Never forward parser exceptions containing file content or payloads.
        result = {"error": str(error) if isinstance(error, ValueError) and type(error).__module__ == "builtins" else "Document parsing or validation failed."}
    (root / "result.json").write_text(json.dumps(result))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
