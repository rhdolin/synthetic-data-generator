# Synthetic data generation: design

Started 2026-11-08. Status: **design discussion, no code yet.**

**Path references.** Moved to this repository (`rhdolin/synthetic-data-generator`) on 2026-11-09. Paths in this doc refer to the **genomics-operations** repository (`FHIR/genomics-operations`):
- `clinical-data/…` means `genomics-apps/mtb-app/clinical-data/…`
- `utilities/…` and the scripts named in it (`vcfPrepper`, `run_vcf2json`, `to_mongo`, `bed2json`, `SPDI_Normalization.py`) are under `utilities/`
- `mcode_helpers.py` is in `clinical-data/`
- `MTB_TumorTypeCrosswalk.csv` is in `genomics-apps/mtb-app/public/data/`

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
| D7 | **No FHIR mapping for anything loaded into Mongo** (Patients, Tests, Variants, MolecConseq, BED). The Operations already turn those records into FHIR. For these, the mapping is cBioPortal → Mongo only. Exceptions are handled case by case where Mongo can't yet hold the data: **fusions** are the known one (Q12). | Bob, 2026-11-08 |
| D8 | **The project gets its own repository, `rhdolin/synthetic-data-generator`** (it has uses beyond genomics-operations). The generator code, mapping spec, ConceptMaps and crosswalks live here. The genomics operations server (Mongo) and the clinical FHIR server are load targets. | Bob, 2026-11-08 (Q9, Q11) |
| D9 | **StructureMap only if necessary.** The structural mapping is a Markdown spec plus code; the clinical-attribute → mCODE part is the candidate for a StructureMap if we later want one. | Bob, 2026-11-08 (Q11) |
| D10 | **Harvest broadly, port selectively.** For each selected patient, extract everything cBioPortal has, even where downstream use is unclear, so nothing has to be harvested again later (e.g. fusions). Porting to Mongo and FHIR is a separate, selective stage. | Bob, 2026-11-08 |
| D11 | **Fully automatic; minor errors tolerated.** No hand-written patient story or scenario; synthetic filler comes from rules, and quality is checked automatically, not by hand review. | Bob, 2026-11-08 |

## Target MongoDB records (from the Google Doc schema)

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
| specimenID | 1..1 | `<MRN>-Sp<n>`, one per cBioPortal sample |
| genomicBuild | 1..1 | the study's reference genome (TCGA PanCancer Atlas is GRCh37) |
| studiedRegion | 1..1 | a BedID for a BED we generate from the panel, kit or assay (see "Test metadata") |
| uncallableRegion | 0..1 | a BedID where derivable; probably omitted at first |
| dnaChangeType | 0..* | exactly the variant types loaded for the test (e.g. SNV, MNV, delins, CNV) |

**Variants**: one per mutation or copy-number call. Required fields: patientID, testDate, testID, specimenID, genomicBuild, CHROM (`chr1` form), POS (zero-based), REF, FILTER. Optional fields we can fill: ALT, END, SVTYPE, CN, GT, allelicFrequency (VAF from tumor alt/ref read counts), genomicSourceClass (`somatic`), SPDI, allelicState, geneCode/geneDesc. Optional fields we can't fill from cBioPortal: PS, CIPOS, CIEND, popAlleleFreq.

**By-products:** the current pipeline also writes **MolecConseq** (snpEff annotations) and **Phasing** (only for phased VCFs; none expected here). Not in D2's list, but MolecConseq is needed for the Genomics Explorer's molecular consequence column (see Q2).

**BED** records are needed too (see the next section).

## Test metadata: BED files now, genomicStudy later

**Built from cBioPortal's test information:** the sample's gene panel (`data_gene_panel_matrix.txt` and the panel definitions), the sequencing assay (WES, WGS or a targeted panel), the profiled data types and the genome build. One set of facts feeds two representations:

