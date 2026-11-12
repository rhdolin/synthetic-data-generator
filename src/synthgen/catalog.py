"""Case catalog: who each synthesized patient is, clinically (one per study, all patients built so far).

Read from each patient's bundle, Mongo files, extract and build notes. Written to
data/output/<study>/cases.csv and cases.md.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

FIELDS = ["mrn", "name", "age_sex", "tumor_type", "stage", "extent", "course", "key_findings", "implications",
          "pdl1", "tmb", "msi", "images", "fit", "rule_filled", "messy"]


def _first(rs, pred):
    return next((r for r in rs if pred(r)), None)


def _display(cc: dict) -> str:
    c = (cc or {}).get("coding") or [{}]
    return c[0].get("display") or (cc or {}).get("text", "")


def tumor_type(primary: dict | None) -> str:
    """Coded cancer type, plus the source's subtype text when it adds something: 'Adenocarcinoma of lung — mixed subtype'."""
    if not primary:
        return ""
    coded = _display(primary.get("code"))
    text = (primary.get("code", {}).get("text") or "").strip()
    sub = text.replace("Lung Adenocarcinoma", "").strip(" ,()-").replace("NOS", "").strip(" ,()")
    sub = sub.removeprefix("Lung ").removeprefix("lung ").lower()
    return f"{coded} — {sub}" if sub else coded


def case_row(patient_dir: Path, harvest_dir: Path, board: dt.date | None = None) -> dict | None:
    bundles = list(patient_dir.glob("*-transaction-bundle.json"))
    if not bundles:
        return None
    rs = [e["resource"] for e in json.loads(bundles[0].read_text())["entry"]]
    notes_path = patient_dir / "notes.json"
    if notes_path.exists():
        notes = json.loads(notes_path.read_text())
    else:  # built before notes.json existed: take the notes from the latest run log
        notes = []
        for log in sorted(patient_dir.parent.glob("run-*/log.txt"), reverse=True):
            notes = [l.split("\t", 2)[2].strip() for l in log.read_text().splitlines()
                     if l.startswith(patient_dir.name + "\tnote\t")]
            if notes:
                break
    pt = _first(rs, lambda r: r["resourceType"] == "Patient")
    sr = _first(rs, lambda r: r["resourceType"] == "ServiceRequest")
    board = dt.date.fromisoformat(sr["occurrenceDateTime"][:10]) if sr else (board or dt.date.today())
    age = (board - dt.date.fromisoformat(pt["birthDate"])).days // 365
    name = pt["name"][0]
    primary = _first(rs, lambda r: r["id"].endswith("-primary-cancer"))
    stage = _display(((primary or {}).get("stage") or [{}])[0].get("summary")).replace(" (AJCC)", "")
    mets = [r for r in rs if r["resourceType"] == "Condition" and "-metastasis-" in r["id"]]
    status = (sr or {}).get("note", [{}])[0].get("text", "")
    extent = ("metastatic (" + ", ".join(_display(m["bodySite"][0]).replace(" structure", "").lower() for m in mets) + ")") if mets \
        else ("recurrent" if "recurrent" in status else "localized")
    meds = sorted((r for r in rs if r["resourceType"] == "MedicationRequest"),
                  key=lambda r: r["dosageInstruction"][0]["timing"]["repeat"]["boundsPeriod"]["start"])
    lines = sorted({r.get("groupIdentifier", {}).get("value") for r in meds})
    active = sorted({r["medicationCodeableConcept"].get("text", "").lower() for r in meds if r["status"] == "active"})
    if "progressing" in status:
        course = f"{len(lines)} line(s); progressing" + (f" on {'/'.join(active)}" if active else "")
    elif active:
        course = f"{len(lines)} line(s); on {'/'.join(active)}"
    elif lines:
        course = f"{len(lines)} line(s) completed"
    else:
        course = "no systemic therapy"
    variants = [r for r in rs if r["resourceType"] == "Observation" and r["meta"].get("profile", [""])[0].endswith("mcode-genomic-variant")]

    def var_label(r):
        comp = {c["code"]["coding"][0]["code"]: c for c in r.get("component", [])}
        genes = [_display(c["valueCodeableConcept"]) for c in r["component"] if c["code"]["coding"][0]["code"] == "48018-6"]
        if "molecular-consequence" in comp and "SO:0001565" in json.dumps(comp["molecular-consequence"]):
            return "-".join(genes) + " fusion"
        if "82155-3" in comp:
            cn = comp["82155-3"]["valueQuantity"]["value"]
            return f"{genes[0]} {'amp' if cn >= 3 else 'deep deletion'} (CN {cn})"
        p = comp.get("48005-3", {}).get("valueCodeableConcept", {}).get("coding", [{}])[0].get("code", "")
        return f"{genes[0]} {p.split(':')[-1]}".strip()
    imps = []
    for r in rs:
        if r["resourceType"] == "Observation" and "-tx-" in r["id"]:
            comp = {c["code"]["coding"][0]["code"]: c for c in r["component"]}
            drugs = " + ".join(_display(c["valueCodeableConcept"]) for c in r["component"] if c["code"]["coding"][0]["code"] == "51963-7")
            level = comp.get("93044-6", {}).get("valueCodeableConcept", {}).get("text", "?")[:1]
            kind = "resist." if "Resistance" in comp.get("predicted-therapeutic-implication", {}).get("valueCodeableConcept", {}).get("text", "") else ""
            imps.append(f"{drugs} ({level}{', ' + kind if kind else ''})")
    obs = lambda suffix: _first(rs, lambda r: r["id"].endswith(suffix))
    pd, tmb, msi = obs("-pdl1"), obs("-tmb"), obs("-msi")
    resources = harvest_dir / "data_resource_patient.txt"
    images = set()
    if resources.exists():
        for line in resources.read_text().splitlines()[1:]:
            rid = line.split("\t")[1] if "\t" in line else ""
            images.add({"IDC_SLIM": "slides", "IDC_OHIF_CT": "CT", "IDC_OHIF_PT": "PET", "IDC_OHIF_NM": "NM"}.get(rid, rid))
    complex_case = bool(mets or "recurrent" in status or "progressing" in status) and bool(lines) and bool(imps)
    rule = [n for n in notes if "rule-filled" in n or "synthetic" in n or "by rule" in n]
    return {
        "mrn": pt["identifier"][0]["value"], "name": f"{name['given'][0]} {name['family']}",
        "age_sex": f"{age} {pt['gender'][0].upper()}",
        "tumor_type": tumor_type(primary),
        "stage": stage, "extent": extent,
        "course": course, "key_findings": "; ".join(var_label(v) for v in variants),
        "implications": "; ".join(imps),
        "pdl1": f"{pd['valueQuantity']['value']}%" if pd else "", "tmb": tmb["valueQuantity"]["value"] if tmb else "",
        "msi": _display(msi["valueCodeableConcept"]) if msi else "",
        "images": ", ".join(sorted(i for i in images if i)), "fit": "MTB (complex)" if complex_case else "other scenarios",
        "rule_filled": "; ".join(sorted({n.split(":")[0].replace("note", "").strip() for n in rule})),
        "messy": "; ".join(n.removeprefix("messy: ") for n in notes if n.startswith("messy:")),
    }


