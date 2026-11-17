"""Seeded software qualification; classroom hardware results are recorded separately.

Run: python -m document_identity.qualification --output test-results/document-qualification
"""
import argparse
import io
import json
import random
import tempfile
from importlib.metadata import version
from pathlib import Path

from PIL import Image, ImageFilter, ImageDraw

from .media import decode_page, render_pages
from .layouts import PROFILE, CORNERS_PROFILE, stamp_boxes


# The layouts that have fixtures to qualify; the 7 mm layout has none, so it is refused, not run on another layout's fixtures.
FIXTURE_SUFFIX = {PROFILE: "", CORNERS_PROFILE: "-corners"}


def jpeg(image, quality):
    encoded = io.BytesIO()
    image.save(encoded, format="JPEG", quality=quality, subsampling=0, optimize=False, progressive=False)
    encoded.seek(0)
    return Image.open(encoded).convert("L")


def qualify(output, profile=PROFILE):
    if profile not in FIXTURE_SUFFIX:
        raise ValueError(f"No qualification fixtures exist for layout {profile}; qualify {', '.join(FIXTURE_SUFFIX)}.")
    from app import create_app
    output.mkdir(parents=True, exist_ok=True)
    randomizer = random.Random(20260913)
    conditions = ["300dpi", "200dpi", "150dpi", "rotate90", "rotate180", "rotate270", "skew+3", "skew-3",
                  "blur0.6", "jpeg70", "contrast64-192", "combined200-90-jpeg70", "characterization-blur2", "characterization-jpeg20"]
    if profile == CORNERS_PROFILE:
        conditions += ["top-corner-only200", "bottom-corner-only200",
                       "characterization-top-corner-only150", "characterization-bottom-corner-only150"]
    cases = []
    with tempfile.TemporaryDirectory(prefix="faculty-qualification-") as temporary:
        app = create_app(temporary)
        app.test_client().post("/api/courses", json={"name": "Qualification", "code": "TEST 001", "term": "Other", "term_other": "Simulation", "year": 2026, "sections": [1]}, headers={"X-Teaching-Request": "1"})
        service = app.extensions["documents"]
        catalog = service.catalog()
        assignment = service.create_assignment(catalog["definitions"][0]["id"], "qualification", "Software qualification")
        for paper in ("letter", "a4"):
            fixtures = Path(__file__).parent / "fixtures"
            fixture = paper + FIXTURE_SUFFIX[profile]
            v = service.register_version(assignment["id"], paper, (fixtures / f"{fixture}.pdf").read_bytes(),
                                         {"main.tex": (fixtures / f"{fixture}.tex").read_bytes()}, {"fixture": fixture})
            batch = service.create_batch(v["id"], catalog["sections"][0]["id"], 2, paper)
            service.build_one()
            pdf = service.download(batch["id"])
            manifest = json.loads(service.download(batch["id"], manifest=True))["pages"]
            rasterizations = {dpi: list(render_pages(pdf, dpi)) for dpi in (150, 200, 300)}
            for index, entry in enumerate(manifest):
                base = rasterizations[300][index]
                variants = {
                    "300dpi": base, "200dpi": rasterizations[200][index], "150dpi": rasterizations[150][index],
                    "rotate90": base.transpose(Image.Transpose.ROTATE_90), "rotate180": base.transpose(Image.Transpose.ROTATE_180),
                    "rotate270": base.transpose(Image.Transpose.ROTATE_270),
                    "skew+3": base.rotate(3, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255),
                    "skew-3": base.rotate(-3, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255),
                    "blur0.6": base.filter(ImageFilter.GaussianBlur(radius=.6)), "jpeg70": jpeg(base, 70),
                    "contrast64-192": base.point([64 + round(value * 128 / 255) for value in range(256)]),
                    "combined200-90-jpeg70": jpeg(rasterizations[200][index].transpose(Image.Transpose.ROTATE_90), 70),
                    "characterization-blur2": rasterizations[150][index].filter(ImageFilter.GaussianBlur(radius=2)),
                    "characterization-jpeg20": jpeg(rasterizations[150][index], 20),
                }
                if profile == CORNERS_PROFILE:
                    for dpi in (150,200):
                        image = rasterizations[dpi][index]
                        boxes = stamp_boxes(image.width * 72 / dpi, image.height * 72 / dpi, profile)
                        for visible, (x,y,size) in zip(("bottom", "top"), boxes):
                            masked = image.copy()
                            ImageDraw.Draw(masked).rectangle((int(x*dpi/72)-2,int(image.height-(y+size)*dpi/72)-2,
                                                             int((x+size)*dpi/72)+2,int(image.height-y*dpi/72)+2), fill=255)
                            variants[f'{"characterization-" if dpi == 150 else ""}{visible}-corner-only{dpi}'] = masked
                order = list(conditions); randomizer.shuffle(order)
                for condition in order:
                    candidates = decode_page(variants[condition])
                    payloads = [candidate["payload"] for candidate in candidates]
                    expected = {"definition_id": catalog["definitions"][0]["id"], "section_id": catalog["sections"][0]["id"],
                                "term_id": catalog["sections"][0]["term_id"], "assignment_id": assignment["id"], "version_id": v["id"],
                                "batch_id": batch["id"], "copy_id": entry["copy_id"], "page_number": entry["page_number"]}
                    provenance = service.resolve_token(payloads[0], authenticated=True) if len(payloads) == 1 else None
                    exact_lookup = provenance is not None and all(provenance[key] == value for key, value in expected.items())
                    correct = payloads == [entry["token"]] and exact_lookup
                    accepted_wrong = any(service.resolve_token(value, authenticated=True) is not None and value != entry["token"] for value in payloads)
                    cases.append({"paper": paper, "copy": entry["copy_number"], "page": entry["page_number"],
                                  "condition": condition, "exact_recovery": correct, "exact_db_lookup": exact_lookup,
                                  "incorrect_accepted": accepted_wrong,
                                  "matching_mark_locations": sum(len(c.get("locations",[c["location"]])) for c in candidates if c["payload"] == entry["token"])})
                    if not correct:
                        name = f"failed-{paper}-{index+1}-{condition}"
                        variants[condition].save(output / f"{name}.png")
                        # Private diagnostic artifact, never ordinary logs or the
                        # checked-in summary. Tokens are synthetic test identities.
                        (output / f"{name}.json").write_text(json.dumps({"expected_token": entry["token"], "condition": condition,
                                                                       "paper": paper, "page": index + 1}, indent=2))
    summary = {condition: {"passed": sum(c["exact_recovery"] for c in cases if c["condition"] == condition),
                           "total": sum(c["condition"] == condition for c in cases)} for condition in conditions}
    required = [c for c in cases if not c["condition"].startswith("characterization")]
    result = {"seed": 20260913, "layout_profile": profile, "libraries": {name: version(name) for name in ("Pillow", "numpy", "pypdf", "pypdfium2", "zxing-cpp", "reportlab")},
              "physical_qualification": "not established by simulation; see the separate physical qualification record",
              "required_passed": all(c["exact_recovery"] for c in required), "incorrect_accepted": sum(c["incorrect_accepted"] for c in cases),
              "summary": summary, "cases": cases}
    (output / "simulation.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("test-results/document-qualification"))
    parser.add_argument("--profile", choices=tuple(FIXTURE_SUFFIX), default=PROFILE)
    args = parser.parse_args()
    result = qualify(args.output, args.profile)
    print(json.dumps({"required_passed": result["required_passed"], "incorrect_accepted": result["incorrect_accepted"], "summary": result["summary"]}, indent=2))
    return 0 if result["required_passed"] and not result["incorrect_accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
