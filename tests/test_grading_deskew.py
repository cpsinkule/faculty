"""Tilted scans (requirement, 2026-09-23: quizzes 10-15 degrees off): the page's own printed lines give its tilt, and a
clearly turned page is straightened before it is compared with a blank exam or its answer areas are cut. Pages that are
not turned take exactly the path they always took, with the scanner's own pixels.

Sheets fed upside down (requirement, 2026-09-25): every page is also tried turned half a turn, and the blank exam decides
which way up it is. Quarter turns do not happen with the department scanner and are not tried.

Paper printed at another size than its blank (requirement, 2026-09-25: Quiz 2 came out a few percent smaller than its PDF):
the blank is also tried at other sizes, and a page at its blank's own size is aligned exactly as before."""
import io
import json
import unittest

from PIL import Image, ImageDraw, ImageFont

from grading import deskew, extraction, version_match

REGION = {"x": .06, "y": .42, "width": .88, "height": .2}


def sheet(answer="x = 6", template=False):
    """A printed quiz page: a header rule, numbered questions in lines of text, and a boxed answer area."""
    image = Image.new("RGB", (1275, 1650), "white")
    draw, font = ImageDraw.Draw(image), ImageFont.load_default(size=26)
    draw.line((80, 120, 1195, 120), fill="black", width=4)
    draw.text((80, 70), "Quiz 3 - Linear equations                        Name: ______________", fill="black", font=font)
    for index in range(34):
        if 20 <= index <= 29:
            continue  # the answer area
        draw.text((80, 160 + index * 40), f"{index + 1}. Solve for x and show every step: {index + 2}x - {index + 3} = {2 * index + 7}.", fill="black", font=font)
    draw.rectangle((75, 700, 1200, 1000), outline="black", width=3)
    if not template:
        handwriting = ImageFont.load_default(size=44)
        draw.text((140, 760), "3x - 8 = 10", fill=(20, 20, 120), font=handwriting)
        draw.text((140, 860), answer, fill=(20, 20, 120), font=handwriting)
    return image


def png(image):
    data = io.BytesIO()
    image.save(data, "PNG")
    return data.getvalue()


def tilted(image, angle):
    """A sheet fed crooked: the paper turns inside the scanner's fixed frame, and what falls outside it is lost."""
    return image.rotate(angle, resample=Image.BICUBIC, fillcolor=(255, 255, 255))


def upside_down(image):
    """A sheet fed the other way round: exactly the same pixels, half a turn."""
    return image.transpose(Image.Transpose.ROTATE_180)


