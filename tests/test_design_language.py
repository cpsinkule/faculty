"""DESIGN-LANGUAGE (docs/decisions/design-language.md): the screen has one appearance and one desktop layout.

Static checks over static/: no stylesheet references a removed Lambda token or a dark/light theme switch; no stylesheet
or script keeps a phone or tablet layout (requirement, 2026-10-03); the tokens are Ledger's
(2026-10-04), the screen is flat and the Ledger components load last; no painting remains; the bundled fonts are the files
their README describes.
"""
import hashlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
# The app's minimum width (layout.css `html { min-width }`): nothing below it is a layout the app has.
DESKTOP_MIN = 1180
REMOVED_TOKENS = re.compile(
    r"var\(\s*--(l-[a-z0-9-]+|neon-[a-z0-9-]+|edge-[a-z0-9-]+|wire-[a-z0-9-]+|button-edge-shadow|text-edge-shadow"
    r"|sidebar-slash|header-wire[a-z0-9-]*|home-attention-[a-z0-9-]+)\b")
# A viewport width condition: one query of a @media list that is not print-only and names a width. A print-only query
# (`print and (max-width: …)`) is the paper's width, and a container query measures a component's own box (a narrow
# column on a desktop page): neither is a phone or tablet tier.
MEDIA = re.compile(r"@media([^{]*)\{")
WIDTH = re.compile(r"\((?:max|min)-width:\s*(\d+)px\)")


def narrow_queries(css):
    for match in MEDIA.finditer(css):
        for query in match.group(1).split(","):
            if query.strip().startswith("print"):
                continue
            for width in WIDTH.findall(query):
                if int(width) < DESKTOP_MIN:
                    yield f"@media{match.group(1).rstrip()}"


# A width condition in a script (a matchMedia call, or a query string kept in a constant for one).
SCRIPT_WIDTH = re.compile(r"[\"'`][^\"'`]*?\((?:max|min)-width:\s*(\d+)px\)")


def stylesheets():
    return sorted(path for path in STATIC.glob("*.css"))


def scripts():
    return sorted(path for path in STATIC.glob("*.js"))


def strip_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


class OneAppearanceTests(unittest.TestCase):
    def test_no_removed_lambda_token_is_referenced(self):
        found = [f"{path.name}: {match.group(0)}" for path in stylesheets()
                 for match in REMOVED_TOKENS.finditer(strip_comments(path.read_text()))]
        found += [f"{path.name}: {match.group(0)}" for path in scripts()
                  for match in REMOVED_TOKENS.finditer(path.read_text())]
        self.assertEqual(found, [])

    def test_no_theme_switch_remains(self):
        css = [f"{path.name}: {line.strip()}" for path in stylesheets()
               for line in strip_comments(path.read_text()).splitlines()
               if "data-theme" in line or "prefers-color-scheme" in line]
        self.assertEqual(css, [])
        js = [path.name for path in scripts() if re.search(r"dataset\.theme|data-theme-choice|faculty\.color-theme", path.read_text())]
        self.assertEqual(js, [])
        index = (STATIC / "index.html").read_text()
        for gone in ("theme.js", "workspace-dark.css", "header-shimmer", "data-theme"):
            self.assertNotIn(gone, index)
        for gone in ("theme.js", "workspace-dark.css", "header-shimmer.css", "header-shimmer.js", "wireframe.js"):
            self.assertFalse((STATIC / gone).exists(), gone)

    def test_the_one_appearance_is_light(self):
        tokens = (STATIC / "design-tokens.css").read_text()
        self.assertIn("color-scheme: light", tokens)
        self.assertNotIn("color-scheme: dark", tokens)


class DesktopOnlyTests(unittest.TestCase):
    def test_no_stylesheet_keeps_a_phone_or_tablet_layout(self):
        narrow = [f"{path.name}: {query}" for path in stylesheets() for query in narrow_queries(strip_comments(path.read_text()))]
        self.assertEqual(narrow, [])

    def test_no_script_branches_on_a_phone_width(self):
        narrow = [f"{path.name}: {match.group(0)}" for path in scripts()
                  for match in SCRIPT_WIDTH.finditer(path.read_text())
                  if int(match.group(1)) < DESKTOP_MIN]
        self.assertEqual(narrow, [])

    def test_the_layout_sets_the_desktop_minimum(self):
        layout = (STATIC / "layout.css").read_text()
        self.assertIn(f"min-width: {DESKTOP_MIN}px", layout)
        self.assertNotIn("data-menu-toggle", (STATIC / "index.html").read_text())


