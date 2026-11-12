# data/

Local working data. Everything here except this file is git-ignored.

- `snapshots/<study>/`: pinned copy of each selected cBioPortal study (datahub commit or tarball), with its version (design doc D10)
- `harvest/<study>/<MRN>/`: per-patient extracts of the data types we port, with provenance
- `output/<study>/`: generated FHIR bundles, Mongo records, build notes and validation output per patient; run reports (`run-<time>/`); the case catalog (`cases.md`, `cases.csv`)
- `cache/`: the FHIR validator jar, the pinned CIViC release, NCBI's MANE list, and cached annotation-service and IDC responses
