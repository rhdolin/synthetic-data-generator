"""FHIR building blocks and code tables (conventions from genomics-operations mcode_helpers.py).

Codes were checked on tx.fhir.org / RxNav on 2026-11-09/10 (see docs/mapping.md).
"""

from __future__ import annotations

import re

MCODE = "http://hl7.org/fhir/us/mcode/StructureDefinition/"
USCORE = "http://hl7.org/fhir/us/core/StructureDefinition/"
SCT = "http://snomed.info/sct"
LOINC = "http://loinc.org"
RXN = "http://www.nlm.nih.gov/research/umls/rxnorm"
UCUM = "http://unitsofmeasure.org"
ICD10CM = "http://hl7.org/fhir/sid/icd-10-cm"
ICDO3 = "http://terminology.hl7.org/CodeSystem/icd-o-3"
OMB = "urn:oid:2.16.840.1.113883.6.238"
MRN_SYSTEM = "http://elimu.io/fhir/mrn"
SPECIMEN_SYSTEM = "http://elimu.io/fhir/specimen-id"
TAG = {"system": "http://elimu.io/fhir/tags", "code": "mtb-demo", "display": "MTB app synthetic demo data"}
OBS_CAT = "http://terminology.hl7.org/CodeSystem/observation-category"
COND_CLINICAL = "http://terminology.hl7.org/CodeSystem/condition-clinical"
COND_VER = "http://terminology.hl7.org/CodeSystem/condition-ver-status"
COND_CAT = "http://terminology.hl7.org/CodeSystem/condition-category"
FHIR_BASE = "https://hapi.fhir.org/baseR4"


def coding(system: str, code: str, display: str) -> dict:
    return {"system": system, "code": code, "display": display}


def cc(system: str, code: str, display: str, text: str | None = None) -> dict:
    out = {"coding": [coding(system, code, display)]}
    if text:
        out["text"] = text
    return out


def sct(code: str, display: str, text: str | None = None) -> dict:
    return cc(SCT, code, display, text)


def loinc(code: str, display: str) -> dict:
    return cc(LOINC, code, display)


def qty(value: float, unit: str, code: str | None = None) -> dict:
    return {"value": value, "unit": unit, "system": UCUM, "code": code or unit}


def ext(name: str, value_key: str, value) -> dict:
    url = name if name.startswith("http") else MCODE + name
    return {"url": url, value_key: value}


def obs_category(code: str, display: str) -> list[dict]:
    return [cc(OBS_CAT, code, display)]


def ref(resource: dict) -> dict:
    return {"reference": f"{resource['resourceType']}/{resource['id']}"}


def safe_id(rid: str) -> str:
    """FHIR ids: letters, digits, '-' and '.', at most 64 characters."""
    rid = re.sub(r"-{2,}", "-", re.sub(r"[^A-Za-z0-9.-]+", "-", rid)).strip("-")
    return rid[:64].rstrip("-")


def resource(rtype: str, rid: str, profile: str | None = None, **body) -> dict:
    rid = safe_id(rid)
    meta = {"tag": [TAG]}
    if profile:
        meta["profile"] = [profile if profile.startswith("http") else MCODE + profile]
    return {"resourceType": rtype, "id": rid, "meta": meta, **body}


def bundle(bundle_id: str, resources: list[dict]) -> dict:
    return {
        "resourceType": "Bundle",
        "id": bundle_id,
        "meta": {"tag": [TAG]},
        "type": "transaction",
        "entry": [
            {
                "fullUrl": f"{FHIR_BASE}/{r['resourceType']}/{r['id']}",
                "resource": r,
                "request": {"method": "PUT", "url": f"{r['resourceType']}/{r['id']}"},
            }
            for r in resources
        ],
    }


# --- crosswalks ------------------------------------------------------------------

RACE = {  # TCGA RACE -> OMB category
    "White": ("2106-3", "White"),
    "Black or African American": ("2054-5", "Black or African American"),
    "Asian": ("2028-9", "Asian"),
    "American Indian or Alaska Native": ("1002-5", "American Indian or Alaska Native"),
    "Native Hawaiian or Other Pacific Islander": ("2076-8", "Native Hawaiian or Other Pacific Islander"),
}
ETHNICITY = {
    "Hispanic Or Latino": ("2135-2", "Hispanic or Latino"),
    "Not Hispanic Or Latino": ("2186-5", "Not Hispanic or Latino"),
}

