# KaTeX 0.18.7

MIT-licensed browser renderer, bundled from the official npm package.
See LICENSE and manifest.json for provenance, package integrity and asset hashes.
The ES module is renamed from katex.mjs to katex.js for Faculty’s asset snapshot.
CSS font sources retain WOFF2 only; all fonts are served locally. No build step.
Three DOM style-attribute assignments (pmb, color box, error text) use equivalent
CSSOM property writes to respect Faculty’s existing CSP. No trust policy changes.
Upstream options: https://katex.org/docs/options
