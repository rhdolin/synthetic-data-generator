"""Extract one patient's data from a pinned study snapshot (design doc D10).

Every per-patient data file in the snapshot is filtered to the patient:

* **row files** (clinical, timelines, mutations, SVs, segments, resources, gene panel):
  rows whose patient or sample ID column matches;
* **matrix files** (CNA, expression, RPPA, methylation, arm-level CNA, ancestry):
  the identifier columns plus the patient's sample column(s).

Comment header rows (``#...``) are kept, so each extracted file has the same format as
its source. Study-level files (``meta_*``, ``case_lists/``, resource definitions, README,
LICENSE) and the legacy normal-tissue files are not copied; they stay in the snapshot.
Writes ``_extract.json`` with the snapshot commit, the files written and their row counts.

Usage:
    python -m synthgen.harvest.extract luad_tcga_pan_can_atlas_2018 TCGA-J2-8194
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path

ROW_ID_COLUMNS = ("PATIENT_ID", "SAMPLE_ID", "Sample_Id", "Tumor_Sample_Barcode", "ID")
SKIP_PREFIXES = ("meta_", "data_normals_", "data_resource_definition", "_", "data_methylation")  # methylation: not ported (D10), 1.9 GB


def read_header(path: Path) -> tuple[list[str], list[str]]:
    """Return (comment lines, column names)."""
    comments = []
    with open(path, newline="") as fh:
        for line in fh:
            if line.startswith("#"):
                comments.append(line)
            else:
                return comments, line.rstrip("\r\n").split("\t")
    return comments, []


def sample_ids_for(snapshot: Path, patient_id: str) -> list[str]:
    path = snapshot / "data_clinical_sample.txt"
    with open(path, newline="") as fh:
        rows = csv.DictReader((l for l in fh if not l.startswith("#")), delimiter="\t")
        return [r["SAMPLE_ID"] for r in rows if r["PATIENT_ID"] == patient_id]


def all_sample_ids(snapshot: Path) -> set[str]:
    with open(snapshot / "data_clinical_sample.txt", newline="") as fh:
        rows = csv.DictReader((l for l in fh if not l.startswith("#")), delimiter="\t")
        return {r["SAMPLE_ID"] for r in rows}


def extract_file(src: Path, dst: Path, ids: set[str], study_samples: set[str]) -> tuple[str, int]:
    """Filter src to the patient; return (kind, data rows written)."""
    comments, header = read_header(src)
    sample_cols = [i for i, c in enumerate(header) if c in study_samples]
    dst.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(src, newline="") as fin, open(dst, "w", newline="") as fout:
        for line in comments:
            fout.write(line)
        lines = (l for l in fin if not l.startswith("#"))
        next(lines)  # header
        if sample_cols:  # matrix: keep identifier columns + this patient's sample columns
            keep = [i for i in range(len(header)) if i not in sample_cols or header[i] in ids]
            fout.write("\t".join(header[i] for i in keep) + "\n")
            for line in lines:
                cells = line.rstrip("\r\n").split("\t")
                fout.write("\t".join(cells[i] if i < len(cells) else "" for i in keep) + "\n")
                written += 1
            return "matrix", written
        id_cols = [header.index(c) for c in ROW_ID_COLUMNS if c in header]
        if not id_cols:
            raise ValueError(f"{src.name}: no patient/sample ID column found")
        fout.write("\t".join(header) + "\n")
        for line in lines:
            cells = line.rstrip("\r\n").split("\t")
            if any(i < len(cells) and cells[i] in ids for i in id_cols):
                fout.write(line if line.endswith("\n") else line + "\n")
                written += 1
        return "rows", written


def extract_patient(snapshot_root: Path, study_id: str, patient_id: str, dest_root: Path, verbose: bool = False) -> dict:
    snapshot = snapshot_root / study_id
    snap_manifest = json.loads((snapshot / "_snapshot.json").read_text())
    samples = sample_ids_for(snapshot, patient_id)
    if not samples:
        raise SystemExit(f"{patient_id} has no samples in {study_id}")
    ids = {patient_id, *samples}
    study_samples = all_sample_ids(snapshot)
    out = dest_root / study_id / patient_id
    manifest = {
        "studyId": study_id,
        "patientId": patient_id,
        "sampleIds": samples,
        "snapshotCommit": snap_manifest["commit"],
        "extracted": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "files": [],
    }
    for src in sorted(snapshot.iterdir()):
        if not src.is_file() or src.name.startswith(SKIP_PREFIXES):
            continue
        if not (src.name.startswith("data_") and src.suffix in (".txt", ".seg")):
            continue
        kind, rows = extract_file(src, out / src.name, ids, study_samples)
        manifest["files"].append({"file": src.name, "kind": kind, "rows": rows})
        if verbose:
            print(f"  {src.name:58} {kind:6} {rows:>8,} rows", flush=True)
    (out / "_extract.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("study_id")
    parser.add_argument("patient_id")
    parser.add_argument("--snapshots", type=Path, default=Path("data/snapshots"))
    parser.add_argument("--dest", type=Path, default=Path("data/harvest"))
    args = parser.parse_args(argv)
    m = extract_patient(args.snapshots, args.study_id, args.patient_id, args.dest, verbose=True)
    print(f"{len(m['files'])} files -> {args.dest / args.study_id / args.patient_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
