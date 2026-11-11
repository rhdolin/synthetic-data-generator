# Mapping: cBioPortal (+ GDC) → FHIR and Mongo

One document for all mappings. Sections: clinical (below), treatments and genomic (to come), and the data quirks that affect mapping (at the end).

# Clinical: TCGA PanCancer Atlas → FHIR (mCODE 4.0.0)

**Status: draft 2, 2026-11-09** (adds the GDC clinical supplement, design D18). Scope: `data_clinical_patient.txt`, `data_clinical_sample.txt`, `data_timeline_status.txt`, `data_timeline_sample_acquisition.txt`, and the GDC case record (`gdc_case.json`, §7). Treatments (`data_timeline_treatment.txt`) come next, in their own section.

**Family:** the 32 `*_tcga_pan_can_atlas_2018` studies share these attribute names (see design Q1). Timeline vocabulary differs by cancer type; this draft is written against `luad_tcga_pan_can_atlas_2018`.

**Worked example:** `TCGA-J2-8194` ([cBioPortal](https://www.cbioportal.org/patient?studyId=luad_tcga_pan_can_atlas_2018&caseId=TCGA-J2-8194)). Every "example" value below is this patient's real value.

**Conventions** (from `clinical-data/README.md` §2 and the `TCGA-DD-A1EH` bundle in genomics-operations):
- **History is coded as history:** a prior condition ("prior X") is coded as "history of X" (e.g. SNOMED "History of malignant neoplasm of …", ICD-10-CM Z85.x), not as X itself.
- Transaction bundle; every entry `PUT <Type>/<id>`; ids `mtb-<mrn-lowercase>-<suffix>`; relative references.
- Every resource tagged `http://elimu.io/fhir/tags#mtb-demo`; a `display` on every coding.
- **Dates:** source dates are days from diagnosis (day 0). `D(n)` below means the synthetic diagnosis date + n days, where the synthetic diagnosis date comes from the seeded date shift (design, step 2).
- Codes marked ✓ were checked on tx.fhir.org on 2026-11-09. Unmarked codes are copied from the `TCGA-DD-A1EH` bundle (validated there).

**Sources:** cBioPortal files by name; GDC fields as `gdc:<path>` (e.g. `gdc:diagnoses[primary].laterality`), from `gdc_case.json` (GDC Data Release 46.0).

Legend for the **Use** column: **FHIR** mapped; **Mongo** to the genomics server only (D7); **input** used to derive something else; **—** not ported (reason given).

---

## 1. Patient (`mcode-cancer-patient`) — from `data_clinical_patient.txt`

| Source column | Example | Use | Target | Transform / notes |
|---|---|---|---|---|
| `PATIENT_ID` | `TCGA-J2-8194` | FHIR, Mongo | `Patient.identifier` (system `http://elimu.io/fhir/mrn`, use `official`); `Patient.id` = `mtb-tcga-j2-8194`; Mongo `Patients.patientID` | MRN = cBioPortal patient ID (D4) |
| `SEX` | `Female` | FHIR, Mongo | `Patient.gender` = `female`; US Core `birthsex` = `F`; Mongo `sex` | `Male`→`male`/`M`, `Female`→`female`/`F` |
| `DAYS_TO_BIRTH` | `-25441` | FHIR, Mongo | `Patient.birthDate` = D(−25441); Mongo `birthdate` | Check: −25441 days = 69.7 years, matches `AGE` 69 |
| `AGE` | `69` | input | — | Consistency check against `DAYS_TO_BIRTH` only |
| `RACE` | `White` | FHIR | US Core `race`: `ombCategory` `urn:oid:2.16.840.1.113883.6.238#2106-3` "White" ✓, `text` "White" | Crosswalk of TCGA race strings → OMB codes |
| `ETHNICITY` | `Not Hispanic Or Latino` | FHIR | US Core `ethnicity`: `ombCategory` `2186-5` "Not Hispanic or Latino" ✓ | Same crosswalk |
| `GENETIC_ANCESTRY_LABEL` | `EUR` | Mongo | `Patients.ancestry` | Not FHIR (D7). Values: AFR, AMR, EAS, EUR, SAS, admixed labels |
| `PRIOR_DX` | `Yes` | input | → §7 (GDC gives the type) | Cross-check: GDC has a `Prior primary` diagnosis |
| `NEW_TUMOR_EVENT_AFTER_INITIAL_TREATMENT` | `Yes` | input | — | Cross-check: should agree with recurrence/metastasis events in the status timeline (§4). 174 LUAD patients say Yes but only 99 have a dated event |
| `RADIATION_THERAPY` | `Yes` | input | — | Cross-check with radiation rows in the treatment timeline |
| `HISTORY_NEOADJUVANT_TRTYN` | `No` | input | — | Cross-check: no treatment before the resection date |
| `PERSON_NEOPLASM_CANCER_STATUS` | (empty) | input | — | Status at last contact; the timeline's `TUMOR_STATUS` is preferred (§4) |
| `OS_STATUS`, `OS_MONTHS`, `DSS_*`, `DFS_*`, `PFS_*` | `0:LIVING`, 23.8 … | input | — | Not ported as values (design "Scope"). Death, if any, comes from the timeline `DECEASED` event → `Patient.deceasedDateTime` |
| `DAYS_LAST_FOLLOWUP` | `724` | input | — | Sets the end of the synthetic timeline; the tumor board date follows it |
| `FORM_COMPLETION_DATE` | `6/6/12` | input | — | The only calendar date in the study; possible input for the treatment-era question (Q15) |
| `OTHER_PATIENT_ID` | GDC UUID | — | | Internal TCGA identifier; no demo value |
| `INFORMED_CONSENT_VERIFIED`, `IN_PANCANPATHWAYS_FREEZE` | `Yes` | — | | Research administration |
| `SUBTYPE`, `CANCER_TYPE_ACRONYM` | `LUAD` | — | | Redundant with the sample's OncoTree code |
| `WEIGHT` | (empty) | — (maybe FHIR) | body weight Observation if present | Rarely filled; decide later |
| Staging columns | | → §3 | | |
| Diagnosis columns (`ICD_10`, `ICD_O_3_*`) | | → §2 | | |
| *(none)* | | FHIR (synthetic) | `name`, `address`, `telecom` | Seeded generator (D11) |

## 2. Primary cancer (`mcode-primary-cancer-condition`)

Built from the patient and sample files.

| Source column | Example | Target | Transform / notes |
|---|---|---|---|
| `ICD_O_3_HISTOLOGY` + `ICD_O_3_SITE`, `ONCOTREE_CODE` | `8255/3`, `C34.3`, `LUAD` | `Condition.code`: SNOMED `254626006` "Adenocarcinoma of lung" ✓ | Via an OncoTree → SNOMED ConceptMap (design Q11). One code per OncoTree code |
| `ICD_10` + `gdc:diagnoses[primary].laterality` | `C34.3` + `Right` | `Condition.code`: a second coding, ICD-10-CM `C34.31` "Malignant neoplasm of lower lobe, right bronchus or lung" ✓ | ICD-10 `C34.3` isn't valid in ICD-10-CM without a side; GDC laterality supplies it (`C34.31` right, `C34.32` left; `C34.30` if GDC has none). *(Was decision 1; resolved by GDC.)* |
| `ICD_O_3_HISTOLOGY` | `8255/3` (adenocarcinoma with mixed subtypes) | mCODE `histology-morphology-behavior` extension: the ICD-O-3 code itself (`http://terminology.hl7.org/CodeSystem/icd-o-3`) | The extension's value set includes ICD-O-3, so the source code is used as is (most precise; SNOMED `35917007` is inactive). The validator warns that tx.fhir.org's ICD-O-3 is a fragment |
| `ICD_O_3_SITE` | `C34.3` | `Condition.bodySite`: SNOMED body structure by site and GDC laterality ✓ (main bronchus, upper/middle/lower lobe, right/left lung) | All LUAD sites C34.0–C34.9 covered; no side → the unsided structure. ICD-10-CM likewise: C34.x0/x1/x2 by side; C34.2 (middle lobe) has none |
| `gdc:diagnoses[primary].laterality` | `Right` | `bodySite` laterality extension: SNOMED `24028007` "Right" | Left = `7771000` (as `mcode_helpers`) |
| `gdc:diagnoses[primary].sites_of_involvement` | (none here) | `Condition.note` or bodySite qualifier | "peripheral lung" / "central lung" when present |
| `TUMOR_TYPE`, `CANCER_TYPE_DETAILED` | `Lung Adenocarcinoma, Mixed Subtype` | `Condition.code.text` | Keeps the source wording visible |
| `GRADE` (sample) | `NA` | (none) | Mostly empty for LUAD |
| *(day 0)* | | `onsetDateTime` = D(0); `recordedDate` = D(0) | |
| *(derived)* | | `clinicalStatus` = `active`, `verificationStatus` = `confirmed` | `active` because the last follow-up is "With Tumor" |
| *(§3)* | | `Condition.stage.summary` + `stage.assessment` → the stage group Observation | As in the TCGA-DD-A1EH bundle |

## 3. Staging (`mcode-tnm-*`)

TCGA gives **pathologic** staging only, in two places that should agree: the patient file (`AJCC_PATHOLOGIC_TUMOR_STAGE`, `PATH_T/N/M_STAGE`, `AJCC_STAGING_EDITION`, upper case) and the day-0 status timeline row (`PATHOLOGIC_STAGE`, `PATHOLOGIC_T/N/M`, `SYSTEM_VERSION`, lower case). Use the patient file; flag disagreements.

| Source | Example | Resource | `code` (LOINC) | `value` (SNOMED) | Notes |
|---|---|---|---|---|---|
| `AJCC_PATHOLOGIC_TUMOR_STAGE` | `STAGE IIB` | `mcode-tnm-stage-group` | `21902-2` Stage group.pathology Cancer | `1222769001` "IIB (AJCC)" ✓ | `hasMember` → the T, N, M Observations |
| `PATH_T_STAGE` | `T3` | `mcode-tnm-primary-tumor-category` | `21899-0` Primary tumor.pathology Cancer | `1229859000` "pT3 (AJCC)" ✓ | |
| `PATH_N_STAGE` | `N0` | `mcode-tnm-regional-nodes-category` | `21900-6` Regional lymph nodes.pathology | `1229947003` "pN0 (AJCC)" ✓ | |
| `PATH_M_STAGE` | `MX` | `mcode-tnm-distant-metastases-category` | `21901-4` Distant metastases.pathology ✓ | **none** | SNOMED has no AJCC "MX" code. **When M is MX, no M Observation** (decision 2) |
| `AJCC_STAGING_EDITION` | `7TH` | `method` on all four, and `Condition.stage.type` | | `443830009` 7th edition ✓, `444256004` 6th edition ✓ | mCODE requires a method. SNOMED has no code for the 3rd–5th editions (43 LUAD patients): the method is then text only, e.g. "American Joint Committee on Cancer, Cancer Staging Manual, 5th edition" |
| *(date)* | | `effectiveDateTime` | | | D(resection day) = D(32): pathologic staging follows the resection (§5) |
| *(focus)* | | `focus` → primary cancer Condition | | | |

Value crosswalks (TCGA strings → SNOMED AJCC qualifiers) live in the code for now (ConceptMaps later if wanted, Q11), covering every T, N, M and stage value in the study.

## 4. Disease course — from `data_timeline_status.txt`

| Event (`STATUS`) | Example | Target | Notes |
|---|---|---|---|
| `Initial Diagnosis` (day 0) | stage IIb, t3 n0 mx, 7th | §2, §3 | Staging duplicate; used only as a check |
| `Distant Metastasis` | day 470 | `mcode-secondary-cancer-condition` (onset D(470), `condition-related` → primary) **and** `mcode-cancer-disease-status` at D(470), value `271299001` "Patient's condition worsened" | **Site isn't in cBioPortal or GDC** → chosen by rule, seeded per patient (decision 3). GDC adds how it was confirmed (`gdc:follow_ups.evidence_of_progression_type` = "Biopsy with Histologic Confirmation") and a surgery at a distant site at day 518 → a biopsy/resection Procedure. Code e.g. `94391008` "Metastatic malignant neoplasm to lung" pattern |
| `Locoregional Recurrence` | — | `mcode-cancer-disease-status` (worsened); a recurrence Condition is optional | |
| `New Primary Tumor` | — | a second `mcode-primary-cancer-condition` | Type from GDC (`classification_of_tumor` = subsequent/synchronous primary, `tissue_or_organ_of_origin`) when present; else rule |
| `Last Follow Up` | day 724: `TUMOR_STATUS` With Tumor, `PRIMARY_THERAPY_OUTCOME_SUCCESS` Progressive Disease, `VITAL_STATUS` Alive | `mcode-cancer-disease-status` at D(724), value worsened | Supports "active" status. Evidence-type extension as in the TCGA-DD-A1EH bundle |
| `DECEASED` | (not this patient) | `Patient.deceasedDateTime` = D(day) | |
| `Last Known Alive` | | input | End of timeline |

Treatment `MEASURE_OF_RESPONSE` values (e.g. "clinical progressive disease" at day 584, "stable disease" at day 640) are also disease-status evidence; they're mapped with the treatment timeline.

## 5. Specimen and resection — from the sample file and `data_timeline_sample_acquisition.txt`

| Source column | Example | Target | Notes |
|---|---|---|---|
| `SAMPLE_ID` | `TCGA-J2-8194-01` | `Specimen.identifier`; Mongo `specimenID` | The TCGA sample ID is the specimen ID in both places (decision 4) |
| `SAMPLE_TYPE` | `Primary` | `Specimen.type`: tumor tissue | All LUAD samples are primary tumors |
| `TUMOR_TISSUE_SITE` / `ICD_O_3_SITE` | `Lung` / `C34.3` | `Specimen.collection.bodySite` | Same site as the Condition |
| `START_DATE` (sample acquisition) | `32` | `Specimen.collection.collectedDateTime` = D(32) | |
| `METHOD_OF_SAMPLE_PROCUREMENT` | `Tumor Resection` | `Specimen.collection.method`; and a `mcode-cancer-related-surgical-procedure` at D(32): resection and excisional biopsy → `77648009` "Excision of lesion of lung"; fine needle aspiration → `432231006` "Fine needle aspiration biopsy of lung"; not recorded (99 LUAD patients) → no procedure, specimen dated D(0) | Code as precisely as the data allows (decision 5): here only "tumor resection" of the right lower lobe is known, so a lung resection code with bodySite right lower lobe |
| `SOMATIC_STATUS` | `Matched` | input: the test was tumor/normal | The matched normal is GDC sample `TCGA-J2-8194-10A` (Blood Derived Normal) → a second `Specimen` (blood, ID `TCGA-J2-8194-10`), referenced by GenomicStudyAnalysis (D12). Collection date isn't recorded: the resection day is used |
| `TUMOR_NUCLEI_PERCENT`, `TUMOR_NECROSIS_PERCENT`, `TUMOR_WEIGHT` | 100, 0, 291 | — (maybe `Specimen.note`) | Lab QC values |
| `TISSUE_SOURCE_SITE`, `TISSUE_SOURCE_SITE_CODE` | `ABS - Lahey Clinic`, `J2` | — | Real institution; not used (the performer is a synthetic lab, D12) |
| `COUNTRY`, `VESSEL_USED`, `SAMPLE_PRESCREENED`, `SUBMITTED_FOR_LCE`, `TOP_SLIDE_SUBMITTED` | | — | Biobank administration |
| `TISSUE_*_COLLECTION_INDICATOR` | | — | |

## 6. Tumor markers (`mcode-tumor-marker-test`) — from the sample file

| Source column | Example | `code` (LOINC) | Value | Notes |
|---|---|---|---|---|
| `TMB_NONSYNONYMOUS` | `4.83` | `94076-7` Mutations/Megabase [# Ratio] in Tumor | `valueQuantity` 4.8, unit `1/10*6` "mutations/Mb" | Rounded to 1 decimal |
| `MSI_SENSOR_SCORE`, `MSI_SCORE_MANTIS` | `0.02`, `0.313` | `81695-9` Microsatellite instability [Interpretation] | `LA14122-8` "Stable" | Interpreted with the header's thresholds (MSIsensor: >10 MSI-H, 4–10 indeterminate; MANTIS: >0.6 MSI-H, 0.4–0.6 indeterminate). If the two disagree, MSIsensor decides (decision 6) |
| `ANEUPLOIDY_SCORE`, `TBL_SCORE`, hypoxia scores | 15, 22, … | — | | Research scores; no clinical use |
| *(missing)* fraction genome altered | | — | | Not in this study (inventory quirk); compute from segments or drop |
| *(derived)* PD-L1 | | see design "Derived clinical values" | | From *CD274* RNA |

All tumor markers: `effectiveDateTime` = the genomic test date: 10–21 days (seeded) after the first recurrence/metastasis, or after the resection if there is none (testing at metastatic diagnosis is standard of care); `specimen` → the tumor Specimen, `mcode-related-condition` → primary cancer.

## 7. From the GDC record only (`gdc_case.json`)

| GDC source | Example (TCGA-J2-8194) | Target | Notes |
|---|---|---|---|
| `diagnoses[classification_of_tumor=Prior primary].tissue_or_organ_of_origin` + `annotations` (LUAD: 86 priors, 33 sites; prostate, breast, uterus, head and neck most common) | `Breast, NOS`; "Prior malignancy breast cancer. No systemic chemotherapy or radiation." | `Condition` (us-core problem): SNOMED `429087003` "History of malignant neoplasm of breast" ✓ + ICD-10-CM `Z85.3` "Personal history of malignant neoplasm of breast" ✓; clinicalStatus `resolved`; note from the annotation | Specific SNOMED "History of malignant neoplasm of …" + ICD-10-CM Z85.x codes for prostate, breast, endometrium, uterus, cervix, bladder, kidney, thyroid, colon, rectum, larynx, skin, ovary, lung; any other site → `266987004` "H/O: malignant neoplasm" + `Z85.9` with the organ as text. **Rule: a prior cancer is past history, coded as "history of X"** (Bob, 2026-11-10), never as a malignancy disorder code, which would make it a current cancer (the MTB app treats any malignancy code as a primary cancer). Onset unknown, so no `onset` |
| `diagnoses[primary].year_of_diagnosis` | `2011` | input | Treatment era (Q15); not ported |
| `diagnoses[primary].site_of_resection_or_biopsy` | `Lung, NOS` | input | Cross-check with the resection Procedure |
| `exposures[Tobacco].tobacco_smoking_status` | `Current Reformed Smoker for > 15 yrs` | US Core smoking status Observation: LOINC `72166-2` "Tobacco smoking status" ✓, value SNOMED `8517006` "Ex-smoker" ✓ | Crosswalk of GDC smoking values → SNOMED; effective = D(0) |
| `exposures[Tobacco].pack_years_smoked`, `tobacco_smoking_onset_year`, `tobacco_smoking_quit_year` | 20; 1956; 1982 | Observation LOINC `8664-5` "Cigarettes smoked total (pack per year) - Reported" ✓ = 20 `{pack-years}`; quit year as a component or note | SNOMED `401201003` "Cigarette pack-years" ✓ is the alternative code. Calendar years are shifted with the patient's date shift (decision 7) |
| `follow_ups.ecog_performance_status`, `karnofsky_performance_status` | (none for this patient) | `mcode-ecog-performance-status` (LOINC `89247-1` ✓, `valueInteger`), `mcode-karnofsky-performance-status` | Real values where present (~225 LUAD patients); otherwise synthetic per Q6 |
| `follow_ups.molecular_tests` (gene, `test_result`, `variant_type`) | (none for this patient; e.g. TCGA-50-5935: KRAS Positive, EGFR Positive) | `mcode-tumor-marker-test` or a simple lab Observation: "KRAS mutation, positive" | Coarse prior clinical tests. **Check against the WES calls**: port as recorded with a note, and log disagreements (decision 8) |
| `follow_ups.other_clinical_attributes` (FEV1, DLCO) | FEV1 83% predicted, DLCO 59% predicted | Lab Observations (LOINC to choose) | Useful for surgery/radiation fitness; lower priority |
| `follow_ups[progression]` (`days_to_progression`, `progression_or_recurrence_type`, `evidence_of_progression_type`) | 470, Distant, Biopsy with Histologic Confirmation | §4 disease status evidence-type extension; a biopsy Procedure | Agrees with the cBioPortal event at day 470 |
| `diagnoses[*].treatments` (`treatment_outcome`, `therapeutic_agents`, …) | Docetaxel → Stable Disease | treatment mapping (next draft) | GDC and cBioPortal treatment rows should match; GDC adds outcome per regimen and surgery rows |
| `samples` (tumor `-01A`, solid tissue normal `-11A`, blood normal `-10A`, `-01Z` slide sample) | 4 samples | `Specimen`s: the tumor (§5) and the blood normal | Solid-tissue normal: not used by a test here; port or skip (decision 4) |
| `samples.portions.slides` (`percent_tumor_cells`, `percent_lymphocyte_infiltration`, `percent_necrosis`, …) | 100% tumor cells, 10% lymphocytes | `Specimen.note`, or pathology Observations | Ties to the slide images (design "Imaging"); lower priority |
| `demographic.vital_status`, `race`, `ethnicity`, `days_to_birth` | Alive, white, not hispanic or latino, −25441 | input | Cross-check against cBioPortal (agree here) |

**Precedence (draft):** cBioPortal for the shared clinical attributes and dates; GDC for anything cBioPortal lacks. Where both have a value and they disagree, use cBioPortal (it's the curated PanCancer release) and log the disagreement in the run report.

## Decisions for Bob

1. ~~Laterality / ICD-10-CM~~: resolved by GDC laterality (`C34.31`); `C34.30` when GDC has no side.
2. ~~pMX~~: **decided (Bob, 2026-11-10): omit the M Observation** when M is MX; the stage group stands.
3. ~~Metastasis site~~: **decided: fill by rule**, seeded per patient, from typical spread for the cancer type (LUAD: brain, bone, liver, adrenal, contralateral lung). Imaging can override it for patients with scans.
4. ~~Specimen IDs~~: **decided: the TCGA sample ID (e.g. `TCGA-J2-8194-01`) is the specimen ID** in both FHIR and Mongo `specimenID`; FHIR Specimens only for the tumor and the blood normal (the two the test used).
5. ~~Resection procedure code~~: **decided: as precise as the data allows**; a broader resection code is fine when the data doesn't say more.
6. ~~MSIsensor vs MANTIS~~: **decided: MSIsensor decides** when they disagree.
7. ~~Calendar years in GDC~~: **decided: shift them** by the patient's date shift, like every other date (birth, diagnosis, treatments, smoking onset/quit), so ages and intervals stay true to the source.
8. ~~Clinical tests that disagree with WES~~: **decided: port as recorded, with a note**, and log the disagreement in the run report.

# Treatments: TCGA PanCancer Atlas → FHIR (mCODE 4.0.0)

**Status: draft 1, 2026-11-10.** Sources: `data_timeline_treatment.txt` (cBioPortal) and `gdc:diagnoses[*].treatments` (GDC). The MTB app reads `MedicationRequest` (dates from `dosageInstruction.timing.repeat.boundsPeriod`), `MedicationAdministration`/`MedicationStatement`, and radiotherapy as a `Procedure` with the `mcode-radiotherapy-course-summary` profile (`mtb-app/src/lib/treatmentTimeline.ts`).

**LUAD coverage:** 558 rows for 202 patients. Filled: agent 99%, start 100%, stop 89%, response 54%, indication 34%, cycles 24%, dose 19%, route 26%; radiation dose 16% of rows, fractions 13%, site 20%.

## Worked example: `TCGA-J2-8194`

| Day | Source row | Becomes |
|---|---|---|
| 72 → (ongoing) | Carboplatin + Pemetrexed, `THERAPY_ONGOING` Yes | Regimen 1: two MedicationRequests, intent curative (adjuvant: after the day-32 resection, before any recurrence). End date by rule T1 |
| 240–271 | Radiation 1 (no type, dose or site) | Radiotherapy course summary, curative, site = the primary (right lower lobe) by rule T3 |
| 470 | *(status: distant metastasis)* | (clinical mapping §4) |
| 546–584 | Carboplatin + Gemcitabine, response "clinical progressive disease" | Regimen 2: palliative; status `stopped`, statusReason `266721009` "Absent response to treatment"; disease status at D(584) worsened, evidence physical examination |
| 597–640 | Docetaxel, response "stable disease" | Regimen 3: palliative; `completed`; disease status at D(640) stable |
| 663 → (ongoing) | Erlotinib, `THERAPY_ONGOING` Yes | Regimen 4: palliative; status `active` (ongoing at last follow-up, day 724) |

(Erlotinib, an EGFR inhibitor, for a KRAS G12C tumor is real 2011–2013 practice before routine testing; kept as is, see Q15.)

## Drugs → `MedicationRequest` (`mcode-cancer-related-medication-request`)

One MedicationRequest per drug. Drugs that start on the same day form one regimen.

| Source | Target | Transform / notes |
|---|---|---|
| `AGENT` / `gdc:therapeutic_agents` | `medicationCodeableConcept`: RxNorm ingredient | Crosswalk (all ✓ via RxNav, 2026-11-10): cisplatin 2555, carboplatin 40048, pemetrexed 68446, paclitaxel 56946, gemcitabine 12574, docetaxel 72962, erlotinib 337525, vincristine 11202, etoposide 4179, bevacizumab 253337, vinorelbine 39541, gefitinib 328134, zoledronic acid 77655, vinblastine 11198, irinotecan 51499, nivolumab 1597876, topotecan 57308, cyclophosphamide 3002, belinostat 1543543, cyanocobalamin (vitamin B12) 11248, pegfilgrastim 338036, denosumab 993449. Unmappable agents (`Nos`, blank, `Placebo`, trial products such as `Recprame + As15`): text only |
| `START_DATE`, `STOP_DATE` | `dosageInstruction.timing.repeat.boundsPeriod` start D(start), end D(stop); `authoredOn` = D(start) | Missing stop: rule T1 |
| same patient + same `START_DATE` | `groupIdentifier` = `<mrn>-regimen-<n>` (n in date order) | GDC lists the same drugs; used as a cross-check |
| `THERAPY_ONGOING`, stop date, response | `status`: `active` (ongoing at last follow-up and not superseded), `stopped` (stopped for progression), else `completed` | `intent` = `order` |
| `MEASURE_OF_RESPONSE` / `gdc:treatment_outcome` = progressive disease | `statusReason`: SNOMED `266721009` "Absent response to treatment" ✓ | From mCODE's termination-reason value set |
| `REGIMEN_INDICATION` | `treatmentIntent` extension: `373808002` "Curative - procedure intent" or `363676003` "Palliative - procedure intent" (mCODE value set) | Adjuvant → curative; Palliative, Recurrence, Progression → palliative. **Empty (most rows) → by rule:** started after the primary treatment and before the first recurrence/metastasis → curative; after → palliative. Supportive drugs (zoledronic acid, denosumab, pegfilgrastim, cyanocobalamin) → `399707004` "Supportive - procedure intent" |
| `NUMBER_OF_CYCLES` | `dosageInstruction.timing.repeat.count` | When present |
| `PRESCRIBED_DOSE` + `_UNITS` | `dosageInstruction.doseAndRate.doseQuantity` (UCUM: mg, mg/m2, mg/kg, mg/d) | `auc` (carboplatin AUC) → text in `dosageInstruction.text`, not a quantity |
| `ROUTE_OF_ADMINISTRATION` | `dosageInstruction.route`: IV `47625008` ✓, PO `26643006` ✓, IM `78421000` ✓ | |
| `TX_ON_CLINICAL_TRIAL` = Yes, `TREATMENT_SUBTYPE` | `note` | e.g. "Given on a clinical trial: Phase II Clinical Trial KRW2170" |
| *(derived)* | `reasonReference` → primary cancer Condition; `requester` → the synthetic oncologist (Practitioner) | US Core requires a requester |
| `TREATMENT_TYPE` | — | Unreliable (quirk 6); the drug code says what it is |
| `TOTAL_DOSE`, `CLINICAL_TRIAL_DRUG_CLASSIFICATION` | — | Rarely filled; no demo value |

## Radiation → `Procedure` (`mcode-radiotherapy-course-summary`)

One Procedure per `Radiation n` row.

| Source | Target | Transform / notes |
|---|---|---|
| *(row)* | `code` = SNOMED `1217123003` "Radiotherapy course of treatment" ✓ (fixed by the profile) | |
| `START_DATE`, `STOP_DATE` | `performedPeriod` | |
| `RADIATION_TREATMENT_ONGOING` | `status`: `in-progress` / `completed` | |
| `RADIATION_TYPE` | `modalityAndTechnique.modality`: External / External Beam → `1156506007` "External beam radiation therapy using photons"; Internal / Implants → brachytherapy (mCODE value set) | Empty (most rows): rule T2. mCODE requires a technique with photons; none is recorded, so technique = `UNC` "un-encoded" with text "Technique not recorded" |
| `RADIATION_DOSAGE` + `RADIATION_UNITS` | `doseDeliveredToVolume.totalDoseDelivered` in cGy | `gy` × 100; `mci` (radiopharmaceutical) → text |
| `NUMBER_OF_FRACTIONS` | `actualNumberOfSessions` | |
| `ANATOMIC_TREATMENT_SITE` | `bodySite` (mCODE treatment-location value set: e.g. `31094006` "Structure of lobe of lung", `39607008` "Lung structure", `12738006` "Brain structure", bones, `23451007` "Adrenal structure", `10200004` "Liver structure") | Primary Tumor Field / Local / Regional → the primary site; Distant Site / Distant Recurrence → the metastasis site (rule-filled, clinical decision 3). Empty: rule T3 |
| *(derived)* | `treatmentIntent`: same rule as drugs; `reasonReference` → primary cancer (or the metastasis Condition when the site is distant) | |

## Response → `mcode-cancer-disease-status`

| Source | Value | Evidence type |
|---|---|---|
| `complete response`, `partial response` | `268910001` "Patient's condition improved" | |
| `stable disease` | `359746009` "Patient's condition stable" | |
| `clinical progressive disease` | `271299001` "Patient's condition worsened" | `5880005` "Physical examination" ✓ |
| `radiographic progressive disease` | `271299001` worsened | `363679005` "Imaging" ✓ |
| *(other or missing)* | no Observation | |

`effectiveDateTime` = D(stop date) of the regimen; `focus` → primary cancer. These join the status-timeline disease status Observations (clinical §4); two on the same day with the same value are merged.

## Treatment rules

- **T1 — ongoing but superseded** (agreed, Bob 2026-11-10): a regimen marked ongoing with no stop date, followed by a later regimen (TCGA-J2-8194's adjuvant carboplatin/pemetrexed). Proposed end: start + cycles × 21 days (4 cycles if unknown), capped at the day before the next treatment; status `completed`. A regimen still ongoing at last follow-up with nothing after it stays `active` with no end.
- **T2 — radiation type unknown** (agreed): assume external-beam photons (by far the most common).
- **T3 — radiation site unknown** (agreed): before any recurrence → the primary tumor site; after → the metastasis site.
- **T4 — request vs. administration** (agreed): `MedicationRequest` only, as in the existing MTB cases. `MedicationAdministration` is for documenting an actual administration (e.g. inpatient); infused chemo blurs that line, so revisit if the demo ever needs per-dose records.

# Clinical: synthetic content (D11, D19)

**Status: draft 1, 2026-11-10.** Things a tumor board record has that TCGA doesn't. All values come from the seeded generator (one seed per MRN + field), so reruns give the same patient. Codes ✓ checked on tx.fhir.org, 2026-11-10.

**Dates.** The tumor board date `T` is the run's reference date (a run setting; fixed, so reruns are stable). The last follow-up lands `T` − 7…21 days (seeded); that fixes D(0) and so the whole date shift. For TCGA-J2-8194 (last follow-up day 724) the diagnosis lands about two years before `T`.

**Messy patients (D19).** A patient is messy if its seed falls in the bottom 15%. A messy patient gets one or two of: no ECOG, no labs, one drug coded as text only (no RxNorm), one encounter missing. Flagged in the run CSV.

| Resource | Content | Rule |
|---|---|---|
| **Practitioner** (`us-core-practitioner`), id `mtb-<mrn>-oncologist` | Name, NPI-format identifier (10 digits, valid check digit), qualification MD | One oncologist per patient; the requester and performer on everything clinical. No separate surgeon or pathologist |
| **Encounter** (`us-core-encounter`) | `class` AMB or IMP; `type` SNOMED (US Core requires a descendant of `308335008` "Patient encounter procedure"); `period`; `participant` → oncologist; `reasonReference` → primary cancer | One per event: diagnosis D(0), `185387006` "New patient consultation" ✓ (AMB); resection D(32), `86181006` "Evaluation and management of inpatient" ✓ (IMP, 3–6 days, seeded); start of each regimen and radiation course, metastasis, last follow-up: `108221006` "Evaluation AND/OR management - established patient" ✓ (AMB) |
| **Tumor board request** (`ServiceRequest`) | `code` SNOMED `384682003` "Multidisciplinary care conference" ✓; `status` active, `intent` order; `authoredOn` = `T` − 3…10 days; `occurrenceDateTime` = `T`; `requester` oncologist; `reasonReference` → primary cancer; `note` from a template | Template: "{age}-year-old {sex} with {stage} {cancer} ({key biomarkers}), now {disease status} after {n} lines of therapy ({drugs}). Presented for next-line treatment recommendation." TCGA-J2-8194: "71-year-old woman with stage IIB lung adenocarcinoma (KRAS G12C), now progressing after 4 lines of therapy (carboplatin/pemetrexed, carboplatin/gemcitabine, docetaxel, erlotinib)…" (biomarkers come from the genomic mapping) |
| **ECOG** (`mcode-ecog-performance-status`) | `valueInteger` + `interpretation` LOINC answer (0 `LA9622-7` ✓, 1 `LA9623-5` ✓, 2 `LA9624-3` ✓, 3 `LA9625-0` ✓); category survey | **Real** from GDC (`follow_ups.ecog_performance_status`, dated by `days_to_follow_up`) when present; Karnofsky likewise. Otherwise one synthetic value at `T` − 0…7 days: progressing on treatment → 1 (60%), 0 (25%), 2 (15%); tumor-free → 0 (70%), 1 (30%) |
| **Labs** (`us-core-observation-lab`) at `T` − 1…7 days | WBC `6690-2`, hemoglobin `718-7`, platelets `777-3`, neutrophils `751-8`, creatinine `2160-0`, ALT `1742-6`, AST `1920-8`, total bilirubin `1975-2`, albumin `1751-7` (all ✓); UCUM units; `referenceRange` | Seeded within normal ranges (sex-specific where needed). Systemic therapy in the last 90 days → mild anemia (hemoglobin 10–12 g/dL). No values that would by themselves change treatment (no organ failure) |
| **AllergyIntolerance** (`us-core-allergyintolerance`) | 80%: `716186003` "No known allergy" ✓; 20%: one drug allergy, e.g. `91936005` "Allergy to penicillin" ✓ with reaction `271807003` "Eruption" ✓ | |

Not generated: comorbidities, medications other than cancer treatment, family history (empty in GDC for LUAD), vital signs. Lung function tests from GDC (FEV1, DLCO) are left out for now.

# Reports: synthetic pathology and imaging (D22)

**Status: draft 1, 2026-11-10.** Simple DiagnosticReports (`us-core-diagnosticreport-note`) whose `conclusion` text comes from a template filled only from data already mapped, so the reports agree with the rest of the record. The MTB app's Reports section shows a report's `conclusion`. Codes ✓ on tx.fhir.org, 2026-11-10.

**Practitioners:** a synthetic pathologist and radiologist (seeded, like the oncologist) are the reports' `performer`. *(Changes the "one oncologist only" rule in the synthetic-content section.)*

## Pathology report (one per resection or biopsy specimen)

| Element | Value |
|---|---|
| `category` | LOINC `LP7839-6` "PATH" ✓ |
| `code` | LOINC `11526-1` "Pathology study" ✓ |
| `effectiveDateTime` | the specimen's collection date (TCGA-J2-8194: D(32)) |
| `issued` | 3–5 days later (seeded) |
| `specimen` | the tumor Specimen |
| `result` | the pathologic stage group Observation |
| `conclusion` | Template: "{SITE}, {PROCEDURE}: Invasive {histology}. Pathologic stage (AJCC {edition} edition): {pT} {pN}{ pM}." TCGA-J2-8194: "Lung, right lower lobe, resection: Invasive adenocarcinoma with mixed subtypes. Pathologic stage (AJCC 7th edition): pT3 pN0." (Grade added when present; MX omitted, decision 2.) |

**Available later, not used now:** the real report (GDC PDF) says right lower *lobectomy*, 4 cm, grade 1/4, visceral pleura and diaphragm invasion, 0/10 nodes, negative margins, and a clinical KRAS G12C / EGFR wild-type test (Clarient).

## Imaging reports

| When | `code` (LOINC) | Template `conclusion` |
|---|---|---|
| **Staging at diagnosis**: D(0) − 3…14 days (seeded) | `24627-2` "CT Chest" ✓ | "{laterality} {lobe} lung mass, consistent with primary lung malignancy. No evidence of distant metastatic disease." (A stage IV patient: the metastasis is described instead) |
| **Each metastasis or recurrence event**: on the event date | by the (rule-filled) site: bone → `39818-0` "NM Whole body Bone Views" ✓; brain → `24590-2` "MR Brain" ✓; liver, adrenal, lung → `72254-6` "CT Chest and Abdomen and Pelvis W contrast IV" ✓ | "New {site} lesion(s), consistent with metastatic disease." |
| **Radiographic progression** (treatment response "radiographic progressive disease") | `72254-6` CT chest, abdomen and pelvis ✓ | "Interval progression of disease." |
| **Restaging before the tumor board** if the last status is "with tumor": the last follow-up date | `72254-6` ✓ | "Persistent/progressive {sites} disease." |

All: `category` LOINC `LP29684-5` "RAD" ✓; `status` final; `issued` same day; `performer` the radiologist; `subject`. For TCGA-J2-8194: CT chest before diagnosis; bone scan at day 470 (her rule-filled metastasis is bone; she also has a real nuclear-medicine study in IDC); restaging CT at day 724.

# Genomic: cBioPortal → Mongo records (D2, D3, D15, D21)

**Status: draft 1, 2026-11-10.** Records for the Genomics Operations server's `FHIRGenomicsData` database, written as JSON (one file per collection) and loaded later. Shapes follow the existing records (`genomics-operations/utilities/vcf2json.py`, `patient_tests.json`) and the Google Doc schema. Worked example: TCGA-J2-8194. Fusions are **not** here (D20).

## Patients

| Field | Source | TCGA-J2-8194 |
|---|---|---|
| `patientID` | MRN | `TCGA-J2-8194` |
| `name` | the same synthetic name as the FHIR Patient | Tonya Jones |
| `sex` | `SEX` → `F` / `M` (as in the existing records) | F |
| `birthdate` | the FHIR Patient's shifted `birthDate`, stored as a date | 1955-03-30 |
| `ancestry` | `GENETIC_ANCESTRY_LABEL` | EUR |

## Tests (one per sequenced tumor sample)

| Field | Source / rule | TCGA-J2-8194 |
|---|---|---|
| `patientID` | MRN | |
| `testID` | `<MRN>-T<n>` | `TCGA-J2-8194-T1` |
| `specimenID` | the TCGA sample ID (decision 4) | `TCGA-J2-8194-01` |
| `testDate` | the genomic test date (clinical §6 rule: 10–21 days after first progression, else after resection), stored as a date | day 486 → 2026-03-24 |
| `genomicBuild` | the study's build (D21) | `GRCh37` |
| `studiedRegion` | the server's existing WES BED for the build (decision G2) | `Default.WES.GRCh37.bed` |
| `uncallableRegion` | the server's matching uncallable BED | `Default.WES.GRCh37.uncallable.bed` |
| `dnaChangeType` | exactly the types loaded for the test | `SNV`, plus `MNV`, `INS`, `DEL` if present, plus `CNV` if any copy-number record |

## Variants: mutations (from `data_mutations.txt`, `FILTER` = PASS only)

| Field | Source / transform | KRAS G12C example |
|---|---|---|
| `_id` | new hex id (also the `variantID` in MolecConseq) | |
| `patientID`, `testID`, `specimenID`, `testDate`, `genomicBuild` | from the Tests record | |
| `CHROM` | `chr` + `Chromosome` | `chr12` |
| `POS` | 0-based. SNV/MNV: `Start_Position` − 1. Indels: MAF has no anchor base, so POS = `Start_Position` − 2 for deletions (anchor = the base before) and `Start_Position` − 1 for insertions, with the anchor base fetched from the reference (Ensembl sequence API) | `25398284` |
| `REF`, `ALT` | `Reference_Allele`, `Tumor_Seq_Allele2`; indels VCF-style with the anchor base prepended (`-` removed) | `C`, `A` |
| `END` | `POS` + length of `REF` | `25398285` |
| `FILTER` | `PASS` | |
| `GT` | not set: somatic calls have no meaningful genotype; `allelicFrequency` carries the information (the Operations server doesn't read `GT`) | |
| `allelicFrequency` | `t_alt_count` / (`t_alt_count` + `t_ref_count`), 2 decimals | `0.38` |
| `genomicSourceClass` | `somatic` | |
| `SPDI` | GRCh37 RefSeq accession (e.g. `NC_000012.11`) + 0-based position; indels normalized (NCBI Variation Services SPDI canonicalization, or the server's `normalize-variant`) | `NC_000012.11:25398284:C:A` |
| *(large indels)* | **Indels longer than 50 bp, or whose SPDI can't be normalized, are structural variants** (Bob, 2026-11-11): `SVTYPE` `DEL`/`INS`; `POS` = the anchor base (0-based), `END` = `POS` + deleted length; `REF` = the anchor base; `ALT` = `<DEL>`/`<INS>`; no `SPDI`; no MolecConseq record. Example: TCGA-50-8460, a 25,855 bp deletion chr9:125,486,803–125,512,657 (OR1L4 into OR1L6) | |
| `allelicState` | not set (somatic, no genotype) | |
| `geneCode`, `geneDesc` | Only for variants within a gene (not `IGR`, `5'Flank`, `3'Flank`, for which the MAF still names a nearby gene). HGNC ID and the current approved symbol, from NCBI's MANE file, else HGNC REST; outdated MAF symbols (e.g. `GCN1L1` → `GCN1`) resolved through HGNC previous/alias symbols | `HGNC:6407`, `KRAS` |
| `popAlleleFreq`, `PS`, `CIPOS`, `CIEND` | not available | |

## Variants: copy number (from `data_cna.txt` + `data_cna_hg19.seg`)

| Field | Source / transform | PDGFRA example |
|---|---|---|
| *(which calls)* | genes with a discrete call of **+2 (amplification) or −2 (deep deletion)**; ±1 (low-level gain/loss) left out | PDGFRA +2, KIT +2 |
| `CHROM`, `POS`, `END` | the gene's GRCh37 coordinates (Ensembl GRCh37 lookup), POS 0-based | |
| `REF` | the reference base at `POS` (required by the schema) | |
| `SVTYPE` | `CNV` | |
| `CN` | estimated copy number from the overlapping segment: round(2 × 2^`seg.mean`), at least 3 for +2 and at most 1 for −2 | e.g. seg.mean 1.1 → 4 |
| `FILTER`, `genomicSourceClass`, test fields | `PASS`, `somatic`, from the Tests record | |
| `geneCode`, `geneDesc` | HGNC ID and symbol | |

## MolecConseq (one or two per mutation; none for copy number, as in the existing data)

| Field | Source (Ensembl VEP REST, GRCh37, `refseq=1`, `hgvs=1`) | KRAS G12C |
|---|---|---|
| `patientID`, `variantID` | | |
| `transcriptRefSeq` | Record 1: the MANE Select transcript (from NCBI's MANE list). Record 2: the transcript with VEP's most severe consequence, if that's a different transcript (prefer `NM_` over `XM_`). No MANE transcript: VEP's canonical RefSeq transcript, plus the most severe if different | `NM_004985.5` |
| `MANE` | true if MANE Select | true |
| `source` | `VEP` | |
| `cHGVS`, `pHGVS` | VEP `hgvsc`, `hgvsp`, both with their RefSeq accession (p. in 3-letter form) | `NM_004985.5:c.34G>T`, `NP_004976.2:p.Gly12Cys` |
| `featureConsequence` | VEP consequence terms → Sequence Ontology codes (`http://sequenceontology.org`) | `SO:0001583` missense_variant |
| `impact` | VEP `impact` | `MODERATE` |
| `functionalEffect` | When `impact` is HIGH: `SO:0002054` "loss_of_function_variant" (`http://sequenceontology.org`), as the existing pipeline sets from snpEff's LOF flag | (none: MODERATE) |

MANE always, plus the worst consequence when it falls on another transcript (decision G3).

## BED (studied region)

None generated: TCGA WES tests use the server's existing `Default.WES.GRCh37.bed` (186,982 ranges) and `Default.WES.GRCh37.uncallable.bed`, both in the `BEDs` collection (checked 2026-11-10). The FHIR side's gene list (Q14) is separate.

## Genomic decisions for Bob

- ~~G1 — copy number~~: **agreed (Bob, 2026-11-10):** only ±2 calls, with an estimated copy number from the segment data.
- ~~G2 — studied region~~: **decided:** use the server's `Default.WES.GRCh37.bed` (and its uncallable companion).
- ~~G3 — molecular consequences~~: **decided:** the MANE transcript always, plus the most severe consequence if it's on a different transcript.

# Genomics report → FHIR (D6, D12; decisions R1–R3)

**Status: draft 1, 2026-11-10.** Decided (Bob, 2026-11-10): **R1** the report's variant Observations are built **directly from our own records** (mutations, copy number, fusions), then checked against the Operations server after loading (not copied from the server, which has no fusions and its own CIViC snapshot); **R2** the region studied lists the **228 reportable genes**, with a description saying the test was whole-exome; **R3** PD-L1 cutoffs are **calibrated across the study's patients** with RNA. Shapes follow the existing MTB bundles (`TCGA-DD-A1EH`) and `clinical-data/README.md` §2.1. Codes ✓ on tx.fhir.org, 2026-11-10, unless copied from the precedent bundles.

## Genomics report (`mcode-genomics-report`), one per test

| Element | Value |
|---|---|
| `category` | LOINC `LP7839-6` Pathology; v2-0074 `GE` Genetics (as the precedent) |
| `code` | LOINC `51969-4` "Genetic analysis report" ✓ |
| `effectiveDateTime` / `issued` | the test date (Tests `testDate`); issued 7–14 days later (seeded) |
| `specimen` | the tumor Specimen (the blood normal is referenced from GenomicStudyAnalysis) |
| `performer` | the synthetic pathologist |
| `result` | the region-studied Observation, every reported variant, every therapeutic implication, TMB and MSI |
| extension `genomic-study` | → the GenomicStudy |
| `conclusion` | Template: "Tumor whole-exome sequencing (reported genes: 228-gene panel): {variant list, gene + p. or 'amplification' or 'fusion'}. {MSI status}; TMB {n} mutations/Mb. {for each implication: gene change: {implication} to {drug} in {disease} (level {X})}." TCGA-J2-8194: "…KRAS p.Gly12Cys, STK11 p.Lys146*, KEAP1 p.Glu540Argfs*32, XRCC1 p.Val326Leu, CSF1R p.Ala354Asp, MED12 p.Arg1462Cys; PDGFRA and KIT amplification. Microsatellite stable; TMB 4.8 mutations/Mb. KRAS p.Gly12Cys: sensitivity to sotorasib…" |

## Region studied (`mcode-genomic-region-studied`)

| Element | Value |
|---|---|
| `code` | LOINC `53041-0` "DNA region of interest panel" ✓ |
| component `48018-6` Gene studied | the 228 reportable genes as `http://www.genenames.org` HGNC codes (as the precedent) |
| description | "Whole-exome sequencing; this report covers 228 genes." (as a description component if the profile has one, else `note`; checked at build) |

## Variant Observations (`mcode-genomic-variant`), one per reported variant

**Which:** PASS mutations, ±2 copy-number calls and fusions (D20), in the 228 reportable genes (for fusions: either partner).

| Component | Mutation (KRAS G12C) | Copy number (PDGFRA amp) |
|---|---|---|
| `48018-6` gene studied | `HGNC:6407` KRAS | the gene |
| `48004-6` c.HGVS | MANE transcript from VEP: `NM_004985.5:c.34G>T` | — |
| `48005-3` p.HGVS | `NP_004976.2:p.Gly12Cys` | — |
| `molecular-consequence` | VEP most severe on MANE: `SO:0001583` missense_variant | — |
| `48019-4` DNA change type | — | `SO:0001019` copy_number_variation (as the server) |
| `82155-3` copy number | — | the estimated CN (Mongo `CN`) ✓ |
| `48002-0` genomic source class | `LA6684-0` Somatic | same |
| `62374-4` reference assembly | `LA14029-5` GRCh37 ✓ | same |
| `48013-7` genomic ref seq | `NC_000012.11` | chromosome accession |
| `81252-9` discrete variant (SPDI) | the Mongo `SPDI` | — |
| `69547-8` / `69551-0` ref / alt allele | `C` / `A` | — |
| `92822-6` coordinate system | `LA30100-4` 0-based interval counting | same |
| `81254-5` exact start-end (mutations) / `81301-4` outer start-end (CN) | `25398284` | gene start–end |
| `81258-6` VAF | 0.38 | — |

Plus: `code` `69548-6`, value `LA9633-4` Present, `method` `LA26398-0` Sequencing, category laboratory + GE, `specimen`, `effectiveDateTime` = test date, performer the pathologist. Every value must equal the Mongo record (same SPDI, build, test, specimen, date): the consistency checks compare them.

## GenomicStudy + GenomicStudyAnalysis (D12)

| Resource | Content |
|---|---|
| **GenomicStudy** (Procedure, `genomic-study`) | `status` completed; `code` whole-exome sequencing (text "Whole exome sequencing"); `identifier` = testID; `performedDateTime` = test date; `reasonReference` → primary cancer; `genomic-study-analysis` extension → the analysis; performer a synthetic lab (Organization) |
| **GenomicStudyAnalysis** (Procedure, `genomic-study-analysis`) | `genome-build` `LA14029-5` GRCh37; `change-type` the test's `dnaChangeType` as SO codes; `genomic-source-class` somatic; `specimen` → tumor **and** blood normal; `regions.studied` = the 228 HGNC genes (Q14); `regions.description` "Whole-exome sequencing; reported genes listed"; `method-type` sequencing |

## PD-L1 (design "Derived clinical values"; R3)

| Element | Value |
|---|---|
| Profile | `mcode-tumor-marker-test` |
| `code` | LOINC `85147-7` "PD-L1 by clone 22C3 in Tissue by Immune stain Report" ✓ (LOINC has no numeric-TPS code for 22C3) |
| `valueQuantity` | TPS in `%` |
| component | `105302-4` "Immune stain PD-L1 scoring method - Tissue" ✓ = `LA34415-2` "PD-L1 TPS/TC/TCE" ✓ |
| `interpretation` / `note` | category: <1%, 1–49%, ≥50% |
| `specimen`, date | tumor Specimen; the test date |

**Calibration (R3):** over the study's patients with RNA, the *CD274* RSEM values are split at the 1/3 and 2/3 quantiles (in LUAD advanced NSCLC roughly a third each); the patient's category comes from their value, and the TPS within the category is drawn by the seeded generator (<1%: 0; 1–49%: 1–49; ≥50%: 50–95). Patients without RNA (56 in LUAD): category drawn with weights shifted by the known correlations (EGFR/ALK, STK11/KEAP1 → lower; KRAS + smoker → higher). Cutoffs recorded in the run report.

# Therapeutic implications → FHIR (from CIViC)

**Status: draft 1, 2026-11-10.** cBioPortal has no therapeutic implications, so they come from CIViC, chosen by a fixed rule (code, not hand-picking, so it runs for any patient). They go in the genomics report as Genomics Reporting `therapeutic-implication` Observations, in the server's CIViC shape (`clinical-data/README.md` §2.1), each citing its CIViC evidence ID.

**Source:** CIViC's monthly data release (evidence summary file), pinned by release date like the datahub snapshot.

**Rule** (Bob, 2026-11-10):
1. **Evidence:** predictive, accepted, direction "supports", **level A or B** only.
2. **Variant match:** the CIViC molecular profile matches a reported variant: gene + protein change (e.g. "KRAS G12C"); "GENE Amplification"; fusions as "A::B Fusion" or partner-agnostic "v::B Fusion" / "A::v Fusion" (e.g. "v::ALK Fusion"). Gene-level "GENE Mutation" only when nothing variant-specific matches.
3. **Same tumor type:** the evidence's disease is the patient's tumor type. For LUAD: lung adenocarcinoma, non-small cell lung carcinoma or lung cancer (DOID 3910, 3908, 1324); not other cancers, and not pan-cancer ("Cancer", "Solid Tumor").
4. **Per variant: at most 2 sensitivity therapies**, highest level first, **plus resistance evidence** (the highest-level resistance item, if any). Ties at the same level (Bob, 2026-11-10): **a therapy available in the US beats an experimental one**, checked in RxNorm: every drug in the therapy has marketed clinical-drug products (SCD/SBD) via RxNav; an experimental drug has none (often not in RxNorm at all). If still tied, **include both**; an extra implication is fine.
5. **Drugs** to RxNorm via RxNav; combinations listed together.

**Example, KRAS G12C (CIViC, checked 2026-11-10):** sensitivity: sotorasib (EID9431, level A, NSCLC; 6 marketed products) and, at level B, docetaxel + selumetinib (EID1142, NSCLC; both marketed), which beats divarasib (EID11652, level B; no RxNorm entry, experimental); resistance: gefitinib (EID2257, level B, lung cancer). Note: the rule picks by CIViC's level and availability only; it doesn't judge whether later trials superseded an item (docetaxel + selumetinib's phase III trial was reportedly negative), so the report reflects CIViC as of the pinned release.

**Identifiers:** each implication carries the same identifiers as the Operations server's: `https://civicdb.org/variant` = the CIViC molecular-profile ID (e.g. 78 for KRAS G12C; what the server puts there) and `https://civicdb.org/evidence` = the evidence ID (e.g. 1142). The consistency check matches report and server implications on these.

**Consistency:** the Genomics Explorer shows the Operations server's own CIViC implications, which may differ (e.g. an older snapshot). Differences are logged by the consistency checks, not forced to agree.

# Fusions → FHIR only (D20)

**Status: draft 1, 2026-11-10.** Source: `data_sv.txt` (cBioPortal). TCGA's SV records are fusion calls (likely from RNA-seq; Q12 findings). They go **only** into the FHIR genomics report, as mCODE GenomicVariants; nothing goes to the genomics server.

**Reference:** mCODE "Fusion Events" (https://hl7.org/fhir/us/mcode/group-genomics.html#fusion-events). Two cautions found while checking it (2026-11-10):
- The page gives the code as "SO:001565"; the Sequence Ontology ID for `gene_fusion` is **SO:0001565** (7 digits). It is a descendant of `structural_variant` (SO:0001537), so it's in the Genomics Reporting molecular-consequence value set.
- mCODE's published example (`Observation-genomic-variant-fusion`) has the two genes but **no** molecular-consequence component. We include it, as the page's text says.

| Source | Target (`mcode-genomic-variant`) | Notes |
|---|---|---|
| *(row)* | `code` LOINC `69548-6` "Genetic variant assessment"; `valueCodeableConcept` LOINC `LA9633-4` "Present"; category laboratory | As in the other GenomicVariants |
| `Site1_Hugo_Symbol`, `Site2_Hugo_Symbol` | two `gene-studied` components (LOINC `48018-6`), each `http://www.genenames.org/geneId` with the HGNC ID and symbol (e.g. `HGNC:326` "AGPAT3") | HGNC IDs looked up by symbol (HGNC REST). Order: 5′ partner (Site1) first |
| *(fixed)* | `molecular-consequence` component (`http://hl7.org/fhir/uv/genomics-reporting/CodeSystem/tbd-codes-cs#molecular-consequence`) = `http://sequenceontology.org#SO:0001565` "gene_fusion" | System URI as in Genomics Reporting **2.0.0**, which mCODE 4.0.0 uses (3.0.0 writes `http://www.sequenceontology.org`) |
| `SV_Status` = SOMATIC | `genomic-source-class` component (LOINC `48002-0`) = `LA6684-0` "Somatic" | |
| `Site2_Effect_On_Frame` | `note` (e.g. "In-frame fusion") | No standard component for frame |
| `Event_Info` | `note` text (e.g. "CCDC6-RET fusion") | Readable label for the app |
| `Tumor_Split_Read_Count`, `Tumor_Paired_End_Read_Count` | — | Evidence counts; not reported |
| `Site1/2_Chromosome`, `_Position`, `NCBI_Build` | — | **Not used:** coordinates are labelled GRCh37 but fit GRCh38 (Q12 findings); genes alone identify the fusion |
| *(derived)* | `specimen` → tumor Specimen; `effectiveDateTime` = genomic test date; `subject`; `method` LOINC `LA26398-0` "Sequencing" | Same test as the other variants |

**Which fusions are reported** (decided, Bob 2026-11-10): a fusion goes in the report if **either partner is one of the 228 reportable genes** (D6). In-frame is not required. TCGA-J2-8194's only fusion, AGPAT3-APP, has neither gene, so this patient's report has no fusion.

# Data quirks: `luad_tcga_pan_can_atlas_2018` (datahub `a8ea891`)

Found by reading every file of the snapshot (2026-11-09). The ones that affect mapping or patient selection:

1. **52 `TCGA-17-Z0nn` patients** have no clinical, timeline, CNA, SV or RNA data, and all their mutations are `FILTER=wga`. They should be excluded from patient selection; 32 of them account for half the radiology links.
2. **13% of MAF rows are not `PASS`** (`wga`, `oxog`, `common_in_exac`, `nonpreferredpair`). Decide the filter policy.
3. **SV coordinates are labelled GRCh37 but fit GRCh38**, and the calls look like RNA fusions (Q12). Mutations and segments are genuinely GRCh37/hg19.
4. **Four "sequenced" samples have no mutation rows** (`TCGA-64-1678-01`, `TCGA-69-8453-01`, `TCGA-78-7143-01`, `TCGA-86-A4D0-01`).
5. **Coverage differs by file:** 566 clinical / 562 mutations / 511 CNA / 510 RNA and SV-profiled / 500 segments / 492 ancestry and sample acquisition / 456 HM450 (case list says 459) / 360 RPPA.
6. **`TREATMENT_TYPE` is inconsistent** (erlotinib under Chemotherapy, Targeted, Immunotherapy and Not Specified; bevacizumab and gefitinib under Immunotherapy). Radiation "agents" are `Radiation 1…4`. Zero-padded counts (`04`); cGy and Gy mixed.
7. **Stage appears twice:** patient file (upper case, more complete) and the status timeline's Initial Diagnosis row (lower case); counts differ slightly.
8. **`DSS_MONTHS` = `OS_MONTHS`** everywhere; `DAYS_TO_INITIAL_PATHOLOGIC_DIAGNOSIS` is always 0 and its description is wrong; `OTHER_PATIENT_ID` holds GDC UUIDs, not "DMP" IDs.
9. **`FORM_COMPLETION_DATE` is the only calendar date** (2010–2014, three formats including day `00`). It's an upper bound on the real diagnosis era, possibly useful for Q15.
10. **Missing tokens differ by file** (`''`, `NA`, `.`, single space); no bracketed TCGA tokens.
11. **Empty columns:** `WEIGHT`, `PRIMARY_LYMPH_NODE_PRESENTATION_ASSESSMENT`, `GRADE`, and several treatment columns; MAF `Hotspot` is all 0 and ExAC columns are empty.
12. **No fraction genome altered** in this study, although the design plans to port it.
13. **`TISSUE_SOURCE_SITE_CODE` lost leading zeros** (`5` for `05`).
14. **Hypoxia file has no meta file** and uses the older 5-row header; its patients equal the RNA-seq set.
15. **Root `data_normals_*` files** are legacy copies of the adjacent-normal expression with tumor (`-01`) sample IDs and no meta file; use `normals/` if ever needed.
16. **Genetic ancestry is transposed** (ancestries as rows, samples as columns) and keyed by sample, while `GENETIC_ANCESTRY_LABEL` is a patient attribute.
17. **Duplicate gene symbols** in the CNA (38) and expression (8, plus 13 blank) matrices; key on Entrez ID.
18. **HM450 meta description** ("one probe per gene") doesn't match the file (all 396k probes).
19. **MAF:** `t_depth` ≠ ref + alt in 31% of rows; `Transcript_ID` ≠ `Feature` in 20%; `Entrez_Gene_Id` = 0 in 10,492 rows; 1 duplicate row.
20. **Segment file uses chromosome `23` for X**; the MAF uses `X`.
