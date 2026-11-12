"""Reports and genomics for the bundle, plus the Mongo records (docs/mapping.md: "Reports",
"Genomic", "Genomics report", "Therapeutic implications", "Fusions").

Called by clinical.tcga.Builder after the clinical resources are built.
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from pathlib import Path

from synthgen import synthesize as syn
from synthgen.clinical import fhir as F
from synthgen.genomic import annotate as A, civic, pdl1, records

GR = "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/"
TBD = "http://hl7.org/fhir/uv/genomics-reporting/CodeSystem/tbd-codes-cs"
HGNC = "http://www.genenames.org/geneId"
SO = "http://www.sequenceontology.org"  # Genomics Reporting 3.0.0 (GenomicStudy)
SO_V2 = "http://sequenceontology.org"  # Genomics Reporting 2.0.0, used by mCODE 4.0.0 variants
HGVS = "http://varnomen.hgvs.org"
V2_0074 = "http://terminology.hl7.org/CodeSystem/v2-0074"
LAB = "http://terminology.hl7.org/CodeSystem/observation-category"
CHANGE_TYPE_SO = {"SNV": ("SO:0001483", "SNV"), "MNV": ("SO:0002007", "MNV"), "INS": ("SO:0000667", "insertion"),
                  "DEL": ("SO:0000159", "deletion"), "CNV": ("SO:0001019", "copy_number_variation"),
                  "FUSION": ("SO:0001565", "gene_fusion")}


FUSION_DRIVERS = {"ALK", "ROS1", "RET", "NTRK1", "NTRK2", "NTRK3", "NRG1", "FGFR1", "FGFR2", "FGFR3", "BRAF", "MET"}


def fusion_rows(rows: list[dict], reportable: set[str]) -> list[dict]:
    """Reportable fusions, one per gene pair. cBioPortal lists the reciprocal breakpoints of one rearrangement
    as two rows (CD74-ROS1 and ROS1-CD74); keep the orientation with the driver/reportable gene as the 3' partner."""
    by_pair: dict[frozenset, list[dict]] = {}
    for f in rows:
        g1, g2 = f.get("Site1_Hugo_Symbol"), f.get("Site2_Hugo_Symbol")
        if g1 in reportable or g2 in reportable:
            by_pair.setdefault(frozenset((g1, g2)), []).append(f)
    keep = []
    for group in by_pair.values():
        rank = lambda f: (f.get("Site2_Hugo_Symbol") not in FUSION_DRIVERS, f.get("Site2_Hugo_Symbol") not in reportable)
        keep.append(sorted(group, key=rank)[0])
    return keep


def site_phrase(display: str) -> str:
    """'Structure of lower lobe of right lung' -> 'right lower lobe'."""
    m = re.match(r"Structure of (upper|middle|lower) lobe of (right|left) lung", display)
    if m:
        return f"{m.group(2)} {m.group(1)} lobe"
    m = re.match(r"(Right|Left) (main bronchus|lung) structure", display)
    if m:
        return f"{m.group(1).lower()} {m.group(2)}"
    return display.replace("Structure of ", "").replace(" structure", "").lower()


NCIT = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl"


def phenotype(row: dict) -> dict:
    """CIViC disease -> coded phenotype: its DOID; tumor-agnostic "Solid Tumor" (no DOID in CIViC) -> NCIt C9292
    Solid Neoplasm, so apps can recognize tumor-agnostic evidence ("Cancer" is DOID:162)."""
    if row["doid"]:
        return {"coding": [{"system": "https://disease-ontology.org", "code": f"DOID:{row['doid']}", "display": row["disease"]}]}
    if row["disease"].startswith("Solid Tumor"):
        return {"coding": [F.coding(NCIT, "C9292", "Solid Neoplasm")], "text": row["disease"]}
    return {"text": row["disease"]}


def reportable_genes() -> set[str]:
    """The 228 reportable genes (genomics-operations clinical-data README appendix), kept in this repo."""
    path = Path(__file__).with_name("reportable_genes.txt")
    return set(path.read_text().split())


