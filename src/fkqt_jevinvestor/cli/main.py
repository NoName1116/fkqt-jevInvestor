import argparse
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fkqt-jevinvestor")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("version")
    args = parser.parse_args(argv)
    if args.command == "version":
        print("fkqt-jevinvestor 0.1.0")
        return 0
    return 2
