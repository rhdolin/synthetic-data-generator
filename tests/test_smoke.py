import pytest

import synthgen
from synthgen.cli import main


def test_version():
    assert synthgen.__version__


def test_cli_unimplemented_stage_exits_nonzero():
    with pytest.raises(SystemExit) as exc:
        main(["harvest"])
    assert exc.value.code == 1