1. **Now: the metadata Operation (`$find-study-metadata`).** It reads the Tests record, and with `ranges`, the BED files it points to. So each test gets:
   - a **studied-region BED**, by assay (Bob: exons for WES, whole genes for WGS):
     - **WGS tests:** whole genes, i.e. in practice the existing default WGS BED
     - **WES tests:** exons. The capture kit's BED if cBioPortal or the study names it; otherwise the coding exons of all genes in the test's build
     - **targeted-panel tests** (e.g. MSK-IMPACT, which is neither WES nor WGS): proposed: exons of the panel's genes, since panels are exon-capture assays. To confirm
     - **Telling the assay apart** (to verify per family): the sample's panel ID in the gene panel matrix (e.g. `IMPACT468`; TCGA samples carry a WES panel or none), sample attributes such as `SEQ_ASSAY_ID` or `GENE_PANEL` where a study has them, and the study description
     - gene and exon coordinates from the server's gene/exon utility collections or RefSeq, in the test's build
   - an **uncallable-region BED**: not at first, unless the survey finds cBioPortal surfaces callability somewhere
   - BED records loaded with `bed2json` into the BED collection; `studiedRegion`/`uncallableRegion` in Tests hold the BedIDs. **`bed2json` may still be buggy: review and test it before use**, whichever route Q2 takes
   - `dnaChangeType` set from the data types actually loaded for the test (e.g. a panel profiled for mutations and CNA but not SVs)
   - **BedIDs** named after the panel or kit and the build (e.g. `IMPACT468.GRCh37.bed`), so tests on the same panel share one BED
2. **Later: the genomicStudy profile** (Genomics Reporting IG). The same test facts (assay, panel or regions studied, specimen, date, performer, build, input and output files) become GenomicStudy / GenomicStudyAnalysis resources. We don't build these yet, but the generator keeps the test facts in one internal record that both outputs can be produced from, so adding genomicStudy later needs no new mapping from cBioPortal.

The FHIR genomics report's **region-studied** Observation (D6) uses the same gene list as the studied-region BED, so the report and `$find-study-metadata` agree on what was tested.

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

## Harvest broadly, port selectively (D10)

Two separate stages:

1. **Harvest:** for every selected patient, fetch **everything cBioPortal holds about that patient**, whether or not we have a use for it yet, into a raw, per-patient store in the new repository. This includes:
   - fusions and other structural variants
   - continuous copy number and segments
   - timelines
   - expression, methylation and protein values
   - generic assays
   - resource links
   - every clinical attribute, including study-specific ones
   - once per study: the study metadata, gene panels and study-level analyses (GISTIC, MutSig)

   Each record keeps its provenance: study ID, cBioPortal or datahub version, and fetch date. Once a patient is selected, the harvest never needs repeating to pick up a data type that turns out to matter later (fusions, Q12, are the motivating case).
2. **Port:** map a chosen subset of the harvest into Mongo and FHIR. The list below scopes only this stage; adding a data type later means a new mapping over data already on disk.

Expected harvest size is small per patient (expression is the largest at ~20,000 values per sample), so this costs little. Cell-line and PDX studies stay out because they're never selected.

## Scope: what not to port yet (proposal)

Harvested, but not ported to FHIR or Mongo for now:
- **Data types:**
  - expression (mRNA, miRNA), methylation, protein/RPPA: no Mongo schema, and no clinical demo use
  - continuous log2 CNA: used only as an input when deriving copy number
  - mutational signatures and other generic assays
  - study-level analyses (GISTIC, MutSig)
- **Studies:**
  - **cell-line and PDX studies** (e.g. CCLE): not patients
  - **studies without per-sample mutation calls** (expression- or CNA-only)
  - **controlled-access or restrictive-licence studies**, until their terms are checked (Q10)
- **Columns:** survival summary columns (`OS_MONTHS`, `DFS_*`) aren't ported as values. They only help set the synthetic timeline. Read-level QC columns in the MAF are dropped.