class LedgerTests(unittest.TestCase):
    """DESIGN-LANGUAGE "Ledger" (requirement, 2026-10-04): the tokens hold Ledger's values, the screen is
    flat (no paper grain), every face is the bundled sans, and the Ledger components load after every older sheet."""

    def tokens(self):
        return dict(re.findall(r"^\s*(--[a-z0-9-]+):\s*([^;]+);", (STATIC / "design-tokens.css").read_text(), re.M))

    def test_the_tokens_are_ledger(self):
        tokens = self.tokens()
        expected = {"--canvas": "#f3f2ec", "--surface": "#ffffff", "--ink": "#101010", "--accent-fill": "#ff6a1a",
                    "--needs-fill": "var(--accent-fill)", "--danger-fill": "#d7263d", "--frame": "2px solid var(--ink)",
                    "--hard-shadow": "3px 3px 0 var(--ink)", "--sidebar-bg": "#101010", "--header-background": "#ffffff",
                    "--radius-control": "0", "--radius-panel": "0", "--radius": "0", "--head-weight": "800"}
        self.assertEqual({key: tokens.get(key) for key in expected}, expected)
        # Focus is slate, never the needs-you orange (requirement, 2026-10-04).
        self.assertEqual(tokens["--focus"], "var(--blue)")
        for name in ("--font-head", "--font-body", "--font-serif", "--font-display"):
            self.assertTrue(tokens[name].startswith('"Manrope"'), name)
        self.assertTrue(tokens["--font-num"].startswith('"Geist Mono"'))

    def test_the_screen_is_flat(self):
        for path in stylesheets():
            self.assertNotIn("paper-grain.png", strip_comments(path.read_text()), path.name)

    def test_the_ledger_components_load_last(self):
        index = (STATIC / "index.html").read_text()
        sheets = re.findall(r'<link rel="stylesheet" href="/static/([a-z0-9-]+\.css)', index)
        self.assertEqual(sheets[-1], "ledger.css", sheets)

    def test_no_common_ligatures(self):
        # DESIGN-LANGUAGE §3: Manrope's liga draws "(c)" as © and "->" as an arrow; off on every element, against `font` shorthands.
        typography = strip_comments((STATIC / "typography.css").read_text())
        self.assertRegex(typography, r"\*,\s*::before,\s*::after\s*\{\s*font-variant-ligatures:\s*no-common-ligatures\s*!important;\s*\}")
        for path in stylesheets():
            rules = re.findall(r"font-variant-ligatures:\s*([^;}]+)", strip_comments(path.read_text()))
            self.assertEqual([r for r in rules if "no-common-ligatures" not in r], [], path.name)
            self.assertNotRegex(strip_comments(path.read_text()), r"font-feature-settings:[^;}]*[\"']liga[\"']\s*(1|on)?\s*[;,}]", path.name)


class RetiredMachineryTests(unittest.TestCase):
    """DESIGN-LANGUAGE §7 (Ledger, 2026-10-04; the instructor: "remove the old plate and arc code too"): the Magazine edition's
    photograph plates, their generator, the paper and film grain and the sidebar arc are gone, and nothing names them."""

    def test_no_plate_or_arc_remains(self):
        for path in (STATIC / "plates.js", STATIC / "plates.css", STATIC / "sidebar-arc.js", STATIC / "plates", ROOT / "tools" / "plates"):
            self.assertFalse(path.exists(), path)
        pattern = re.compile(r"\bplates\.(js|css)|sidebar-arc|plate-image|plate-grain|--arc-\d|--plate-(ink|bar|muted|faint|next|rule|height)|"
                             r"film-grain|ink-cream|paper-grain|createPlates|mountSidebarArc|static/plates/")
        for path in stylesheets() + scripts() + [STATIC / "index.html"]:
            found = pattern.search(strip_comments(path.read_text()))
            self.assertIsNone(found, (path.name, found and found.group(0)))


class PaintingTests(unittest.TestCase):
    """DESIGN-LANGUAGE §9 withdrawn (requirement, 2026-10-04): no painting files, module or rules remain, and no
    page names one."""

    def test_no_painting_remains(self):
        self.assertFalse((STATIC / "plates" / "paintings").exists())
        self.assertFalse((STATIC / "paintings.js").exists())
        self.assertFalse((STATIC / "paintings.css").exists())
        text = "".join(path.read_text() for path in scripts() + stylesheets())
        for word in ("paintingMarkup", "paintings.js", "plates/paintings/", 'class="painting'):
            self.assertNotIn(word, text)
        self.assertNotIn("/_blob/", text)


class FontTests(unittest.TestCase):
    def test_bundled_fonts_match_their_readme(self):
        readme = (STATIC / "fonts" / "README.md").read_text()
        entries = re.findall(r"- `([^`]+\.woff2)` \(([\d,]+) bytes\).*?\n\s+SHA-256: `([0-9a-f]{64})`", readme)
        self.assertGreaterEqual(len(entries), 8)
        for name, size, digest in entries:
            data = (STATIC / "fonts" / name).read_bytes()
            self.assertEqual(len(data), int(size.replace(",", "")), name)
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest, name)
        faces = (STATIC / "fonts" / "fonts.css").read_text()
        for family in ("Manrope", "Geist Mono", "Berkeley Mono"):
            self.assertIn(f'font-family: "{family}"', faces)
        for license_file in ("manrope-OFL.txt", "geist-OFL.txt"):
            self.assertIn("SIL Open Font License", (STATIC / "fonts" / license_file).read_text())


if __name__ == "__main__":
    unittest.main()