def printed_at(image, size, offset=(9, -7)):
    """Paper printed smaller or larger than its blank ("fit to page"): the content scaled and centred on the sheet, a
    little off centre, as a printer places it. Made without `deskew`, so the tests do not check it against itself."""
    content = image.resize((round(image.width * size), round(image.height * size)), Image.BICUBIC)
    page = Image.new(image.mode, image.size, "white")
    page.paste(content, ((image.width - content.width) // 2 + offset[0], (image.height - content.height) // 2 + offset[1]))
    return page


def region_on(size, offset=(9, -7), width=1275, height=1650):
    """Where REGION's box lands on paper `printed_at` that size, in pixels (left, top, right, bottom)."""
    x0, y0 = (width - round(width * size)) // 2 + offset[0], (height - round(height * size)) // 2 + offset[1]
    return (x0 + REGION["x"] * width * size, y0 + REGION["y"] * height * size,
            x0 + (REGION["x"] + REGION["width"]) * width * size, y0 + (REGION["y"] + REGION["height"]) * height * size)


def version_b():
    """Another version: the same kind of page with different lines."""
    other = Image.new("L", (1275, 1650), 255)
    draw, font = ImageDraw.Draw(other), ImageFont.load_default(size=26)
    for index in range(30):
        draw.text((80 + (index % 4) * 40, 200 + index * 45), f"Version B problem {index}: graph y = {index}x + 1", fill=0, font=font)
    return other


class Estimate(unittest.TestCase):
    def test_the_tilt_is_read_from_the_printed_lines_in_either_direction(self):
        page = sheet()
        for angle in (-15, -12, -5, 5, 10, 15):
            with self.subTest(angle=angle):
                found = deskew.plan(tilted(page, angle))
                self.assertLess(abs(found["angle"] + angle), .6, found)  # The rotation that undoes it.
                self.assertTrue(found["applied"])
                self.assertGreaterEqual(found["confidence"], deskew.CONFIDENT)

    def test_an_upright_page_and_a_page_with_nothing_printed_are_left_alone(self):
        self.assertFalse(deskew.plan(sheet())["applied"])
        blank = deskew.plan(Image.new("RGB", (1275, 1650), "white"))
        self.assertFalse(blank["applied"])
        self.assertLess(blank["confidence"], deskew.CONFIDENT)
        self.assertLessEqual(abs(blank["angle"]), deskew.LIMIT)
        scribbles = Image.new("RGB", (1275, 1650), "white")
        draw = ImageDraw.Draw(scribbles)
        for index in range(60):  # Handwriting alone: short strokes in every direction say nothing about the page's tilt.
            x, y = 100 + (index * 97) % 1000, 100 + (index * 211) % 1400
            draw.line([(x + (index * k * 37) % 80 - 40, y + (index * k * 53) % 80 - 40) for k in range(1, 7)], fill="black", width=4)
        self.assertFalse(deskew.plan(scribbles)["applied"])
        # Long straight strokes can mislead the reading, which is why a blank exam decides (`Crops`, below).

    def test_the_captured_area_excludes_what_straightening_filled(self):
        turned, captured = deskew.straighten(tilted(sheet(), 12), -12, (255, 255, 255))
        mask = captured.convert("L")
        self.assertEqual(turned.size, (1275, 1650))
        self.assertEqual(mask.getpixel((0, 0)), 0)  # A corner the scanner never saw.
        self.assertEqual(mask.getpixel((637, 825)), 255)


class Crops(unittest.TestCase):
    def prepare(self, angle):
        return extraction._prepare(png(tilted(sheet(), angle)), REGION, png(sheet(template=True)))

    def test_a_tilted_page_is_cut_straight_from_its_straightened_image_and_aligns_with_its_blank(self):
        upright = self.prepare(0)["metadata"]
        for angle in (-15, -10, 12, 15):
            with self.subTest(angle=angle):
                result = self.prepare(angle)
                meta = result["metadata"]
                self.assertTrue(meta["registered"], meta)
                self.assertEqual((meta["deskew"]["straightened"], meta["deskew"]["rule"]), (True, deskew.RULE))
                self.assertGreaterEqual(meta["alignment_quality"], .55)
                # The same answer area as on the upright page, within a few pixels, and upright: its box is not widened.
                for got, expected in zip(meta["crop_bounds"], upright["crop_bounds"]):
                    self.assertLess(abs(got - expected), 12, (meta["crop_bounds"], upright["crop_bounds"]))
                json.dumps(meta)  # What the sandbox writes.

    def test_an_upright_page_keeps_the_scanners_own_pixels(self):
        original = tilted(sheet(), 0)
        result = extraction._prepare(png(original), REGION, png(sheet(template=True)))
        meta = result["metadata"]
        self.assertFalse(meta["deskew"]["straightened"])
        crop = Image.open(io.BytesIO(result["images"]["original"]))
        self.assertEqual(crop.tobytes(), original.crop(meta["crop_bounds"]).tobytes())

    def test_a_straightened_page_is_used_only_when_it_aligns_better(self):
        # A wrong reading cannot make things worse where a blank exam can check it: the page as scanned competes.
        page = tilted(sheet(), 2)
        forced = {"rule": deskew.RULE, "angle": 9.0, "confidence": 50.0, "applied": True}
        from unittest.mock import patch
        with patch.object(deskew, "plan", return_value=forced):
            meta = extraction._prepare(png(page), REGION, png(sheet(template=True)))["metadata"]
        self.assertFalse(meta["deskew"]["straightened"])
        self.assertTrue(meta["registered"])

    def test_a_tilted_page_is_straightened_in_the_actual_document_sandbox(self):
        result = extraction.extract(png(tilted(sheet(), 12)), REGION, png(sheet(template=True)))
        self.assertTrue(result["metadata"]["registered"], result["metadata"])
        self.assertTrue(result["metadata"]["deskew"]["straightened"])
        self.assertTrue(result["images"]["original"].startswith(b"\x89PNG"))

    def test_the_cut_has_a_new_name_so_earlier_crops_are_never_reused_for_it(self):
        self.assertEqual(extraction.VERSION, "frq-extraction-v4")
        self.assertEqual(self.prepare(0)["metadata"]["version"], extraction.VERSION)


class UpsideDown(unittest.TestCase):
    blank = png(sheet(template=True))

    def test_a_sheet_fed_upside_down_is_cut_from_the_page_turned_upright_with_the_scanners_own_pixels(self):
        page = sheet()
        upright = extraction._prepare(png(page), REGION, self.blank)
        result = extraction._prepare(png(upside_down(page)), REGION, self.blank)
        meta = result["metadata"]
        self.assertTrue(meta["registered"], meta)
        self.assertEqual((meta["orientation"]["turned"], meta["orientation"]["rule"]), (True, deskew.TURN_RULE))
        self.assertGreaterEqual(meta["orientation"]["turned_quality"] - meta["orientation"]["upright_quality"], deskew.TURN_MARGIN)
        self.assertFalse(meta["deskew"]["straightened"])
        # A half turn is exact: the same answer area, pixel for pixel, as if the sheet had gone in the right way round.
        self.assertEqual(meta["crop_bounds"], upright["metadata"]["crop_bounds"])
        self.assertEqual(result["images"]["original"], upright["images"]["original"])
        json.dumps(meta)

    def test_an_upside_down_sheet_that_is_also_tilted_is_turned_and_straightened(self):
        upright = extraction._prepare(png(sheet()), REGION, self.blank)["metadata"]
        for angle in (-12, 10):
            with self.subTest(angle=angle):
                meta = extraction._prepare(png(upside_down(tilted(sheet(), angle))), REGION, self.blank)["metadata"]
                self.assertTrue(meta["registered"], meta)
                self.assertTrue(meta["orientation"]["turned"] and meta["deskew"]["straightened"], meta)
                for got, expected in zip(meta["crop_bounds"], upright["crop_bounds"]):
                    self.assertLess(abs(got - expected), 12, (meta["crop_bounds"], upright["crop_bounds"]))

    def test_a_page_the_right_way_up_is_never_turned_and_is_cut_exactly_as_before(self):
        for page in (sheet(), tilted(sheet(), 12)):
            meta = extraction._prepare(png(page), REGION, self.blank)["metadata"]
            self.assertFalse(meta["orientation"]["turned"], meta)
            self.assertLess(meta["orientation"]["turned_quality"], meta["orientation"]["upright_quality"])
        original = sheet()
        result = extraction._prepare(png(original), REGION, self.blank)
        self.assertEqual(result["images"]["original"], png(original.crop(result["metadata"]["crop_bounds"])))
        self.assertEqual(result["metadata"]["template_size"]["size"], 1)

    def test_without_a_blank_exam_nothing_is_turned_over(self):
        meta = extraction._prepare(png(upside_down(sheet())), REGION)["metadata"]
        self.assertEqual(meta["orientation"], {"rule": deskew.TURN_RULE, "turned": False, "upright_quality": None, "turned_quality": None})
        self.assertFalse(meta["registered"])

    def test_a_half_turn_needs_a_clear_lead(self):
        self.assertEqual(deskew.TURN_MARGIN, .06)  # Measured on real scans; see its comment before changing it.
        self.assertIs(deskew.turned_wins(.05, .25), True)  # A page that aligns poorly either way (a digital blank) still turns.
        import numpy
        self.assertIs(deskew.turned_wins(numpy.float64(.05), numpy.float64(.25)), True)  # Never numpy.True_, which JSON refuses.
        self.assertFalse(deskew.turned_wins(.3, .3 + deskew.TURN_MARGIN / 2))  # Not clearly better.
        self.assertFalse(deskew.turned_wins(.02, .04))  # Nothing on the page says which way up it is.
        self.assertFalse(deskew.turned_wins(.7, None))
        self.assertFalse(deskew.turned_wins(None, .7))

    def test_an_upside_down_sheet_is_cut_in_the_actual_document_sandbox(self):
        result = extraction.extract(png(upside_down(sheet())), REGION, self.blank)
        self.assertTrue(result["metadata"]["registered"], result["metadata"])
        self.assertTrue(result["metadata"]["orientation"]["turned"])


class Sizes(unittest.TestCase):
    blank = png(sheet(template=True))

    def cut(self, page):
        return extraction._prepare(png(page), REGION, self.blank)["metadata"]

    def assertCutAround(self, meta, expected, margin=.015):
        """The crop holds the answer area where the paper has it, with the usual outside-writing margin and a few pixels."""
        left, top, right, bottom = meta["crop_bounds"]
        self.assertLessEqual(left, expected[0]); self.assertLessEqual(top, expected[1])
        self.assertGreaterEqual(right, expected[2]); self.assertGreaterEqual(bottom, expected[3])
        self.assertLess(abs(top - (expected[1] - 1650 * margin)), 12, (meta["crop_bounds"], expected))
        self.assertLess(abs(bottom - (expected[3] + 1650 * margin)), 12, (meta["crop_bounds"], expected))

    def test_paper_printed_smaller_or_larger_than_its_blank_aligns_and_is_cut_where_the_paper_has_the_answer_area(self):
        for size in (.92, .95, 1.05):
            with self.subTest(size=size):
                meta = self.cut(printed_at(sheet(), size))
                self.assertTrue(meta["registered"], meta)
                self.assertLess(abs(meta["template_size"]["size"] - size), .011, meta["template_size"])
                self.assertGreaterEqual(meta["alignment_quality"] - meta["template_size"]["own_size_quality"], deskew.SIZE_GAIN)
                self.assertEqual(meta["template_size"]["rule"], deskew.SIZE_RULE)
                self.assertCutAround(meta, region_on(size))
                json.dumps(meta)

    def test_a_page_at_its_blanks_own_size_is_aligned_exactly_as_before(self):
        for page in (sheet(), tilted(sheet(), 12), upside_down(sheet())):
            meta = self.cut(page)
            self.assertEqual(meta["template_size"]["size"], 1, meta)
            self.assertEqual(meta["alignment_quality"], meta["template_size"]["own_size_quality"])

    def test_paper_printed_small_and_fed_upside_down_and_crooked_is_turned_straightened_and_resized(self):
        meta = self.cut(upside_down(tilted(printed_at(sheet(), .94), 8)))
        self.assertTrue(meta["registered"], meta)
        self.assertTrue(meta["orientation"]["turned"] and meta["deskew"]["straightened"], meta)
        self.assertLess(abs(meta["template_size"]["size"] - .94), .011, meta["template_size"])

    def test_another_size_is_used_only_when_it_aligns_clearly_better(self):
        # A size that aligns a little better than the blank's own is noise, not paper printed at another size.
        scores = {1: .60, .98: .60 + deskew.SIZE_GAIN / 3}
        found, own = deskew.search(lambda size, angle: (scores.get(size, .1) - abs(angle) / 100,), (-1, 0, 1))
        self.assertEqual((found, own), ((.60, 1, 0), (.60, 1, 0)))
        scores = {1: .30, .94: .70, .945: .72}
        found, own = deskew.search(lambda size, angle: (scores.get(size, .1) - abs(angle) / 100,), (-1, 0, 1))
        self.assertEqual((found[1], found[2], own[1]), (.945, 0, 1))
        self.assertEqual(deskew.resized(Image.new("L", (10, 10)), 1, 255).size, (10, 10))

    def test_paper_printed_smaller_is_cut_in_the_actual_document_sandbox(self):
        result = extraction.extract(png(printed_at(sheet(), .94)), REGION, self.blank)
        self.assertTrue(result["metadata"]["registered"], result["metadata"])
        self.assertLess(abs(result["metadata"]["template_size"]["size"] - .94), .011)


class Versions(unittest.TestCase):
    def test_a_tilted_paper_still_matches_its_own_blank_and_an_upright_one_scores_as_before(self):
        blank = sheet(template=True).convert("L")
        other = version_b()
        upright = sheet().convert("L")
        before = {"A": version_match._quality(upright, blank), "B": version_match._quality(upright, other)}
        # One page with nothing to straighten scores exactly as under the earlier matcher.
        self.assertEqual(version_match._quality(version_match._candidates(upright), blank), before["A"])
        for angle in (-12, 15):
            with self.subTest(angle=angle):
                page = version_match._candidates(tilted(sheet(), angle).convert("L"))
                self.assertEqual(len(page), 2)
                scores = {"A": version_match._quality(page, blank), "B": version_match._quality(page, other)}
                self.assertGreater(scores["A"], before["A"] - .08, scores)
                decided = version_match.decide({"versions": {k: {"mean": v} for k, v in scores.items()}})
                self.assertEqual(decided["matched"], "A", (scores, before))

    def test_a_tilted_paper_is_compared_in_the_actual_document_sandbox(self):
        pages = {1: png(tilted(sheet(), -13)), 2: png(tilted(sheet(answer="x = 8"), 11))}
        result = version_match.compare(pages, {"A": {1: png(sheet(template=True)), 2: png(sheet(template=True))}})
        self.assertEqual(result["matcher"], version_match.MATCHER)
        self.assertEqual(result["turned"], [])
        self.assertEqual(version_match.decide(result)["matched"], "A", result)

    def test_a_paper_with_a_sheet_fed_upside_down_matches_its_own_version_and_says_which_pages_were_turned(self):
        blank, other = png(sheet(template=True)), png(version_b())
        pages = {1: png(sheet()), 2: png(upside_down(sheet(answer="x = 8"))), 3: png(upside_down(tilted(sheet(), 9)))}
        result = version_match.compare(pages, {"A": {n: blank for n in pages}, "B": {n: other for n in pages}})
        self.assertEqual((result["turned"], result["orientation_rule"]), ([2, 3], deskew.TURN_RULE))
        decided = version_match.decide(result)
        self.assertEqual(decided["matched"], "A", result)
        # Each turned page scores like the upright one; the other version gains nothing from the extra way up.
        upright = version_match.compare({1: png(sheet())}, {"A": {1: blank}, "B": {1: other}})
        for number in ("2", "3"):
            self.assertGreater(result["versions"]["A"]["pages"][number], upright["versions"]["A"]["pages"]["1"] - .08, result)
            self.assertLess(result["versions"]["B"]["pages"][number], result["versions"]["A"]["pages"][number] - version_match.MARGIN, result)

    def test_paper_printed_smaller_than_its_blank_matches_its_own_version(self):
        blank, other = png(sheet(template=True)), png(version_b())
        pages = {1: png(printed_at(sheet(), .94)), 2: png(upside_down(printed_at(sheet(answer="x = 8"), .95)))}
        result = version_match.compare(pages, {"A": {n: blank for n in pages}, "B": {n: other for n in pages}})
        decided = version_match.decide(result)
        self.assertEqual((decided["matched"], result["turned"]), ("A", [2]), result)
        # Every page on its own clears the pass mark and leads the other version by the margin (a resized page is resampled,
        # so it does not score quite as high as a synthetic page at the blank's own size).
        for number in ("1", "2"):
            own, another = result["versions"]["A"]["pages"][number], result["versions"]["B"]["pages"][number]
            self.assertGreaterEqual(own, version_match.PASS, result)
            self.assertGreaterEqual(own - another, version_match.MARGIN, result)

    def test_the_matcher_has_a_new_name_and_remembers_the_earlier_ones(self):
        self.assertEqual((version_match.MATCHER, version_match.EARLIER), ("whole-paper-v4", ("whole-paper-v3", "whole-paper-v2", "whole-paper-v1")))


if __name__ == "__main__":
    unittest.main()