Tumor mutational burden (TMB), microsatellite instability (MSI) and fraction of genome altered *are* ported, as tumor-marker Observations.

## What cBioPortal provides (to verify against the API)

- **Clinical attributes** per patient and per sample: sex, age, cancer type and detailed type, stage, survival and status, and in some studies race/ethnicity, treatments and a timeline (days relative to diagnosis).
- **Mutations** (MAF-derived): gene, chromosome, start/end, ref/alt alleles, variant classification, protein change, tumor alt/ref read counts.
- **Copy number:** gene-level discrete calls (−2…+2, GISTIC), and in some studies segment data.
- **Structural variants / fusions** in some studies.
- **Gene panels** per sample (which genes were profiled).
- **No VCFs and no germline data** for most public studies: only somatic calls.

So "raw genetic data" will be generated from the somatic calls, not downloaded.

## Proposed pipeline

1. **Select and harvest:** choose study families and patients (Q1), then harvest everything cBioPortal holds for them (D10) via the REST API or datahub. The harvest is the cache, so runs repeat exactly.
2. **Synthesize identity and timeline, by rule (D11):** MRN; name, address and other filler from a **seeded generator**; one fixed date shift per patient (as with TCGA-DD-A1EH); encounters and test dates placed on the shifted timeline from the harvested days-from-diagnosis. Free-text items (report conclusion, referral reason) come from templates filled from the structured data.
   - **The seeded generator:**
     - **What it is:** a fake-data library (e.g. Python Faker, plus our own pickers for dates and the like) whose random choices are seeded from the MRN.
     - **Repeatable:** the same MRN always gets the same name, address and date shift.
     - **Overhead:** negligible, microseconds per patient.
     - **Why it matters:** reruns are idempotent. The same FHIR resources are PUT again and the same Mongo records are replaced, so re-harvesting, remapping or harvesting more later (D10) never renames a patient or moves their timeline. That holds because the seed depends only on the MRN, never on the clinical or genomic data.
     - **One field per seed** (e.g. hash of MRN + `"name"`): adding a new filler field later then doesn't shift the existing ones.
     - **Alternative:** generate the filler once, save it in the patient's harvest folder, and reuse it. Equally stable, but it adds a file to keep.
