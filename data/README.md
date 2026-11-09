# data/

Local working data. Everything here except this file is git-ignored.

- `snapshots/<study>/`: pinned copy of each selected cBioPortal study (datahub commit or tarball), with its version (design doc D10)
- `harvest/<study>/<MRN>/`: per-patient extracts of the data types we port, with provenance
- `output/`: generated FHIR bundles, Mongo records, BED files and run reports