def write_catalog(out_study: Path, harvest_study: Path) -> tuple[Path, int]:
    rows = []
    for d in sorted(p for p in out_study.iterdir() if p.is_dir() and not p.name.startswith("run-")):
        row = case_row(d, harvest_study / d.name)
        if row:
            rows.append(row)
    rows.sort(key=lambda r: (r["fit"] != "MTB (complex)", r["mrn"]))
    with open(out_study / "cases.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    md = [f"# Case catalog: {out_study.name}", "",
          f"{len(rows)} synthesized patients ({sum(r['fit'] == 'MTB (complex)' for r in rows)} complex, suited to the MTB). "
          "Names, dates, labs and report texts are synthetic; *rule-filled* lists values not in the source (e.g. metastasis "
          "sites, PD-L1 derived from RNA); *messy* lists deliberate gaps (D19). Generated "
          f"{dt.date.today().isoformat()}.", "",
          "| MRN | Name | Age/sex | Tumor type | Stage → extent | Course | Key findings | Implications (level) | PD-L1 / TMB / MSI | Images | Fit | Notes |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        notes = "; ".join(x for x in [("rule-filled: " + r["rule_filled"]) if r["rule_filled"] else "", ("messy: " + r["messy"]) if r["messy"] else ""] if x)
        md.append(f"| {r['mrn']} | {r['name']} | {r['age_sex']} | {r['tumor_type']} | {r['stage']} → {r['extent']} | {r['course']} | {r['key_findings']} | "
                  f"{r['implications']} | {r['pdl1']} / {r['tmb']} / {r['msi']} | {r['images']} | {r['fit']} | {notes} |")
    (out_study / "cases.md").write_text("\n".join(md) + "\n")
    return out_study / "cases.md", len(rows)
