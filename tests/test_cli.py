import pytest

from synthgen import cli, select
from synthgen.studies import SUPPORTED


def test_help_lists_commands(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert "studies" in out and "patients" in out and "run" in out


def test_unsupported_study_refused():
    with pytest.raises(SystemExit) as exc:
        cli.main(["run", "--study", "coadread_tcga_pan_can_atlas_2018", "--patients", "TCGA-XX-0000"])
    assert "not supported" in str(exc.value)


def test_eligibility_rules():
    assert select.ineligible_reason({"OS_STATUS": "0:LIVING"}) is None
    assert "died" in select.ineligible_reason({"OS_STATUS": "1:DECEASED"})
    assert select.ineligible_reason({"OS_STATUS": ""}) == "no clinical follow-up data"


def test_choose_requires_and_prefers():
    cands = [
        {"patient": "A", "eligible": True, "features": ["actionable_variant"], "treatment_lines": 1},
        {"patient": "B", "eligible": True, "features": ["actionable_variant", "fusion"], "treatment_lines": 0},
        {"patient": "C", "eligible": False, "features": ["actionable_variant", "fusion"], "treatment_lines": 5},
        {"patient": "D", "eligible": True, "features": ["fusion"], "treatment_lines": 3},
    ]
    picked = [c["patient"] for c in select.choose(cands, 5, ["actionable_variant"], ["fusion"])]
    assert picked == ["B", "A"]


def test_supported_registry_has_required_fields():
    for s in SUPPORTED.values():
        assert {"datahub_commit", "oncotree", "status", "caveats"} <= set(s)
