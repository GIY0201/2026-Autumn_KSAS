"""Fetch the pinned Tal--Karaman arXiv source without redistributing it."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
from urllib.request import Request, urlopen

SOURCE_URL = "https://arxiv.org/pdf/2207.13218v1"
EXPECTED_SHA256 = "3d485b0125c90d1ac9c0dd8b2f894c0e1753fbdcae473326eebf5ba466236067"
TARGET_PATH = Path(__file__).with_name("2207.13218v1.pdf")
CHUNK_SIZE = 1024 * 1024

assert len(EXPECTED_SHA256) == 64
assert CHUNK_SIZE > 0


def file_sha256(path: Path) -> str:
    """Return the lowercase SHA-256 digest of one file."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_source(path: Path) -> None:
    """Fail closed when the local source does not match the pinned artifact."""
    actual = file_sha256(path)
    if actual != EXPECTED_SHA256:
        raise RuntimeError(
            f"SHA-256 mismatch for {path}: expected {EXPECTED_SHA256}, got {actual}"
        )


def download_source(target: Path = TARGET_PATH) -> None:
    """Download to a temporary file, validate it, then install atomically."""
    if target.exists():
        validate_source(target)
        print(f"source already verified: {target}")
        return

    temporary = target.with_suffix(target.suffix + ".part")
    request = Request(SOURCE_URL, headers={"User-Agent": "KSAS-source-fetch/1.0"})
    try:
        with urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            while chunk := response.read(CHUNK_SIZE):
                output.write(chunk)
        validate_source(temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"source downloaded and verified: {target}")


def main() -> int:
    """Run download mode or validate an existing local source."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the existing source without downloading it",
    )
    arguments = parser.parse_args()

    if arguments.check:
        if not TARGET_PATH.is_file():
            raise FileNotFoundError(f"source is missing: {TARGET_PATH}")
        validate_source(TARGET_PATH)
        print(f"source verified: {TARGET_PATH}")
        return 0

    download_source()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
