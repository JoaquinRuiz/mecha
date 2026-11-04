"""Assert that the built wheel ships the compiled frontend.

Usage: python scripts/check_wheel.py [dist_dir]

``src/mecha/static/`` is git-ignored, so this guards against a build backend that
selects files from VCS (or a missing ``vite build``) silently producing a wheel without UI.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

REQUIRED = (
    "mecha/static/index.html",
    "mecha/data/detect_patterns.toml",
    "mecha/store/migrations/001_initial.sql",
)
REQUIRED_PREFIX = "mecha/static/assets/"


def main() -> int:
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    wheels = sorted(dist.glob("mecha_keys-*.whl"))
    if not wheels:
        print(f"error: no mecha_keys wheel found in {dist}/", file=sys.stderr)
        return 1
    status = 0
    for wheel in wheels:
        names = set(zipfile.ZipFile(wheel).namelist())
        missing = [n for n in REQUIRED if n not in names]
        if not any(n.startswith(REQUIRED_PREFIX) for n in names):
            missing.append(REQUIRED_PREFIX + "*")
        if missing:
            print(f"error: {wheel.name} is missing {', '.join(missing)}", file=sys.stderr)
            status = 1
        else:
            count = sum(n.startswith("mecha/static/") for n in names)
            print(f"ok: {wheel.name} ships the frontend ({count} files under mecha/static/)")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
