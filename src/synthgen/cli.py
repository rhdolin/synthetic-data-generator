"""Command-line entry point: `synthgen <stage> ...`."""

import argparse

from synthgen import __version__

STAGES = ("harvest", "synthesize", "genomic", "clinical", "validate")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="synthgen", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("stage", choices=STAGES, help="pipeline stage to run")
    args = parser.parse_args(argv)
    parser.exit(1, f"synthgen: stage '{args.stage}' is not implemented yet\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
