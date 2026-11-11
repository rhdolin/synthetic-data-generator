"""Synthetic PD-L1 (22C3 TPS) from the tumor's CD274 RNA (design "Derived clinical values"; R3)."""

from __future__ import annotations

import csv
import statistics
from pathlib import Path

from synthgen import synthesize as syn

CD274_ENTREZ = "29126"


def cd274_values(snapshot: Path) -> dict[str, float]:
    with open(snapshot / "data_mrna_seq_v2_rsem.txt", newline="") as fh:
        rd = csv.reader((l for l in fh if not l.startswith("#")), delimiter="\t")
        header = next(rd)
        for row in rd:
            if row[1] == CD274_ENTREZ:
                return {s: float(v) for s, v in zip(header[2:], row[2:]) if v not in ("", "NA")}
    return {}


def cutoffs(values: dict[str, float]) -> tuple[float, float]:
    q = statistics.quantiles(values.values(), n=3)
    return q[0], q[1]


def tps(mrn: str, sample_id: str, values: dict[str, float], cuts: tuple[float, float], mutated_genes: set[str], smoker: bool) -> dict:
    r = syn.rng(mrn, "pdl1")
    if sample_id in values:
        v = values[sample_id]
        category = 0 if v < cuts[0] else 1 if v < cuts[1] else 2
        basis = f"CD274 RNA {v:.1f} (study cutoffs {cuts[0]:.1f} / {cuts[1]:.1f})"
    else:
        w = [1.0, 1.0, 1.0]
        if mutated_genes & {"EGFR", "ALK", "STK11", "KEAP1"}:
            w = [1.6, 1.0, 0.5]
        elif "KRAS" in mutated_genes and smoker:
            w = [0.7, 1.0, 1.4]
        category = r.choices([0, 1, 2], w)[0]
        basis = "no RNA: rule by genotype"
    value = 0 if category == 0 else r.randint(1, 49) if category == 1 else r.randint(50, 95)
    return {"tps": value, "category": ["<1%", "1-49%", ">=50%"][category], "basis": basis}
