"""Build a patient's mCODE transaction bundle from a TCGA PanCancer extract + GDC record.

Implements docs/mapping.md: "Clinical", "Treatments" and "Clinical: synthetic content".
Not yet included: GenomicStudy, the genomics report, PD-L1 (genomic mapping pending).

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
        self.status = read_rows(extract_dir / "data_timeline_status.txt")
        self.treat = read_rows(extract_dir / "data_timeline_treatment.txt")
        self.acq = read_rows(extract_dir / "data_timeline_sample_acquisition.txt")
        gdc_path = extract_dir / "gdc_case.json"
        self.gdc = json.loads(gdc_path.read_text())["case"] if gdc_path.exists() else {}
        self.mrn = self.pt["PATIENT_ID"]
        self.key = self.mrn.lower()
        self.board = tumor_board
        last = max([int(self.pt.get("DAYS_LAST_FOLLOWUP") or 0)]
                   + [int(r["START_DATE"]) for r in self.status + self.treat if r.get("START_DATE")]
                   + [int(r["STOP_DATE"]) for r in self.treat if r.get("STOP_DATE")])
        self.last_day = last
        self.d0 = syn.diagnosis_date(self.mrn, tumor_board, last)
        self.messy = syn.messy_kinds(self.mrn)
        self.resources: list[dict] = []
        self.notes: list[str] = []  # rule-filled or flagged choices, for the run report

    # --- helpers -------------------------------------------------------------------
    def D(self, day) -> str:
        return (self.d0 + dt.timedelta(days=int(day))).isoformat()

    def rid(self, suffix: str) -> str:
        return f"mtb-{self.key}-{suffix}"

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
        onco = F.ONCOTREE[s["ONCOTREE_CODE"]]
        icd = F.icd10cm_lung(p.get("ICD_10") or "", lat)
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
                               F.sct(F.AJCC_N[n], f"p{n.upper()} (AJCC)")))
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

    def disease_course(self):
        progress = {f.get("days_to_progression"): f for f in self.gdc.get("follow_ups", []) if f.get("days_to_progression") is not None}
        for s in self.status:
            day = int(s["START_DATE"])
            if "Distant Metastasis" in s["STATUS"]:
                sites = F.METASTASIS_SITES[self.sample["ONCOTREE_CODE"]]
                cond, struct = syn.rng(self.mrn, f"met-site-{day}").choice(sites)
                self.add(F.resource(
                    "Condition", self.rid(f"metastasis-d{day}"), "mcode-secondary-cancer-condition",
                    extension=[F.ext("http://hl7.org/fhir/StructureDefinition/condition-related", "valueReference", self.primary_ref)],
                    clinicalStatus=F.cc(F.COND_CLINICAL, "active", "Active"),
                    verificationStatus=F.cc(F.COND_VER, "confirmed", "Confirmed"),
                    category=[F.cc(F.COND_CAT, "problem-list-item", "Problem List Item")],
                    code=F.sct(*cond), bodySite=[F.sct(*struct)], subject=self.subject,
                    onsetDateTime=self.D(day), recordedDate=self.D(day)))
                self.notes.append(f"metastasis site at day {day}: rule-filled ({cond[1]})")
                ev = F.EVIDENCE.get((progress.get(day) or {}).get("evidence_of_progression_type"))
                self.disease_status(day, "worsened", ev)
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
            v = float(s["MSI_SENSOR_SCORE"])  # MSIsensor decides (decision 6)
            ans = ("LA14122-8", "Stable") if v < 4 else ("LA26203-2", "Indeterminate") if v <= 10 else ("LA14123-6", "High")
            self.add(F.resource("Observation", self.rid("msi"), "mcode-tumor-marker-test",
                                code=F.loinc("81695-9", "Microsatellite instability [Interpretation] in Cancer specimen Qualitative"),
                                valueCodeableConcept=F.cc(F.LOINC, *ans), **common))

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

    def treatments(self):
        first_prog = self.first_progression_day()
        drugs = [t for t in self.treat if not (t.get("AGENT") or "").startswith("Radiation")]
        rads = [t for t in self.treat if (t.get("AGENT") or "").startswith("Radiation")]
        starts = sorted({int(t["START_DATE"]) for t in self.treat})
        regimens = sorted({int(t["START_DATE"]) for t in drugs})
        self.treatment_starts = starts

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
            n = regimens.index(start) + 1
            later = [d for d in starts if d > start]
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
            r = F.resource("MedicationRequest", self.rid(f"med-r{n}-{agent.lower().replace(' ', '-')}"),
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
            body = F.sct("31094006", "Structure of lobe of lung")  # rule T3 (primary site)
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
        regs = []
        for d in sorted({int(t["START_DATE"]) for t in self.treat if not (t.get("AGENT") or "").startswith("Radiation")}):
            regs.append("/".join(t["AGENT"].lower() for t in self.treat if int(t["START_DATE"]) == d))
        statuses = sorted((x["effectiveDateTime"], x["valueCodeableConcept"]["coding"][0]["code"]) for x in self.resources
                          if x["resourceType"] == "Observation" and x["id"].startswith(self.rid("disease-status")))
        status = "progressing" if statuses and statuses[-1][1] == "271299001" else "under treatment"
        markers = f" ({', '.join(self.key_biomarkers)})" if self.key_biomarkers else ""
        note = (f"{age}-year-old {sex} with {stage} {cancer}{markers}, now {status} after {len(regs)} line"
                f"{'s' if len(regs) != 1 else ''} of therapy ({', '.join(regs)}). Presented for next-line treatment recommendation.")
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
