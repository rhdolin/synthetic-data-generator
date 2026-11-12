"""Patient eligibility and features for a supported study (plan milestone 3; D23).

Reads the pinned snapshot only. Features are cached per study and datahub commit, since
computing them reads the whole mutation file.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from synthgen.genomic import civic

from synthgen.genomic.records import PROTEIN_CHANGING
FEATURES = ("actionable_variant", "fusion", "imaging", "radiology", "treatment_timeline", "recurrence_or_metastasis",
            "prior_cancer", "rna")


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader((l for l in fh if not l.startswith("#")), delimiter="\t"))


def reportable_genes() -> set[str]:
    return set((Path(__file__).parent / "clinical" / "reportable_genes.txt").read_text().split())


def ineligible_reason(p: dict) -> str | None:
    """None if eligible. Living patients with clinical data only (D23)."""
    status = p.get("OS_STATUS", "")
    if not status:
        return "no clinical follow-up data"
    if status.startswith("1"):
        return "patient died during follow-up (D23)"
    return None


def candidates(snapshot: Path, study: dict, cache_dir: Path = Path("data/cache")) -> list[dict]:
    cache = cache_dir / f"candidates-{snapshot.name}-{study['datahub_commit'][:8]}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    genes = reportable_genes()
    patients = {p["PATIENT_ID"]: p for p in _rows(snapshot / "data_clinical_patient.txt")}
    sample_of = {s["SAMPLE_ID"]: s["PATIENT_ID"] for s in _rows(snapshot / "data_clinical_sample.txt")}
    doids = civic.TUMOR_TYPE_DOIDS.get(study["oncotree"], set())
    evidence = {r["molecular_profile"] for r in civic.release_rows()
                if r["evidence_type"] == "Predictive" and r["evidence_status"] == "accepted"
                and r["evidence_direction"] == "Supports" and r["evidence_level"] in ("A", "B") and r["doid"] in doids}
    actionable = defaultdict(set)
    with open(snapshot / "data_mutations.txt", newline="") as fh:
        for m in csv.DictReader((l for l in fh if not l.startswith("#")), delimiter="\t"):
            if m["FILTER"] != "PASS" or m["Hugo_Symbol"] not in genes or m["Variant_Classification"] not in PROTEIN_CHANGING:
                continue
            gene, p = m["Hugo_Symbol"], (m.get("HGVSp_Short") or "").removeprefix("p.")
            names = civic.mutation_names(gene, p, m["Variant_Classification"], m.get("EXON") or m.get("Exon_Number", ""))
            names.discard(f"{gene} Mutation")  # "actionable" here means variant-specific evidence
            if any(civic.profile_matches(e, names) for e in evidence):
                actionable[sample_of.get(m["Tumor_Sample_Barcode"], m["Tumor_Sample_Barcode"][:12])].add(f"{gene} {p}")
    amp_profiles = {e.split(" ")[0] for e in evidence if e.endswith(" Amplification")}
    cna = snapshot / "data_cna.txt"
    if cna.exists() and amp_profiles:
        with open(cna, newline="") as fh:
            rd = csv.reader(fh, delimiter="\t")
            header = next(rd)
            for row in rd:
                if row[0] in amp_profiles and row[0] in genes:
                    for sid, v in zip(header[2:], row[2:]):
                        if v == "2":
                            actionable[sample_of.get(sid, sid[:12])].add(f"{row[0]} amplification")
    fusions = defaultdict(set)
    from synthgen.clinical.genomics import fusion_rows
    for f in fusion_rows(_rows(snapshot / "data_sv.txt"), genes):
        a, b = f.get("Site1_Hugo_Symbol"), f.get("Site2_Hugo_Symbol")
        if True:
            pid = sample_of.get(f["Sample_Id"], f["Sample_Id"][:12])
            fusions[pid].add(f"{a}-{b}")
            if {f"{a}::{b} Fusion", f"v::{b} Fusion", f"{a}::v Fusion"} & evidence:
                actionable[pid].add(f"{a}-{b} fusion")
    images = defaultdict(set)
    for r in _rows(snapshot / "data_resource_patient.txt"):
        images[r["PATIENT_ID"]].add(r["RESOURCE_ID"])
    regimens = defaultdict(set)
    for t in _rows(snapshot / "data_timeline_treatment.txt"):
        if not (t.get("AGENT") or "").startswith("Radiation"):
            regimens[t["PATIENT_ID"]].add(t["START_DATE"])
    events = Counter(s["PATIENT_ID"] for s in _rows(snapshot / "data_timeline_status.txt")
                     if any(x in s["STATUS"] for x in ("Recurrence", "Metastasis", "New Primary")))
    rna_list = (snapshot / "case_lists" / "cases_RNA_Seq_v2_mRNA.txt").read_text() if (snapshot / "case_lists" / "cases_RNA_Seq_v2_mRNA.txt").exists() else ""
    out = []
    for pid, p in sorted(patients.items()):
        img = images[pid]
        out.append({
            "patient": pid,
            "eligible": ineligible_reason(p) is None,
            "reason": ineligible_reason(p) or "",
            "stage": p.get("AJCC_PATHOLOGIC_TUMOR_STAGE", ""),
            "actionable": sorted(actionable[pid]),
            "fusions": sorted(fusions[pid]),
            "treatment_lines": len(regimens[pid]),
            "features": sorted(f for f, on in {
                "actionable_variant": bool(actionable[pid]), "fusion": bool(fusions[pid]),
                "imaging": bool(img), "radiology": bool(img - {"IDC_SLIM"}),
                "treatment_timeline": bool(regimens[pid]), "recurrence_or_metastasis": bool(events[pid]),
                "prior_cancer": (p.get("PRIOR_DX") or "").startswith("Yes"), "rna": f"{pid}-01" in rna_list,
            }.items() if on),
        })
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out))
    return out


def choose(cands: list[dict], count: int, require: list[str], prefer: list[str]) -> list[dict]:
    pool = [c for c in cands if c["eligible"] and all(r in c["features"] for r in require)]
    score = lambda c: (sum(p in c["features"] for p in prefer), c["treatment_lines"], len(c["features"]), c["patient"])
    return sorted(pool, key=score, reverse=True)[:count]
