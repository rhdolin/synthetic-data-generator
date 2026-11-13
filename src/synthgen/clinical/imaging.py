"""Link reports to the patient's real images in the NCI Imaging Data Commons (design D16).

Best effort: report texts are synthetic (D22), so a linked scan may not show what the report says.
- Pathology report -> the slide study (the patient's own resected tumor).
- Radiology: scans within BASELINE_DAYS of the patient's first scan (the pre-surgery staging workup) go to
  the staging report; only clearly later scans go to the metastasis/progression reports, in date order (extras
  to the last). A nuclear-medicine study goes to a bone-scan report only if its description says bone.
TCGA radiology is almost all baseline, so most metastasis/progression reports get no image.
Each real study becomes an ImagingStudy (DICOM UIDs, modality, series, IDC viewer link) with a shared
DICOMweb Endpoint for programmatic access. Study dates come from IDC's DICOMweb service (cached).
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from synthgen.clinical import fhir as F
from synthgen.genomic import annotate as A

IDC_DICOMWEB = "https://proxy.imaging.datacommons.cancer.gov/current/viewer-only-no-downloads-see-tinyurl-dot-com-slash-3j3d9jyp/dicomWeb"
DCM = "http://dicom.nema.org/resources/ontology/DCM"
MODALITY = {"IDC_OHIF_CT": ("CT", "Computed Tomography"), "IDC_OHIF_PT": ("PT", "Positron emission tomography"),
            "IDC_OHIF_NM": ("NM", "Nuclear Medicine"), "IDC_SLIM": ("SM", "Slide Microscopy")}
ENDPOINT_ID = "nci-idc-dicomweb"
BASELINE_DAYS = 90  # scans this close to the first scan are the pre-treatment staging workup


def studies(resource_file: Path) -> dict[str, dict]:
    """StudyInstanceUID -> {modalities, series, viewer url} from cBioPortal's IDC links."""
    out: dict[str, dict] = {}
    if not resource_file.exists():
        return out
    for line in resource_file.read_text().splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 3 or parts[1] not in MODALITY:
            continue
        url = parts[2]
        m = re.search(r"StudyInstanceUIDs=([\d.]+)(?:&initialSeriesInstanceUID=([\d.]+))?", url) or \
            re.search(r"/studies/([\d.]+)/series/([\d.]+)", url)
        if not m:
            continue
        study, series = m.group(1), m.group(2)
        s = out.setdefault(study, {"modalities": [], "series": [], "url": url})
        mod = MODALITY[parts[1]][0]
        if mod not in s["modalities"]:
            s["modalities"].append(mod)
        if series and all(x["uid"] != series for x in s["series"]):
            s["series"].append({"uid": series, "modality": mod})
    return out


def study_info(uid: str) -> dict:
    def fetch():
        r = A._request("GET", f"{IDC_DICOMWEB}/studies", params={"StudyInstanceUID": uid, "includefield": ["00080020", "00081030"]},
                       headers={"Accept": "application/dicom+json"})
        if not r.ok or not r.text.strip():
            return {}
        d = r.json()[0]
        return {"date": (d.get("00080020", {}).get("Value") or [""])[0],
                "description": (d.get("00081030", {}).get("Value") or [""])[0]}
    return A._cached("idc", uid, fetch)


def real_date(info: dict) -> str:
    d = info.get("date") or ""
    return f"The study date in the DICOM headers (shown by the IDC viewer) is {d[:4]}-{d[4:6]}-{d[6:8]}." if len(d) == 8 else ""


def link(b, pathology_report: dict | None, radiology_reports: list[dict]) -> int:
    """Add ImagingStudy resources and report links. Returns the number of studies linked."""
    found = studies(b.dir / "data_resource_patient.txt")
    if not found:
        return 0
    endpoint = b.add(F.resource("Endpoint", ENDPOINT_ID, status="active",
                                connectionType=F.coding("http://terminology.hl7.org/CodeSystem/endpoint-connection-type",
                                                        "dicom-wado-rs", "DICOM WADO-RS"),
                                name="NCI Imaging Data Commons DICOMweb (viewer proxy)",
                                payloadType=[{"text": "DICOM"}], address=IDC_DICOMWEB))

    def imaging_study(uid, s, day, report):
        info = study_info(uid)
        names = {code: display for code, display in MODALITY.values()}
        mods = [F.coding(DCM, m, names[m]) for m in s["modalities"]]
        r = b.add(F.resource(
            "ImagingStudy", b.rid(f"imaging-study-{uid[-12:]}"), status="available",
            identifier=[{"system": "urn:dicom:uid", "value": f"urn:oid:{uid}"}],
            modality=mods, subject=b.subject, started=b.D(day), endpoint=[F.ref(endpoint)],
            description=info.get("description") or None,
            series=[{"uid": x["uid"], "modality": next(c for c in mods if c["code"] == x["modality"]), "endpoint": [F.ref(endpoint)]}
                    for x in s["series"]] or None,
            reasonReference=[b.primary_ref],
            note=[{"text": f"Images in the NCI Imaging Data Commons: {s['url']} (real images from TCGA; this record's dates and "
                           "report texts are synthetic, so the images may not match the report). "
                           f"{real_date(info)}".strip()}]))
        for k in ("description", "series"):
            if r.get(k) is None:
                r.pop(k, None)
        report.setdefault("imagingStudy", []).append(F.ref(r))
        return r

    day_of = lambda rep: (b.d0.fromisoformat(rep["effectiveDateTime"][:10]) - b.d0).days
    linked = 0
    slides = [(u, s) for u, s in found.items() if "SM" in s["modalities"]]
    if pathology_report:
        for uid, s in slides:
            imaging_study(uid, s, day_of(pathology_report), pathology_report)
            linked += 1
    radiology = sorted(((u, s) for u, s in found.items() if "SM" not in s["modalities"]),
                       key=lambda us: study_info(us[0]).get("date") or "")
    reports = sorted(radiology_reports, key=lambda r: r["effectiveDateTime"])
    if radiology and reports:
        bone = next((r for r in reports if r["code"]["coding"][0]["code"] == "39818-0"), None)
        nm = next(((u, s) for u, s in radiology if s["modalities"] == ["NM"]
                   and "BONE" in (study_info(u).get("description") or "").upper()), None)
        pairs = []
        if bone and nm:
            pairs.append((nm, bone))
            radiology = [x for x in radiology if x != nm]
            reports = [r for r in reports if r is not bone] or [bone]
        # The staging workup: scans within BASELINE_DAYS of the patient's first scan go to the staging (first) report;
        # only clearly later scans go to the later reports, in date order (extras to the last report).
        def when(u):
            d = study_info(u).get("date") or ""
            return dt.date(int(d[:4]), int(d[4:6]), int(d[6:8])) if len(d) == 8 else None
        dates = [when(u) for u, _ in radiology if when(u)]
        first = min(dates) if dates else None
        later_reports = reports[1:] if len(reports) > 1 else []
        k = 0
        for us in radiology:
            d = when(us[0])
            if first is None or d is None or (d - first).days <= BASELINE_DAYS or not later_reports:
                pairs.append((us, reports[0]))
            else:
                pairs.append((us, later_reports[min(k, len(later_reports) - 1)]))
                k += 1
        for (uid, s), rep in pairs:
            imaging_study(uid, s, day_of(rep), rep)
            linked += 1
    b.notes.append(f"imaging: {linked} real IDC studies linked to reports (best effort; report texts are synthetic)")
    return linked