# AJCC values (SNOMED qualifiers), keyed by TCGA value without the prefix letter
AJCC_T = {"T1": "1228957006", "T1A": "1228959009", "T1B": "1228962007", "T2": "1229852009",
          "T2A": "1229853004", "T2B": "1229856007", "T3": "1229859000", "T4": "1229864001",
          "TX": "1228950008", "TIS": "1228953005", "T4A": "1229865000", "T4B": "1229866004"}
AJCC_N = {"N0": "1229947003", "N1": "1229951001", "N2": "1229957002", "N3": "1229962001",
          "NX": "1229945006", "N1A": "1229954009", "N1B": "1229955005", "N1C": "1229956006",
          "N2A": "1229959004", "N2B": "1229960009"}
AJCC_M = {"M1": "1229916009", "M1A": "1229917000", "M1B": "1229920008"}  # MX/M0: no M Observation
AJCC_STAGE = {"STAGE I": ("1222723001", "I"), "STAGE IA": ("1222724007", "IA"),
              "STAGE IB": ("1222728005", "IB"), "STAGE II": ("1222765007", "II"),
              "STAGE IIA": ("1222766008", "IIA"), "STAGE IIB": ("1222769001", "IIB"),
              "STAGE IIIA": ("1222802001", "IIIA"), "STAGE IIIB": ("1222805004", "IIIB"),
              "STAGE IIC": ("1222770000", "IIC"), "STAGE III": ("1222801008", "III"),
              "STAGE IIIC": ("1222806003", "IIIC"), "STAGE IV": ("1222837003", "IV"),
              "STAGE IVA": ("1222838008", "IVA"), "STAGE IVB": ("1222841004", "IVB")}
AJCC_EDITION = {
    "6TH": ("444256004", "American Joint Committee on Cancer, Cancer Staging Manual, 6th edition neoplasm staging system"),
    "7TH": ("443830009", "American Joint Committee on Cancer, Cancer Staging Manual, 7th edition neoplasm staging system"),
}  # 3rd-5th: no SNOMED code found -> no method

# OncoTree -> primary cancer (SNOMED disorder)
ONCOTREE = {"LUAD": ("254626006", "Adenocarcinoma of lung")}
COLORECTAL = {"COAD", "READ", "MACR"}  # colorectal OncoTree codes: the primary-cancer code follows the site
COLORECTAL_PRIMARY = {"C18": ("1701000119104", "Primary adenocarcinoma of colon"),
                      "C19": ("425178004", "Adenocarcinoma of rectosigmoid junction"),
                      "C20": ("254582000", "Adenocarcinoma of rectum")}


def primary_code(oncotree: str, icdo_site: str):
    """SNOMED primary-cancer disorder: by OncoTree code, or by site for colorectal (mucinous: histology extension)."""
    if oncotree in COLORECTAL:
        return COLORECTAL_PRIMARY.get((icdo_site or "C18")[:3], COLORECTAL_PRIMARY["C18"])
    return ONCOTREE[oncotree]
