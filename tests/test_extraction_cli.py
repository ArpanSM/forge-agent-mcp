from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge_agent_mcp.extraction.cli import main
from forge_agent_mcp.extraction.models import CaseExtraction


def test_cli_extracts_a_case_offline_and_writes_the_report(
    generated_corpus, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _manifest, result = generated_corpus
    out = tmp_path / "reports" / "case.json"

    code = main(
        [
            "--case",
            "case_onb_ridgeline",
            "--corpus",
            str(result.root),
            "--out",
            str(out),
            "--offline",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "case_onb_ridgeline" in captured.out
    assert "application_kyc" in captured.out
    assert "legal_name" in captured.out
    assert "page 1" in captured.out
    assert "bbox=" in captured.out
    assert "quote=" in captured.out
    assert "confidence=1.00" in captured.out
    assert str(out) in captured.out
    assert out.is_file()

    report = CaseExtraction.model_validate_json(out.read_text(encoding="utf-8"))
    assert report.status == "extracted"
    assert report.provider == "deterministic"
    assert json.loads(out.read_text(encoding="utf-8"))["case_id"] == "case_onb_ridgeline"


def test_cli_prints_missing_documents_and_issues(
    generated_corpus, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _manifest, result = generated_corpus

    code = main(
        [
            "--case",
            "case_inv_sagebrush",
            "--corpus",
            str(result.root),
            "--out",
            str(tmp_path / "sagebrush.json"),
            "--offline",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "Missing documents: proof_of_delivery" in captured.out


def test_cli_with_fake_provider_selects_fake_and_still_extracts_offline(
    generated_corpus,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _manifest, result = generated_corpus
    out = tmp_path / "fake-report.json"
    monkeypatch.setenv("MODEL_PROVIDER", "fake")

    code = main(
        [
            "--case",
            "case_onb_ridgeline",
            "--corpus",
            str(result.root),
            "--out",
            str(out),
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "fallback: deterministic template parser used" in captured.out
    report = CaseExtraction.model_validate_json(out.read_text(encoding="utf-8"))
    assert report.provider == "fake"
    assert report.status == "extracted"
    assert all(document.fallback_used for document in report.documents)


def test_cli_returns_two_for_an_unknown_case(
    generated_corpus, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _manifest, result = generated_corpus

    code = main(
        [
            "--case",
            "case_nope",
            "--corpus",
            str(result.root),
            "--out",
            str(tmp_path / "nope.json"),
            "--offline",
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "unknown case" in captured.err


def test_cli_returns_two_when_the_corpus_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "--case",
            "case_onb_ridgeline",
            "--corpus",
            str(tmp_path / "absent"),
            "--offline",
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "corpus index not found" in captured.err
