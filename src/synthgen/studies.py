"""Supported studies (design "Orchestration and distribution": readiness must be explicit).

A study is listed here only after its mapping has been checked and a varied test set built and
validated. `synthgen run` refuses any study not listed.
"""

from __future__ import annotations

SUPPORTED = {
    "luad_tcga_pan_can_atlas_2018": {
        "name": "Lung Adenocarcinoma (TCGA, PanCancer Atlas)",
        "datahub_commit": "a8ea891da32add81fabaa8e744cf8cb72a8a0e66",
        "oncotree": "LUAD",
        "builder": "tcga",
        "status": "supported (2026-11-11)",
        "verified": "10 test patients built and validated against mCODE 4.0.0: lower/upper lobe, all histologies, "
                    "AJCC 4th-7th editions, resection/biopsy/none, ALK fusion, prior and new primaries, no RNA, stage IV.",
        "caveats": [
            "Eligible: living patients with clinical data (328 of 566; D23).",
            "Metastasis sites are rule-filled (not in TCGA or GDC).",
            "PD-L1 is derived from CD274 RNA, not measured.",
            "Treatments are 2005-2013 practice shifted to the present (Q15).",
            "Fusion coordinates in the source are GRCh38 though labelled GRCh37; fusions go to FHIR only (D20).",
            "Bundles mix Genomics Reporting 2.0.0 (mCODE) and 3.0.0 (GenomicStudy) (Q18).",
        ],
    },
    "coadread_tcga_pan_can_atlas_2018": {
        "name": "Colorectal Adenocarcinoma (TCGA, PanCancer Atlas)",
        "datahub_commit": "a8ea891da32add81fabaa8e744cf8cb72a8a0e66",
        "oncotree": "COADREAD",
        "builder": "tcga",
        "status": "in development (milestone 4)",
        "verified": "",
        "caveats": [
            "Eligible: living patients with clinical data (471 of 594; D23).",
            "Metastasis sites are rule-filled unless GDC names one.",
            "Recurrences come from GDC where cBioPortal's timeline lacks them.",
            "No PD-L1 (not standard in colorectal; MSI drives immunotherapy).",
            "MMR IHC showing loss of all four proteins is treated as a data error and not ported.",
            "Treatments are 2005-2013 practice shifted to the present (Q15).",
        ],
    },
}
