"""Makes a Promptfoo JSON export (`eval -o FILE`) shareable inside the repo: local absolute paths
(Promptfoo records the config's `basePath`) become repository-relative.

    python3 evals/promptfoo/scrub.py FILE.json"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def scrub(text: str) -> str:
    return text.replace(str(HERE), "evals/promptfoo").replace(str(ROOT), ".").replace(str(Path.home()), "~")


if __name__ == "__main__":
    for name in sys.argv[1:]:
        p = Path(name)
        p.write_text(scrub(p.read_text(encoding="utf-8")), encoding="utf-8")
