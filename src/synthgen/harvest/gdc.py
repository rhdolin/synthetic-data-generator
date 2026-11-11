"""Harvest a TCGA patient's full clinical record from the NCI Genomic Data Commons (design D18).

Fetches the whole GDC case record (every clinical and biospecimen entity, not a chosen
subset) and saves it next to the patient's cBioPortal extract, with the GDC data release
it came from. The GDC API has no version pinning, so the saved response *is* the pin.

Usage:
    python -m synthgen.harvest.gdc luad_tcga_pan_can_atlas_2018 TCGA-J2-8194
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import requests

GDC_API = "https://api.gdc.cancer.gov"

# Every entity hanging off a case in the GDC clinical and biospecimen data model.
EXPAND = ",".join([
    "project",
    "demographic",
    "diagnoses",
    "diagnoses.treatments",
    "diagnoses.pathology_details",
    "diagnoses.annotations",
    "follow_ups",
    "follow_ups.molecular_tests",
    "follow_ups.other_clinical_attributes",
    "exposures",
    "family_histories",
    "annotations",
    "tissue_source_site",
    "samples",
    "samples.annotations",
    "samples.portions",
    "samples.portions.slides",
    "samples.portions.analytes",
    "samples.portions.analytes.aliquots",
])


def gdc_release() -> str:
    resp = requests.get(f"{GDC_API}/status", timeout=60)
    resp.raise_for_status()
    return resp.json().get("data_release", "unknown")


def fetch_case(submitter_id: str) -> dict:
    flt = {"op": "=", "content": {"field": "submitter_id", "value": submitter_id}}
    resp = requests.get(
        f"{GDC_API}/cases",
        params={"filters": json.dumps(flt), "expand": EXPAND, "format": "json", "size": 2},
        timeout=120,
    )
    resp.raise_for_status()
    hits = resp.json()["data"]["hits"]
    if len(hits) != 1:
        raise SystemExit(f"{submitter_id}: expected 1 GDC case, found {len(hits)}")
    return hits[0]


def harvest_case(study_id: str, patient_id: str, dest_root: Path) -> Path:
    out = dest_root / study_id / patient_id / "gdc_case.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "source": GDC_API,
        "dataRelease": gdc_release(),
        "retrieved": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "expand": EXPAND.split(","),
        "case": fetch_case(patient_id),
    }
    out.write_text(json.dumps(record, indent=2) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("study_id")
    parser.add_argument("patient_id")
    parser.add_argument("--dest", type=Path, default=Path("data/harvest"))
    args = parser.parse_args(argv)
    print(harvest_case(args.study_id, args.patient_id, args.dest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
