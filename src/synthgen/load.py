"""Load a patient's generated records into the Genomics Operations MongoDB.

Replaces only this patient's records: for each collection (Patients, Tests, Variants,
MolecConseq), deletes documents with this patientID, then inserts the generated ones.
Dates (testDate, birthdate) are stored as dates, as in the existing records. BEDs and other
patients are not touched. Connection settings come from .env (MONGO_URI, MONGO_DB).

Usage:
    python -m synthgen.load mongo luad_tcga_pan_can_atlas_2018 TCGA-J2-8194
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import pymongo
from dotenv import dotenv_values

COLLECTIONS = ("Patients", "Tests", "Variants", "MolecConseq")
DATE_FIELDS = ("testDate", "birthdate")


def to_dates(doc: dict) -> dict:
    for f in DATE_FIELDS:
        if isinstance(doc.get(f), str) and doc[f]:
            doc[f] = dt.datetime.fromisoformat(doc[f])
    return doc


def load_mongo(out_root: Path, study_id: str, patient_id: str, env_file: Path = Path(".env")) -> dict:
    env = dotenv_values(env_file)
    client = pymongo.MongoClient(env["MONGO_URI"], serverSelectionTimeoutMS=30000)
    db = client[env.get("MONGO_DB") or "FHIRGenomicsData"]
    folder = out_root / study_id / patient_id / "mongo"
    summary = {}
    for name in COLLECTIONS:
        docs = [to_dates(d) for d in json.loads((folder / f"{name}.json").read_text())]
        if any(d.get("patientID") != patient_id for d in docs):
            raise SystemExit(f"{name}.json has records for another patient; refusing to load")
        deleted = db[name].delete_many({"patientID": patient_id}).deleted_count
        inserted = len(db[name].insert_many(docs).inserted_ids) if docs else 0
        summary[name] = {"deleted": deleted, "inserted": inserted}
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("target", choices=["mongo"])
    parser.add_argument("study_id")
    parser.add_argument("patient_id")
    parser.add_argument("--out", type=Path, default=Path("data/output"))
    args = parser.parse_args(argv)
    for name, counts in load_mongo(args.out, args.study_id, args.patient_id).items():
        print(f"{name:12} deleted {counts['deleted']:4}  inserted {counts['inserted']:4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
