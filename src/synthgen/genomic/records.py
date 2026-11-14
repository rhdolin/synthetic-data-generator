"""Mongo records for the Genomics Operations server (docs/mapping.md "Genomic: cBioPortal -> Mongo").

Patients, Tests, Variants (PASS mutations; +/-2 copy-number calls) and MolecConseq. No fusions (D20).
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from synthgen.genomic import annotate as A

# Reportable / matchable: changes the protein. Excludes synonymous and splice-region-only variants.
PROTEIN_CHANGING = {"Missense_Mutation", "Nonsense_Mutation", "Frame_Shift_Del", "Frame_Shift_Ins", "In_Frame_Del",
                    "In_Frame_Ins", "Splice_Site", "Nonstop_Mutation", "Translation_Start_Site"}
SV_MIN_LENGTH = 50  # indels longer than this are structural variants (no SPDI)
# Outside any gene: the MAF still names a nearby gene for flanking variants, but the variant isn't in it
OUTSIDE_GENE = {"IGR", "5'Flank", "3'Flank"}


def read_rows(path: Path) -> list[dict]:
    with open(path, newline="") as fh:
        return list(csv.DictReader((l for l in fh if not l.startswith("#")), delimiter="\t"))


def stable_id(*parts) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:32]


def ensembl_input(m: dict, vid: str) -> str:
    """MAF row -> Ensembl default VEP input (handles '-' alleles natively)."""
    chrom, start, end = m["Chromosome"], int(m["Start_Position"]), int(m["End_Position"])
    ref, alt = m["Reference_Allele"], m["Tumor_Seq_Allele2"]
    if ref == "-":  # insertion between Start and End: Ensembl uses start = end + 1
        start, end = end, start
    return f"{chrom} {start} {end} {ref}/{alt} + {vid}"


def mutations(extract: Path, test: dict, notes: list | None = None) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (variant records, molecconseq records, report-facing details)."""
    notes = notes if notes is not None else []
    rows = [m for m in read_rows(extract / "data_mutations.txt") if m["FILTER"] == "PASS"]
    ids = [stable_id(test["testID"], m["Chromosome"], m["Start_Position"], m["Reference_Allele"], m["Tumor_Seq_Allele2"]) for m in rows]
    annotations = A.vep([ensembl_input(m, vid) for m, vid in zip(rows, ids)])
    # anchor bases for indels (the base before the event)
    anchors_needed = {}
    for m in rows:
        if m["Reference_Allele"] == "-" :
            anchors_needed[m["Chromosome"]+":"+m["Start_Position"]] = f"{m['Chromosome']}:{m['Start_Position']}..{m['Start_Position']}"
        elif m["Tumor_Seq_Allele2"] == "-":
            p = int(m["Start_Position"]) - 1
            anchors_needed[m["Chromosome"]+":"+str(p)] = f"{m['Chromosome']}:{p}..{p}"
    bases = A.ref_bases(list(anchors_needed.values()))
    so = A.consequence_so()
    mane = A.mane()
    variants, conseq, details = [], [], []
    for m, vid in zip(rows, ids):
        chrom, start = m["Chromosome"], int(m["Start_Position"])
        ref, alt = m["Reference_Allele"], m["Tumor_Seq_Allele2"]
        if ref == "-":  # insertion after Start_Position
            anchor = bases[f"{chrom}:{start}..{start}"]
            pos0, ref, alt = start - 1, anchor, anchor + alt
        elif alt == "-":  # deletion starting at Start_Position
            anchor = bases[f"{chrom}:{start - 1}..{start - 1}"]
            pos0, ref, alt = start - 2, anchor + ref, anchor
        else:
            pos0 = start - 1
        acc = A.GRCH37_ACCESSION[chrom]
        spdi = f"{acc}:{pos0}:{ref}:{alt}"
        structural = max(len(ref), len(alt)) - 1 > SV_MIN_LENGTH
        if len(ref) != len(alt) and not structural:
            try:
                spdi = A.normalize_spdi(spdi)
            except Exception as exc:
                structural = True
                notes.append(f"SPDI not normalized for chr{chrom}:{start}; stored as a structural variant: {str(exc)[-60:]}")
        t_alt, t_ref = int(m["t_alt_count"] or 0), int(m["t_ref_count"] or 0)
        rec = {
            "_id": vid, "patientID": test["patientID"], "testDate": test["testDate"], "testID": test["testID"],
            "specimenID": test["specimenID"], "genomicBuild": test["genomicBuild"],
            "CHROM": f"chr{chrom}", "POS": pos0, "REF": ref, "ALT": alt, "END": pos0 + len(ref),
            "FILTER": "PASS",
            "allelicFrequency": round(t_alt / (t_alt + t_ref), 2) if (t_alt + t_ref) else 0,
            "genomicSourceClass": "somatic", "SPDI": spdi,
        }
        if structural:  # large indel: structural variant, no SPDI, no molecular consequence
            svtype = "DEL" if len(ref) > len(alt) else "INS"
            rec.update({"REF": ref[0], "ALT": f"<{svtype}>", "SVTYPE": svtype})
            del rec["SPDI"]
            notes.append(f"{svtype} of {abs(len(ref) - len(alt))} bp at chr{chrom}:{start} stored as a structural variant")
        gene = m["Hugo_Symbol"]
        if gene and gene != "Unknown" and m["Variant_Classification"] not in OUTSIDE_GENE:
            g = A.hgnc_gene(gene)
            if g:
                rec["geneCode"] = g["hgnc"]
            rec["geneDesc"] = g["symbol"] if g else gene  # current approved symbol
        variants.append(rec)
        if structural:
            details.append({"variant": rec, "maf": m, "gene": gene, "mane": None, "protein_altering": False})
            continue
        # molecular consequences: MANE always, plus the most severe if on another transcript
        ann = annotations.get(vid, {})
        tcs = [t for t in ann.get("transcript_consequences", []) if t.get("transcript_id", "").startswith(("NM_", "XM_", "NR_"))]
        mane_tc = next((t for t in tcs if t["transcript_id"] in mane["select"]), None)
        primary = mane_tc or next((t for t in tcs if t.get("canonical")), None)
        worst_term = ann.get("most_severe_consequence")
        worst = sorted([t for t in tcs if worst_term in t.get("consequence_terms", [])],
                       key=lambda t: (t is not mane_tc, not t["transcript_id"].startswith("NM_"), not t.get("canonical")))
        chosen = [primary] if primary else []
        if worst and (not primary or worst[0]["transcript_id"] != primary["transcript_id"]):
            chosen.append(worst[0])
        for t in chosen:
            phgvs = (t.get("hgvsp") or "").replace("%3D", "=")  # full form, e.g. NP_004976.2:p.Gly12Cys
            c = {"patientID": test["patientID"], "variantID": vid, "transcriptRefSeq": t["transcript_id"],
                 "MANE": t["transcript_id"] in mane["select"], "source": "VEP",
                 "cHGVS": t.get("hgvsc") or f"{t['transcript_id']}:?",
                 "featureConsequence": [{"system": "http://sequenceontology.org", "code": so.get(term, ""), "display": term}
                                        for term in t.get("consequence_terms", [])],
                 "impact": t.get("impact")}
            if phgvs:
                c["pHGVS"] = phgvs
            if t.get("impact") == "HIGH":  # as the existing pipeline's LOF flag (vcf2json.py)
                c["functionalEffect"] = [{"system": "http://sequenceontology.org", "code": "SO:0002054",
                                          "display": "loss_of_function_variant"}]
            conseq.append(c)
        details.append({"variant": rec, "maf": m, "gene": m["Hugo_Symbol"], "mane": mane_tc,
                        "protein_altering": m["Variant_Classification"] in PROTEIN_CHANGING})
    return variants, conseq, details


