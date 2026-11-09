# synthetic-data-generator

Turns real cBioPortal patients into synthetic demo patients. Each patient has two parts that describe the same person and the same tests:

1. **Clinical data as FHIR**: an mCODE transaction bundle, including a structured genomics report per test, loaded to a clinical FHIR server.
2. **Genomic data as MongoDB records** (Patients, Tests, Variants, MolecConseq, BED) for the FHIR Genomics Operations server.

The pipeline runs end to end with no person in the loop. Synthetic filler (names, addresses, date shifts) comes from a generator seeded by the MRN, so reruns are repeatable.

> **Status:** design phase. The modules are stubs. See [docs/design.md](docs/design.md) for the decisions so far and the open questions.

## Pipeline

| Stage | Package | What it does |
|---|---|---|
| 1. Harvest | `synthgen.harvest` | Fetch everything cBioPortal holds for each selected patient, with provenance (D10) |
| 2. Synthesize | `synthgen.synthesize` | MRN, seeded filler, date shift, timeline (D4, D11) |
| 3. Genomic | `synthgen.genomic` | Patients, Tests, Variants, MolecConseq and BED records for MongoDB |
| 4. Clinical | `synthgen.clinical` | mCODE bundle and genomics report for the clinical FHIR server |
| 5. Validate and load | `synthgen.validate` | Validate, load, run consistency checks, write the run report |

## Repository layout

```
docs/            design doc and, later, the mapping spec
mappings/
  conceptmaps/     FHIR ConceptMaps for code crosswalks (OncoTree → SNOMED CT, etc.)
  crosswalks/      plain CSV crosswalks, where a ConceptMap isn't used
  logical-models/  StructureDefinitions for cBioPortal inputs (only if StructureMap is adopted, D9)
src/synthgen/    the generator
tests/           pytest suite
data/            local harvest cache and outputs (git-ignored)
```

## Getting started

Requires Python 3.11 or later.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e . --no-deps
cp .env.example .env             # then fill in server URLs
pytest
```

`requirements.txt` pins exact versions; `pyproject.toml` declares the unpinned dependencies. In VS Code, the workspace settings select `.venv` automatically and activate it in new terminals.

## Related repositories

- [FHIR/genomics-operations](https://github.com/FHIR/genomics-operations): the Genomics Operations server (MongoDB load target), the MTB app's clinical data and `mcode_helpers.py`, and the existing VCF utilities.

## Data use

Source data comes from cBioPortal. Each study's terms must allow redistribution in a public demo before its patients are published (design doc Q10). Harvested data is kept out of git by default.

## License

[Apache License 2.0](LICENSE).
