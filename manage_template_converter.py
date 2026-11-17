"""Extract Ubuntu's LibreOffice packages into an isolated NVMe runtime.

No system packages or services are changed. apt uses the host's configured,
authenticated distribution metadata; downloads and extracted files stay on /mnt.
Run: python manage_template_converter.py
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

DEFAULT_ROOT = Path("/mnt/nvme/Teaching/runtime/libreoffice-v2")
PACKAGES = ("libreoffice-writer-nogui", "libreoffice-math-nogui", "libreoffice-core-nogui", "fonts-liberation", "fonts-lmodern")


def setup(destination):
    destination = destination.resolve()
    if Path("/mnt") not in destination.parents:
        raise ValueError("The converter runtime must be on a /mnt drive.")
    if destination.exists():
        raise ValueError("The runtime directory already exists; preserve it and choose another --root for an upgrade.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    plan = subprocess.check_output(["apt-get", "-s", "--no-install-recommends", "install", *PACKAGES], text=True)
    packages = [f"{name}={version}" for name, version in re.findall(r"^Inst (\S+)(?: \[[^\]]+\])? \((\S+)", plan, re.M)]
    # Explicit roots are needed even when installed on the host; dependencies
    # already present in /usr are exposed read-only by the document sandbox.
    for name in PACKAGES:
        if not any(item.startswith(name + "=") for item in packages):
            candidate = subprocess.check_output(["apt-cache", "policy", name], text=True)
            version = re.search(r"Candidate: (\S+)", candidate).group(1)
            if version == "(none)":
                raise ValueError(f"No distribution package is available for {name}.")
            packages.append(f"{name}={version}")
    with tempfile.TemporaryDirectory(prefix="converter-setup-", dir=destination.parent) as temporary:
        work = Path(temporary)
        unpacked = work / "runtime"
        unpacked.mkdir()
        env = {**os.environ, "TMPDIR": str(work), "XDG_CACHE_HOME": str(work / "cache")}
        subprocess.run(["apt-get", "download", *packages], cwd=work, env=env, check=True)
        manifest = []
        for package in sorted(work.glob("*.deb")):
            subprocess.run(["dpkg-deb", "--extract", str(package), str(unpacked)], check=True, env=env)
            with package.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            manifest.append({"package": package.name, "sha256": digest})
        if not (unpacked / "usr/lib/libreoffice/program/soffice.bin").is_file():
            raise ValueError("The extracted packages did not contain LibreOffice Writer.")
        if not (unpacked / "usr/lib/libreoffice/program/libsmlo.so").is_file():
            raise ValueError("The extracted packages did not contain LibreOffice Math; equations would be lost.")
        (unpacked / "runtime-manifest.json").write_text(json.dumps({"packages": manifest}, indent=2) + "\n")
        unpacked.rename(destination)
    print(f"Template converter runtime: {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    setup(parser.parse_args().root)