# ICD-O-3 morphology codes used as-is in the histology extension (its value set includes ICD-O-3)
MORPHOLOGY = {
    "8140/3": "Adenocarcinoma, NOS", "8255/3": "Adenocarcinoma with mixed subtypes",
    "8550/3": "Acinar cell carcinoma", "8260/3": "Papillary adenocarcinoma, NOS",
    "8252/3": "Bronchiolo-alveolar carcinoma, non-mucinous", "8253/3": "Bronchiolo-alveolar carcinoma, mucinous",
    "8250/3": "Bronchiolo-alveolar adenocarcinoma, NOS", "8480/3": "Mucinous adenocarcinoma",
    "8230/3": "Solid carcinoma, NOS", "8507/3": "Micropapillary carcinoma, NOS",
    "8310/3": "Clear cell adenocarcinoma, NOS", "8490/3": "Signet ring cell carcinoma",
    "8263/3": "Adenocarcinoma in tubulovillous adenoma", "8211/3": "Tubular adenocarcinoma", "8010/3": "Carcinoma, NOS",
}
# ICD-O-3 topography (+ laterality) -> SNOMED body structure
SITE = {
    ("C34.0", "Right"): ("70074004", "Right main bronchus structure"),
    ("C34.0", "Left"): ("75245000", "Left main bronchus structure"),
    ("C34.0", None): ("102297006", "Main bronchus structure"),
    ("C34.1", "Right"): ("42400003", "Structure of upper lobe of right lung"),
    ("C34.1", "Left"): ("44714003", "Structure of upper lobe of left lung"),
    ("C34.1", None): ("45653009", "Structure of upper lobe of lung"),
    ("C34.2", None): ("72481006", "Structure of middle lobe of right lung"),
    ("C34.3", "Right"): ("266005", "Structure of lower lobe of right lung"),
    ("C34.3", "Left"): ("41224006", "Structure of lower lobe of left lung"),
    ("C34.3", None): ("90572001", "Structure of lower lobe of lung"),
    ("C34.8", "Right"): ("3341006", "Right lung structure"), ("C34.8", "Left"): ("44029006", "Left lung structure"),
    ("C34.8", None): ("39607008", "Lung structure"),
    ("C34.9", "Right"): ("3341006", "Right lung structure"), ("C34.9", "Left"): ("44029006", "Left lung structure"),
    ("C34.9", None): ("39607008", "Lung structure"),
    ("C18.0", None): ("32713005", "Cecum structure"), ("C18.1", None): ("66754008", "Appendix structure"),
    ("C18.2", None): ("9040008", "Ascending colon structure"), ("C18.3", None): ("48338005", "Structure of right colic flexure"),
    ("C18.4", None): ("485005", "Transverse colon structure"), ("C18.5", None): ("72592005", "Structure of left colic flexure"),
    ("C18.6", None): ("32622004", "Descending colon structure"), ("C18.7", None): ("60184004", "Sigmoid colon structure"),
    ("C18.8", None): ("71854001", "Colon structure"), ("C18.9", None): ("71854001", "Colon structure"),
    ("C19.9", None): ("49832006", "Structure of rectosigmoid junction"), ("C20.9", None): ("34402009", "Rectum structure"),
}


def site_for(icdo_site: str, laterality: str | None):
    key = icdo_site[:5]
    return SITE.get((key, laterality)) or SITE.get((key, None))


LATERALITY = {"Right": ("24028007", "Right"), "Left": ("7771000", "Left")}
_ICD10CM_DISPLAY = {
    "C34.00": "Malignant neoplasm of unspecified main bronchus", "C34.01": "Malignant neoplasm of right main bronchus",
    "C34.02": "Malignant neoplasm of left main bronchus",
    "C34.10": "Malignant neoplasm of upper lobe, unspecified bronchus or lung",
    "C34.11": "Malignant neoplasm of upper lobe, right bronchus or lung",
    "C34.12": "Malignant neoplasm of upper lobe, left bronchus or lung",
    "C34.2": "Malignant neoplasm of middle lobe, bronchus or lung",
    "C34.30": "Malignant neoplasm of lower lobe, unspecified bronchus or lung",
    "C34.31": "Malignant neoplasm of lower lobe, right bronchus or lung",
    "C34.32": "Malignant neoplasm of lower lobe, left bronchus or lung",
    "C34.80": "Malignant neoplasm of overlapping sites of unspecified bronchus and lung",
    "C34.81": "Malignant neoplasm of overlapping sites of right bronchus and lung",
    "C34.82": "Malignant neoplasm of overlapping sites of left bronchus and lung",
    "C34.90": "Malignant neoplasm of unspecified part of unspecified bronchus or lung",
    "C34.91": "Malignant neoplasm of unspecified part of right bronchus or lung",
    "C34.92": "Malignant neoplasm of unspecified part of left bronchus or lung",
    "C18.0": "Malignant neoplasm of cecum", "C18.1": "Malignant neoplasm of appendix",
    "C18.2": "Malignant neoplasm of ascending colon", "C18.3": "Malignant neoplasm of hepatic flexure",
    "C18.4": "Malignant neoplasm of transverse colon", "C18.5": "Malignant neoplasm of splenic flexure",
    "C18.6": "Malignant neoplasm of descending colon", "C18.7": "Malignant neoplasm of sigmoid colon",
    "C18.8": "Malignant neoplasm of overlapping sites of colon", "C18.9": "Malignant neoplasm of colon, unspecified",
    "C19": "Malignant neoplasm of rectosigmoid junction", "C20": "Malignant neoplasm of rectum",
}


