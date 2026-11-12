"""Build a patient's mCODE transaction bundle from a TCGA PanCancer extract + GDC record.

Implements docs/mapping.md: "Clinical", "Treatments", "Clinical: synthetic content" and the rules in
"Colorectal" that apply to every study; reports, genomics and image links are added by clinical.genomics.

Usage:
    python -m synthgen.clinical.tcga luad_tcga_pan_can_atlas_2018 TCGA-J2-8194 --tumor-board 2026-12-01
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path

from synthgen import synthesize as syn
from synthgen.clinical import fhir as F


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader((l for l in fh if not l.startswith("#")), delimiter="\t"))


def blank(v) -> bool:
    return v is None or str(v).strip() in ("", "NA", "[Not Available]", "Not Reported", "not reported", "Unknown", "unknown")


class Builder:
    def __init__(self, extract_dir: Path, tumor_board: dt.date, snapshot: Path | None = None):
        self.dir = extract_dir
        self.snapshot = snapshot
        self.key_biomarkers: list[str] = []
        self.mongo: dict = {}
        self.pt = read_rows(extract_dir / "data_clinical_patient.txt")[0]
        self.sample = read_rows(extract_dir / "data_clinical_sample.txt")[0]
        self.treat = read_rows(extract_dir / "data_timeline_treatment.txt")
        self.acq = read_rows(extract_dir / "data_timeline_sample_acquisition.txt")
        gdc_path = extract_dir / "gdc_case.json"
        self.gdc = json.loads(gdc_path.read_text())["case"] if gdc_path.exists() else {}
        self.mrn = self.pt["PATIENT_ID"]
        self.notes: list[str] = []  # rule-filled or flagged choices, for the run report
        self.m1 = (self.pt.get("PATH_M_STAGE") or "").upper().startswith("M1")
        self.colorectal = self.sample.get("ONCOTREE_CODE") in F.COLORECTAL
        self.gdc_met_site: dict[int, str] = {}
        self.progressed: dict[int, str] = {}  # day -> site of an existing metastasis that progressed
        self.status = self.disease_events(read_rows(extract_dir / "data_timeline_status.txt"))
        self.key = self.mrn.lower()
        self.board = tumor_board
        last = max([int(self.pt.get("DAYS_LAST_FOLLOWUP") or 0)]
                   + [int(r["START_DATE"]) for r in self.status + self.treat if r.get("START_DATE")]
                   + [int(r["STOP_DATE"]) for r in self.treat if r.get("STOP_DATE")])
        self.last_day = last
        self.d0 = syn.diagnosis_date(self.mrn, tumor_board, last)
        self.messy = syn.messy_kinds(self.mrn)
        self.resources: list[dict] = []

    # --- helpers -------------------------------------------------------------------
    def D(self, day) -> str:
        return (self.d0 + dt.timedelta(days=int(day))).isoformat()

    def rid(self, suffix: str) -> str:
        return F.safe_id(f"mtb-{self.key}-{suffix}")

    def add(self, r: dict) -> dict:
        r.setdefault("subject", None)
        if r["subject"] is None:
            del r["subject"]
        self.resources.append(r)
        return r

    def gdc_primary(self) -> dict:
        for d in self.gdc.get("diagnoses", []):
            if d.get("diagnosis_is_primary_disease") or d.get("classification_of_tumor") == "primary":
                return d
        return {}

    STATUS_NAMES = {"Metastatic": "Distant Metastasis", "Locoregional Disease": "Locoregional Recurrence"}  # colorectal wording

    def disease_events(self, status: list[dict]) -> list[dict]:
        """cBioPortal status events (wording normalized), plus dated recurrences/progressions that only GDC records
        (decision, Bob 2026-11-12). Type unknown: progression if metastatic at diagnosis, else a recurrence."""
        for e in status:
            e["STATUS"] = self.STATUS_NAMES.get(e["STATUS"], e["STATUS"])
        known = [int(e["START_DATE"]) for e in status if any(x in e["STATUS"] for x in ("Metastasis", "Recurrence"))]
        for f in self.gdc.get("follow_ups", []):
            if f.get("progression_or_recurrence") != "Yes":
                continue
            day = f.get("days_to_recurrence") if f.get("days_to_recurrence") is not None else f.get("days_to_progression")
            if day is None:
                self.notes.append("GDC recurrence without a date: not placed on the timeline")
                continue
            day = int(day)
            if any(abs(day - k) <= 30 for k in known):
                continue
            kind = f.get("progression_or_recurrence_type")
            name = ("Locoregional Recurrence" if kind == "Locoregional"
                    else "Distant Metastasis" if kind == "Distant" or self.m1 else "Recurrence")
            status.append({"PATIENT_ID": self.mrn, "START_DATE": str(day), "STATUS": name})
            known.append(day)
            site = f.get("progression_or_recurrence_anatomic_site")
            if not blank(site):
                self.gdc_met_site[day] = site
            self.notes.append(f"{name.lower()} at day {day}: from GDC (type {kind or 'unknown'})")
        return sorted(status, key=lambda e: int(e["START_DATE"] or 0))

    @property
    def subject(self) -> dict:
        return {"reference": f"Patient/{self.rid('patient')}"}

    # --- build ---------------------------------------------------------------------
    def build(self) -> dict:
        self.patient()
        self.oncologist()
        self.primary_cancer()
        self.staging()
        self.prior_primaries()
        self.specimens_and_resection()
        self.disease_course()
        self.tumor_markers()
        self.smoking()
        self.treatments()
        self.encounters()
        self.ecog()
        self.labs()
        self.allergy()
        if self.snapshot:
            from synthgen.clinical.genomics import Genomics
            g = Genomics(self, self.snapshot)
            g.build()
            self.mongo = g.mongo
        self.tumor_board_request()
        return F.bundle(self.rid("transaction"), self.resources)

    def patient(self):
        p, mrn = self.pt, self.mrn
        sex = p["SEX"].lower()
        given, family = syn.person_name(mrn, sex)
        self.patient_name = (given, family)
        exts = []
        if p.get("RACE") in F.RACE:
            code, disp = F.RACE[p["RACE"]]
            exts.append({"url": F.USCORE + "us-core-race", "extension": [
                {"url": "ombCategory", "valueCoding": F.coding(F.OMB, code, disp)},
                {"url": "text", "valueString": disp}]})
        if p.get("ETHNICITY") in F.ETHNICITY:
            code, disp = F.ETHNICITY[p["ETHNICITY"]]
            exts.append({"url": F.USCORE + "us-core-ethnicity", "extension": [
                {"url": "ombCategory", "valueCoding": F.coding(F.OMB, code, disp)},
                {"url": "text", "valueString": disp}]})
        exts.append({"url": F.USCORE + "us-core-birthsex", "valueCode": "F" if sex == "female" else "M"})
        r = F.resource("Patient", self.rid("patient"), "mcode-cancer-patient",
                       extension=exts,
                       identifier=[{"use": "official",
                                    "type": F.cc("http://terminology.hl7.org/CodeSystem/v2-0203", "MR", "Medical record number"),
                                    "system": F.MRN_SYSTEM, "value": mrn}],
                       active=True,
                       name=[{"use": "official", "family": family, "given": [given]}],
                       telecom=[{"system": "phone", "value": syn.phone(mrn), "use": "home"}],
                       gender=sex,
                       birthDate=self.D(p["DAYS_TO_BIRTH"]),
                       address=[syn.address(mrn)])
        dead = [s for s in self.status if s["STATUS"] == "DECEASED"]
        if dead:
            r["deceasedDateTime"] = self.D(dead[0]["START_DATE"])
        self.add(r)

    def oncologist(self):
        sex = "female" if syn.rng(self.mrn, "oncologist-sex").random() < 0.5 else "male"
        given, family = syn.person_name(self.mrn, sex, "oncologist-name")
        self.add(F.resource("Practitioner", self.rid("oncologist"), F.USCORE + "us-core-practitioner",
                            identifier=[{"system": "http://hl7.org/fhir/sid/us-npi", "value": syn.npi(self.mrn, "oncologist-npi")}],
                            name=[{"family": family, "given": [given], "prefix": ["Dr."]}],
                            gender=sex,
                            qualification=[{"code": F.cc("http://terminology.hl7.org/CodeSystem/v2-0360", "MD", "Doctor of Medicine")}]))
        self.onc_ref = {"reference": f"Practitioner/{self.rid('oncologist')}", "display": f"Dr. {given} {family}"}

    def primary_cancer(self):
        p, s, g = self.pt, self.sample, self.gdc_primary()
        lat = g.get("laterality") if g.get("laterality") in F.LATERALITY else None
        onco = F.primary_code(s["ONCOTREE_CODE"], p.get("ICD_O_3_SITE") or "")
        icd = F.icd10cm(p.get("ICD_10") or "", lat)
        code = {"coding": [F.coding(F.SCT, *onco)], "text": s.get("TUMOR_TYPE") or s.get("CANCER_TYPE_DETAILED")}
        if icd:
            code["coding"].append(F.coding(F.ICD10CM, *icd))
        site = F.site_for(p.get("ICD_O_3_SITE") or "C34.9", lat)
        body = {"coding": [F.coding(F.SCT, *site)]}
        if lat:
            body["extension"] = [F.ext("mcode-laterality-qualifier", "valueCodeableConcept", F.sct(*F.LATERALITY[lat]))]
        exts = []
        if p.get("ICD_O_3_HISTOLOGY") in F.MORPHOLOGY:
            exts.append(F.ext("mcode-histology-morphology-behavior", "valueCodeableConcept",
                              F.cc(F.ICDO3, p["ICD_O_3_HISTOLOGY"], F.MORPHOLOGY[p["ICD_O_3_HISTOLOGY"]])))
        elif p.get("ICD_O_3_HISTOLOGY"):
            self.notes.append(f"histology {p['ICD_O_3_HISTOLOGY']} not in the crosswalk")
        active = any(s.get("TUMOR_STATUS") == "With Tumor" for s in self.status) or p.get("PERSON_NEOPLASM_CANCER_STATUS") == "With Tumor"
        self.primary = self.add(F.resource(
            "Condition", self.rid("primary-cancer"), "mcode-primary-cancer-condition",
            extension=exts or None,
            clinicalStatus=F.cc(F.COND_CLINICAL, "active", "Active") if active else F.cc(F.COND_CLINICAL, "remission", "Remission"),
            verificationStatus=F.cc(F.COND_VER, "confirmed", "Confirmed"),
            category=[F.cc(F.COND_CAT, "problem-list-item", "Problem List Item")],
            code=code, bodySite=[body], subject=self.subject,
            onsetDateTime=self.D(0), recordedDate=self.D(0)))
        if not exts:
            del self.primary["extension"]
        self.primary_site = site
        self.primary_ref = {"reference": f"Condition/{self.primary['id']}", "display": onco[1]}

    def staging(self):
        p = self.pt
        stage = p.get("AJCC_PATHOLOGIC_TUMOR_STAGE")
        if stage not in F.AJCC_STAGE:
            self.notes.append(f"stage {stage!r} not mapped")
            return
        ed_raw = p.get("AJCC_STAGING_EDITION") or ""
        edition = F.AJCC_EDITION.get(ed_raw)
        if edition:
            method_cc = F.sct(*edition)
        else:  # mCODE requires a method; SNOMED has no code for the 3rd-5th editions: state it as text
            ordinal = ed_raw.lower() if ed_raw else "unknown"
            method_cc = {"text": f"American Joint Committee on Cancer, Cancer Staging Manual, {ordinal} edition"}
            self.notes.append(f"AJCC edition {ed_raw!r}: no SNOMED code, method given as text")
        day = self.resection_day()
        method = {"method": method_cc}

        def obs(suffix, profile, code, value):
            return self.add(F.resource("Observation", self.rid(suffix), profile, status="final",
                                       category=F.obs_category("laboratory", "Laboratory"),
                                       code=F.loinc(*code), subject=self.subject, focus=[self.primary_ref],
                                       effectiveDateTime=self.D(day), valueCodeableConcept=value, **method))
        members = []
        t, n, m = p.get("PATH_T_STAGE"), p.get("PATH_N_STAGE"), p.get("PATH_M_STAGE")
        if t in F.AJCC_T:
            members.append(obs("tnm-pathologic-t", "mcode-tnm-primary-tumor-category", ("21899-0", "Primary tumor.pathology Cancer"),
                               F.sct(F.AJCC_T[t], f"p{t[0]}{t[1:].lower()} (AJCC)".replace("pT1a", "pT1a"))))
        if n in F.AJCC_N:
            members.append(obs("tnm-pathologic-n", "mcode-tnm-regional-nodes-category", ("21900-6", "Regional lymph nodes.pathology [Class] Cancer"),
                               F.sct(F.AJCC_N[n], f"p{n[:2]}{n[2:].lower()} (AJCC)")))
        if m in F.AJCC_M:
            members.append(obs("tnm-pathologic-m", "mcode-tnm-distant-metastases-category", ("21901-4", "Distant metastases.pathology [Class] Cancer"),
                               F.sct(F.AJCC_M[m], f"pM1{m[2:].lower()} (AJCC)")))
        code, label = F.AJCC_STAGE[stage]
        group = obs("tnm-pathologic-stage-group", "mcode-tnm-stage-group", ("21902-2", "Stage group.pathology Cancer"),
                    F.sct(code, f"{label} (AJCC)"))
        group["hasMember"] = [F.ref(x) for x in members]
        summary = {"summary": F.sct(code, f"{label} (AJCC)"), "assessment": [F.ref(group)], "type": method_cc}
        self.primary["stage"] = [summary]

    def prior_primaries(self):
        notes = [a.get("notes") for a in self.gdc.get("annotations", []) if a.get("category") == "Prior malignancy"]
        for i, d in enumerate(x for x in self.gdc.get("diagnoses", []) if x.get("classification_of_tumor") == "Prior primary"):
            organ = d.get("tissue_or_organ_of_origin") or "Unknown"
            sct_code, icd, specific = F.prior_primary(organ)
            code = {"coding": [F.coding(F.SCT, *sct_code), F.coding(F.ICD10CM, *icd)]}
            if not specific:
                code["text"] = f"History of malignant neoplasm ({organ})"
            r = F.resource("Condition", self.rid(f"prior-primary-{i + 1}"), F.USCORE + "us-core-condition-problems-health-concerns",
                           clinicalStatus=F.cc(F.COND_CLINICAL, "resolved", "Resolved"),
                           verificationStatus=F.cc(F.COND_VER, "confirmed", "Confirmed"),
                           category=[F.cc(F.COND_CAT, "problem-list-item", "Problem List Item")],
                           code=code, subject=self.subject)
            if notes:
                r["note"] = [{"text": notes[0]}]
            self.add(r)

    def resection_day(self) -> int:
        """Day the tumor specimen was collected (resection or biopsy); day 0 if not recorded."""
        days = [int(a["START_DATE"]) for a in self.acq if a.get("START_DATE")]
        return days[0] if days else 0

    @property
    def procurement(self) -> str:
        return (self.acq[0].get("METHOD_OF_SAMPLE_PROCUREMENT") or "") if self.acq else ""

    def specimens_and_resection(self):
        acq = self.acq[0] if self.acq else {}
        day = int(acq.get("START_DATE") or 0)
        body = {"coding": [F.coding(F.SCT, *self.primary_site)]}
        self.tumor_specimen = self.add(F.resource(
            "Specimen", self.rid("specimen-tumor"),
            identifier=[{"system": F.SPECIMEN_SYSTEM, "value": self.sample["SAMPLE_ID"]}],
            status="available",
            type=F.cc("http://terminology.hl7.org/CodeSystem/v2-0487", "TUMOR", "Tumor"),
            subject=self.subject,
            collection={"collectedDateTime": self.D(day), "bodySite": body,
                        **({"method": {"text": acq["METHOD_OF_SAMPLE_PROCUREMENT"]}} if acq.get("METHOD_OF_SAMPLE_PROCUREMENT") else {})}))
        blood = [s for s in self.gdc.get("samples", []) if s.get("sample_type") == "Blood Derived Normal"]
        if self.sample.get("SOMATIC_STATUS") == "Matched" and blood:
            self.normal_specimen = self.add(F.resource(
                "Specimen", self.rid("specimen-blood-normal"),
                identifier=[{"system": F.SPECIMEN_SYSTEM, "value": blood[0]["submitter_id"][:15]}],
                status="available",
                type=F.cc("http://terminology.hl7.org/CodeSystem/v2-0487", "BLD", "Whole blood"),
                subject=self.subject, collection={"collectedDateTime": self.D(day)}))
            self.notes.append("blood normal collected-date: assumed the resection day")
        if self.procurement in F.PROCUREMENT:
            (pcode, _noun) = F.PROCUREMENT[self.procurement]
            self.add(F.resource(
                "Procedure", self.rid("resection"), "mcode-cancer-related-surgical-procedure",
                extension=[F.ext("mcode-procedure-intent", "valueCodeableConcept", F.sct(*F.INTENT["curative"]))],
                status="completed", code=F.sct(*pcode),
                subject=self.subject, performedDateTime=self.D(day),
                performer=[{"actor": self.onc_ref}], reasonReference=[self.primary_ref], bodySite=[body]))

    def first_progression_day(self) -> int | None:
        days = [int(s["START_DATE"]) for s in self.status
                if any(x in s["STATUS"] for x in ("Distant Metastasis", "Recurrence", "New Primary"))]
        return min(days) if days else None

    def disease_status(self, day, trend, evidence=None, suffix=None):
        code, disp = F.STATUS_TREND[trend]
        r = F.resource("Observation", self.rid(suffix or f"disease-status-d{day}"), "mcode-cancer-disease-status",
                       status="final", category=F.obs_category("exam", "Exam"),
                       code=F.loinc("97509-4", "Cancer Disease Progression"),
                       subject=self.subject, focus=[self.primary_ref], effectiveDateTime=self.D(day),
                       valueCodeableConcept=F.sct(code, disp), performer=[self.onc_ref])
        if evidence:
            r["extension"] = [F.ext("mcode-cancer-disease-status-evidence-type", "valueCodeableConcept", F.sct(*evidence))]
        existing = [x for x in self.resources if x["id"] == r["id"]]
        if not existing:
            self.add(r)

    def metastasis(self, day: int):
        sites = F.metastasis_sites(self.sample["ONCOTREE_CODE"])
        real = F.GDC_MET_SITE.get(self.gdc_met_site.get(day, ""))
        cond, struct = real or syn.rng(self.mrn, f"met-site-{day}").choice(sites)
        if any(r["id"].startswith(self.rid("metastasis-")) and r["code"]["coding"][0]["code"] == cond[0] for r in self.resources):
            self.progressed[day] = F.site_word(struct[1])  # same site again: progression of that metastasis
            self.notes.append(f"distant event at day {day}: progression of the existing {F.site_word(struct[1])} metastasis")
            return
        self.add(F.resource(
            "Condition", self.rid(f"metastasis-d{day}"), "mcode-secondary-cancer-condition",
            extension=[F.ext("http://hl7.org/fhir/StructureDefinition/condition-related", "valueReference", self.primary_ref)],
            clinicalStatus=F.cc(F.COND_CLINICAL, "active", "Active"),
            verificationStatus=F.cc(F.COND_VER, "confirmed", "Confirmed"),
            category=[F.cc(F.COND_CAT, "problem-list-item", "Problem List Item")],
            code=F.sct(*cond), bodySite=[F.sct(*struct)], subject=self.subject,
            onsetDateTime=self.D(day), recordedDate=self.D(day)))
        self.notes.append(f"metastasis site at day {day}: " + ("from GDC" if real else "rule-filled") + f" ({cond[1]})")

    def disease_course(self):
        progress = {f.get("days_to_progression"): f for f in self.gdc.get("follow_ups", []) if f.get("days_to_progression") is not None}
        if self.m1:  # metastatic at diagnosis (pM1): the metastasis is part of the initial picture, not a progression
            self.metastasis(0)
        for s in self.status:
            day = int(s["START_DATE"])
            if "Distant Metastasis" in s["STATUS"]:
                self.metastasis(day)
                ev = F.EVIDENCE.get((progress.get(day) or {}).get("evidence_of_progression_type"))
                self.disease_status(day, "worsened", ev)
            elif "Recurrence" in s["STATUS"]:
                self.disease_status(day, "worsened")
            elif s["STATUS"] == "Last Follow Up":
                if s.get("TUMOR_STATUS") == "With Tumor":
                    trend = "worsened" if s.get("PRIMARY_THERAPY_OUTCOME_SUCCESS") == "Progressive Disease" else "stable"
                    self.disease_status(day, trend)

    def tumor_markers(self):
        s = self.sample
        self.test_day = (self.first_progression_day() or self.resection_day()) + syn.rng(self.mrn, "test-day").randint(10, 21)
        self.notes.append(f"genomic test date: day {self.test_day} (rule: after first progression, else after resection)")
        common = dict(status="final", category=F.obs_category("laboratory", "Laboratory"), subject=self.subject,
                      effectiveDateTime=self.D(self.test_day), specimen=F.ref(self.tumor_specimen),
                      extension=[F.ext("mcode-related-condition", "valueReference", self.primary_ref)])
        if not blank(s.get("TMB_NONSYNONYMOUS")):
            self.add(F.resource("Observation", self.rid("tmb"), "mcode-tumor-marker-test",
                                code=F.loinc("94076-7", "Mutations/Megabase [# Ratio] in Tumor"),
                                valueQuantity=F.qty(round(float(s["TMB_NONSYNONYMOUS"]), 1), "mutations/Mb", "1/10*6"), **common))
        if not blank(s.get("MSI_SENSOR_SCORE")):
            # MSIsensor (>10 high, 4-10 indeterminate) and MANTIS (>0.6 high, 0.4-0.6 indeterminate): a call only
            # when both agree, otherwise indeterminate (Bob, 2026-11-12)
            v = float(s["MSI_SENSOR_SCORE"])
            calls = {"S": "stable" if v < 4 else "indeterminate" if v <= 10 else "high"}
            if not blank(s.get("MSI_SCORE_MANTIS")):
                m = float(s["MSI_SCORE_MANTIS"])
                calls["M"] = "stable" if m < 0.4 else "indeterminate" if m <= 0.6 else "high"
            call = calls["S"] if len(set(calls.values())) == 1 else "indeterminate"
            if call == "indeterminate" and len(set(calls.values())) > 1:
                self.notes.append(f"MSI indeterminate: MSIsensor {calls['S']}, MANTIS {calls['M']}")
            ans = {"stable": ("LA14122-8", "Stable"), "indeterminate": ("LA11884-6", "Indeterminate"),
                   "high": ("LA26203-2", "MSI-H")}[call]
            self.msi_call = call
            self.add(F.resource("Observation", self.rid("msi"), "mcode-tumor-marker-test",
                                code=F.loinc("81695-9", "Microsatellite instability [Interpretation] in Cancer specimen Qualitative"),
                                valueCodeableConcept=F.cc(F.LOINC, *ans), **common))
        self.gdc_lab_markers()

    def gdc_lab_markers(self):
        """Real clinical markers recorded in GDC (colorectal): preoperative CEA and MMR immunohistochemistry."""
        tests = [t for f in self.gdc.get("follow_ups", []) for t in (f.get("molecular_tests") or [])]
        related = [F.ext("mcode-related-condition", "valueReference", self.primary_ref)]
        cea = next((t for t in tests if t.get("antigen") == "CEA" and t.get("test_value") is not None), None)
        if cea:
            day = self.resection_day() - syn.rng(self.mrn, "cea-day").randint(3, 14)  # preoperative
            self.add(F.resource("Observation", self.rid("cea"), "mcode-tumor-marker-test", status="final",
                                category=F.obs_category("laboratory", "Laboratory"), code=F.loinc(*F.CEA),
                                subject=self.subject, effectiveDateTime=self.D(day), extension=related,
                                valueQuantity=F.qty(float(cea["test_value"]), "ng/mL", "ng/mL"),
                                referenceRange=[{"high": F.qty(5.0, "ng/mL", "ng/mL")}]))
            self.notes.append("CEA: real preoperative value from GDC; date set before surgery by rule")
        mmr = {t["gene_symbol"]: t.get("test_result") for t in tests
               if t.get("gene_symbol") in F.MMR_IHC and t.get("test_result") in ("Loss of Expression", "Normal")}
        if not mmr:
            return
        if len(mmr) == 4 and all(v == "Loss of Expression" for v in mmr.values()):
            # loss of all four proteins: every TCGA colorectal case with this pattern is microsatellite stable,
            # so it is almost certainly a data-entry error (data quirk): not ported
            self.notes.append("MMR IHC not ported: GDC shows loss of all four proteins (data quirk)")
            return
        for gene, result in sorted(mmr.items()):
            lost = result == "Loss of Expression"
            self.add(F.resource("Observation", self.rid(f"mmr-{gene.lower()}"), "mcode-tumor-marker-test", status="final",
                                category=F.obs_category("laboratory", "Laboratory"), code=F.loinc(*F.MMR_IHC[gene]),
                                subject=self.subject, effectiveDateTime=self.D(self.resection_day()),
                                specimen=F.ref(self.tumor_specimen), extension=related,
                                valueCodeableConcept=F.cc(F.LOINC, *(("LA9634-2", "Absent") if lost else ("LA9633-4", "Present")))))
        deficient = any(v == "Loss of Expression" for v in mmr.values())
        msi = getattr(self, "msi_call", None)
        if (deficient and msi == "stable") or (not deficient and len(mmr) == 4 and msi == "high"):
            self.notes.append(f"MMR IHC ({'deficient' if deficient else 'proficient'}) disagrees with MSI ({msi}): "
                              "ported as recorded")

    def year_shift(self) -> int:
        y = self.gdc_primary().get("year_of_diagnosis")
        return (self.d0.year - int(y)) if y else 0

    def smoking(self):
        tob = [e for e in self.gdc.get("exposures", []) if e.get("exposure_type") == "Tobacco"]
        if not tob or tob[0].get("tobacco_smoking_status") not in F.SMOKING:
            return
        e = tob[0]
        self.add(F.resource("Observation", self.rid("smoking-status"), F.USCORE + "us-core-smokingstatus",
                            status="final", category=F.obs_category("social-history", "Social History"),
                            code=F.loinc("72166-2", "Tobacco smoking status"), subject=self.subject,
                            effectiveDateTime=self.D(0), valueCodeableConcept=F.sct(*F.SMOKING[e["tobacco_smoking_status"]])))
        if e.get("pack_years_smoked"):
            shift = self.year_shift()
            years = [f"{k} {int(e[f]) + shift}" for k, f in (("started", "tobacco_smoking_onset_year"), ("quit", "tobacco_smoking_quit_year")) if e.get(f)]
            r = F.resource("Observation", self.rid("pack-years"), status="final",
                           category=F.obs_category("social-history", "Social History"),
                           code=F.loinc("8664-5", "Cigarettes smoked total (pack per year) - Reported"), subject=self.subject,
                           effectiveDateTime=self.D(0), valueQuantity=F.qty(e["pack_years_smoked"], "pack-years", "{pack-years}"))
            if years:
                r["note"] = [{"text": "Smoking history: " + ", ".join(years) + "."}]
            self.add(r)

    LINE_WINDOW = 30  # a drug starting within this many days of a line's start joins that line

    def drug_rows(self) -> list[dict]:
        """One row per drug and start day: combined agents ("A + B") split; the same drug on the same day
        (e.g. fluorouracil bolus + infusion) merged, keeping the later stop."""
        rows: dict[tuple, dict] = {}
        for t in self.treat:
            if (t.get("AGENT") or "").startswith("Radiation"):
                continue
            parts = [a.strip() for a in (t.get("AGENT") or "").split("+")]
            if not all(a.lower() in F.RXNORM for a in parts):  # split only known drugs (not e.g. "recPRAME + AS15", one vaccine)
                parts = [(t.get("AGENT") or "").strip()]
            for agent in parts:
                r = {**t, "AGENT": agent}
                key = (agent.lower(), t["START_DATE"])
                old = rows.get(key)
                if old is None or int(r.get("STOP_DATE") or -1) > int(old.get("STOP_DATE") or -1):
                    rows[key] = r
        return sorted(rows.values(), key=lambda t: int(t["START_DATE"]))  # stable: keeps the source order within a day

    def treatment_lines(self, drugs: list[dict]) -> list[int]:
        """Line start days. Supportive drugs never start a line; drugs within LINE_WINDOW days join the line."""
        starts: list[int] = []
        for d in sorted({int(t["START_DATE"]) for t in drugs if t["AGENT"].lower() not in F.SUPPORTIVE_DRUGS}):
            if not starts or d - starts[-1] > self.LINE_WINDOW:
                starts.append(d)
        return starts or sorted({int(t["START_DATE"]) for t in drugs})[:1]

    @staticmethod
    def line_of(starts: list[int], day: int) -> int:
        return max(1, sum(1 for s in starts if s <= day))

    def treatments(self):
        first_prog = self.first_progression_day()
        drugs = self.drug_rows()
        rads = [t for t in self.treat if (t.get("AGENT") or "").startswith("Radiation")]
        self.treatment_starts = sorted({int(t["START_DATE"]) for t in self.treat})
        lines = self.line_starts = self.treatment_lines(drugs)

        def intent_for(t, day, agent=""):
            if agent.lower() in F.SUPPORTIVE_DRUGS:
                return "supportive"
            ind = t.get("REGIMEN_INDICATION")
            if ind == "Adjuvant":
                return "curative"
            if ind in ("Palliative", "Recurrence", "Progression"):
                return "palliative"
            return "palliative" if first_prog is not None and day >= first_prog else "curative"

        text_only = "drug-text-only" in self.messy
        for t in drugs:
            start = int(t["START_DATE"])
            agent = (t.get("AGENT") or "").strip()
            if agent.lower() in ("", "nos", "not otherwise specified"):  # TCGA "Nos": drug not recorded
                agent = f"Unspecified {(t.get('TREATMENT_TYPE') or 'therapy').lower()}"
            n = self.line_of(lines, start)
            later = [d for d in lines if d > start]
            stop = int(t["STOP_DATE"]) if t.get("STOP_DATE") else None
            status = "completed"
            if stop is None and t.get("THERAPY_ONGOING") == "Yes" and not later:
                status = "active"
            elif stop is None:  # rule T1
                cycles = int(t["NUMBER_OF_CYCLES"]) if (t.get("NUMBER_OF_CYCLES") or "").isdigit() else 4
                stop = start + cycles * 21
                if later:
                    stop = min(stop, later[0] - 1)
                self.notes.append(f"{agent} from day {start}: end date by rule T1 (day {stop})")
            response = (t.get("MEASURE_OF_RESPONSE") or "").lower()
            rx = F.RXNORM.get(agent.lower())
            med = {"text": agent}
            if rx and not text_only:
                med = {"coding": [F.coding(F.RXN, rx, agent.lower())], "text": agent}
            elif rx and text_only:
                text_only = False
                self.notes.append(f"messy: {agent} coded as text only")
            bounds = {"start": self.D(start)}
            if stop is not None:
                bounds["end"] = self.D(stop)
            rid = self.rid(f"med-r{n}-{agent.lower().replace(' ', '-')}")
            if any(x["id"] == rid for x in self.resources):  # the same drug restarted within a line
                rid = self.rid(f"med-r{n}-{agent.lower().replace(' ', '-')}-d{start}")
            r = F.resource("MedicationRequest", rid,
                           "mcode-cancer-related-medication-request",
                           extension=[F.ext("mcode-procedure-intent", "valueCodeableConcept",
                                            F.sct(*F.INTENT[intent_for(t, start, agent)]))],
                           status=status, intent="order", medicationCodeableConcept=med, subject=self.subject,
                           authoredOn=self.D(start), requester=self.onc_ref, reasonReference=[self.primary_ref],
                           groupIdentifier={"system": "http://elimu.io/fhir/regimen", "value": f"{self.mrn}-regimen-{n}"},
                           dosageInstruction=[{"timing": {"repeat": {"boundsPeriod": bounds}}}])
            if "progressive" in response:
                r["status"] = "stopped"
                r["statusReason"] = F.sct("266721009", "Absent response to treatment")
            if (t.get("NUMBER_OF_CYCLES") or "").lstrip("0").isdigit():
                r["dosageInstruction"][0]["timing"]["repeat"]["count"] = int(t["NUMBER_OF_CYCLES"])
            if t.get("TX_ON_CLINICAL_TRIAL") == "Yes":
                r["note"] = [{"text": "Given on a clinical trial" + (f": {t['TREATMENT_SUBTYPE']}" if t.get("TREATMENT_SUBTYPE") else "") + "."}]
            self.add(r)
            if response in F.RESPONSE and stop is not None:
                trend, ev = F.RESPONSE[response]
                self.disease_status(stop, trend, ev)

        for i, t in enumerate(rads):
            start = int(t["START_DATE"])
            stop = int(t["STOP_DATE"]) if t.get("STOP_DATE") else None
            distant = first_prog is not None and start >= first_prog
            body = (F.sct(*self.primary_site) if self.colorectal
                    else F.sct("31094006", "Structure of lobe of lung"))  # rule T3 (primary site)
            if distant:
                met = [x for x in self.resources if x["id"].startswith(self.rid("metastasis-"))]
                if met:
                    body = met[0]["bodySite"][0]
            self.notes.append(f"radiation from day {start}: modality and site by rules T2/T3")
            exts = [F.ext("mcode-procedure-intent", "valueCodeableConcept", F.sct(*F.INTENT["palliative" if distant else "curative"])),
                    {"url": F.MCODE + "mcode-radiotherapy-modality-and-technique", "extension": [
                        {"url": F.MCODE + "mcode-radiotherapy-modality", "valueCodeableConcept": F.sct("1156506007", "External beam radiation therapy using photons")},
                        {"url": F.MCODE + "mcode-radiotherapy-technique", "valueCodeableConcept": F.cc("http://terminology.hl7.org/CodeSystem/v3-NullFlavor", "UNC", "un-encoded", "Technique not recorded")}]}]
            if (t.get("NUMBER_OF_FRACTIONS") or "").isdigit():
                exts.append(F.ext("mcode-radiotherapy-sessions", "valueUnsignedInt", int(t["NUMBER_OF_FRACTIONS"])))
            period = {"start": self.D(start)}
            if stop is not None:
                period["end"] = self.D(stop)
            self.add(F.resource("Procedure", self.rid(f"radiotherapy-{i + 1}"), "mcode-radiotherapy-course-summary",
                                extension=exts,
                                status="in-progress" if t.get("RADIATION_TREATMENT_ONGOING") == "Yes" else "completed",
                                code=F.sct("1217123003", "Radiotherapy course of treatment"),
                                subject=self.subject, performedPeriod=period, performer=[{"actor": self.onc_ref}],
                                reasonReference=[self.primary_ref], bodySite=[body]))

    def encounters(self):
        events = [(0, "AMB", ("185387006", "New patient consultation"), None)]
        r_day = self.resection_day()
        if r_day:
            stay = syn.rng(self.mrn, "admission-days").randint(3, 6)
            events.append((r_day, "IMP", ("86181006", "Evaluation and management of inpatient"), r_day + stay))
        follow = sorted(set(self.treatment_starts) | {int(s["START_DATE"]) for s in self.status
                                                       if s["STATUS"] not in ("Initial Diagnosis", "DECEASED")})
        for d in follow:
            if d not in (0, r_day):
                events.append((d, "AMB", ("108221006", "Evaluation AND/OR management - established patient"), None))
        if "missing-encounter" in self.messy and len(events) > 2:
            dropped = events.pop(syn.rng(self.mrn, "drop-encounter").randrange(1, len(events)))
            self.notes.append(f"messy: encounter on day {dropped[0]} omitted")
        classes = {"AMB": "ambulatory", "IMP": "inpatient encounter"}
        for d, cls, typ, end in events:
            period = {"start": self.D(d)}
            if end:
                period["end"] = self.D(end)
            self.add(F.resource("Encounter", self.rid(f"encounter-d{d}"), F.USCORE + "us-core-encounter",
                                status="finished",
                                **{"class": F.coding("http://terminology.hl7.org/CodeSystem/v3-ActCode", cls, classes[cls])},
                                type=[F.sct(*typ)], subject=self.subject,
                                participant=[{"individual": self.onc_ref}], period=period,
                                reasonReference=[self.primary_ref]))

    def ecog(self):
        if "no-ecog" in self.messy:
            self.notes.append("messy: no ECOG")
            return
        real = [(f.get("days_to_follow_up"), f["ecog_performance_status"]) for f in self.gdc.get("follow_ups", [])
                if not blank(f.get("ecog_performance_status")) and f.get("days_to_follow_up") is not None]
        values = [(int(d), int(v)) for d, v in real if str(v).isdigit()]
        if not values:
            r = syn.rng(self.mrn, "ecog")
            progressing = self.primary["clinicalStatus"]["coding"][0]["code"] == "active"
            score = r.choices([1, 0, 2], [60, 25, 15])[0] if progressing else r.choices([0, 1], [70, 30])[0]
            day = (self.board - self.d0).days - r.randint(0, 7)
            values = [(day, score)]
            self.notes.append(f"ECOG synthetic: {score}")
        for day, score in values:
            self.add(F.resource("Observation", self.rid(f"ecog-d{day}"), "mcode-ecog-performance-status",
                                status="final", category=F.obs_category("survey", "Survey"),
                                code=F.loinc("89247-1", "ECOG Performance Status score"), subject=self.subject,
                                effectiveDateTime=self.D(day), performer=[self.onc_ref], valueInteger=score,
                                interpretation=[F.cc(F.LOINC, *F.ECOG_ANSWER[score])]))

    def labs(self):
        if "no-labs" in self.messy:
            self.notes.append("messy: no labs")
            return
        r = syn.rng(self.mrn, "labs")
        day = (self.board - self.d0).days - r.randint(1, 7)
        recent_systemic = any(int(t["START_DATE"]) >= day - 90 or (t.get("STOP_DATE") and int(t["STOP_DATE"]) >= day - 90)
                              or (not t.get("STOP_DATE") and t.get("THERAPY_ONGOING") == "Yes")
                              for t in self.treat if not (t.get("AGENT") or "").startswith("Radiation"))
        female = self.pt["SEX"].lower() == "female"
        for code, disp, unit, low, high, fem in F.LABS:
            if female and fem:
                low, high = fem
            if code == "718-7" and recent_systemic:
                value = round(r.uniform(10.0, 12.0), 1)
            else:
                span = high - low
                value = r.uniform(low + 0.15 * span, high - 0.15 * span)
                value = round(value, 1) if high < 50 else round(value)
            self.add(F.resource("Observation", self.rid(f"lab-{code}"), F.USCORE + "us-core-observation-lab",
                                status="final", category=F.obs_category("laboratory", "Laboratory"),
                                code=F.loinc(code, disp), subject=self.subject, effectiveDateTime=self.D(day),
                                valueQuantity=F.qty(value, unit),
                                referenceRange=[{"low": F.qty(low, unit), "high": F.qty(high, unit)}]))

    def allergy(self):
        r = syn.rng(self.mrn, "allergy")
        base = dict(clinicalStatus=F.cc("http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "active", "Active"),
                    verificationStatus=F.cc("http://terminology.hl7.org/CodeSystem/allergyintolerance-verification", "confirmed", "Confirmed"),
                    patient=self.subject, recordedDate=self.D(0))
        if r.random() < 0.8:
            body = dict(code=F.sct("716186003", "No known allergy"))
        else:
            body = dict(code=F.sct("91936005", "Allergy to penicillin"),
                        reaction=[{"manifestation": [F.sct("271807003", "Eruption")]}])
        self.add(F.resource("AllergyIntolerance", self.rid("allergy"), F.USCORE + "us-core-allergyintolerance", **base, **body))

    def tumor_board_request(self):
        age = (self.board - dt.date.fromisoformat(self.D(self.pt["DAYS_TO_BIRTH"]))).days // 365
        sex = "woman" if self.pt["SEX"].lower() == "female" else "man"
        stage = self.pt.get("AJCC_PATHOLOGIC_TUMOR_STAGE", "").replace("STAGE ", "stage ")
        cancer = self.sample.get("CANCER_TYPE_DETAILED", "cancer").lower()
        if self.colorectal:  # "mucinous adenocarcinoma of the colon and rectum" -> the patient's own site
            organ = {"C19": "rectosigmoid", "C20": "rectal"}.get((self.pt.get("ICD_O_3_SITE") or "")[:3], "colon")
            cancer = ("mucinous " if self.sample.get("ONCOTREE_CODE") == "MACR" else "") + f"{organ} adenocarcinoma"
        drugs = [t for t in self.drug_rows() if t["AGENT"].lower() not in F.SUPPORTIVE_DRUGS]
        starts = getattr(self, "line_starts", [])

        def name(t):
            agent = (t.get("AGENT") or "").strip()
            return ("unspecified " + (t.get("TREATMENT_TYPE") or "therapy").lower()) if agent.lower() in ("", "nos") else agent.lower()
        regs = ["/".join(dict.fromkeys(name(t) for t in drugs if self.line_of(starts, int(t["START_DATE"])) == i + 1))
                for i in range(len(starts))]
        active = [r for r in self.resources if r["resourceType"] == "MedicationRequest" and r["status"] == "active"]
        statuses = sorted((x["effectiveDateTime"], x["valueCodeableConcept"]["coding"][0]["code"]) for x in self.resources
                          if x["resourceType"] == "Observation" and x["id"].startswith(self.rid("disease-status")))
        # progressing: the latest status is "worsened" and no line of therapy started after it
        progressing = (bool(statuses) and statuses[-1][1] == "271299001"
                       and not any(self.D(d) > statuses[-1][0][:10] for d in starts))
        markers = f" ({', '.join(self.key_biomarkers)})" if self.key_biomarkers else ""
        lines = f"{len(regs)} line{'s' if len(regs) != 1 else ''} of therapy ({', '.join(regs)})"
        events = " ".join(st["STATUS"] for st in self.status)
        extent = ("metastatic " if "Metastasis" in events or self.m1 else "recurrent " if "Recurrence" in events else "")
        bio = ", ".join(self.key_biomarkers)
        if extent:
            head = f"{age}-year-old {sex} with {extent}{cancer} (initially {stage}" + (f"; {bio})" if bio else ")")
        else:
            head = f"{age}-year-old {sex} with {stage} {cancer}{markers}"
        if progressing and regs:
            course, ask = f"now progressing after {lines}", "next-line treatment recommendation"
        elif progressing:
            course, ask = "now with disease progression, no systemic therapy to date", "treatment recommendation"
        elif active:
            current = "/".join(sorted({m["medicationCodeableConcept"].get("text", "").lower() for m in active}))
            course = f"currently on {current}" + (f" after {len(regs) - 1} prior line{'s' if len(regs) - 1 != 1 else ''} of therapy" if len(regs) > 1 else "")
            ask = "review of treatment in light of tumor genomic results"
        elif regs:
            course = f"after completing {lines}" + ("" if extent else ", with no evidence of progression")
            ask = "review of treatment options in light of tumor genomic results"
        else:
            proc = F.PROCUREMENT.get(self.procurement, (None, ""))[1]
            months = max(0, round(((self.board - self.d0).days - self.resection_day()) / 30.4))
            course = (f"status post {proc} {months} months ago, " if proc else "") + "no evidence of recurrence, no systemic therapy to date"
            ask = "treatment recommendation in light of tumor genomic results"
        note = f"{head}, {course}. Presented for {ask}."
        r = syn.rng(self.mrn, "board-request")
        self.add(F.resource("ServiceRequest", self.rid("tumor-board"),
                            status="active", intent="order",
                            code=F.sct("384682003", "Multidisciplinary care conference"),
                            subject=self.subject, occurrenceDateTime=self.board.isoformat(),
                            authoredOn=(self.board - dt.timedelta(days=r.randint(3, 10))).isoformat(),
                            requester=self.onc_ref, reasonReference=[self.primary_ref], note=[{"text": note}]))


def build_patient(harvest_root: Path, study_id: str, patient_id: str, out_root: Path, tumor_board: dt.date,
                  snapshots: Path | None = Path("data/snapshots")) -> tuple[Path, list[str]]:
    snapshot = snapshots / study_id if snapshots else None
    b = Builder(harvest_root / study_id / patient_id, tumor_board, snapshot)
    bundle = b.build()
    out = out_root / study_id / patient_id / f"{patient_id}-transaction-bundle.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, indent=2) + "\n")
    (out.parent / "notes.json").write_text(json.dumps(b.notes, indent=1) + "\n")
    if b.mongo:
        mongo_dir = out.parent / "mongo"
        mongo_dir.mkdir(exist_ok=True)
        for name, docs in b.mongo.items():
            (mongo_dir / f"{name}.json").write_text(json.dumps(docs, indent=2) + "\n")
    return out, b.notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("study_id")
    parser.add_argument("patient_id")
    parser.add_argument("--tumor-board", type=dt.date.fromisoformat, default=dt.date(2026, 12, 1),
                        help="tumor board date T (fixed, so reruns are stable)")
    parser.add_argument("--harvest", type=Path, default=Path("data/harvest"))
    parser.add_argument("--out", type=Path, default=Path("data/output"))
    args = parser.parse_args(argv)
    out, notes = build_patient(args.harvest, args.study_id, args.patient_id, args.out, args.tumor_board)
    print(out)
    for n in notes:
        print("  note:", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
