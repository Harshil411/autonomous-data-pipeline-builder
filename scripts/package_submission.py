"""Build the Canvas submission zip: source + README + UML PDF, never secrets.

    uv run python scripts/package_submission.py

Includes only files tracked by git (so var/, .venv and .env are excluded by construction),
then scans every file for anything shaped like a credential and refuses to write the zip
if it finds one.
"""

from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_RE = re.compile(
    r"(sk-ant-[A-Za-z0-9_\-]{20,}|sk-(?:proj-)?[A-Za-z0-9]{32,}|gh[pousr]_[A-Za-z0-9]{30,}"
    r"|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN (?:RSA |EC )?PRIVATE KEY-----)")
NEVER = {".env"}


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                         check=True).stdout.splitlines()
    return [ROOT / f for f in out if Path(f).name not in NEVER]


def main() -> int:
    files = tracked_files()
    leaks = []
    for f in files:
        try:
            text = f.read_text(errors="ignore")
        except (IsADirectoryError, FileNotFoundError):
            continue
        for m in SECRET_RE.finditer(text):
            leaks.append(f"{f.relative_to(ROOT)}: {m.group(0)[:12]}...")
    if leaks:
        print("Refusing to package - possible secrets found:\n  " + "\n  ".join(leaks))
        return 1
    if not (ROOT / "docs" / "uml" / "uml_diagrams.pdf").exists():
        print("docs/uml/uml_diagrams.pdf is missing; render the UML first (see README).")
        return 1
    # The README's setup step copies .env.example; a zip without it cannot be set up.
    if ROOT / ".env.example" not in files:
        print(".env.example is missing or not tracked by git; restore it before packaging.")
        return 1
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    target = dist / f"autonomous-data-pipeline-builder-{date.today():%Y%m%d}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            if f.is_file():
                z.write(f, Path("autonomous-data-pipeline-builder") / f.relative_to(ROOT))
    print(f"{target}  ({len(files)} files, {target.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