def icd10cm(icd10: str, laterality: str | None):
    """WHO ICD-10 (+ GDC laterality for lung) -> ICD-10-CM code and display; None if not in the crosswalk."""
    base = icd10[:5]
    if not base.startswith("C34"):  # colorectal: WHO and CM codes are the same
        return (base, _ICD10CM_DISPLAY[base]) if base in _ICD10CM_DISPLAY else None
    if base == "C34.2":
        code = "C34.2"
    else:
        code = base + {"Right": "1", "Left": "2"}.get(laterality, "0")
    return (code, _ICD10CM_DISPLAY[code]) if code in _ICD10CM_DISPLAY else None


# Sample procurement -> (procedure code, noun used in the pathology report); none recorded -> no procedure
PROCUREMENT = {
    "Tumor Resection": (("77648009", "Excision of lesion of lung"), "resection"),
    "Excisional Biopsy": (("77648009", "Excision of lesion of lung"), "excisional biopsy"),
    "Fine Needle Aspiration": (("432231006", "Fine needle aspiration biopsy of lung"), "fine needle aspiration"),
    "Right Hemicolectomy": (("359571009", "Right colectomy"), "right hemicolectomy"),
    "Left Hemicolectomy": (("82619000", "Left colectomy"), "left hemicolectomy"),
    "Sigmoid Colectomy": (("84604002", "Sigmoid colectomy"), "sigmoid colectomy"),
    "Transverse Colectomy": (("26925005", "Transverse colectomy"), "transverse colectomy"),
    "Total Colectomy": (("26390003", "Total colectomy"), "total colectomy"),
    "Anterior Resection Of Rectum": (("4558008", "Anterior resection of rectum"), "anterior resection"),
    "Abdomino-Perineal Resection Of Rectum": (("265414003", "Abdominoperineal resection"), "abdominoperineal resection"),
}
# Prior cancers are past history ("prior X" = history of X), never a malignancy code.
# GDC tissue_or_organ_of_origin (keyword) -> (SNOMED history-of code, ICD-10-CM Z85 code); checked on tx.fhir.org
_PRIOR = [
    ("prostate", ("428262008", "History of malignant neoplasm of prostate"), ("Z85.46", "Personal history of malignant neoplasm of prostate")),
    ("breast", ("429087003", "History of malignant neoplasm of breast"), ("Z85.3", "Personal history of malignant neoplasm of breast")),
    ("endometrium", ("429089000", "History of malignant neoplasm of endometrium"), ("Z85.42", "Personal history of malignant neoplasm of other parts of uterus")),
    ("uterus", ("266987004", "H/O: malignant neoplasm"), ("Z85.42", "Personal history of malignant neoplasm of other parts of uterus")),
    ("cervix", ("429484003", "History of malignant neoplasm of cervix"), ("Z85.41", "Personal history of malignant neoplasm of cervix uteri")),
    ("bladder", ("415086001", "History of malignant neoplasm of bladder"), ("Z85.51", "Personal history of malignant neoplasm of bladder")),
    ("kidney", ("415081006", "History of malignant neoplasm of kidney"), ("Z85.528", "Personal history of other malignant neoplasm of kidney")),
    ("thyroid", ("429254008", "History of malignant neoplasm of thyroid"), ("Z85.850", "Personal history of malignant neoplasm of thyroid")),
    ("colon", ("429699009", "History of malignant neoplasm of colon"), ("Z85.038", "Personal history of other malignant neoplasm of large intestine")),
    ("rectum", ("429084005", "History of malignant neoplasm of rectum"), ("Z85.048", "Personal history of other malignant neoplasm of rectum, rectosigmoid junction, and anus")),
    ("larynx", ("473064002", "History of malignant neoplasm of larynx"), ("Z85.21", "Personal history of malignant neoplasm of larynx")),
    ("skin", ("429050006", "History of malignant neoplasm of skin"), ("Z85.828", "Personal history of other malignant neoplasm of skin")),
    ("ovary", ("429090009", "History of malignant neoplasm of ovary"), ("Z85.43", "Personal history of malignant neoplasm of ovary")),
    ("lung", ("415082004", "History of malignant neoplasm of lung"), ("Z85.118", "Personal history of other malignant neoplasm of bronchus and lung")),
]
_PRIOR_GENERIC = (("266987004", "H/O: malignant neoplasm"), ("Z85.9", "Personal history of malignant neoplasm, unspecified"))


