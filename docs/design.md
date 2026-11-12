# Synthetic data generation: design

Started 2026-11-08. Status: **two studies supported** (lung, colorectal; see "Plan"). This doc keeps decisions, findings and open questions; how each field is mapped, as implemented, is in `docs/mapping.md`. Sections written before the code (marked "proposal") are kept for their reasoning; where the build differs, a note says so.

**Path references.** Moved to this repository (`rhdolin/synthetic-data-generator`) on 2026-11-09. Paths in this doc refer to the **genomics-operations** repository (`FHIR/genomics-operations`):
- `clinical-data/…` means `genomics-apps/mtb-app/clinical-data/…`
- `utilities/…` and the scripts named in it (`vcfPrepper`, `run_vcf2json`, `to_mongo`, `bed2json`, `SPDI_Normalization.py`) are under `utilities/`
- `mcode_helpers.py` is in `clinical-data/`
- `MTB_TumorTypeCrosswalk.csv` is in `genomics-apps/mtb-app/public/data/`

## Plan

**Milestone 1 (done 2026-11-10):** one patient end to end, `TCGA-J2-8194`: mapping, FHIR bundle (validated, loaded to HAPI), Mongo records (loaded, checked through the Operations server).

**Milestone 3 (LUAD cohort): done** (2026-11-11). Crosswalks, 10 test patients, one command (`synthgen studies / patients / run`), selection (D23: living only), run report and case catalog; 29 patients built, validated and loaded; reports linked to real IDC images (best effort).

**Milestone 4 (colorectal, D17): done** (2026-11-12). Colorectal crosswalks; rules that apply to every study (pM1 at diagnosis, recurrences from GDC, lines of therapy T5, CEA and MMR IHC, MSI only when both scores agree, tumor-agnostic level-A evidence, one resistance implication per drug); 4 test patients reviewed, then 31 patients built, validated and loaded. The MTB app's colorectal terminology updates are on Bob's list.

**Then:** milestone 2, the imaging patient (needs Q16, Q17); milestone 5, distribution (packaging, "Supported studies" docs kept current).

Each step starts when Bob says so.

## Goal

Take real patients from cBioPortal and turn each one into a synthetic demo patient made of two parts:

1. **Clinical data as FHIR** (an mCODE transaction bundle for the clinical FHIR server, built the same way as the existing MTB cases in `genomics-apps/mtb-app/clinical-data/`).
2. **Genomic data as MongoDB records** for the genomics operations server, following the schema in the *FHIR Genomics Operations – Design Document* (Google Doc, "Database → Collections – Patient Data").

The two parts must describe the same patient and the same tests, following the consistency rules in `clinical-data/README.md` §4.

**Fully automatic (D11).** This is a synthetic-data pipeline: it runs end to end without a person in the loop and tolerates a few minor errors. Unlike the hand-built MTB cases, nobody writes a patient story, a scenario file or a case description. Everything comes from the harvested data plus deterministic rules.

## Decisions so far