3. **Genomic data**, two routes (Q2). Either way, a small generator writes the Patients and Tests records, which `run_vcf2json` doesn't produce, and the studied-region BEDs (see "Test metadata").
   - **(a) Write a VCF per test, then run the existing pipeline:** `vcfPrepper` (snpEff + gnomAD AF) → `run_vcf2json` → `to_mongo`. Reuses SPDI normalization and the snpEff molecular consequences, and leaves a VCF per test.
     **Cost:** `vcfPrepper`, `run_vcf2json` and the related scripts have known bugs (e.g. the AD/DP allele-count error in the Google Doc). Taking this route means fixing them first, so the new patients don't inherit the bugs.
   - **(b) Write the Mongo records directly** from the cBioPortal calls. The bugs in `vcfPrepper` and `run_vcf2json` can then wait. The generator does its own SPDI normalization (`SPDI_Normalization.py` or the server's `normalize-variant`) and writes its own MolecConseq records (see "Molecular consequences" below).
   - No recommendation yet: it depends on whether fixing the pipeline is work we want now anyway.
4. **Clinical bundle:** build an mCODE bundle with `mcode_helpers.py`: Patient with the MRN identifier, primary cancer, staging, disease status and treatments from the timeline. Add a structured genomics report per test (D6): region studied, a GenomicVariant per reportable variant, and therapeutic implications sourced from CIViC, as described in `clinical-data/README.md` §2.1. The reported variants must match the Mongo variants for the same test exactly (same SPDI, build, test, specimen and date).
5. **Validate and load, automatically:** validate each bundle (mCODE 4.0.0), load to HAPI, and load the Mongo records. Then run automated consistency checks: reported variants match Mongo variants; tests, specimens and dates agree; `dnaChangeType` matches the loaded types; no Y-chromosome calls for female patients.
   - **Failures:** a patient with a minor problem (e.g. an unmapped code, a validator warning) is loaded anyway and logged; a patient with a serious one (e.g. the bundle won't load, variants won't normalize) is skipped and logged.
   - **One report per run, not per patient:**
     - a short summary: patients loaded or skipped, error counts by kind
     - one CSV with a row per patient (MRN, study, status, error counts), so 1,000 patients means 1,000 rows to filter, not 1,000 files
     - a detailed log for drilling into a given patient
   - **Provenance** stays with each patient's harvest (D10), not in the report.
   - **Case syntheses:** the MTB app's AI syntheses are not part of this pipeline.

## Molecular consequences

Two sources, used in this order:

1. **cBioPortal's own annotations.** Its mutation data is annotated by Genome Nexus (VEP-based): consequence terms, HGVSc, HGVSp and the transcript. To verify: whether the transcript is RefSeq or MANE (the MolecConseq schema wants `transcriptRefSeq`, a `MANE` flag and cHGVS with a RefSeq transcript). cBioPortal's default transcripts are Ensembl, so they may need mapping to RefSeq through MANE, or we may switch to (2).
2. **snpEff**, run on the variants (`snpEff -v GRCh38.p13` or the GRCh37 equivalent, as `vcfPrepper` does). This means writing a minimal VCF just for snpEff, but not using the rest of the pipeline. It produces the same MolecConseq content the server's existing patients have.

Copy-number and structural calls get no molecular consequences, matching the existing data.

## Mapping discussion (to do)

A dedicated pass on how cBioPortal data maps into FHIR (mCODE) and Mongo, field by field, before any code. Per D7: genomic data maps to Mongo only (the Operations produce its FHIR), clinical data maps to FHIR. It must cover:

- clinical attributes → Patient, primary and secondary cancer conditions, staging, disease status, tumor markers (MSI, TMB), treatments and procedures from timelines
- mutations → Variants (Mongo)
- **the genomics report's GenomicVariant Observations** (D6): these are FHIR built from Mongo-held data. Proposal, in keeping with D7: after loading Mongo, call the Operations (`$find-subject-variants`, `$find-subject-molecular-consequences`, `$find-subject-tx-implications`) for the test, keep the variants in the 228 reportable genes, and place the returned resources (adjusted to the mCODE profiles) in the report. The report and the server then agree by construction, and there's no second variant → FHIR mapping. To check: how far the Operations' output already conforms to mCODE GenomicVariant
- **copy number** (D5): discrete gene-level calls (−2…+2) vs. segment data; how to get POS/END (gene coordinates or segments), CN (the discrete call is a relative level, not a copy number), SVTYPE (DEL/DUP/CNV) and dnaChangeType; how a report states copy-number findings
- **structural variants/fusions** (D5): breakpoints → Variant records (SVTYPE, END, pairs of breakpoints). **Fusions are the known exception to D7** and are deferred to Q12
- samples → Tests and Specimens; dates on the shifted timeline
- gene panels and assays → studied-region BEDs, Tests fields and the region-studied Observation; the internal test record that genomicStudy will later be built from

**Level of effort (rough):** the genomic side (mutations, CNA, SVs, BEDs, Mongo records) is largely shared across study families. The clinical side (attributes, timelines, crosswalks) has to be redone for each family. For one family: about 2–3 sessions to agree the mapping, then about 1–2 weeks to build and validate. Each further family adds mostly clinical-mapping work. Mapping arbitrary studies is open-ended, so we choose families deliberately (Q1).

## Open questions

- **Q1. Which study families, and how many?** A *study family* is a group of cBioPortal studies produced with the same sequencing pipeline and the same clinical-data conventions. Families differ widely in assay, genome build, which data types they have, and especially in clinical attribute names, values and timelines. **Each family we add needs its own clinical mapping and crosswalk entries**, so the number of families is the biggest driver of effort (see the level-of-effort note in the mapping discussion). Not yet decided; we first need to survey the candidates. Each candidate below still has to be checked:

  | Candidate family | Expected character (to verify) |
  |---|---|
  | TCGA PanCancer Atlas (~32 studies) | Uniform pipeline across cancer types; WES, GRCh37; mutations, CNA, segments, some SVs; curated clinical data with stage; little treatment detail. Open-access tier |
  | MSK-IMPACT (incl. the large 2017 cohort) | Targeted panels (341/410/468 genes), GRCh37; mutations, CNA, SVs; sparse clinical data. Closest to a real clinical NGS report |
  | MSK-CHORD | IMPACT genomics plus rich clinical timelines (treatments, labs, progression), some NLP-derived. Licence terms to check |
  | AACR Project GENIE | Many institutions and panels; minimal clinical data; registration and use terms |
  | CPTAC, TARGET/pediatric, other single-study datasets | Case by case |

  The survey should record, per family: assay and panels, genome build, data types available, the clinical attributes and timeline event types, the licence, and how many patients have actionable variants in the 228 reportable genes. Then decide: which families, how many, which cancer types, how many patients, and the selection criteria (e.g. actionable variants, treatment history present).
- **Q2. VCF route (a), with the pipeline bugs fixed, or direct records (b), with the fixes deferred?** MolecConseq records are wanted either way.
- ~~Q3. MRN scheme~~: decided, D4.
- ~~Q4. Copy number and fusions~~: included (D5); the mapping is covered in the mapping discussion.
- **Q5. Tests per patient.** One test per cBioPortal sample (primary and metastasis become two tests)? Add a synthetic germline test, or stay somatic only?
- **Q6. Clinical depth (all generated by rule, D11).** Clinical attributes only, or also timeline data (treatments, labs) where the study has it? Which synthetic filler do we generate (names, address, encounters, practitioner), and do we fill gaps the source lacks (e.g. ECOG, labs) with plausible values or leave them absent? **To come back to.** Bob's thoughts so far (2026-11-08):
  - **Fill** fields that a real record would generally have (e.g. ECOG).
  - **But** some messy data, closer to the real world, helps test how robustly the Case Summary and other consumers render gaps and oddities.
  - **Possible middle ground** (not decided): fill realistically by default, and leave a seeded fraction of patients deliberately messy (missing values, unmapped codes, free-text-only items), flagged in the run CSV so the mess is known.
- **Q13. Error tolerance.** Where is the line between "minor: load and log" and "serious: skip the patient"? Proposed starting point: anything that breaks loading, the Operations or report/server consistency is serious; unmapped codes, missing optional fields and validator warnings are minor.
- ~~Q7. Genetic reports in FHIR~~: yes, filtered to the 228 reportable genes (D6).
- **Q8. Genome build.** Keep the study's native build (GRCh37 for TCGA), or lift over to GRCh38 so all new patients use one build?
- ~~Q9. Where the code lives~~: `rhdolin/synthetic-data-generator` (D8). Still open: one script per step or a single driver. If route (a) is chosen, the pipeline fixes still happen in genomics-operations, in `utilities/`.
- **Q12. Fusions: to revisit (Bob, 2026-11-08).** Fusions are important for treatment decisions (e.g. ALK, ROS1, RET, NTRK, FGFR2), but today they are **neither in Mongo nor in our FHIR**: no Mongo schema, no Operations support, and none in the demo bundles. A dedicated discussion will decide how to represent them in Mongo, in the Operations and in the FHIR report, and whether the cBioPortal fusions wait until then. Until it's resolved, other structural variants and copy number go ahead under D5.
- **Q10. Data use terms:** confirm the chosen studies' terms allow redistribution in a public demo (TCGA open-access tier: yes; check others).
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
