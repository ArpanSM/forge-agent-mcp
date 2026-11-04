"""Command-line entry point for validating and generating the corpus."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .errors import CorpusError
from .generator import generate_corpus
from .manifest import ManifestError, load_manifest

DEFAULT_MANIFEST = Path(__file__).resolve().parents[3] / "corpus" / "manifest.json"
DEFAULT_OUTPUT = Path("data") / "corpus"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m forge_agent_mcp.corpus",
        description="Validate and generate the synthetic freight underwriting corpus.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate the committed manifest")
    validate.add_argument("--manifest", default=str(DEFAULT_MANIFEST))

    generate = subparsers.add_parser("generate", help="generate PDFs and ground truth")
    generate.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    generate.add_argument("--out", default=str(DEFAULT_OUTPUT))

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        if args.command == "validate":
            print(f"manifest valid: {len(manifest.carriers)} carriers, {len(manifest.cases)} cases")
            return 0

        result = generate_corpus(manifest, args.out)
        pdf_count = sum(len(case.documents) for case in result.index.cases)
        record_count = sum(len(case.structured_records) for case in result.index.cases)
        print(
            f"generated {result.index.case_count} cases "
            f"({result.index.split_counts.eval} eval, "
            f"{result.index.split_counts.held_out} held out) in {result.root}"
        )
        print(f"  {pdf_count} PDFs, {record_count} structured records")
        print(f"  index: {result.root / 'corpus.json'}")
        return 0
    except (ManifestError, CorpusError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
