"""Pinned bootstrap entry for Mechanicus."""
import argparse
import sys

from mechanicus.repl import repl
from mechanicus.script import run_script


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="mechanicus",
        description=(
            "Specification-driven coding REPL. "
            "With no arguments, starts the interactive session."
        ),
    )
    parser.add_argument(
        "-s",
        "--script",
        metavar="FILE",
        help="Run slash commands from a text file, then exit.",
    )
    args = parser.parse_args(argv)
    if args.script:
        raise SystemExit(run_script(args.script))
    repl()


if __name__ == "__main__":
    main(sys.argv[1:])
