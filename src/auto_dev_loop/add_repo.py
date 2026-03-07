"""Repo onboarding logic for ``adl add``."""

from __future__ import annotations

import shutil
from pathlib import Path


def scaffold_files(source_dir: Path, target_dir: Path) -> list[str]:
    """Copy files from source_dir into target_dir, skipping existing.

    Returns list of filenames that were actually copied.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for src_file in sorted(source_dir.iterdir()):
        if not src_file.is_file():
            continue
        dest = target_dir / src_file.name
        if dest.exists():
            continue
        shutil.copy2(src_file, dest)
        copied.append(src_file.name)
    return copied
