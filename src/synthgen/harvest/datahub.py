"""Pin a cBioPortal study from the datahub repository (design doc D10).

Downloads every file in ``public/<study>/`` at a fixed datahub commit into
``<dest>/<study>/``, resolving Git LFS pointers through datahub's LFS server,
verifying each LFS object's SHA-256, and writing ``_snapshot.json`` with the
commit, download date and per-file size and hash.

Usage:
    python -m synthgen.harvest.datahub luad_tcga_pan_can_atlas_2018 \\
        --commit a8ea891da32add81fabaa8e744cf8cb72a8a0e66 --dest data/snapshots
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

import requests

REPO = "cBioPortal/datahub"
CONTENTS_API = f"https://api.github.com/repos/{REPO}/contents"
# From datahub's .lfsconfig
LFS_BATCH_URL = "https://nsssw8k94d.execute-api.us-east-1.amazonaws.com/objects/batch"
LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"
CHUNK = 1 << 20


def list_files(path: str, commit: str) -> list[dict]:
    """Recursively list files under a datahub path at a commit (GitHub contents API)."""
    resp = requests.get(f"{CONTENTS_API}/{path}", params={"ref": commit}, timeout=60)
    resp.raise_for_status()
    files = []
    for entry in resp.json():
        if entry["type"] == "dir":
            files.extend(list_files(entry["path"], commit))
        elif entry["type"] == "file":
            files.append(entry)
    return files


def parse_lfs_pointer(data: bytes) -> tuple[str, int] | None:
    if not data.startswith(LFS_POINTER_PREFIX):
        return None
    fields = dict(line.split(" ", 1) for line in data.decode().splitlines() if " " in line)
    return fields["oid"].removeprefix("sha256:"), int(fields["size"])


def lfs_download_url(oid: str, size: int) -> str:
    resp = requests.post(
        LFS_BATCH_URL,
        headers={
            "Accept": "application/vnd.git-lfs+json",
            "Content-Type": "application/vnd.git-lfs+json",
        },
        json={
            "operation": "download",
            "transfers": ["basic"],
            "objects": [{"oid": oid, "size": size}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    obj = resp.json()["objects"][0]
    if "error" in obj:
        raise RuntimeError(f"LFS error for {oid}: {obj['error']}")
    return obj["actions"]["download"]["href"]


def file_sha256(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            sha.update(chunk)
    return sha.hexdigest()


def download(url: str, target: Path) -> tuple[int, str]:
    """Stream url to target; return (size, sha256)."""
    sha = hashlib.sha256()
    size = 0
    tmp = target.with_suffix(target.suffix + ".part")
    with requests.get(url, stream=True, timeout=300) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(CHUNK):
                fh.write(chunk)
                sha.update(chunk)
                size += len(chunk)
    tmp.replace(target)
    return size, sha.hexdigest()


def pin_study(study_id: str, commit: str, dest: Path) -> dict:
    study_dir = dest / study_id
    study_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"public/{study_id}/"
    manifest = {
        "studyId": study_id,
        "source": f"https://github.com/{REPO}",
        "commit": commit,
        "downloaded": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "files": [],
    }
    for entry in list_files(f"public/{study_id}", commit):
        rel = entry["path"].removeprefix(prefix)
        target = study_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = requests.get(entry["download_url"], timeout=60)
        raw.raise_for_status()
        pointer = parse_lfs_pointer(raw.content)
        if pointer:
            oid, expected_size = pointer
            if target.exists() and target.stat().st_size == expected_size:
                size, sha = expected_size, file_sha256(target)
            else:
                print(f"  LFS  {rel} ({expected_size / 1e6:,.1f} MB)", flush=True)
                size, sha = download(lfs_download_url(oid, expected_size), target)
            if sha != oid or size != expected_size:
                raise RuntimeError(f"{rel}: checksum or size mismatch")
        else:
            target.write_bytes(raw.content)
            size, sha = len(raw.content), hashlib.sha256(raw.content).hexdigest()
        manifest["files"].append({"path": rel, "size": size, "sha256": sha, "lfs": bool(pointer)})
    (study_dir / "_snapshot.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("study_id")
    parser.add_argument("--commit", required=True, help="datahub commit SHA to pin")
    parser.add_argument("--dest", type=Path, default=Path("data/snapshots"))
    args = parser.parse_args(argv)
    manifest = pin_study(args.study_id, args.commit, args.dest)
    total = sum(f["size"] for f in manifest["files"])
    print(f"{len(manifest['files'])} files, {total / 1e9:.2f} GB -> {args.dest / args.study_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
