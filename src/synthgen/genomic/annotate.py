"""Public web services used to annotate variants (design D21). No accounts, no local installs.

- Ensembl REST, GRCh37 endpoint: VEP (consequences, HGVS on RefSeq transcripts), reference
  sequence (indel anchor bases), gene coordinates, the consequence-term -> SO table.
- NCBI: the MANE summary file (which RefSeq transcript is MANE Select; HGNC IDs) and
  Variation Services (SPDI normalization).
- RxNav: RxNorm codes and whether a drug has marketed products.

Every response is cached under data/cache, so reruns are fast and repeatable.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import time
from pathlib import Path

import requests

CACHE = Path("data/cache")
ENSEMBL37 = "https://grch37.rest.ensembl.org"
MANE_URL = "https://ftp.ncbi.nlm.nih.gov/refseq/MANE/MANE_human/release_1.5/MANE.GRCh38.v1.5.summary.txt.gz"
SPDI_API = "https://api.ncbi.nlm.nih.gov/variation/v0/spdi"
RXNAV = "https://rxnav.nlm.nih.gov/REST"

# GRCh37 RefSeq chromosome accessions
GRCH37_ACCESSION = {
    "1": "NC_000001.10", "2": "NC_000002.11", "3": "NC_000003.11", "4": "NC_000004.11", "5": "NC_000005.9",
    "6": "NC_000006.11", "7": "NC_000007.13", "8": "NC_000008.10", "9": "NC_000009.11", "10": "NC_000010.10",
    "11": "NC_000011.9", "12": "NC_000012.11", "13": "NC_000013.10", "14": "NC_000014.8", "15": "NC_000015.9",
    "16": "NC_000016.9", "17": "NC_000017.10", "18": "NC_000018.9", "19": "NC_000019.9", "20": "NC_000020.10",
    "21": "NC_000021.8", "22": "NC_000022.10", "X": "NC_000023.10", "Y": "NC_000024.9",
}

_session = requests.Session()


def _request(method: str, url: str, **kw) -> requests.Response:
    for attempt in range(6):
        resp = _session.request(method, url, timeout=120, **kw)
        if resp.status_code in (429, 500, 502, 503, 504):
            time.sleep(float(resp.headers.get("Retry-After", 2 ** attempt)))
            continue
        return resp
    resp.raise_for_status()
    return resp


def _cached(kind: str, key, fetch):
    path = CACHE / kind / (hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:24] + ".json")
    if path.exists():
        return json.loads(path.read_text())
    value = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return value


# --- MANE ----------------------------------------------------------------------------
_mane = None


def mane() -> dict:
    """symbol -> {hgnc, refseq_nuc, refseq_prot} for MANE Select; plus the set of MANE Select nuc accessions."""
    global _mane
    if _mane is None:
        path = CACHE / MANE_URL.rsplit("/", 1)[1]
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_request("GET", MANE_URL).content)
        genes, select = {}, set()
        with gzip.open(path, "rt") as fh:
            header = fh.readline().lstrip("#").rstrip("\n").split("\t")
            for line in fh:
                row = dict(zip(header, line.rstrip("\n").split("\t")))
                if row["MANE_status"] == "MANE Select":
                    genes[row["symbol"]] = {"hgnc": row["HGNC_ID"], "refseq_nuc": row["RefSeq_nuc"], "refseq_prot": row["RefSeq_prot"]}
                    select.add(row["RefSeq_nuc"])
        _mane = {"genes": genes, "select": select}
    return _mane


def hgnc_gene(symbol: str) -> dict | None:
    """{'hgnc': 'HGNC:n', 'symbol': approved symbol}; outdated symbols resolved via HGNC previous/alias symbols."""
    g = mane()["genes"].get(symbol)
    if g:
        return {"hgnc": g["hgnc"], "symbol": symbol}

    def fetch():
        for field in ("symbol", "prev_symbol", "alias_symbol"):
            r = _request("GET", f"https://rest.genenames.org/fetch/{field}/{symbol}", headers={"Accept": "application/json"})
            docs = r.json()["response"]["docs"] if r.ok else []
            if len(docs) == 1:
                return {"hgnc": docs[0]["hgnc_id"], "symbol": docs[0]["symbol"]}
        return None
    return _cached("hgnc2", symbol, fetch)


def hgnc_id(symbol: str) -> str | None:
    g = hgnc_gene(symbol)
    return g["hgnc"] if g else None


# --- Ensembl -------------------------------------------------------------------------
def consequence_so() -> dict:
    """VEP consequence term -> SO accession (from Ensembl)."""
    def fetch():
        r = _request("GET", f"{ENSEMBL37}/info/variation/consequence_types", headers={"Accept": "application/json"})
        return {c["SO_term"]: c["SO_accession"] for c in r.json()}
    return _cached("ensembl", "consequence_types", fetch)


def vep(variants: list[str]) -> dict:
    """Ensembl-format input ("chr start end REF/ALT + id") -> {id: VEP result}. RefSeq transcripts, HGVS."""
    out = {}
    for i in range(0, len(variants), 200):
        batch = variants[i:i + 200]

        def fetch(batch=batch):
            r = _request("POST", f"{ENSEMBL37}/vep/homo_sapiens/region",
                         params={"refseq": 1, "hgvs": 1, "canonical": 1},
                         headers={"Content-Type": "application/json", "Accept": "application/json"},
                         json={"variants": batch})
            r.raise_for_status()
            return r.json()
        for res in _cached("vep", batch, fetch):
            out[res["id"]] = res
    return out


def ref_bases(regions: list[str]) -> dict:
    """GRCh37 regions "chr:start..end" (1-based) -> sequence."""
    out = {}
    for i in range(0, len(regions), 50):
        batch = regions[i:i + 50]

        def fetch(batch=batch):
            r = _request("POST", f"{ENSEMBL37}/sequence/region/human",
                         headers={"Content-Type": "application/json", "Accept": "application/json"},
                         json={"regions": [f"{x}:1" for x in batch]})
            r.raise_for_status()
            return {d["query"].rsplit(":", 1)[0]: d["seq"] for d in r.json()}
        out.update(_cached("sequence", batch, fetch))
    return out


def gene_coords(symbols: list[str]) -> dict:
    """symbol -> (chrom, start, end), GRCh37, 1-based inclusive. Unknown symbols are omitted."""
    out = {}
    symbols = sorted(set(symbols))
    for i in range(0, len(symbols), 200):  # Ensembl's batch limit is 1000; stay well under it
        batch = symbols[i:i + 200]

        def fetch(batch=batch):
            r = _request("POST", f"{ENSEMBL37}/lookup/symbol/homo_sapiens",
                         headers={"Content-Type": "application/json", "Accept": "application/json"},
                         json={"symbols": batch})
            if r.status_code == 400:  # a symbol Ensembl can't parse: fall back to one at a time
                res = {}
                for sym in batch:
                    one = _request("GET", f"{ENSEMBL37}/lookup/symbol/homo_sapiens/{sym}", headers={"Accept": "application/json"})
                    if one.ok:
                        v = one.json()
                        res[sym] = [v["seq_region_name"], v["start"], v["end"]]
                return res
            r.raise_for_status()
            return {k: [v["seq_region_name"], v["start"], v["end"]] for k, v in r.json().items() if v}
        out.update(_cached("genes37", batch, fetch))
    return out


# --- NCBI SPDI -------------------------------------------------------------------------
def normalize_spdi(spdi: str) -> str:
    """Contextual (normalized) SPDI on the same reference sequence; also checks the reference allele."""
    def fetch():
        r = _request("GET", f"{SPDI_API}/{spdi}/contextual")
        if not r.ok:
            return {"error": r.text[:300]}
        d = r.json()["data"]
        return {"spdi": f"{d['seq_id']}:{d['position']}:{d['deleted_sequence']}:{d['inserted_sequence']}"}
    res = _cached("spdi", spdi, fetch)
    if "error" in res:
        raise ValueError(f"SPDI {spdi}: {res['error']}")
    return res["spdi"]


# --- RxNav -----------------------------------------------------------------------------
def rxnorm(name: str) -> dict:
    """{'rxcui': str|None, 'name': str, 'marketed': bool} for a drug name (exact RxNorm match)."""
    def fetch():
        r = _request("GET", f"{RXNAV}/rxcui.json", params={"name": name, "search": 0})
        ids = (r.json().get("idGroup", {}) or {}).get("rxnormId") or []
        if not ids:
            return {"rxcui": None, "name": name, "marketed": False}
        props = _request("GET", f"{RXNAV}/rxcui/{ids[0]}/properties.json").json().get("properties", {})
        rel = _request("GET", f"{RXNAV}/rxcui/{ids[0]}/related.json", params={"tty": "SCD SBD"}).json()
        groups = (rel.get("relatedGroup", {}) or {}).get("conceptGroup", []) or []
        n = sum(len(g.get("conceptProperties", []) or []) for g in groups)
        return {"rxcui": ids[0], "name": props.get("name", name), "marketed": n > 0}
    return _cached("rxnav", name.lower(), fetch)
