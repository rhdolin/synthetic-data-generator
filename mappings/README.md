# mappings/

The mapping specification and its computable artifacts (design doc D8, D9, Q11).

- `conceptmaps/`: FHIR ConceptMaps for code-to-code crosswalks (OncoTree → SNOMED CT / ICD-O-3, stage strings → AJCC, drug names → RxNorm, `Variant_Classification` → Sequence Ontology, timeline event types → resource types).
- `crosswalks/`: plain CSV crosswalks, where a ConceptMap isn't used.
- `logical-models/`: StructureDefinitions describing normalized cBioPortal inputs. Needed only if the clinical-attributes → mCODE step becomes a StructureMap.

The structural mapping (cBioPortal file → FHIR element or Mongo field → transform) will be a Markdown spec in this folder, with one table per cBioPortal file.
