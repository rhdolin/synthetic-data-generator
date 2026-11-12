"""Therapeutic implications from a pinned CIViC release (docs/mapping.md "Therapeutic implications").

Rule (Bob, 2026-11-10): predictive, accepted, "Supports", level A or B, the patient's tumor type.
Per variant: at most 2 sensitivity therapies (higher level first; at the same level a US-marketed
therapy beats an experimental one; remaining ties are all kept) plus the best resistance item.
"""

from __future__ import annotations

import csv
from pathlib import Path

from synthgen.genomic import annotate as A

RELEASE = "01-Nov-2026"
LEVEL_TEXT = {"A": "A (Validated association)", "B": "B (Clinical evidence)"}
# OncoTree -> Disease Ontology IDs counted as "the same tumor type"
TUMOR_TYPE_DOIDS = {"LUAD": {"3910", "3908", "1324"}, "LUSC": {"3907", "3908", "1324"}}


def release_rows(release: str = RELEASE) -> list[dict]:
    path = A.CACHE / f"civic-{release}-ClinicalEvidenceSummaries.tsv"
    if not path.exists():
        url = f"https://civicdb.org/downloads/{release}/{release}-ClinicalEvidenceSummaries.tsv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(A._request("GET", url).content)
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def mutation_names(gene: str, protein_short: str, classification: str, exon: str) -> set[str]:
    """CIViC profile names a protein-changing mutation matches: protein change, exon-level category, gene level."""
    names = {f"{gene} Mutation"}
    if protein_short:
        names.add(f"{gene} {protein_short}")
    n = (exon or "").split("/")[0]
    if n.isdigit():
        if classification == "In_Frame_Del":
            names.add(f"{gene} Exon {n} Deletion")
        elif classification == "In_Frame_Ins":
            names.add(f"{gene} Exon {n} Insertion")
    if gene == "MET" and (classification == "Splice_Site" or (classification == "In_Frame_Del" and n == "14")):
        names.add("MET Exon 14 Skipping Mutation")  # MET splice-site calls in lung are almost all exon 14 skipping
    return names


def profiles_for(v: dict) -> set[str]:
    """CIViC molecular-profile names a reported variant matches."""
    gene = v["gene"]
    if v["kind"] == "mutation":
        maf = (v.get("detail") or {}).get("maf", {})
        return mutation_names(gene, v.get("protein_short", ""), maf.get("Variant_Classification", ""),
                              maf.get("EXON") or maf.get("Exon_Number", ""))
    if v["kind"] == "amplification":
        return {f"{gene} Amplification"}
    if v["kind"] == "fusion":
        a, b = v["genes"]
        # CIViC names partner-agnostic fusions with "v" (any partner), e.g. "v::ALK Fusion"
        return {f"{a}::{b} Fusion", f"v::{b} Fusion", f"{a}::v Fusion", f"{b} Fusion", f"{a} Fusion"}
    return set()


def profile_matches(profile: str, names: set[str]) -> bool:
    """A profile matches if it is one of the names, or an "A OR B" profile with any part among them.
    Profiles with AND / NOT need conditions we can't check, so they never match."""
    if " AND " in profile or "NOT " in profile:
        return False
    return any(part.strip(" ()") in names for part in profile.split(" OR "))


def therapies_of(row: dict) -> list[list[dict]]:
    """Drug groups for one evidence item: a combination is one group; substitutes are one group each."""
    names = [t.strip() for t in row["therapies"].split(",") if t.strip()]
    drugs = [A.rxnorm(n) for n in names]
    if row["therapy_interaction_type"] == "Substitutes":
        return [[d] for d in drugs]
    return [drugs]


def select(reported: list[dict], oncotree: str, release: str = RELEASE) -> list[dict]:
    doids = TUMOR_TYPE_DOIDS.get(oncotree, set())
    rows = [r for r in release_rows(release) if r["evidence_type"] == "Predictive" and r["evidence_status"] == "accepted"
            and r["evidence_direction"] == "Supports" and r["evidence_level"] in ("A", "B") and r["doid"] in doids]
    out = []
    for v in reported:
        names = profiles_for(v)
        matched = [r for r in rows if profile_matches(r["molecular_profile"], names)]
        # the most specific profile wins: gene-level "Mutation" only when nothing variant-specific matches
        specific = [r for r in matched if not r["molecular_profile"].endswith(" Mutation")
                    or r["molecular_profile"] == "MET Exon 14 Skipping Mutation"]
        matched = specific or matched
        items = []
        for r in matched:
            groups = therapies_of(r)
            items.append({"variant": v, "row": r, "groups": groups, "level": r["evidence_level"],
                          "marketed": all(d["marketed"] for g in groups for d in g),
                          "rating": int(r["rating"] or 0), "eid": int(r["evidence_id"]), "significance": r["significance"]})
        rank = lambda c: (c["level"], not c["marketed"], -c["rating"])
        sens = sorted((c for c in items if c["significance"] == "Sensitivity/Response"), key=lambda c: (rank(c), -c["eid"]))
        picked, seen = [], set()
        for c in sens:
            therapy = tuple(sorted(d["name"].lower() for g in c["groups"] for d in g))
            if therapy in seen:
                continue
            if len(picked) >= 2 and rank(c) != rank(picked[1]):  # an extra only on an exact tie with #2
                break
            seen.add(therapy)
            picked.append(c)
        res = sorted((c for c in items if c["significance"] == "Resistance"), key=lambda c: (rank(c), -c["eid"]))
        picked += res[:1]
        for c in picked:  # one implication per drug group
            for g in c["groups"]:
                out.append({**c, "drugs": g})
    return out