| # | Decision | Source |
|---|---|---|
| D1 | The source is cBioPortal. | Bob, 2026-11-08 |
| D2 | Each patient gets one **Patient** record, one or more **Test** records, and multiple **Variant** records in MongoDB. | Bob, 2026-11-08 |
| D3 | The Mongo record shapes follow the Google Doc schema (summarized below). | Bob, 2026-11-08 |
| D4 | **MRN = the cBioPortal patient ID** (e.g. `TCGA-DD-A1EH`), used everywhere: FHIR identifier, Mongo `patientID`, folder and file names. | Bob, 2026-11-08 (Q3) |
| D5 | **Copy number and structural variants/fusions are included.** How they map is settled in the mapping discussion (below). | Bob, 2026-11-08 (Q4) |
| D6 | **The FHIR bundle includes a structured mCODE genomics report** for each test. Its variants are limited to the 228 reportable genes used for the other synthetic case reports (the appendix of `clinical-data/README.md`). A variant outside that list is in Mongo only, so only the Genomics Explorer shows it. | Bob, 2026-11-08 (Q7) |
| D7 | **No FHIR mapping for anything loaded into Mongo** (Patients, Tests, Variants, MolecConseq, BED). The Operations already turn those records into FHIR. For these, the mapping is cBioPortal → Mongo only. Exceptions are handled case by case where Mongo or the Operations can't yet handle the data: **fusions** (Q12) and **genomicStudy** (D12). | Bob, 2026-11-08 |
| D8 | **The project gets its own repository, `rhdolin/synthetic-data-generator`** (it has uses beyond genomics-operations). The generator code, mapping spec, ConceptMaps and crosswalks live here. The genomics operations server (Mongo) and the clinical FHIR server are load targets. | Bob, 2026-11-08 (Q9, Q11) |
| D9 | **StructureMap only if necessary.** The structural mapping is a Markdown spec plus code; the clinical-attribute → mCODE part is the candidate for a StructureMap if we later want one. | Bob, 2026-11-08 (Q11) |
| D10 | **Pin the source broadly, extract selectively.** Keep a pinned snapshot of each selected study (datahub commit or tarball), which holds everything cBioPortal has for it. Extract per patient only the data types we port; a data type needed later is extracted from the same snapshot, so it matches the version of the data already loaded. Porting to Mongo and FHIR is a separate stage. *Revised 2026-11-09: originally "harvest broadly" (extract everything per patient). The seeded generator keeps filler stable across later extractions, and the snapshot keeps the data version stable, so extracting unused data types (and writing their parsers) is no longer needed.* | Bob, 2026-11-08; revised 2026-11-09 |
| D11 | **Fully automatic; minor errors tolerated.** No hand-written patient story or scenario; synthetic filler comes from rules, and quality is checked automatically, not by hand review. | Bob, 2026-11-08 |
| D12 | **Test metadata goes out both ways now: BED files and genomicStudy.** Each test gets its Mongo Tests and BED records (for `$find-study-metadata`) *and* GenomicStudy / GenomicStudyAnalysis resources in the FHIR bundle. This is an exception to D7: `$find-study-metadata` returns a `Parameters` resource, not GenomicStudy, so the Operations can't produce it yet. Both outputs come from one internal test record, so they agree by construction. See "Test metadata". **As built:** no BEDs are generated; Tests point to the server's existing `Default.WES.GRCh37` BEDs (mapping G2), and GenomicStudyAnalysis lists the 228 reportable genes (R2). **Correction (2026-11-10):** mCODE 4.0.0 depends on Genomics Reporting **2.0.0**, which has no GenomicStudy; GenomicStudy/GenomicStudyAnalysis come from Genomics Reporting 3.0.0, so the bundle mixes the two (variants, implications and the report per 2.0.0 via mCODE; GenomicStudy per 3.0.0). See open question Q18. | Bob, 2026-11-09 |
| D13 | **WES or WGS studies only.** Targeted-panel studies (e.g. MSK-IMPACT) are out. A study with a mix of WES and panel samples is out unless only its WES/WGS samples are used. | Bob, 2026-11-09 |
| D14 | **First study: `luad_tcga_pan_can_atlas_2018`** (TCGA PanCancer Atlas lung adenocarcinoma; WES, GRCh37, 566 samples). It has every data type we map, many actionable variants (EGFR, KRAS G12C, BRAF, MET) and real fusions (ALK, ROS1, RET; Q12). Its clinical mapping then covers the other 31 TCGA PanCancer studies. See Q1. | Bob, 2026-11-09 |
| D15 | **Genomic data is written straight to Mongo records; no VCF for now** (route (b) of Q2). The generator writes Patients, Tests, Variants, MolecConseq and BED records from the cBioPortal calls, doing its own SPDI normalization and molecular consequences. The existing VCF pipeline (`vcfPrepper` → `run_vcf2json` → `to_mongo`) and its known bugs are left alone. A VCF per test can be added later if wanted. | Bob, 2026-11-09 |
| D16 | **A couple of representative patients have images** (pathology slides and/or radiology), linked from their FHIR record to the NCI Imaging Data Commons. The images must (1) **render in the MTB app** and (2) feed an **AI interpretation pipeline** that looks for imaging findings that would change therapy recommendations. So: link to IDC for full viewing; download the selected patients' images for rendering key images and for the AI. Patient selection takes image availability into account for these few patients. See "Imaging". | Bob, 2026-11-09 |
| D17 | **Second study: `coadread_tcga_pan_can_atlas_2018`** (TCGA PanCancer colorectal; WES, 594 patients), after lung. It adds metastatic-at-diagnosis patients (83 M1), richer treatment lines (FOLFOX, FOLFIRI, bevacizumab, cetuximab) and colorectal biomarkers (MSI-high 78, RAS and BRAF status, ERBB2, an NTRK3 fusion). Weaker on dated recurrences (23) and radiology (CT for 21); no metastatic sites. **New cancer type, so terminology updates too** (Bob, 2026-11-10): beyond this repo's colorectal crosswalks (sites, histologies, metastasis sites, CIViC tumor-type DOIDs, timeline vocabulary), the MTB app's cancer-type-specific terminology needs colorectal entries, e.g. the preset actionable filter, oncogenicity prediction, and the tumor-type crosswalk. **Cohort must include a couple of WNT-pathway and hedgehog-pathway patients** (Bob, 2026-11-12); no PD-L1 for colorectal (MSI drives immunotherapy). **Done 2026-11-12:** 31 patients; GDC supplies most recurrences, CEA and MMR IHC (mapping "Colorectal"). | Bob, 2026-11-09 |
| D18 | **For TCGA studies, the GDC clinical record supplements cBioPortal.** For every TCGA patient, harvest the **whole** NCI Genomic Data Commons case record (demographics, all diagnoses with treatments and pathology details, follow-ups with molecular tests and other clinical attributes, exposures, family histories, biospecimens down to slides and aliquots, annotations), not a chosen subset; use is decided at mapping time (as D10). Pinned by saving the response with the GDC data release (46.0 at first harvest). cBioPortal stays the primary source (one format across all studies; curated PanCancer genomics); where both hold a value, the mapping states which wins. See "GDC clinical supplement". | Bob, 2026-11-09 |
| D19 | **Gaps are filled with plausible values for most patients; a seeded fraction is deliberately messy** (resolves Q6). Values a real record would have but the source lacks (ECOG, labs, allergies, encounters) are generated by rule, seeded per patient. A seeded fraction of patients (default 15%) is left messy (some of: missing ECOG or labs, a drug coded as text only, a missing encounter), flagged in the run CSV so the mess is known. | Bob, 2026-11-10 |
| D20 | **Fusions go to the FHIR bundle only, not to the genomics server** (updates D5, resolves Q12 for now). Each reported fusion is an mCODE GenomicVariant per mCODE "Fusion Events" (https://hl7.org/fhir/us/mcode/group-genomics.html#fusion-events): two `gene-studied` components and a `molecular-consequence` component = `gene_fusion` **SO:0001565** (the mCODE page prints "SO:001565", a typo; checked in the Sequence Ontology). No SV records in Mongo, so `dnaChangeType` doesn't list SVs. See `docs/mapping.md` "Fusions". | Bob, 2026-11-10 |
| D21 | **Genomic records: native build, PASS calls, VEP annotation** (resolves Q8 and the molecular-consequence question). (a) A test's variants use the same genome build as the test: the study's native build (GRCh37 for TCGA), in Mongo and in the FHIR report alike. (b) Only mutations with `FILTER` = PASS. (c) Molecular consequences come from the **Ensembl VEP REST API** (GRCh37 endpoint, RefSeq transcripts), with the MANE Select transcript identified from NCBI's MANE list: no local install, so others can run the code easily. | Bob, 2026-11-10 |
| D22 | **Simple synthetic pathology and imaging reports, for now.** Each patient gets a pathology report for the resection or biopsy and imaging reports at diagnosis and at each progression, as DiagnosticReports whose text is filled from a template using only data we already map (so they agree with the rest of the record). Not yet used: the real TCGA pathology report PDFs (open in GDC; scanned, OCR text, much richer: size, grade, margins, nodes, clinical biomarker tests), which could later be extracted (an LLM step run by maintainers); and imaging AI findings (Q16). | Bob, 2026-11-10 |
| D23 | **Patient selection: living patients only.** A patient who died during TCGA follow-up (a `DECEASED` status event) isn't selected: a tumor board for them would fall after their death, and changing the record to avoid that isn't wanted. LUAD: 182 of the 514 usable patients died, leaving ~330. | Bob, 2026-11-11 |
| D24 | **Lean toward complex cases, keep simple ones.** For the MTB, selection prefers patients with a treatment history and a recurrence or metastasis plus an actionable variant (only ~12 such living LUAD patients: TCGA is mostly resected early-stage disease, 63% stage I). Simpler early-stage cases are kept: useful for other scenarios (e.g. adjuvant decisions with EGFR or ALK). | Bob, 2026-11-11 |

## Target MongoDB records (from the Google Doc schema)

*As built: `docs/mapping.md` "Genomic: cBioPortal → Mongo records". This section is the original target.*

**Patients**: one per patient.

| Field | Card. | From cBioPortal (proposed) |
|---|---|---|
| patientID | 1..1 | the MRN (see Q3) |
| name | 0..1 | synthetic (cBioPortal has no names) |
| sex | 0..1 | patient attribute `SEX` |
| birthdate | 0..1 | derived from age at diagnosis plus the date shift (Q6) |
| ancestry | 0..1 | race/ethnicity attributes, where present |

**Tests**: one per sequenced sample (or per assay, see Q5).

| Field | Card. | From cBioPortal (proposed) |
|---|---|---|
| patientID | 1..1 | MRN |
| testDate | 1..1 | synthetic, on the shifted timeline |
| testID | 1..1 | `<MRN>-T<n>` |
| specimenID | 1..1 | the TCGA/cBioPortal sample ID (e.g. `TCGA-J2-8194-01`), one per sample (mapping decision 4) |
| genomicBuild | 1..1 | the study's reference genome (TCGA PanCancer Atlas is GRCh37) |
| studiedRegion | 1..1 | a BedID for a BED we generate from the panel, kit or assay (see "Test metadata") |
| uncallableRegion | 0..1 | a BedID where derivable; probably omitted at first |
| dnaChangeType | 0..* | exactly the variant types loaded for the test (e.g. SNV, MNV, delins, CNV) |

**Variants**: one per mutation or copy-number call. Required fields: patientID, testDate, testID, specimenID, genomicBuild, CHROM (`chr1` form), POS (zero-based), REF, FILTER. Optional fields we can fill: ALT, END, SVTYPE, CN, GT, allelicFrequency (VAF from tumor alt/ref read counts), genomicSourceClass (`somatic`), SPDI, allelicState, geneCode/geneDesc. Optional fields we can't fill from cBioPortal: PS, CIPOS, CIEND, popAlleleFreq.

**By-products:** the current pipeline also writes **MolecConseq** (snpEff annotations) and **Phasing** (only for phased VCFs; none expected here). Not in D2's list, but MolecConseq is needed for the Genomics Explorer's molecular consequence column (see Q2).

**BED** records are needed too (see the next section).

## Test metadata: BED files and genomicStudy (D12)

*As built (2026-11-10): no BEDs generated (the server's `Default.WES.GRCh37` BEDs, mapping G2); GenomicStudy/GenomicStudyAnalysis per test, listing the 228 reportable genes (R2), with `gene_fusion` added when a fusion is reported. The automated consistency check against `$find-study-metadata` isn't implemented yet. The plan below is kept for its reasoning.*

**Built from cBioPortal's test information:** the sample's gene panel (`data_gene_panel_matrix.txt` and the panel definitions), the sequencing assay (WES, WGS or a targeted panel), the profiled data types and the genome build. The generator keeps these facts in **one internal test record** per test, and both representations below are produced from it:

1. **The metadata Operation (`$find-study-metadata`).** It reads the Tests record, and with `ranges`, the BED files it points to. So each test gets:
   - a **studied-region BED**, by assay (Bob: exons for WES, whole genes for WGS):
     - **WGS tests:** whole genes, i.e. in practice the existing default WGS BED
     - **WES tests:** exons. The capture kit's BED if cBioPortal or the study names it; otherwise the coding exons of all genes in the test's build
     - **targeted-panel tests** (e.g. MSK-IMPACT, which is neither WES nor WGS): proposed: exons of the panel's genes, since panels are exon-capture assays. To confirm
     - **Telling the assay apart** (to verify per family): the sample's panel ID in the gene panel matrix (e.g. `IMPACT468`; TCGA samples carry a WES panel or none), sample attributes such as `SEQ_ASSAY_ID` or `GENE_PANEL` where a study has them, and the study description
     - gene and exon coordinates from the server's gene/exon utility collections or RefSeq, in the test's build
   - an **uncallable-region BED**: not at first, unless the survey finds cBioPortal surfaces callability somewhere
   - BED records loaded with `bed2json` into the BED collection; `studiedRegion`/`uncallableRegion` in Tests hold the BedIDs. **`bed2json` may still be buggy: review and test it before use** (still needed under D15)
   - `dnaChangeType` set from the data types actually loaded for the test (e.g. a panel profiled for mutations and CNA but not SVs)
   - **BedIDs** named after the panel or kit and the build (e.g. `IMPACT468.GRCh37.bed`), so tests on the same panel share one BED
2. **The genomicStudy profiles in the FHIR bundle** (Genomics Reporting STU3, `hl7.fhir.uv.genomics-reporting#3.0.0`, which mCODE 4.0.0 builds on; FHIR R4). In R4 these are Procedure profiles with extensions. **Provisional:** the element-by-element mapping is settled in the mapping discussion, once we've examined the cBioPortal data. Per test:
   - **GenomicStudy** (`genomic-study`, a Procedure): subject = the Patient; `performedDateTime` = testDate; `code` = the assay (WES, WGS or targeted panel); `identifier` = testID; `reasonReference` = the primary cancer Condition; the `genomic-study-analysis` extension points to the analysis below.
   - **GenomicStudyAnalysis** (`genomic-study-analysis`, a Procedure), one per test to start:
     - `genome-build`: from the Tests record (same LOINC code `$find-study-metadata` returns)
     - `change-type`: the same Sequence Ontology codes as `dnaChangeType`
     - `genomic-source-class`: somatic
     - `specimen`: the Specimen for specimenID
     - `regions.studied`: **the gene list**, one repetition per gene as an HGNC CodeableConcept (`http://www.genenames.org/geneId`, e.g. `HGNC:76` "ABL1"), as in the IG example [analysisTumorNormalDNA](https://build.fhir.org/ig/HL7/genomics-reporting/Procedure-analysisTumorNormalDNA.json.html). No BED file is referenced (no GenomicDataFile). The genes are the same ones the studied-region BED is built from. **To decide:** for a panel this is the panel's genes (a few hundred); for WES or WGS it would be ~19,000 genes, so those may list only the 228 reportable genes (D6), with `regions.description` saying so (Q14)
     - `method-type`, `device` (sequencer), `metrics` (read depth, coverage): where the study supplies them; mostly absent for cBioPortal
     - `input` / `output`: omitted at first (there are no real BAM or VCF files, and no VCF is written for now, D15)
   - **The genomics report** (D6) links to its GenomicStudy through the `genomic-study` extension on GenomicReport.
   - **Performer:** a synthetic lab Organization, from the seeded generator (D11), shared by every test.
   - **Consistency check** (pipeline step 5): for each test, call `$find-study-metadata` and compare its testID, testDate, specimenID, genomicBuild and dnaChangeType with the GenomicStudy and GenomicStudyAnalysis in the bundle.
   - **Later option:** if `$find-study-metadata` is extended to return GenomicStudy, the bundle's copy can be replaced by the Operation's output, which removes the D7 exception.

The FHIR genomics report's **region-studied** Observation (D6) uses the same gene list as the studied-region BED, so the report, the GenomicStudyAnalysis and `$find-study-metadata` all agree on what was tested.

## cBioPortal data format

- **Spec:** https://docs.cbioportal.org/file-formats/ (one section per data type).
- **Bulk download:** the study tarballs at https://www.cbioportal.org/datasets, also in the `cBioPortal/datahub` GitHub repo (Git LFS).
- **REST API:** https://www.cbioportal.org/api/swagger-ui/index.html. Returns the same data as JSON, per study, patient or sample.
- **Study layout:** a folder of tab-delimited `data_*.txt` files, each paired with a `meta_*.txt` (stable ID, profile type, genome build).
  - `data_clinical_patient.txt` and `data_clinical_sample.txt`: four `#` header rows (display name, description, datatype, priority), then the attribute names. **Most attribute names and values are chosen by each study**; only a few are shared (`SEX`, `AGE`, `CANCER_TYPE`, `CANCER_TYPE_DETAILED`, `ONCOTREE_CODE`, `OS_STATUS`/`OS_MONTHS`, TMB and MSI scores).
  - `data_mutations.txt`: MAF (`Hugo_Symbol`, `Chromosome`, `Start_Position`, `End_Position`, `Reference_Allele`, `Tumor_Seq_Allele2`, `Variant_Classification`, `HGVSp_Short`, `t_ref_count`/`t_alt_count`, `NCBI_Build`, plus Genome Nexus annotation columns). MAF positions are 1-based and indels use `-`, with no anchor base.
  - `data_cna.txt`: gene × sample matrix of discrete calls (−2 deep loss … +2 amplification), **with no coordinates**. Some studies add `data_log2_cna.txt` (continuous) and `*.seg` (segments: chrom, start, end, `seg.mean`).
  - `data_sv.txt`: breakpoint pairs (`Site1_*`/`Site2_*` gene, chromosome, position), `Class`, `Event_Info`, `SV_Status`.
  - `data_timeline_*.txt`: events (treatment, specimen, status, lab test, imaging…) dated in **days relative to diagnosis**.
  - `data_gene_panel_matrix.txt` plus gene panel definitions: which genes each sample was profiled for.
  - Also: expression, methylation, protein (RPPA), generic assays (mutational signatures, arm-level CNA), GISTIC/MutSig, case lists, resources (slide links).

## Pin the source broadly, extract selectively (D10)

Revised 2026-11-09. The original plan extracted everything cBioPortal holds for each patient, so nothing would ever need fetching again. Two things make that unnecessary:
- **The seeded generator** (see "Proposed pipeline") gives the same name, address and date shift whenever a patient is processed, so a later extraction doesn't rename anyone or move their timeline.
- **A pinned snapshot** of the study keeps the data itself stable. Fetching a new data type later from the live API could return a newer, re-annotated version of the study, inconsistent with what's already loaded. Test dates come from days-from-diagnosis in the data, so they could move too.

Three stages:

1. **Pin:** once per selected study, record a fixed copy of the whole study: the `cBioPortal/datahub` commit, or the downloaded study tarball. This is the "broad" part. It costs one download and no code, and it lives in the git-ignored `data/` folder (TCGA PanCancer studies with expression and methylation can be hundreds of MB).
2. **Extract:** for each selected patient, pull from the snapshot only the data types we port:
   - every clinical attribute, including study-specific ones (the patient and sample rows are small, and the mapping needs to see them)
   - timelines
   - mutations
   - discrete copy number, plus segments where the study has them (an input for copy-number coordinates)
   - structural variants and fusions (Q12)
   - gene panels and the study metadata (genome build, assay)

   Each extract records its provenance: study ID, datahub commit or tarball version, and extraction date.
3. **Port:** map the extracts into Mongo and FHIR.

**Adding a data type later** means a new extractor and mapping run over the same pinned snapshot. Methylation, protein (RPPA), generic assays and study-level analyses (GISTIC, MutSig) get no extractor until something needs them. Expression is extracted only for *CD274*, the input for synthetic PD-L1 ("Derived clinical values").

**Studies not on datahub.** Public studies (TCGA, MSK-IMPACT) are on datahub. Restricted ones (GENIE, possibly MSK-CHORD) may be available only through the REST API or under their own terms (Q1, Q10). For those, pinning means saving the API responses for the whole study at harvest time, since the API has no versioning.

Cell-line and PDX studies stay out because they're never selected.

## GDC clinical supplement (D18)

**Why:** cBioPortal's TCGA clinical tables are a curated subset of the original TCGA clinical record. The NCI Genomic Data Commons (GDC, part of the Cancer Research Data Commons) holds the full record, joined by the same patient ID (`submitter_id` = the TCGA ID = our MRN). Checked 2026-11-09 for `TCGA-J2-8194` and across the 585 TCGA-LUAD cases (Data Release 46.0):

| In GDC, not in cBioPortal (LUAD) | Coverage |
|---|---|
| Laterality | ~510 patients (TCGA-J2-8194: right) |
| Other cancers with type: prior, synchronous and subsequent primaries; metastasis and recurrence diagnosis records | 75 prior, 24 synchronous, 13 subsequent; 82 metastasis, 66 recurrence records (TCGA-J2-8194: prior breast cancer) |
| Year of diagnosis | most patients (TCGA-J2-8194: 2011); bears on Q15 |
| Smoking status and pack-years | ~510 |
| ECOG / Karnofsky performance status | ~225 / ~125; real values instead of synthetic (Q6) |
| Clinical biomarker tests (gene, positive/negative) | KRAS 62, EGFR 24; coarse ("Mutation, NOS"); must be checked against the WES calls |
| Progression evidence (e.g. biopsy-confirmed), per-regimen treatment outcome, surgery at distant site | where recorded |
| Central vs. peripheral tumor location | 191 |
| Lung function (FEV1, DLCO) | some patients |
| Biospecimens: tumor, **blood-normal** and tissue-normal samples; slide-level pathology (% tumor cells, lymphocytes, necrosis) | all; the blood-normal sample is the matched normal of the tumor/normal test (GenomicStudyAnalysis, D12) |
| Annotations (curator free text) | some |

**Still missing in GDC:** the recurrence/metastasis **site** (`progression_or_recurrence_anatomic_site` is "not reported" for all 184 LUAD patients who have the field; colorectal names it for 13, mostly liver, and those are used), and family history (empty for LUAD). These stay rule-filled (D11).

**Colorectal (2026-11-12):** GDC also has preoperative CEA (402 cases), MMR immunohistochemistry (MLH1, MSH2, MSH6, PMS2) and dated recurrences for many more patients than cBioPortal's timeline (117 vs 18); all are used (mapping "Colorectal").

**How:** `synthgen.harvest.gdc` fetches the case with every entity expanded and saves `gdc_case.json` beside the patient's cBioPortal extract, with the data release and retrieval date. Not for non-TCGA studies.

**Precedence:** cBioPortal for the curated PanCancer genomics and the shared clinical attributes; GDC for clinical detail cBioPortal lacks. Where both hold the same fact (stage, dates, treatments), the mapping names the winner and the run report logs disagreements.

## Scope: what not to port yet (proposal)

In the pinned snapshot, but not extracted or ported to FHIR or Mongo for now:
- **Data types:**
  - expression (mRNA, miRNA), methylation, protein/RPPA: no Mongo schema, and no clinical demo use. **Exception:** *CD274* mRNA is extracted as the input for synthetic PD-L1 (see "Derived clinical values")
  - continuous log2 CNA: used only as an input when deriving copy number
  - mutational signatures and other generic assays
  - study-level analyses (GISTIC, MutSig)
- **Studies:**
  - **cell-line and PDX studies** (e.g. CCLE): not patients
  - **studies without per-sample mutation calls** (expression- or CNA-only)
  - **controlled-access or restrictive-licence studies**, until their terms are checked (Q10)
- **Columns:** survival summary columns (`OS_MONTHS`, `DFS_*`) aren't ported as values. They only help set the synthetic timeline. Read-level QC columns in the MAF are dropped.

Tumor mutational burden (TMB) and microsatellite instability (MSI) *are* ported, as tumor-marker Observations. Fraction of genome altered is not (absent from LUAD; not ported).

## Derived clinical values (synthetic, by rule)

Some results a tumor board expects aren't in the source studies. Where a value matters for treatment decisions, it's derived by rule from the patient's real data (D11) rather than drawn at random, and marked as synthetic (e.g. a note on the Observation).

**PD-L1 in lung cancer (proposed, 2026-11-09).** PD-L1 immunohistochemistry (tumor proportion score, TPS) drives first-line therapy in advanced NSCLC without a targetable driver (TPS ≥50%: immunotherapy alone is an option; 1–49%: usually chemo-immunotherapy; <1%: no single-agent immunotherapy), and some adjuvant or consolidation immunotherapy uses require ≥1%. With a targetable driver (EGFR, ALK, ROS1, RET, …), targeted therapy comes first and PD-L1 matters much less. TCGA has no PD-L1 IHC, so:
- **Input:** the tumor's own *CD274* (PD-L1 gene) mRNA expression, from the pinned snapshot (TCGA has RNA-seq for nearly every tumor). RNA correlates only moderately with IHC, but this keeps the value plausible for that tumor.
- **Calibration:** expression cutoffs chosen so the study's TPS categories come out at roughly a third each (≥50%, 1–49%, <1%), as reported for advanced NSCLC. A numeric TPS is drawn within the category by the seeded generator.
- **Known correlations,** checked on the output (the RNA approach should mostly produce them): lower PD-L1 in EGFR- and ALK-driven tumors; higher in KRAS-mutant tumors in smokers; lower with STK11 or KEAP1 mutations.
- **Fallback** where there's no expression: a rule using those correlations plus the category frequencies.
- **Output:** an mCODE tumor marker test Observation, on the sample's specimen and dated on the shifted timeline. Exact codes are settled in the mapping discussion.
- **Applies to:** lung studies first. Other cancer types where PD-L1 guides therapy can be added later. **As built:** lung only (mapping R3); none for colorectal, where MSI drives immunotherapy (Bob, 2026-11-12).
- **To check:** the treatment-era problem (Q15).

## Imaging (D16)

**Where the images are.** cBioPortal holds no images, only links (`data_resource_definition.txt` / `data_resource_patient.txt`) to viewers at the **NCI Imaging Data Commons (IDC)**. IDC stores the TCGA images as DICOM in public cloud buckets (AWS `s3://idc-open-data`, and Google Cloud), free to download with no account.

**What exists for TCGA** (IDC v25, checked 2026-11-09 with `idc-index`):

| Collection | Pathology slides (SM) | Radiology | Licence |
|---|---|---|---|
| `tcga_luad` (our first study) | 522 patients | CT 67, PET 23, NM 2. **No MRI** | CC BY 3.0 (images); CC BY 4.0 (IDC's derived annotations and segmentations) |
| `tcga_brca` | 1,098 | **MRI 137**, mammography 5 | same |
| `tcga_kirc` | 537 | CT 237, **MRI 62** | same |
| `tcga_gbm` | 607 | none in this IDC index (TCIA's TCGA-GBM MRI is a separate collection, to check) | same |

In `luad_tcga_pan_can_atlas_2018`, cBioPortal links slides for 514 patients and radiology for 62, but 32 of the 62 are the near-empty `TCGA-17-Z…` patients (see "Data quirks" in `docs/mapping.md`), so **radiology is usable for 30 patients**, all of whom also have slides. All 17 patients with actionable fusions (ALK, RET, ROS1, MET) have slides; only `TCGA-50-8460` also has radiology (chest CT).

**How IDC is accessed:**
- **Find:** the `idc-index` Python package ships a local index of all IDC DICOM series, queried with SQL. One row per series gives PatientID (= the TCGA patient ID = our MRN, D4), modality, StudyInstanceUID / SeriesInstanceUID, study date, size, licence and the bucket URL. BigQuery offers the same metadata in more depth, but needs a Google Cloud project.
- **View:** IDC hosts browser viewers: OHIF for radiology, SLIM for slide microscopy. URLs are built from the Study and Series UIDs (`get_viewer_URL`); these are the links cBioPortal shows.
- **Download (only if needed):** `idc-index` downloads series, studies or patients from the buckets (`download_dicom_series` etc.). A slide is often hundreds of MB and a CT series tens of MB.
- **No general-purpose image API to call at run time.** IDC's public DICOMweb proxy exists to drive its own viewers ("viewer-only, no downloads") and is quota-limited.

**Requirements (Bob, 2026-11-09):** images must (1) render in the MTB app, and (2) go through an AI interpretation pipeline that checks for imaging findings that would change therapy recommendations. Requirement (2) needs the pixels, so "link only" isn't enough: the selected patients' images are **downloaded** from IDC's buckets (free, a few hundred MB per patient). They are not re-hosted publicly unless a rendering option below needs it.

**Rendering in the MTB app: options**
- **(a) Link out to, or embed, IDC's viewers** (OHIF for radiology, SLIM for slides). No hosting, full viewer. **To check in a browser:** whether IDC allows its viewer pages to be embedded in another site (an iframe); the URLs return 404 to command-line requests, so this couldn't be tested from a script.
- **(b) An open-source viewer inside the app** (OHIF / Cornerstone3D for radiology, SLIM or OpenSeadragon for slides) reading from a DICOMweb server we control (e.g. Orthanc, or a Google Cloud Healthcare DICOM store) loaded with the downloaded series. IDC's own DICOMweb proxy is quota-limited and meant only for its viewers, so it can't serve our app. Most capable, most work.
- **(c) Key images:** pre-rendered PNG/JPEG images (selected CT slices at standard windows, a slide thumbnail and a few regions) attached to the patient's FHIR record (R4 `Media`, or a DocumentReference), plus a link out to the IDC viewer for the full study. Simplest for the app, and the same images can feed the AI.
- **Proposed:** (c) plus link-out first; (b) later if the demo needs full interactive viewing.

**AI interpretation: what it needs**
- **Inputs:**
  - **CT/PET:** the full series as DICOM, plus derived 2D images at standard windows (lung, mediastinal, bone). General multimodal models read 2D images, not volumes, so slice selection or sampling is part of preprocessing; specialized models (e.g. nodule detection, organ segmentation) could run on the full volume.
  - **Slides:** a thumbnail plus tiles at chosen magnifications; whole slides are too large to pass whole.
  - IDC already holds AI-derived annotations and segmentations for `tcga_luad` (ANN for 478 patients, SEG for 489; CC BY 4.0). Possible extra inputs; to examine.
- **Findings that alter therapy** (examples for the pipeline to look for): previously unknown metastases (brain, liver, bone, adrenal), which can change stage and intent; malignant pleural effusion or contralateral nodules (M1a); progression or response on current therapy; on pathology, a histology that differs from the recorded one.
- **No ground truth:** TCGA has no radiology reports, so the pipeline's findings can't be checked against a real report. If needed, a clinician could review the couple of demo cases.
- **To decide:** whether the AI pipeline lives in this repository or in the MTB app (the MTB app's case syntheses are outside this pipeline). Either way, this repository supplies the downloaded images, the derived key images, and the FHIR resources pointing at them.

**FHIR**
- An **ImagingStudy** per DICOM study (StudyInstanceUID, modality, series, an `endpoint` pointing at IDC), plus key images as `Media` or DocumentReference. **As built (2026-11-11):** ImagingStudy per real study, linked from the reports (mapping "Linking reports to real images"); no key images or downloads yet (Q16, Q17). AI findings, if this repository produces them, would be an imaging DiagnosticReport with Observations.

**Placing images on the timeline** (resolved 2026-11-11: `ImagingStudy.started` = the linked report's shifted date; the note gives the real DICOM date the viewer shows; README caveat)
- What the AI concludes depends on *when* the scan was taken: a pre-treatment baseline and a restaging scan mean different things for therapy. DICOM headers carry IDC's own (de-identified) dates, e.g. 2003-02-05 for `TCGA-50-8460`'s CT, while the TCGA clinical data counts days from diagnosis and gives no calendar diagnosis date. **To check:** whether the scan date can be placed relative to diagnosis (e.g. IDC or TCIA clinical metadata, or the date-shifting convention used for the TCGA collections). If it can't, each demo patient's scans get a stated, plausible position (e.g. staging at diagnosis), recorded in the run CSV.
- The FHIR resources use our shifted timeline, so they won't match the DICOM header dates.

**Finding (2026-11-11): TCGA has almost no real metastasis or progression scans.** The DICOM dates of the LUAD cohort's radiology cluster within weeks of each other, before surgery (e.g. TCGA-50-8460: four scans Jan–Mar 2003; TCGA-50-8459: CT and PET/CT two weeks apart). So reports link to images by the real gaps between scans: within 90 days of the first scan → the staging report; only clearly later scans → metastasis/progression reports (mapping.md, "Linking reports to real images"). Imaging AI on progression scans needs a source other than TCGA.

**Selection:** pick a couple of patients with images, at least one with radiology. For lung, radiology means CT/PET (no MRI in `tcga_luad`); an MRI example would come from another TCGA study (BRCA, KIRC).

**Attribution:** IDC images are CC BY: cite IDC and the source collection (`idc-index` provides citations). Sending the images to an AI model is compatible with CC BY.

## What cBioPortal provides (to verify against the API)

- **Clinical attributes** per patient and per sample: sex, age, cancer type and detailed type, stage, survival and status, and in some studies race/ethnicity, treatments and a timeline (days relative to diagnosis).
- **Mutations** (MAF-derived): gene, chromosome, start/end, ref/alt alleles, variant classification, protein change, tumor alt/ref read counts.
- **Copy number:** gene-level discrete calls (−2…+2, GISTIC), and in some studies segment data.
- **Structural variants / fusions** in some studies.
- **Gene panels** per sample (which genes were profiled).
- **No VCFs and no germline data** for most public studies: only somatic calls.

So "raw genetic data" will be generated from the somatic calls, not downloaded.

## Proposed pipeline

1. **Select, pin and extract:** choose study families and patients (Q1), pin each study's snapshot, and extract the data types we port for each patient (D10). The snapshot is the cache, so runs repeat exactly.
2. **Synthesize identity and timeline, by rule (D11):** MRN; name, address and other filler from a **seeded generator**; one fixed date shift per patient (as with TCGA-DD-A1EH); encounters and test dates placed on the shifted timeline from the harvested days-from-diagnosis. Free-text items (report conclusion, referral reason) come from templates filled from the structured data.
   - **The seeded generator:**
     - **What it is:** a fake-data library (e.g. Python Faker, plus our own pickers for dates and the like) whose random choices are seeded from the MRN.
     - **Repeatable:** the same MRN always gets the same name, address and date shift.
     - **Overhead:** negligible, microseconds per patient.
     - **Why it matters:** reruns are idempotent. The same FHIR resources are PUT again and the same Mongo records are replaced, so re-extracting, remapping or extracting more data types later (D10) never renames a patient or moves their timeline. That holds because the seed depends only on the MRN, never on the clinical or genomic data.
     - **One field per seed** (e.g. hash of MRN + `"name"`): adding a new filler field later then doesn't shift the existing ones.
     - **Alternative:** generate the filler once, save it in the patient's extract folder, and reuse it. Equally stable, but it adds a file to keep.
3. **Genomic data: Mongo records written directly (D15).** The generator writes all the records: Patients, Tests, Variants and MolecConseq (no BEDs: the server's default WES BEDs are used).
   - ~~**(a) Write a VCF per test, then run the existing pipeline:** `vcfPrepper` (snpEff + gnomAD AF) → `run_vcf2json` → `to_mongo`. Reuses SPDI normalization and the snpEff molecular consequences, and leaves a VCF per test.
     **Cost:** `vcfPrepper`, `run_vcf2json` and the related scripts have known bugs (e.g. the AD/DP allele-count error in the Google Doc). Taking this route means fixing them first, so the new patients don't inherit the bugs.~~ Not chosen (D15).
   - **(b) Write the Mongo records directly** from the cBioPortal calls (chosen, D15). The bugs in `vcfPrepper` and `run_vcf2json` can then wait. The generator does its own SPDI normalization (`SPDI_Normalization.py` or the server's `normalize-variant`) and writes its own MolecConseq records (see "Molecular consequences" below).
4. **Clinical bundle:** build an mCODE bundle with `mcode_helpers.py`: Patient with the MRN identifier, primary cancer, staging, disease status and treatments from the timeline. Add a GenomicStudy and GenomicStudyAnalysis per test (D12), and a structured genomics report per test (D6): region studied, a GenomicVariant per reportable variant, and therapeutic implications sourced from CIViC, as described in `clinical-data/README.md` §2.1. The reported variants must match the Mongo variants for the same test exactly (same SPDI, build, test, specimen and date).
5. **Validate and load, automatically:** validate each bundle (mCODE 4.0.0), load to HAPI, and load the Mongo records. *(As built: validation, loading and the run report; bundles load even with validation errors, which the report shows. The consistency checks below are not implemented yet.)* Then run automated consistency checks: reported variants match Mongo variants; tests, specimens and dates agree; `dnaChangeType` matches the loaded types; no Y-chromosome calls for female patients.
   - **Failures:** a patient with a minor problem (e.g. an unmapped code, a validator warning) is loaded anyway and logged; a patient with a serious one (e.g. the bundle won't load, variants won't normalize) is skipped and logged.
   - **One report per run, not per patient:**
     - a short summary: patients loaded or skipped, error counts by kind
     - one CSV with a row per patient (MRN, study, status, error counts), so 1,000 patients means 1,000 rows to filter, not 1,000 files
     - a detailed log for drilling into a given patient
   - **Provenance** stays with each patient's extract (D10), not in the report.
   - **Case syntheses:** the MTB app's AI syntheses are not part of this pipeline.

## Orchestration and distribution (proposal, 2026-11-09)

*As built (2026-11-11): one command, `synthgen` (`studies`, `patients`, `run`, `catalog`), with command-line options instead of a YAML config; supported studies live in `src/synthgen/studies.py`, not `studies/<id>/study.yaml`; outputs and the run report as proposed, plus a per-study case catalog. Readiness (below) is done: the README's "Supported studies" table, `synthgen studies` with caveats, and `run` refusing unsupported studies and patients. Packaging (pip or Docker) is milestone 5.*

Two roles, with different jobs:

1. **Maintainers (Bob and Claude) curate supported studies.** Choosing a study means making sure everything it needs is in place: the pinned snapshot (D10), the family's clinical mapping and crosswalks, known data quirks and their fixes (e.g. the SV build mismatch, Q12), derived-value rules (e.g. PD-L1), and a validation run that passes. A study is **supported** only when all of that is in the repository.
2. **Users synthesize their own patients** from supported studies, with **one command**, not a sequence of scripts.

**The user's view (sketch):**
```bash
pip install git+https://github.com/rhdolin/synthetic-data-generator   # or a Docker image
synthgen run --config my-run.yaml
```
```yaml
# my-run.yaml
study: luad_tcga_pan_can_atlas_2018      # must be a supported study
patients:
  count: 20                              # or an explicit list of patient IDs
  require: [actionable_variant, treatment_timeline]   # selection criteria
  prefer: [fusion, imaging]
output:
  dir: out/
  fhir_server: null                      # optional: load bundles here
  mongo_uri: null                        # optional: load genomic records here
```
- `synthgen run` runs every stage in order (pin → extract → synthesize → genomic → clinical → validate → load), downloading the pinned snapshot on first use.
- **Stages are cached and rerunnable:** a stage is skipped if its inputs haven't changed, so a failed run resumes where it stopped. The individual stages remain available as subcommands (`synthgen harvest`, …) for maintainers and debugging.
- **Output to files by default:** FHIR bundles, Mongo records as JSON, BEDs and the run report. Loading to a FHIR server or Mongo happens only if the user names one, so a user needs no servers to get started.
- **One run report** as already proposed (summary, per-patient CSV, detailed log).

**What lives in the repository for each supported study** (sketch): `studies/<study_id>/study.yaml` with the datahub commit, the study family, the mapping version, the data types supported, known quirks and their fixes, and the validation status. The family's mapping and crosswalks live in `mappings/`.

**Packaging:** `pip install` from GitHub works for pure Python. If the pipeline needs non-Python tools (e.g. snpEff, which is Java, for molecular consequences), a **Docker image** spares users from installing them. To decide once the tool list is known.

**Readiness must be explicit (placeholder, Bob 2026-11-11).** Users need to know exactly which studies and conversions are ready and what the caveats are. To do:
- **Main README:** a "Supported studies" table (study, status, what was verified, known limitations), kept current; plus general caveats (synthetic filler, rule-filled values such as metastasis sites and PD-L1, treatment era Q15, Genomics Reporting version mix Q18, report vs. Genomics Explorer differences).
- **Per study:** what "supported" required: clinical attributes and timeline vocabulary checked; crosswalks complete (sites, histologies, metastasis sites, CIViC tumor-type DOIDs, MTB app terminology); data quirks handled; a varied test set built and validated. Probably in `studies/<study_id>/study.yaml` plus a short notes section.
- **The tool enforces it:** `synthgen run` refuses unsupported studies and patients a study can't support (e.g. deceased, D23), with the reason.
- **Moving to a new study is prep work, not a switch:** the README should say so.

**Open questions:**
- **Who are the users?** CodeX participants, implementers, other projects? Their technical level sets how much the config can assume.
- **Same patient, same synthetic person?** The seed depends only on the MRN, so two users who pick `TCGA-50-8460` get the identical synthetic patient (name, dates, PD-L1). Good for reproducibility; but if users load into a shared server, or want variety, an optional **user seed** mixed into the MRN seed would give each user different filler. MRNs would then need a user-specific prefix to stay unique on a shared server.
- **Where users load:** their own servers, or the shared demo servers (the public HAPI server and the Genomics Operations Mongo)? Shared servers need write credentials and a policy.
- **Imaging:** downloads (D16) can be large; make them opt-in per run.

## Molecular consequences

*Superseded by D21: Ensembl VEP REST (GRCh37, RefSeq) with NCBI's MANE list. Kept for its reasoning.*

Two sources, used in this order:

1. **cBioPortal's own annotations.** Its mutation data is annotated by Genome Nexus (VEP-based): consequence terms, HGVSc, HGVSp and the transcript. To verify: whether the transcript is RefSeq or MANE (the MolecConseq schema wants `transcriptRefSeq`, a `MANE` flag and cHGVS with a RefSeq transcript). cBioPortal's default transcripts are Ensembl, so they may need mapping to RefSeq through MANE, or we may switch to (2).
2. **snpEff**, run on the variants (`snpEff -v GRCh38.p13` or the GRCh37 equivalent, as `vcfPrepper` does). This means writing a minimal VCF just for snpEff, but not using the rest of the pipeline. It produces the same MolecConseq content the server's existing patients have.

Copy-number and structural calls get no molecular consequences, matching the existing data.

## Mapping discussion

*Done: see `docs/mapping.md`. One change from the proposal below: the report's variants are built from our own records, not copied from the Operations server (mapping R1).*

A dedicated pass on how cBioPortal data maps into FHIR (mCODE) and Mongo, field by field, before any code. Per D7: genomic data maps to Mongo only (the Operations produce its FHIR), clinical data maps to FHIR. It must cover:

- clinical attributes → Patient, primary and secondary cancer conditions, staging, disease status, tumor markers (MSI, TMB), treatments and procedures from timelines
- mutations → Variants (Mongo)
- **the genomics report's GenomicVariant Observations** (D6): these are FHIR built from Mongo-held data. Proposal, in keeping with D7: after loading Mongo, call the Operations (`$find-subject-variants`, `$find-subject-molecular-consequences`, `$find-subject-tx-implications`) for the test, keep the variants in the 228 reportable genes, and place the returned resources (adjusted to the mCODE profiles) in the report. The report and the server then agree by construction, and there's no second variant → FHIR mapping. To check: how far the Operations' output already conforms to mCODE GenomicVariant
- **copy number** (D5): discrete gene-level calls (−2…+2) vs. segment data; how to get POS/END (gene coordinates or segments), CN (the discrete call is a relative level, not a copy number), SVTYPE (DEL/DUP/CNV) and dnaChangeType; how a report states copy-number findings
- **structural variants/fusions** (D5): breakpoints → Variant records (SVTYPE, END, pairs of breakpoints). **Fusions are the known exception to D7** and are deferred to Q12
- samples → Tests and Specimens; dates on the shifted timeline
- gene panels and assays → studied-region BEDs, Tests fields, the region-studied Observation, and GenomicStudy / GenomicStudyAnalysis (D12), all from the one internal test record

**Level of effort (rough):** the genomic side (mutations, CNA, SVs, BEDs, Mongo records) is largely shared across study families. The clinical side (attributes, timelines, crosswalks) has to be redone for each family. For one family: about 2–3 sessions to agree the mapping, then about 1–2 weeks to build and validate. Each further family adds mostly clinical-mapping work. Mapping arbitrary studies is open-ended, so we choose families deliberately (Q1).

## Open questions

- **Q1. Which study families, and how many?** A *study family* is a group of cBioPortal studies produced with the same sequencing pipeline and the same clinical-data conventions. Families differ widely in assay, genome build, which data types they have, and especially in clinical attribute names, values and timelines. **Each family we add needs its own clinical mapping and crosswalk entries**, so the number of families is the biggest driver of effort (see the level-of-effort note in the mapping discussion). **First study decided (D14); later families still open.**

  **Survey (2026-11-09)** of all 551 cBioPortal studies (REST API, plus the file list of `cBioPortal/datahub` at commit `a8ea891`):
  - 66 studies have mutations, discrete CNA, SVs and timelines. Licences of those 66 (from each study's `LICENSE` file): 33 Broad GDAC TCGA, 32 ODbL (stated or default), 1 CC BY-NC-ND 4.0 (MSK-CHORD; "NoDerivatives" likely rules out publishing synthetic derivatives). Some MSK studies state CC BY-NC-ND in their description while their `LICENSE` file says ODbL or is missing; where they disagree, the stricter applies.
  - **Assay (D13):** a sample with no gene-panel ID usually means WES/WGS, but not always (`ntrk_msk_2019` is MSK-IMPACT), so the assay is confirmed from the study description. All MSK-IMPACT studies are excluded.
  - Studies passing all criteria (WES/WGS; mutations, CNA, SVs, timelines; usable licence):

  | Study / family | Assay | Size | Licence | Character |
  |---|---|---|---|---|
  | **TCGA PanCancer Atlas** (32 studies, one per cancer type) | WES, GRCh37 | 36–1,084 samples each | Broad GDAC TCGA (a caution that data are preliminary; no reuse restriction) | Mutations, CNA, segments, SVs. Rich clinical data: TNM and AJCC edition, ICD-10 and ICD-O-3, grade, race, ethnicity and **genetic ancestry**, days to birth, MSI, TMB, fraction genome altered. Timelines: treatments (agent, regimen, intent, route, dose, cycles), recurrences and distant metastases with sites, sample acquisition. No labs, ECOG, PD-L1 or radiation detail. *(Corrects the earlier expectation of "little treatment detail".)* |
  | `pog570_bcgsc_2020` (BC Cancer POG) | **WGS**, GRCh37 | 570, advanced/metastatic | ODbL | Biopsy and treatment timelines; 556 SVs; no segment file. The only WGS candidate |
  | `brca_mbcproject_2022` | WES | 379 | ODbL | Metastatic breast; treatment timelines; "provisional" |
  | `nsclc_tcga_broad_2016` | WES | 1,144 | ODbL | Same patients as TCGA LUAD/LUSC; not used separately |
  | `blca_msk_tcga_2020` | WES + panel mix | 476 | TCGA | Only its WES samples would qualify; skip |

  - Excluded: MSK-IMPACT studies, incl. MSK-CHORD and MSK-MET (panels, D13); AACR GENIE (panels; not in public datahub; own terms).
  - **Proposed second family:** `pog570_bcgsc_2020`, to exercise WGS (whole-gene studied regions, Q14) and a different institution's clinical conventions. *(The second study chosen was colorectal TCGA instead, D17; POG570 remains a candidate.)*
  - **Within TCGA, attribute names are uniform but timeline vocabulary isn't** (checked 2026-11-09 across all 32 studies). 56 of 60 clinical attributes appear in every study. But the status timeline's `ANATOMIC_SITE` column (metastasis or recurrence site) exists in 24 studies and is **absent in 8, including LUAD, LUSC and COADREAD**; and event names vary by cancer type (e.g. liver "Intrahepatic/Extrahepatic Recurrence"; prostate "Bone Scan", "Diagnostic MRI", "First Biochemical Recurrence"; cervical and LGG "Performance Status Assessment"). So the TCGA clinical mapping is shared, but each cancer type needs its own timeline crosswalk entries.
  - **Consequence for LUAD and COADREAD:** recurrence and metastasis events have dates but no site in cBioPortal. The GDC record (D18) supplies the types of prior, synchronous and subsequent primaries, but not metastasis sites either; those are filled by rule (D11), or from imaging for patients with scans.
  - ~~Still to do for the chosen studies: count patients with actionable variants and set the selection criteria~~: done (`synthgen patients` features; D23, D24).
- ~~Q2. VCF route (a) or direct records (b)~~: decided, direct records (D15).
- ~~Q3. MRN scheme~~: decided, D4.
- ~~Q4. Copy number and fusions~~: included (D5); the mapping is covered in the mapping discussion.
- **Q5. Tests per patient.** One test per cBioPortal sample (primary and metastasis become two tests)? Add a synthetic germline test, or stay somatic only? *(As built: one somatic test per patient; every TCGA PanCancer sample used so far is a primary tumor.)*
- ~~Q6. Clinical depth~~: decided, D19. *(Original discussion kept below.)* **Q6. Clinical depth (all generated by rule, D11).** Clinical attributes only, or also timeline data (treatments, labs) where the study has it? Which synthetic filler do we generate (names, address, encounters, practitioner), and do we fill gaps the source lacks (e.g. ECOG, labs) with plausible values or leave them absent? **To come back to.** Bob's thoughts so far (2026-11-08):
  - **Fill** fields that a real record would generally have (e.g. ECOG).
  - **But** some messy data, closer to the real world, helps test how robustly the Case Summary and other consumers render gaps and oddities.
  - **Possible middle ground** (not decided): fill realistically by default, and leave a seeded fraction of patients deliberately messy (missing values, unmapped codes, free-text-only items), flagged in the run CSV so the mess is known.
- **Q14. Gene lists for large assays in GenomicStudyAnalysis (Bob, 2026-11-09: to revisit).** `regions.studied` lists studied genes one HGNC code per gene (D12). That works for panels but gives ~19,000 entries for a WES or WGS test. Options:
  - list only the 228 reportable genes (D6), with `regions.description` saying so
  - list all genes regardless of size
  - **propose a change to the Genomics Reporting profile** (e.g. a way to state "all genes in the build" or reference a named gene set)

  Revisit once we've examined the cBioPortal data (Q1). *(As built: the 228 reportable genes, with a description saying the test was whole-exome; mapping R2.)*
- **Q15. Treatment era vs. the shifted timeline.** TCGA patients were treated roughly 2005–2013, before immunotherapy and many targeted drugs were standard; their real treatments are mostly chemotherapy. Shifted into the present, histories can look like off-guideline care (e.g. PD-L1 ≥50% and no driver, yet chemotherapy alone; an EGFR mutation with no EGFR inhibitor). This applies to every TCGA patient, with or without synthetic PD-L1. Options:
  - accept it, as part of the deliberately messy data (Q6)
  - choose date shifts that keep each patient in their original treatment era
  - treat the history as past and focus the demo on the tumor board's next-step recommendation
- **Q16. Where the imaging AI pipeline lives** (Bob, 2026-11-09: to come back to): in this repository or in the MTB app? Either way this repository supplies the downloaded images, key images and FHIR pointers (see "Imaging").
- **Q17. Image rendering in the MTB app** (Bob, 2026-11-09: to come back to): key images plus a link to IDC's viewer, or a full interactive viewer in the app from the start? See "Imaging" options (a)–(c).
- **Q19. Wild-type biomarkers** (Bob, 2026-11-12: on the MTB backlog). In colorectal cancer, RAS/BRAF wild-type is what makes a patient a candidate for cetuximab or panitumumab, but CIViC evidence can't express "no RAS mutation", so these patients get no implication. The MTB app will handle it by layering in Cat-VRS and using the GenomicStudy (gene studied, no variant reported = wild type). Nothing to do in this pipeline for now.
- ~~Q18. Genomics Reporting 2.0.0 vs 3.0.0~~: **decided (Bob, 2026-11-10): keep the mix.** Everything follows Genomics Reporting 2.0.0 (mCODE 4.0.0, the MTB app, the Operations server) except GenomicStudy, GenomicStudyAnalysis and the report's `genomic-study-reference` extension (3.0.0). Validate with both IGs loaded; the cross-version errors on the 2.0.0 implication code are expected. Known effect: the MTB app shows both Procedures on the treatment timeline (GenomicStudyAnalysis as an unlabeled "Procedure"); Bob is tracking an app tweak to skip them.
- **Q13. Error tolerance.** Where is the line between "minor: load and log" and "serious: skip the patient"? Proposed starting point: anything that breaks loading, the Operations or report/server consistency is serious; unmapped codes, missing optional fields and validator warnings are minor.
- ~~Q7. Genetic reports in FHIR~~: yes, filtered to the 228 reportable genes (D6).
- ~~Q8. Genome build~~: decided, D21. *(Original:)* **Q8. Genome build.** Keep the study's native build (GRCh37 for TCGA), or lift over to GRCh38 so all new patients use one build?
- ~~Q9. Where the code lives~~: `rhdolin/synthetic-data-generator` (D8). Still open: one script per step or a single driver. With direct records (D15), no pipeline fixes in genomics-operations are needed for now.
- **Q12. Fusions** — *for now: D20 (FHIR bundle only).* Original question: **Fusions: to revisit (Bob, 2026-11-08).** Fusions are important for treatment decisions (e.g. ALK, ROS1, RET, NTRK, FGFR2), but today they are **neither in Mongo nor in our FHIR**: no Mongo schema, no Operations support, and none in the demo bundles. A dedicated discussion will decide how to represent them in Mongo, in the Operations and in the FHIR report, and whether the cBioPortal fusions wait until then. Until it's resolved, other structural variants and copy number go ahead under D5.

  **Findings from `luad_tcga_pan_can_atlas_2018` (2026-11-09):**
  - **The SV records look like RNA-seq fusion calls.** All 1,493 records have `tumorSplitReadCount` (reads crossing the junction) and `tumorPairedEndReadCount` (read pairs with a mate in each gene), the two kinds of evidence RNA fusion callers such as STAR-Fusion report. Most also give the frame (`in-frame` / `frameshift`), and `eventInfo` reads like "EML4-ALK fusion". `dnaSupport`/`rnaSupport` are empty and the study's README doesn't name the source; likely the TCGA PanCancer fusion analysis (RNA-seq). **To confirm.** This means TCGA "SVs" are mostly expressed fusions, not DNA rearrangements, which also affects how they map under D5.
  - **The coordinates appear to be GRCh38 although labelled GRCh37.** Every record says `ncbiBuild: GRCh37`, but the actionable fusion breakpoints fall inside their genes only on GRCh38 (checked against Ensembl): RET chr10:43,116,584 (RET is 43.57–43.63 Mb on GRCh37, 43.08–43.13 Mb on GRCh38); EML4 chr2:42,264,731 (42.40–42.56 Mb on GRCh37, ~42.17–42.33 Mb on GRCh38); CCDC6 chr10:59,906,122 (61.55–61.67 Mb on GRCh37, 59.79–59.91 Mb on GRCh38). Consistent with fusions called from RNA aligned to GRCh38, while the study's mutations are GRCh37. **Before loading any fusion or SV:** check every study's SV coordinates against gene positions on both builds, and use the build that fits (bears on Q8).
  - **Interim (Bob, 2026-11-12):** mCODE reports fusions as DNA variants, so a patient's GenomicStudyAnalysis lists `gene_fusion` when a fusion is reported (FHIR only). A separate project is defining an RNA variant profile, which may replace this.
  - **Candidate Mongo representation:** VCF breakend records (`SVTYPE=BND`, ALT such as `G]chr2:29223528]`, `MATEID` linking the two ends), which DNA SV callers (Manta, GRIDSS) use natively and Arriba can export. The Variants schema already has VCF-style SV fields (SVTYPE, END). Fusion-specific facts (genes, frame, read support, RNA vs DNA evidence) would need fields beyond that.
- **Q10. Data use terms:** confirm the chosen studies' terms allow redistribution in a public demo. Findings (2026-11-09):
  - **This work is for the CodeX FHIR Accelerator and is non-commercial** (Bob), so studies restricted to non-commercial use are acceptable.
  - Public cBioPortal/datahub data is under the **ODC Open Database License (ODbL)** unless a study says otherwise: attribute cBioPortal and the source study, and offer published derived data under ODbL (share-alike). This applies to the generated data, not the code (Apache-2.0).
  - TCGA data is under the Broad Institute GDAC TCGA Analysis Pipeline License. Some studies add their own restrictions, stated in the study's information, so check per study. GENIE is not in the public datahub (registration and its own terms).
- **Q11. Where and how the mapping specification is written.** It must outlive this design doc. Options, by layer:
  - **Code-to-code crosswalks:** OncoTree → SNOMED CT and ICD-O-3, stage strings → AJCC codes, drug names → RxNorm, cBioPortal `Variant_Classification` → Sequence Ontology, timeline event types → resource types.
    - **FHIR ConceptMap** fits these well: standard, can be validated, can be loaded to a terminology server and queried with `$translate`, and the generator can read it at run time as easily as a CSV.
    - Alternative: plain CSV (as `MTB_TumorTypeCrosswalk.csv` does today).
  - **Structural mapping:** cBioPortal files → FHIR resources (clinical) and Mongo records (genomic).
    - **FHIR StructureMap** (FHIR Mapping Language) plus logical models (StructureDefinitions) for the cBioPortal files would make the mapping itself a computable FHIR artifact.
    - Costs:
      - Many of our transforms are procedural: 1-based MAF → zero-based POS, MAF indels → anchored alleles from FASTA, SPDI normalization, snpEff or Genome Nexus lookups, gene-panel → BED, date shifting, filtering to the reportable genes, joining CIViC implications.
      - The Mongo target isn't FHIR, so it would need its own logical models.
      - Tooling (the FHIR validator's `-transform`, Matchbox, HAPI) is usable but heavy to debug.
    - Alternative: a **Markdown spec** (one table per cBioPortal file: source column → FHIR element / Mongo field → transform → notes) with the transforms in Python.
  - **Direction:** ConceptMaps for the crosswalks (proposed; not yet confirmed); Markdown spec plus Python for the structural mapping; StructureMap only if necessary (D9).
  - **The StructureMap candidate: clinical attributes → mCODE.** A patient's clinical attributes are one flat key/value row (`SEX`, `AGE`, `ONCOTREE_CODE`, `AJCC_PATHOLOGIC_TUMOR_STAGE`, `TMB_NONSYNONYMOUS`, …), and most of the mapping is declarative: this column → that element, with codes looked up in a ConceptMap (FML's `translate()`). That's what StructureMap does well. To keep it declarative, Python would first:
    - shift dates
    - assign resource ids and the MRN
    - add the synthetic content
    - write one normalized JSON row per patient, described by a logical model for that study family

    StructureMap then turns the row into Patient, primary cancer Condition, TNM stage Observations, tumor markers and disease status. Timelines (treatments, procedures) are messier and stay in Python.
  - Files live in the new repository (D8).
