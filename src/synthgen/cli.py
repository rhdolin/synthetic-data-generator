"""synthgen: synthetic mCODE (FHIR) + genomics (MongoDB) patients from cBioPortal studies.

Commands:
  studies    list the supported studies and their caveats
  patients   list a study's patients: eligibility and features, to choose from
  run        generate patients (by ID or by criteria): harvest, build, validate, load; writes a run report

Run "synthgen <command> --help" for a command's options.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import subprocess
import sys
import textwrap
from pathlib import Path

from synthgen import __version__, select
from synthgen.studies import SUPPORTED

DATA = Path("data")


def study_or_exit(study_id: str) -> dict:
    if study_id not in SUPPORTED:
        sys.exit(f"synthgen: study {study_id!r} is not supported. Supported: {', '.join(SUPPORTED)}.\n"
                 "Moving to a new study takes mapping and verification work first (see the README).")
    return SUPPORTED[study_id]


def ensure_snapshot(study_id: str, study: dict) -> Path:
    snapshot = DATA / "snapshots" / study_id
    if not (snapshot / "_snapshot.json").exists():
        from synthgen.harvest import datahub
        print(f"Pinning {study_id} at datahub {study['datahub_commit'][:8]} (first use; several GB)...", flush=True)
        datahub.pin_study(study_id, study["datahub_commit"], DATA / "snapshots")
    return snapshot


# --- studies -----------------------------------------------------------------------------
def cmd_studies(args) -> int:
    for sid, s in SUPPORTED.items():
        print(f"{sid}\n  {s['name']}\n  status: {s['status']}\n  verified: {textwrap.fill(s['verified'], 96, subsequent_indent='            ')}")
        print("  caveats:")
        for c in s["caveats"]:
            print(f"    - {c}")
    return 0


# --- patients ----------------------------------------------------------------------------
def cmd_patients(args) -> int:
    study = study_or_exit(args.study)
    cands = select.candidates(ensure_snapshot(args.study, study), study)
    shown = [c for c in cands if args.all or c["eligible"]]
    if args.require:
        shown = [c for c in shown if all(r in c["features"] for r in args.require)]
    print(f"{len(shown)} of {len(cands)} patients ({sum(c['eligible'] for c in cands)} eligible)")
    print(f"{'patient':14} {'stage':11} {'lines':>5}  {'actionable':30} features")
    for c in shown:
        flag = "" if c["eligible"] else f"  [not eligible: {c['reason']}]"
        print(f"{c['patient']:14} {c['stage']:11} {c['treatment_lines']:>5}  {', '.join(c['actionable'])[:30]:30} "
              f"{', '.join(c['features'])}{flag}")
    return 0


# --- run -----------------------------------------------------------------------------------
def validate(bundle: Path, validator: Path) -> tuple[int, int]:
    """(errors, known errors) using the HL7 validator; known = cross-version implication code, tx HGVS limits."""
    out = bundle.with_suffix(".validation.txt")
    out.unlink(missing_ok=True)
    proc = subprocess.run(["java", "-jar", str(validator), str(bundle), "-version", "4.0.1", "-ig", "hl7.fhir.us.mcode#4.0.0",
                           "-ig", "hl7.fhir.uv.genomics-reporting#3.0.0", "-output-style", "compact", "-output", str(out)],
                          capture_output=True, text=True, check=False)
    if not out.exists():  # never report "0 errors" for a validation that didn't run
        raise RuntimeError(f"validator did not run: {(proc.stderr or proc.stdout)[-300:]}")
    errors = known = 0
    for line in out.read_text().splitlines():
        if ": Error - " in line:
            errors += 1
            if any(k in line for k in ("predicted-therapeutic-implication", "varnomen", "hgvs-vs")):
                known += 1
    return errors, known


def load_fhir(bundle: Path, base: str) -> str:
    import requests
    r = requests.post(base, data=bundle.read_bytes(), timeout=300,
                      headers={"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"})
    r.raise_for_status()
    statuses = [e["response"]["status"] for e in r.json().get("entry", [])]
    return f"{len(statuses)} resources ({sum(s.startswith('201') for s in statuses)} created)"


def run_patient(study_id: str, pid: str, args, log) -> dict:
    from synthgen.clinical import tcga
    from synthgen.harvest import extract, gdc
    row = {"patient": pid, "status": "failed", "reason": "", "resources": "", "variants": "", "reported": "",
           "implications": "", "messy": "", "rule_filled": "", "validation_errors": "", "loaded": ""}
    harvest = DATA / "harvest" / study_id / pid
    if not (harvest / "_extract.json").exists():
        extract.extract_patient(DATA / "snapshots", study_id, pid, DATA / "harvest")
    if not (harvest / "gdc_case.json").exists():
        gdc.harvest_case(study_id, pid, DATA / "harvest")
    bundle, notes = tcga.build_patient(DATA / "harvest", study_id, pid, DATA / "output", args.tumor_board)
    for n in notes:
        log.write(f"{pid}\tnote\t{n}\n")
    entries = json.loads(bundle.read_text())["entry"]
    mongo = bundle.parent / "mongo"
    variants = json.loads((mongo / "Variants.json").read_text()) if (mongo / "Variants.json").exists() else []
    gen = next((n for n in notes if n.startswith("genomic:")), "")
    row.update({"status": "built", "resources": len(entries), "variants": len(variants),
                "reported": gen.split("reported ")[-1].split(";")[0] if "reported " in gen else "",
                "implications": gen.split("implications ")[-1] if "implications " in gen else "",
                "messy": "; ".join(n.removeprefix("messy: ") for n in notes if n.startswith("messy:")),
                "rule_filled": sum(("rule" in n or "synthetic" in n) for n in notes)})
    if args.validator:
        errors, known = validate(bundle, args.validator)
        row["validation_errors"] = f"{errors - known} (+{known} known)"
        if errors - known:
            row["status"] = "built, validation errors"
    loaded = []
    if args.load_fhir:
        loaded.append("FHIR " + load_fhir(bundle, args.load_fhir))
    if args.load_mongo:
        from synthgen import load
        counts = load.load_mongo(DATA / "output", study_id, pid)
        loaded.append("Mongo " + ", ".join(f"{k} {v['inserted']}" for k, v in counts.items()))
    if loaded:
        row["loaded"] = "; ".join(loaded)
        row["status"] = "loaded" if row["status"] == "built" else row["status"]
    return row


def cmd_run(args) -> int:
    study = study_or_exit(args.study)
    if args.validator and not args.validator.exists():
        sys.exit(f"synthgen: validator jar not found: {args.validator}\n"
                 "Download validator_cli.jar from https://github.com/hapifhir/org.hl7.fhir.core/releases (needs Java 17+).")
    snapshot = ensure_snapshot(args.study, study)
    cands = {c["patient"]: c for c in select.candidates(snapshot, study)}
    if args.patients:
        chosen, refused = [], []
        for pid in args.patients:
            c = cands.get(pid)
            if not c:
                refused.append((pid, "not in this study"))
            elif not c["eligible"]:
                refused.append((pid, c["reason"]))
            else:
                chosen.append(pid)
    else:
        chosen = [c["patient"] for c in select.choose(list(cands.values()), args.count, args.require or [], args.prefer or [])]
        refused = []
    if args.load_fhir == "env" or args.load_mongo:
        from dotenv import dotenv_values
        env = dotenv_values(".env")
        if args.load_fhir == "env":
            args.load_fhir = env.get("FHIR_SERVER_URL")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    report_dir = DATA / "output" / args.study / f"run-{stamp}"
    report_dir.mkdir(parents=True, exist_ok=True)
    rows = [{"patient": pid, "status": "refused", "reason": why} for pid, why in refused]
    with open(report_dir / "log.txt", "w") as log:
        for pid in chosen:
            print(f"{pid} ...", end=" ", flush=True)
            try:
                row = run_patient(args.study, pid, args, log)
            except Exception as exc:  # serious: skip the patient, keep going (design Q13)
                row = {"patient": pid, "status": "failed", "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
                log.write(f"{pid}\terror\t{exc!r}\n")
            rows.append(row)
            print(row["status"], flush=True)
    fields = ["patient", "status", "reason", "resources", "variants", "reported", "implications", "messy", "rule_filled",
              "validation_errors", "loaded"]
    with open(report_dir / "patients.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows(rows)
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    summary = (f"synthgen {__version__} run {stamp}\nstudy: {args.study} (datahub {study['datahub_commit'][:8]})\n"
               f"tumor board date: {args.tumor_board}\npatients: {len(rows)} — " + ", ".join(f"{k} {v}" for k, v in counts.items()) +
               f"\noutputs: {DATA / 'output' / args.study}/<patient>/\nreport: {report_dir}/patients.csv, log.txt\n")
    (report_dir / "summary.txt").write_text(summary)
    print("\n" + summary + "".join(f"  refused {r['patient']}: {r['reason']}\n" for r in rows if r["status"] == "refused"), end="")
    return 0 if not any(r["status"] == "failed" for r in rows) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="synthgen", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("studies", help="list supported studies and their caveats").set_defaults(func=cmd_studies)

    p = sub.add_parser("patients", help="list a study's patients, eligibility and features")
    p.add_argument("--study", required=True, help="a supported study id (see 'synthgen studies')")
    p.add_argument("--require", nargs="*", metavar="FEATURE", help=f"only patients with these features: {', '.join(select.FEATURES)}")
    p.add_argument("--all", action="store_true", help="include ineligible patients, with the reason")
    p.set_defaults(func=cmd_patients)

    r = sub.add_parser("run", help="generate patients: harvest, build, validate, load; writes a run report",
                       description="Generate patients by ID (--patients) or by criteria (--count/--require/--prefer). "
                                   "Outputs go to data/output/<study>/<patient>/; the run report to data/output/<study>/run-<time>/.")
    r.add_argument("--study", required=True, help="a supported study id (see 'synthgen studies')")
    who = r.add_mutually_exclusive_group(required=True)
    who.add_argument("--patients", nargs="+", metavar="ID", help="patient IDs, e.g. TCGA-J2-8194 (see 'synthgen patients')")
    who.add_argument("--count", type=int, help="how many patients to select by criteria")
    r.add_argument("--require", nargs="*", metavar="FEATURE", help=f"with --count: features every patient must have: {', '.join(select.FEATURES)}")
    r.add_argument("--prefer", nargs="*", metavar="FEATURE", help="with --count: features to rank patients by")
    r.add_argument("--tumor-board", type=dt.date.fromisoformat, default=dt.date(2026, 12, 1), metavar="YYYY-MM-DD",
                   help="tumor board date; all dates are shifted relative to it (default 2026-12-01)")
    r.add_argument("--validator", type=Path, metavar="JAR", help="HL7 FHIR validator jar (needs Java 17+) to validate each bundle, "
                   "e.g. data/cache/validator_cli.jar")
    r.add_argument("--load-fhir", nargs="?", const="env", metavar="URL",
                   help="load bundles to this FHIR server (no URL: FHIR_SERVER_URL from .env)")
    r.add_argument("--load-mongo", action="store_true", help="load Mongo records (MONGO_URI, MONGO_DB from .env); replaces each patient's records")
    r.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