def prior_primary(organ: str) -> tuple:
    """(SNOMED, ICD-10-CM, specific?) for a GDC prior-primary organ; generic SNOMED (+ organ text) when no specific code."""
    o = (organ or "").lower()
    for key, sct_code, icd in _PRIOR:
        if key in o:
            return sct_code, icd, sct_code[0] != "266987004"
    return _PRIOR_GENERIC[0], _PRIOR_GENERIC[1], False

# Metastasis sites for the rule-filled site: (condition code, body structure)
METASTASIS_SITES = {
    "LUAD": [
        (("94225005", "Metastatic malignant neoplasm to brain"), ("12738006", "Brain structure")),
        (("94222008", "Metastatic malignant neoplasm to bone"), ("272673000", "Bone structure")),
        (("94381002", "Metastatic malignant neoplasm to liver"), ("10200004", "Liver structure")),
        (("94161006", "Metastatic malignant neoplasm to adrenal gland"), ("23451007", "Adrenal structure")),
        (("94391008", "Metastatic malignant neoplasm to lung"), ("39607008", "Lung structure")),
    ],
    # colorectal: liver most often (listed twice to weight it), then lung, peritoneum, distant lymph nodes
    "COLORECTAL": [
        (("94381002", "Metastatic malignant neoplasm to liver"), ("10200004", "Liver structure")),
        (("94381002", "Metastatic malignant neoplasm to liver"), ("10200004", "Liver structure")),
        (("94391008", "Metastatic malignant neoplasm to lung"), ("39607008", "Lung structure")),
        (("94627008", "Metastatic malignant neoplasm to peritoneum"), ("15425007", "Structure of serous membrane of peritoneum")),
        (("94392001", "Metastatic malignant neoplasm to lymph node"), ("59441001", "Structure of lymph node")),
    ],
}


# GDC progression_or_recurrence_anatomic_site -> (condition, body structure), when GDC names the site
GDC_MET_SITE = {
    "Liver": (("94381002", "Metastatic malignant neoplasm to liver"), ("10200004", "Liver structure")),
    "Lung, NOS": (("94391008", "Metastatic malignant neoplasm to lung"), ("39607008", "Lung structure")),
    "Brain, NOS": (("94225005", "Metastatic malignant neoplasm to brain"), ("12738006", "Brain structure")),
    "Bone, NOS": (("94222008", "Metastatic malignant neoplasm to bone"), ("272673000", "Bone structure")),
}
# GDC molecular_tests (IHC) -> LOINC; results: "Loss of Expression" -> Absent, retained -> Present
MMR_IHC = {"MLH1": ("81691-8", "MLH-1 Ag [Presence] in Cancer specimen by Immune stain"),
           "MSH2": ("81692-6", "MSH-2 Ag [Presence] in Cancer specimen by Immune stain"),
           "MSH6": ("81693-4", "MSH-6 Ag [Presence] in Cancer specimen by Immune stain"),
           "PMS2": ("81694-2", "PMS2 Ag [Presence] in Cancer specimen by Immune stain")}
CEA = ("2039-6", "Carcinoembryonic Ag [Mass/volume] in Serum or Plasma")


_SITE_WORD = {"Structure of serous membrane of peritoneum": "peritoneal", "Structure of lymph node": "lymph node",
              "Adrenal structure": "adrenal"}


def site_word(display: str) -> str:
    """Body structure display -> the word used in report and note text: 'Liver structure' -> 'liver'."""
    return _SITE_WORD.get(display) or display.replace(" structure", "").replace("Structure of ", "").lower()


def tnm(value: str) -> str:
    """'T4A' -> 'T4a', 'N2B' -> 'N2b', 'TIS' -> 'Tis'; X stays upper case ('NX')."""
    return value[0] + value[1:].lower().replace("x", "X") if value else value


def metastasis_sites(oncotree: str) -> list:
    return METASTASIS_SITES["COLORECTAL" if oncotree in COLORECTAL else oncotree]

SMOKING = {  # GDC tobacco_smoking_status -> SNOMED
    "Current Reformed Smoker for > 15 yrs": ("8517006", "Ex-smoker"),
    "Current Reformed Smoker for < or = 15 yrs": ("8517006", "Ex-smoker"),
    "Current Reformed Smoker, Duration Not Specified": ("8517006", "Ex-smoker"),
    "Current Smoker": ("77176002", "Smoker"),
    "Lifelong Non-Smoker": ("266919005", "Never smoked tobacco"),
}