def _segment_for(segs: list[dict], chrom: str, gstart: int, gend: int, call: int) -> dict | None:
    on = [s for s in segs if (s["chrom"] if s["chrom"] != "23" else "X") == chrom]
    if not on:
        return None

    def overlap(s):
        return min(gend, int(s["loc.end"])) - max(gstart, int(s["loc.start"])) + 1
    hits = [s for s in on if overlap(s) > 0]
    if hits:
        return max(hits, key=lambda s: ((float(s["seg.mean"]) > 0) == (call > 0), overlap(s)))
    return min(on, key=lambda s: -overlap(s))  # nearest: smallest gap


def copy_numbers(extract: Path, test: dict, sample_id: str) -> tuple[list[dict], list[dict]]:
    """One CNV record per segment that holds a +/-2 gene call.

    Records carry the segment's coordinates and CN, with no gene fields: a copy-number record is
    positional. `details` stays one entry per gene and points at its segment's record.
    Gene -> segment: among overlapping segments, prefer the one whose direction matches the call,
    then the largest overlap; with no overlap (gaps between segments, or past the last probe),
    the nearest segment on the chromosome. The gene's own coordinates are used instead when that
    segment's mean points the other way from the call, or the chromosome has no segments.
    """
    with open(extract / "data_cna.txt", newline="") as fh:
        rd = csv.reader(fh, delimiter="\t")
        header = next(rd)
        col = header.index(sample_id)
        calls, seen = [], set()
        for r in rd:  # the matrix repeats some gene symbols (data quirk 17): keep the first call per gene
            if r[col] in ("2", "-2") and r[0] not in seen:
                seen.add(r[0])
                calls.append((r[0], int(r[col])))
    if not calls:
        return [], []
    coords = A.gene_coords([g for g, _ in calls])
    segs = [s for s in read_rows(extract / "data_cna_hg19.seg") if s["ID"] == sample_id]

    # group the calls by covering segment (keyed by chrom/start/end)
    groups: dict[tuple, dict] = {}
    for gene, call in calls:
        if gene not in coords:
            continue
        chrom, gstart, gend = coords[gene]
        seg = _segment_for(segs, chrom, gstart, gend, call)
        if seg and (float(seg["seg.mean"]) > 0) == (call > 0):
            key = (chrom, int(seg["loc.start"]), int(seg["loc.end"]))
            mean = float(seg["seg.mean"])
        elif seg:  # the segment points the other way from the call: don't claim it, use the gene's footprint
            key = (chrom, gstart, gend)
            mean = float(seg["seg.mean"])  # CN still clamped to the call below
        else:  # no segment on the chromosome: fall back to the gene's footprint
            key = (chrom, gstart, gend)
            mean = None
        g = groups.setdefault(key, {"mean": mean, "genes": []})
        g["genes"].append((gene, call))

    bases = A.ref_bases([f"{c}:{s}..{s}" for c, s, _ in groups])
    records, details = [], []
    for (chrom, start, end), g in groups.items():
        signs = {call for _, call in g["genes"]}
        if g["mean"] is not None:
            cn = round(2 * 2 ** g["mean"])
        else:
            cn = 4 if signs == {2} else 0
        if signs == {2}:
            cn = max(cn, 3)
        elif signs == {-2}:
            cn = min(cn, 1)
        rec = {
            "_id": stable_id(test["testID"], "CNV", f"{chrom}:{start}-{end}"), "patientID": test["patientID"],
            "testDate": test["testDate"], "testID": test["testID"], "specimenID": test["specimenID"],
            "genomicBuild": test["genomicBuild"],
            "CHROM": f"chr{chrom}", "POS": start - 1, "REF": bases[f"{chrom}:{start}..{start}"], "END": end,
            "FILTER": "PASS", "SVTYPE": "CNV", "CN": cn, "genomicSourceClass": "somatic",
        }
        records.append(rec)
        for gene, call in g["genes"]:
            details.append({"variant": rec, "gene": gene, "call": "amplification" if call == 2 else "deep deletion"})
    return records, details


def dna_change_types(variants: list[dict]) -> list[str]:
    types = set()
    for v in variants:
        if v.get("SVTYPE"):
            types.add(v["SVTYPE"])  # CNV, or DEL/INS for large indels
        elif len(v["REF"]) == len(v["ALT"]):
            types.add("SNV" if len(v["REF"]) == 1 else "MNV")
        else:
            types.add("DEL" if len(v["REF"]) > len(v["ALT"]) else "INS")
    order = ["SNV", "MNV", "INS", "DEL", "CNV"]
    return [t for t in order if t in types]