class Genomics:
    def __init__(self, b, snapshot: Path):
        self.b = b
        self.snapshot = snapshot
        self.genes = reportable_genes()
        self.notes = b.notes
        self.mongo: dict[str, list] = {}

    # --- people ------------------------------------------------------------------------
    def practitioner(self, role: str, title: str) -> dict:
        sex = "female" if syn.rng(self.b.mrn, f"{role}-sex").random() < 0.5 else "male"
        given, family = syn.person_name(self.b.mrn, sex, f"{role}-name")
        self.b.add(F.resource("Practitioner", self.b.rid(role), F.USCORE + "us-core-practitioner",
                              identifier=[{"system": "http://hl7.org/fhir/sid/us-npi", "value": syn.npi(self.b.mrn, f"{role}-npi")}],
                              name=[{"family": family, "given": [given], "prefix": ["Dr."]}], gender=sex,
                              qualification=[{"code": F.cc("http://terminology.hl7.org/CodeSystem/v2-0360", "MD", "Doctor of Medicine")}]))
        return {"reference": f"Practitioner/{self.b.rid(role)}", "display": f"Dr. {given} {family}, {title}"}

    def build(self):
        b = self.b
        self.pathologist = self.practitioner("pathologist", "Pathology")
        self.radiologist = self.practitioner("radiologist", "Radiology")
        # one lab shared by all patients (a fixed id): identical per-patient copies are rejected by servers
        # that refuse duplicate content (e.g. the public HAPI server), and one lab is also more realistic
        self.lab = b.add(F.resource("Organization", "mtb-synthetic-genomics-lab", name="Synthetic Genomics Laboratory", active=True,
                                    identifier=[{"system": "http://elimu.io/fhir/organization", "value": "synthetic-genomics-lab"}]))
        self.pathology_report()
        self.imaging_reports()
        from synthgen.clinical import imaging
        reports = [r for r in b.resources if r["resourceType"] == "DiagnosticReport"]
        path = next((r for r in reports if r["id"] == b.rid("pathology-report")), None)
        imaging.link(b, path, [r for r in reports if r["id"].startswith(b.rid("imaging-"))])
        self.genomic()

    # --- reports (D22) -----------------------------------------------------------------
    def report(self, suffix, category, code, day, issued_days, performer, conclusion, **extra):
        rid = self.b.rid(suffix)
        existing = next((r for r in self.b.resources if r["id"] == rid), None)
        if existing:  # one study per day: a second finding on the same day joins the existing report
            existing["conclusion"] += " " + conclusion
            return existing
        return self.b.add(F.resource(
            "DiagnosticReport", self.b.rid(suffix), F.USCORE + "us-core-diagnosticreport-note",
            status="final", category=[F.cc(F.LOINC, *category)], code=F.loinc(*code), subject=self.b.subject,
            effectiveDateTime=self.b.D(day), issued=self.b.D(day + issued_days) + "T12:00:00Z",
            performer=[performer], conclusion=conclusion, **extra))

    def pathology_report(self):
        b = self.b
        day = b.resection_day()
        p = b.pt
        site = site_phrase(b.primary_site[1])
        histo = F.MORPHOLOGY.get(p.get("ICD_O_3_HISTOLOGY"), "carcinoma").replace(", NOS", "").lower()
        t, n, m = p.get("PATH_T_STAGE", ""), p.get("PATH_N_STAGE", ""), p.get("PATH_M_STAGE", "")
        stage = " ".join(x for x in [f"p{F.tnm(t)}" if t else "", f"p{F.tnm(n)}" if n else "",
                                     f"p{F.tnm(m)}" if m and m != "MX" else ""] if x)
        ed = (p.get("AJCC_STAGING_EDITION") or "").lower()
        noun = F.PROCUREMENT.get(b.procurement, (None, "specimen"))[1]
        text = (f"{site[0].upper() + site[1:]}, {noun}: Invasive {histo}." if b.colorectal
                else f"Lung, {site}, {noun}: Invasive {histo}.")
        if stage:
            text += f" Pathologic stage (AJCC {ed} edition): {stage}." if ed else f" Pathologic stage: {stage}."
        group = next((r for r in b.resources if r["id"] == b.rid("tnm-pathologic-stage-group")), None)
        extra = {"specimen": [F.ref(b.tumor_specimen)]}
        if group:
            extra["result"] = [F.ref(group)]
        self.report("pathology-report", ("LP7839-6", "PATH"), ("11526-1", "Pathology study"), day,
                    syn.rng(b.mrn, "path-issued").randint(3, 5), self.pathologist, text, **extra)

    def imaging_reports(self):
        b = self.b
        rad = ("LP29684-5", "RAD")
        pre = syn.rng(b.mrn, "staging-ct").randint(3, 14)
        where = site_phrase(b.primary_site[1])
        cap = where[0].upper() + where[1:]

        def met_site(met):
            return F.site_word(met["bodySite"][0]["coding"][0]["display"])
        mets = [r for r in b.resources if r["id"].startswith(b.rid("metastasis-"))]
        at_dx = [m for m in mets if m["id"].endswith("-d0")]
        distant = (f" {met_site(at_dx[0])[0].upper() + met_site(at_dx[0])[1:]} lesion(s), consistent with metastatic disease."
                   if at_dx else " No evidence of distant metastatic disease.")
        if b.colorectal:
            self.report("imaging-staging", rad, ("72254-6", "CT Chest and Abdomen and Pelvis W contrast IV"), -pre, 0,
                        self.radiologist, f"{cap} mass, consistent with primary colorectal malignancy.{distant}")
        else:
            self.report("imaging-staging", rad, ("24627-2", "CT Chest"), -pre, 0, self.radiologist,
                        f"{cap} mass, consistent with primary lung malignancy.{distant}")
        study_for = {"94222008": ("39818-0", "NM Whole body Bone Views"), "94225005": ("24590-2", "MR Brain")}
        for met in [m for m in mets if m not in at_dx]:
            day = int(met["id"].rsplit("-d", 1)[1])
            code = met["code"]["coding"][0]["code"]
            study = study_for.get(code, ("72254-6", "CT Chest and Abdomen and Pelvis W contrast IV"))
            site = met_site(met)
            self.report(f"imaging-d{day}", rad, study, day, 0, self.radiologist,
                        f"New {site} lesion(s), consistent with metastatic disease.")
        for day, site in b.progressed.items():
            self.report(f"imaging-d{day}", rad, ("72254-6", "CT Chest and Abdomen and Pelvis W contrast IV"), day, 0,
                        self.radiologist, f"Progression of {site} metastatic disease.")
        for e in b.status:
            if e["STATUS"] in ("Recurrence", "Locoregional Recurrence"):
                day = int(e["START_DATE"])
                text = (f"Locoregional recurrence at the {where}." if e["STATUS"] == "Locoregional Recurrence"
                        else "Findings consistent with recurrent disease.")
                self.report(f"imaging-d{day}", rad, ("72254-6", "CT Chest and Abdomen and Pelvis W contrast IV"), day, 0,
                            self.radiologist, text)
        for t in b.treat:
            if (t.get("MEASURE_OF_RESPONSE") or "") == "radiographic progressive disease" and t.get("STOP_DATE"):
                day = int(t["STOP_DATE"])
                self.report(f"imaging-d{day}", rad, ("72254-6", "CT Chest and Abdomen and Pelvis W contrast IV"), day, 0,
                            self.radiologist, "Interval progression of disease.")
        last = [s for s in b.status if s["STATUS"] == "Last Follow Up" and s.get("TUMOR_STATUS") == "With Tumor"]
        if last:
            day = int(last[0]["START_DATE"])
            sites = sorted({F.site_word(r["bodySite"][0]["coding"][0]["display"])
                            for r in b.resources if r["id"].startswith(b.rid("metastasis-"))})
            self.report(f"imaging-d{day}", rad, ("72254-6", "CT Chest and Abdomen and Pelvis W contrast IV"), day, 0,
                        self.radiologist, f"Persistent/progressive disease{' (' + ', '.join(sites) + ')' if sites else ''}.")

    # --- genomics ----------------------------------------------------------------------
    def genomic(self):
        b = self.b
        sample = b.sample["SAMPLE_ID"]
        test = {"patientID": b.mrn, "testID": f"{b.mrn}-T1", "specimenID": sample,
                "testDate": b.D(b.test_day), "genomicBuild": "GRCh37"}
        muts, conseq, mdet = records.mutations(b.dir, test, self.notes)
        cnvs, cdet = records.copy_numbers(b.dir, test, sample)
        test.update({"studiedRegion": "Default.WES.GRCh37.bed", "uncallableRegion": "Default.WES.GRCh37.uncallable.bed",
                     "dnaChangeType": records.dna_change_types(muts + cnvs)})
        given, family = b.patient_name
        self.mongo = {
            "Patients": [{"patientID": b.mrn, "name": f"{given} {family}", "sex": "F" if b.pt["SEX"] == "Female" else "M",
                          "birthdate": b.D(b.pt["DAYS_TO_BIRTH"]), "ancestry": b.pt.get("GENETIC_ANCESTRY_LABEL") or ""}],
            "Tests": [test], "Variants": muts + cnvs, "MolecConseq": conseq,
        }
        # reported variants: protein-altering PASS mutations, +/-2 CNVs and fusions in the 228 genes
        reported = []
        for d in mdet:
            if d["gene"] in self.genes and d["protein_altering"]:
                p = (d["maf"].get("HGVSp_Short") or "").removeprefix("p.")
                reported.append({"kind": "mutation", "gene": d["gene"], "detail": d, "protein_short": p})
        for d in cdet:
            if d["gene"] in self.genes:
                reported.append({"kind": "amplification" if d["call"] == "amplification" else "deletion",
                                 "gene": d["gene"], "detail": d})
        for f in fusion_rows(records.read_rows(b.dir / "data_sv.txt"), self.genes):
            g1, g2 = f.get("Site1_Hugo_Symbol"), f.get("Site2_Hugo_Symbol")
            reported.append({"kind": "fusion", "gene": f"{g1}-{g2}", "genes": (g1, g2), "row": f})
        self.reported = reported
        obs = [self.variant_observation(v) for v in reported]
        implications = civic.select(reported, b.sample["ONCOTREE_CODE"])
        imp_obs = [self.implication(c) for c in implications]
        region = self.region_studied()
        pd = None if b.colorectal else self.pdl1(mdet)  # no PD-L1 for colorectal (Bob, 2026-11-12)
        study = self.genomic_study(test)
        markers = [r for r in b.resources if r["id"] in (b.rid("tmb"), b.rid("msi"))]
        self.genomics_report(test, region, obs, imp_obs, markers + ([pd] if pd else []), study, reported, implications)
        # for the tumor board note: standard lung drivers, or anything with level-A evidence
        drivers = {"EGFR", "ALK", "ROS1", "KRAS", "NRAS", "BRAF", "MET", "RET", "ERBB2", "NTRK1", "NTRK2", "NTRK3", "NRG1"}
        def label(v):
            if v["kind"] == "fusion":
                return f"{v['gene']} fusion"
            return f"{v['gene']} {v.get('protein_short') or v['kind']}"
        b.key_biomarkers = sorted({label(c["variant"]) for c in implications if c["significance"] == "Sensitivity/Response"
                                   and (c["level"] == "A" or set(c["variant"].get("genes") or [c["variant"]["gene"]]) & drivers)})
        if getattr(b, "msi_call", None) == "high":
            b.key_biomarkers.append("MSI-high")
        self.notes.append(f"genomic: {len(muts)} PASS mutations, {len(cnvs)} CNV records, {len(conseq)} MolecConseq; "
                          f"reported {len(reported)}; implications {len(implications)}")

    def gene_cc(self, symbol: str) -> dict:
        return F.cc(HGNC, A.hgnc_id(symbol) or symbol, symbol)

    def comp(self, code, display, **value) -> dict:
        system = TBD if not code[0].isdigit() else F.LOINC
        return {"code": F.cc(system, code, display), **value}

    def variant_observation(self, v: dict) -> dict:
        b = self.b
        comps = []
        if v["kind"] == "fusion":
            g1, g2 = v["genes"]
            comps += [self.comp("48018-6", "Gene studied [ID]", valueCodeableConcept=self.gene_cc(g1)),
                      self.comp("48018-6", "Gene studied [ID]", valueCodeableConcept=self.gene_cc(g2)),
                      self.comp("molecular-consequence", "Molecular Consequence",
                                valueCodeableConcept=F.cc(SO_V2, "SO:0001565", "gene_fusion")),
                      self.comp("48002-0", "Genomic source class [Type]", valueCodeableConcept=F.cc(F.LOINC, "LA6684-0", "Somatic"))]
            note = [{"text": f"{v['row'].get('Event_Info', '')}" + (f"; {v['row']['Site2_Effect_On_Frame']}" if v["row"].get("Site2_Effect_On_Frame") else "")}]
            suffix = f"fusion-{g1}-{g2}".lower()
        else:
            rec = v["detail"]["variant"]
            acc = rec["SPDI"].split(":")[0] if "SPDI" in rec else A.GRCH37_ACCESSION[rec["CHROM"][3:]]
            comps.append(self.comp("48018-6", "Gene studied [ID]", valueCodeableConcept=self.gene_cc(v["gene"])))
            if v["kind"] == "mutation":
                t = v["detail"]["mane"]
                if t and t.get("hgvsc"):
                    comps.append(self.comp("48004-6", "DNA change (c.HGVS)", valueCodeableConcept={"coding": [{"system": HGVS, "code": t["hgvsc"]}]}))
                if t and t.get("hgvsp"):
                    comps.append(self.comp("48005-3", "Amino acid change (pHGVS)", valueCodeableConcept={"coding": [{"system": HGVS, "code": t["hgvsp"].replace("%3D", "=")}]}))
                if t:
                    term = next((x for x in t.get("consequence_terms", [])), None)
                    if term:
                        comps.append(self.comp("molecular-consequence", "Molecular Consequence",
                                               valueCodeableConcept=F.cc(SO_V2, A.consequence_so().get(term, ""), term)))
            else:
                comps.append(self.comp("48019-4", "DNA change type", valueCodeableConcept=F.cc(SO_V2, "SO:0001019", "copy_number_variation")))
                comps.append(self.comp("82155-3", "Genomic structural variant copy number", valueQuantity={"value": rec["CN"], "system": F.UCUM, "code": "1"}))
            comps += [self.comp("48002-0", "Genomic source class [Type]", valueCodeableConcept=F.cc(F.LOINC, "LA6684-0", "Somatic")),
                      self.comp("62374-4", "Human reference sequence assembly version", valueCodeableConcept=F.cc(F.LOINC, "LA14029-5", "GRCh37")),
                      self.comp("48013-7", "Genomic reference sequence [ID]", valueCodeableConcept={"coding": [{"system": "http://www.ncbi.nlm.nih.gov/nuccore", "code": acc}]}),
                      self.comp("92822-6", "Genomic coordinate system [Type]", valueCodeableConcept=F.cc(F.LOINC, "LA30100-4", "0-based interval counting"))]
            if v["kind"] == "mutation":
                comps += [self.comp("81252-9", "Discrete genetic variant", valueCodeableConcept={"coding": [{"system": "https://api.ncbi.nlm.nih.gov/variation/v0/", "code": rec["SPDI"], "display": rec["SPDI"]}]}),
                          self.comp("69547-8", "Genomic ref allele [ID]", valueString=rec["REF"]),
                          self.comp("69551-0", "Genomic alt allele [ID]", valueString=rec["ALT"]),
                          self.comp("81254-5", "Genomic allele start-end", valueRange={"low": {"value": rec["POS"]}}),
                          self.comp("81258-6", "Sample VAF", valueQuantity={"value": rec["allelicFrequency"], "system": F.UCUM, "code": "1"})]
            else:
                comps.append(self.comp("81301-4", "Variant outer start-end", valueRange={"low": {"value": rec["POS"]}, "high": {"value": rec["END"]}}))
            note = None
            label = v.get("protein_short") or v["kind"]
            suffix = f"variant-{v['gene']}-{label}".lower()
        suffix = re.sub(r"[^a-z0-9-]+", "-", suffix).strip("-")[:50]
        r = F.resource("Observation", b.rid(suffix), "mcode-genomic-variant", status="final",
                       category=[F.cc(LAB, "laboratory", "Laboratory"), F.cc(V2_0074, "GE", "Genetics")],
                       code=F.loinc("69548-6", "Genetic variant assessment"), subject=b.subject,
                       effectiveDateTime=b.D(b.test_day), performer=[self.pathologist],
                       valueCodeableConcept=F.cc(F.LOINC, "LA9633-4", "Present"),
                       method=F.cc(F.LOINC, "LA26398-0", "Sequencing"), specimen=F.ref(b.tumor_specimen), component=comps)
        if note:
            r["note"] = note
        v["observation"] = b.add(r)
        return v["observation"]

    def implication(self, c: dict) -> dict:
        b, row = self.b, c["row"]
        comps = [self.comp("81259-4", "Associated phenotype",
                           valueCodeableConcept=phenotype(row)),
                 self.comp("93044-6", "Level of evidence", valueCodeableConcept={"text": civic.LEVEL_TEXT[c["level"]]})]
        for d in c["drugs"]:
            med = {"text": d["name"].title()}
            if d["rxcui"]:
                med = {"coding": [F.coding(F.RXN, d["rxcui"], d["name"].title())]}
            comps.append(self.comp("51963-7", "Medication assessed [ID]", valueCodeableConcept=med))
        comps.append(self.comp("predicted-therapeutic-implication", "Predicted Therapeutic Implication",
                               valueCodeableConcept={"text": f"Supports {c['significance']}"}))
        first = re.sub(r"[^a-z0-9]+", "", c["drugs"][0]["name"].lower())[:16]
        label = re.sub(r"[^a-z0-9]+", "", (c["variant"].get("protein_short") or c["variant"]["kind"]).lower())[:12]
        suffix = re.sub(r"[^a-z0-9-]+", "-", f"tx-{c['variant']['gene']}-{label}-eid{c['eid']}-{first}".lower())[:56]
        return b.add(F.resource("Observation", b.rid(suffix),
                                GR + "therapeutic-implication|2.0.0", status="final",  # the mCODE 4.0.0 / MTB app / server shape
                                category=[F.cc(LAB, "laboratory", "Laboratory"), F.cc(V2_0074, "GE", "Genetics")],
                                code=F.cc(TBD, "therapeutic-implication", "Therapeutic Implication"), subject=b.subject,
                                effectiveDateTime=b.D(b.test_day), performer=[self.pathologist],
                                derivedFrom=[F.ref(c["variant"]["observation"])], component=comps,
                                # same identifiers as the Operations server's implications (its "variant" value is
                                # CIViC's molecular-profile ID), so report and server can be matched exactly
                                identifier=[{"system": "https://civicdb.org/variant", "value": row["molecular_profile_id"]},
                                            {"system": "https://civicdb.org/evidence", "value": str(c["eid"])}],
                                note=[{"text": f"CIViC evidence EID{c['eid']} ({civic.RELEASE} release): {row['molecular_profile']}, {row['disease']}."}]))

    def region_studied(self) -> dict:
        b = self.b
        genes = {"coding": [self.gene_cc(g)["coding"][0] for g in sorted(self.genes)]}
        comps = [self.comp("48018-6", "Gene studied [ID]", valueCodeableConcept=genes)]
        return b.add(F.resource("Observation", b.rid("region-studied"), "mcode-genomic-region-studied", status="final",
                                category=[F.cc(LAB, "laboratory", "Laboratory"), F.cc(V2_0074, "GE", "Genetics")],
                                code=F.loinc("53041-0", "DNA region of interest panel"), subject=b.subject,
                                effectiveDateTime=b.D(b.test_day), specimen=F.ref(b.tumor_specimen), component=comps,
                                note=[{"text": "Whole-exome sequencing; this report covers 228 genes."}]))

    def pdl1(self, mdet) -> dict | None:
        b = self.b
        values = pdl1.cd274_values(self.snapshot)
        if not values:
            return None
        cuts = pdl1.cutoffs(values)
        smoker = any(r["id"] == b.rid("pack-years") for r in b.resources)
        genes = {d["gene"] for d in mdet if d["protein_altering"]}
        res = pdl1.tps(b.mrn, b.sample["SAMPLE_ID"], values, cuts, genes, smoker)
        self.notes.append(f"PD-L1 TPS {res['tps']}% ({res['category']}); {res['basis']}")
        return b.add(F.resource("Observation", b.rid("pdl1"), "mcode-tumor-marker-test", status="final",
                                category=[F.cc(LAB, "laboratory", "Laboratory")],
                                code=F.loinc("85147-7", "PD-L1 by clone 22C3 in Tissue by Immune stain Report"),
                                subject=b.subject, effectiveDateTime=b.D(b.test_day), specimen=F.ref(b.tumor_specimen),
                                valueQuantity=F.qty(res["tps"], "%"),
                                component=[self.comp("105302-4", "Immune stain PD-L1 scoring method - Tissue",
                                                     valueCodeableConcept=F.cc(F.LOINC, "LA34415-2", "PD-L1 TPS/TC/TCE"))],
                                note=[{"text": f"Tumor proportion score {res['tps']}% ({res['category']})."}],
                                extension=[F.ext("mcode-related-condition", "valueReference", b.primary_ref)]))

    def genomic_study(self, test: dict) -> dict:
        b = self.b
        specimens = [F.ref(b.tumor_specimen)] + ([F.ref(b.normal_specimen)] if getattr(b, "normal_specimen", None) else [])
        exts = [{"url": GR + "genomic-study-analysis-method-type",
                 "valueCodeableConcept": F.cc("http://hl7.org/fhir/uv/genomics-reporting/CodeSystem/genomic-study-method-type-cs",
                                              "sequence-analysis-of-the-entire-coding-region", "Sequence analysis of the entire coding region")}]
        # fusions are reported as DNA variants (mCODE), so the analysis lists gene_fusion when the patient has one
        # (Bob, 2026-11-12; FHIR only: Mongo holds no fusions, D20)
        types = test["dnaChangeType"] + (["FUSION"] if any(v["kind"] == "fusion" for v in self.reported) else [])
        exts += [{"url": GR + "genomic-study-analysis-change-type", "valueCodeableConcept": F.cc(SO, *CHANGE_TYPE_SO[t])}
                 for t in types]
        exts += [{"url": GR + "genomic-study-analysis-genome-build", "valueCodeableConcept": F.cc(F.LOINC, "LA14029-5", "GRCh37")},
                 {"url": GR + "genomic-study-analysis-genomic-source-class", "valueCodeableConcept": F.cc(F.LOINC, "LA6684-0", "Somatic")}]
        exts += [{"url": GR + "genomic-study-analysis-specimen", "valueReference": s} for s in specimens]
        exts.append({"url": GR + "genomic-study-analysis-regions", "extension":
                     [{"url": "description", "valueString": "Whole-exome sequencing; reported genes listed."}]
                     + [{"url": "studied", "valueCodeableConcept": self.gene_cc(g)} for g in sorted(self.genes)]})
        analysis = b.add(F.resource("Procedure", b.rid("genomic-study-analysis"), GR + "genomic-study-analysis",
                                    extension=exts, status="completed", category=F.cc(LAB, "laboratory", "Laboratory"),
                                    subject=b.subject,
                                    performedDateTime=b.D(b.test_day), performer=[{"actor": F.ref(self.lab)}]))
        return b.add(F.resource("Procedure", b.rid("genomic-study"), GR + "genomic-study",
                                extension=[{"url": GR + "genomic-study-analysis-ext", "valueReference": F.ref(analysis)}],
                                identifier=[{"system": "http://elimu.io/fhir/test-id", "value": test["testID"]}],
                                status="completed", category=F.cc(LAB, "laboratory", "Laboratory"),
                                code={"text": "Whole exome sequencing"}, subject=b.subject,
                                performedDateTime=b.D(b.test_day), performer=[{"actor": F.ref(self.lab)}],
                                reasonReference=[b.primary_ref]))

    def genomics_report(self, test, region, obs, imp_obs, markers, study, reported, implications):
        b = self.b
        parts = []
        for v in reported:
            if v["kind"] == "mutation":
                t = v["detail"]["mane"]
                p = (t.get("hgvsp") or "").split(":", 1)[-1] if t else ""
                parts.append(f"{v['gene']} {p or v.get('protein_short') or ''}".strip())
            elif v["kind"] == "fusion":
                parts.append(f"{v['gene']} fusion")
            else:
                parts.append(f"{v['gene']} {v['kind']}")
        msi = next((m for m in markers if m["id"] == b.rid("msi")), None)
        tmb = next((m for m in markers if m["id"] == b.rid("tmb")), None)
        pd = next((m for m in markers if m["id"] == b.rid("pdl1")), None)
        text = "Tumor whole-exome sequencing (reported genes: 228-gene panel): " + (", ".join(parts) or "no reportable variants") + "."
        if msi:
            text += {"stable": " Microsatellite stable;", "high": " Microsatellite instability-high (MSI-H);",
                     "indeterminate": " Microsatellite status indeterminate;"}[b.msi_call]
        if tmb:
            text += f" TMB {tmb['valueQuantity']['value']} mutations/Mb."
        if pd:
            text += f" PD-L1 (22C3) TPS {pd['valueQuantity']['value']}%."
        for c in implications:
            drugs = " + ".join(d["name"].lower() for d in c["drugs"])
            what = "sensitivity" if c["significance"] == "Sensitivity/Response" else "resistance"
            label = c["variant"].get("protein_short") or c["variant"]["kind"]
            text += f" {c['variant']['gene']} {label}: {what} to {drugs} in {c['row']['disease'].lower()} (level {c['level']})."
        issued = b.test_day + syn.rng(b.mrn, "ngs-issued").randint(7, 14)
        self.report_resource = b.add(F.resource(
            "DiagnosticReport", b.rid("genomics-report"), "mcode-genomics-report",
            extension=[{"url": GR + "genomic-study-reference", "valueReference": F.ref(study)}],
            status="final", category=[F.cc(F.LOINC, "LP7839-6", "Pathology"), F.cc(V2_0074, "GE", "Genetics")],
            code=F.loinc("51969-4", "Genetic analysis report"), subject=b.subject,
            effectiveDateTime=b.D(b.test_day), issued=b.D(issued) + "T12:00:00Z", performer=[self.pathologist],
            specimen=[F.ref(b.tumor_specimen)],
            result=[F.ref(region)] + [F.ref(o) for o in obs] + [F.ref(o) for o in imp_obs] + [F.ref(m) for m in markers],
            conclusion=text))


def reportable_genes_from_readme(readme: Path) -> list[str]:
    text = readme.read_text()
    app = text[text.index("## Appendix: reportable genes"):]
    return sorted({g.strip(" .") for line in app.splitlines() if re.match(r"^[A-Z0-9]+, ", line) for g in line.split(",")})