RXNORM = {  # TCGA AGENT (lower case) -> RxNorm ingredient
    "cisplatin": "2555", "carboplatin": "40048", "pemetrexed": "68446", "pemetrexed disodium": "68446",
    "paclitaxel": "56946", "gemcitabine": "12574", "docetaxel": "72962", "erlotinib": "337525",
    "vincristine": "11202", "etoposide": "4179", "bevacizumab": "253337", "vinorelbine": "39541",
    "gefitinib": "328134", "zoledronic acid": "77655", "vinblastine": "11198", "irinotecan": "51499",
    "nivolumab": "1597876", "topotecan": "57308", "cyclophosphamide": "3002", "belinostat": "1543543",
    "cyanocobalamin": "11248", "pegfilgrastim": "338036", "denosumab": "993449",
    # colorectal (checked on RxNav 2026-11-12)
    "fluorouracil": "4492", "leucovorin": "6313", "oxaliplatin": "32592", "capecitabine": "194000",
    "cetuximab": "318341", "panitumumab": "263034", "aflibercept": "1232150", "regorafenib": "1312397",
    "mitomycin": "632", "floxuridine": "4488", "raltitrexed": "196239", "filgrastim": "68442", "dexamethasone": "3264",
}
SUPPORTIVE_DRUGS = {"zoledronic acid", "denosumab", "pegfilgrastim", "cyanocobalamin", "filgrastim", "dexamethasone"}

INTENT = {
    "curative": ("373808002", "Curative - procedure intent"),
    "palliative": ("363676003", "Palliative - procedure intent"),
    "supportive": ("399707004", "Supportive - procedure intent"),
}
STATUS_TREND = {
    "improved": ("268910001", "Patient's condition improved"),
    "stable": ("359746009", "Patient's condition stable"),
    "worsened": ("271299001", "Patient's condition worsened"),
}
RESPONSE = {  # MEASURE_OF_RESPONSE / GDC treatment_outcome -> (trend, evidence)
    "complete response": ("improved", None), "partial response": ("improved", None),
    "stable disease": ("stable", None),
    "clinical progressive disease": ("worsened", ("5880005", "Physical examination")),
    "radiographic progressive disease": ("worsened", ("363679005", "Imaging")),
    "progressive disease": ("worsened", None),
}
EVIDENCE = {"Biopsy with Histologic Confirmation": ("252416005", "Histopathology test")}

ECOG_ANSWER = {
    0: ("LA9622-7", "Fully active, able to carry on all pre-disease performance without restriction"),
    1: ("LA9623-5", "Restricted in physically strenuous activity but ambulatory and able to carry out work of a light or sedentary nature, e.g., light house work, office work"),
    2: ("LA9624-3", "Ambulatory and capable of all selfcare but unable to carry out any work activities. Up and about more than 50% of waking hours"),
    3: ("LA9625-0", "Capable of only limited selfcare, confined to bed or chair more than 50% of waking hours"),
}

# (LOINC, display, UCUM unit, low, high, female range override)
LABS = [
    ("6690-2", "Leukocytes [#/volume] in Blood by Automated count", "10*3/uL", 4.0, 11.0, None),
    ("718-7", "Hemoglobin [Mass/volume] in Blood", "g/dL", 13.5, 17.5, (12.0, 15.5)),
    ("777-3", "Platelets [#/volume] in Blood by Automated count", "10*3/uL", 150, 400, None),
    ("751-8", "Neutrophils [#/volume] in Blood by Automated count", "10*3/uL", 1.8, 7.7, None),
    ("2160-0", "Creatinine [Mass/volume] in Serum or Plasma", "mg/dL", 0.7, 1.3, (0.5, 1.1)),
    ("1742-6", "Alanine aminotransferase [Enzymatic activity/volume] in Serum or Plasma", "U/L", 7, 56, None),
    ("1920-8", "Aspartate aminotransferase [Enzymatic activity/volume] in Serum or Plasma", "U/L", 10, 40, None),
    ("1975-2", "Bilirubin.total [Mass/volume] in Serum or Plasma", "mg/dL", 0.1, 1.2, None),
    ("1751-7", "Albumin [Mass/volume] in Serum or Plasma", "g/dL", 3.5, 5.0, None),
]
