from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from dermatology_kg.build import build_distribution, classify_document, load_scope


SCOPE_PATH = Path(__file__).parents[1] / "dermatology_kg" / "scope.yaml"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fixture_scope(tmp_path: Path) -> Path:
    scope = yaml.safe_load(SCOPE_PATH.read_text(encoding="utf-8"))
    scope["minimum_selected_conditions"] = 1
    scope["maximum_selected_conditions"] = 20
    path = tmp_path / "scope.yaml"
    path.write_text(yaml.safe_dump(scope, sort_keys=False), encoding="utf-8")
    return path


def _minimal_repo(tmp_path: Path, *, reference: str = "PMID:1") -> Path:
    repo = tmp_path / "repo"
    _write(
        repo / "kb" / "disorders" / "Acne_Vulgaris.yaml",
        f"""name: Acne Vulgaris
description: A primary pilosebaceous disorder.
disease_term:
  term:
    id: MONDO:0011438
    label: acne
pathophysiology:
  - name: Follicular inflammation
    evidence:
      - reference: {reference}
        snippet: Exact evidence.
phenotypes:
  - name: Comedone
""",
    )
    if reference:
        _write(
            repo / "references_cache" / "PMID_1.md",
            """---
reference_id: PMID:1
title: Fixture reference
---

Exact evidence.
""",
        )
    _write(repo / "LICENSE", "fixture license\n")
    return repo


def test_scope_loads_and_version_is_pinned() -> None:
    scope = load_scope(SCOPE_PATH)
    assert scope["classifier_version"] == "dermkg-scope-1"
    assert scope["minimum_selected_conditions"] > 0


def test_classifier_admits_core_and_shared_care_but_not_a_lone_rash() -> None:
    scope = load_scope(SCOPE_PATH)
    acne = classify_document(
        {"name": "Acne Vulgaris", "parents": ["Pilosebaceous unit disorder"]},
        Path("kb/disorders/Acne_Vulgaris.yaml"),
        scope,
    )
    lupus = classify_document(
        {"name": "Systemic Lupus Erythematosus"},
        Path("kb/disorders/Systemic_Lupus_Erythematosus.yaml"),
        scope,
    )
    cystic_fibrosis = classify_document(
        {
            "name": "Cystic Fibrosis",
            "phenotypes": [{"name": "Transient skin rash"}],
        },
        Path("kb/disorders/Cystic_Fibrosis.yaml"),
        scope,
    )

    assert acne.included and acne.tier == "core"
    assert lupus.included and lupus.tier == "shared-care"
    assert not cystic_fibrosis.included


def test_combined_structured_signals_can_admit_a_consultative_condition() -> None:
    scope = load_scope(SCOPE_PATH)
    result = classify_document(
        {
            "name": "Synthetic Multisystem Syndrome",
            "pathophysiology": [
                {
                    "name": "Tissue change",
                    "locations": [{"preferred_term": "skin"}],
                }
            ],
            "phenotypes": [
                {"name": "Telangiectasia"},
                {"name": "Digital ulcer"},
                {"name": "Alopecia"},
            ],
            "diagnosis": [
                {
                    "name": "Evaluation",
                    "description": "Dermatology assessment with skin biopsy and dermoscopy.",
                }
            ],
        },
        Path("kb/disorders/Synthetic_Multisystem_Syndrome.yaml"),
        scope,
    )

    assert result.included
    assert result.tier == "consultative"
    assert result.structured_score >= 6


def test_build_is_lossless_and_copies_reference_closure(tmp_path: Path) -> None:
    repo = _minimal_repo(tmp_path)
    scope = _fixture_scope(tmp_path)
    source = repo / "kb" / "disorders" / "Acne_Vulgaris.yaml"
    output = tmp_path / "out"
    archive = tmp_path / "dermatology-kg.zip"

    result = build_distribution(
        repo,
        output,
        scope_path=scope,
        archive_path=archive,
    )

    copied = output / "kb" / "disorders" / source.name
    assert copied.read_bytes() == source.read_bytes()
    assert (output / "references_cache" / "PMID_1.md").is_file()
    assert result.selected_count == 1
    assert result.reference_count == 1
    assert archive.is_file()

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["selection"]["selected_condition_count"] == 1
    assert manifest["source"]["dirty"] is False
    completed = subprocess.run(
        [sys.executable, str(output / "validate.py")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_build_fails_closed_when_reference_cache_is_incomplete(tmp_path: Path) -> None:
    repo = _minimal_repo(tmp_path, reference="PMID:404")
    (repo / "references_cache" / "PMID_1.md").unlink()
    scope = _fixture_scope(tmp_path)

    with pytest.raises(FileNotFoundError, match="absent from references_cache"):
        build_distribution(repo, tmp_path / "out", scope_path=scope)


def test_archives_are_deterministic(tmp_path: Path) -> None:
    repo = _minimal_repo(tmp_path)
    scope = _fixture_scope(tmp_path)
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    build_distribution(repo, tmp_path / "out-a", scope_path=scope, archive_path=first)
    build_distribution(repo, tmp_path / "out-b", scope_path=scope, archive_path=second)

    assert hashlib.sha256(first.read_bytes()).hexdigest() == hashlib.sha256(
        second.read_bytes()
    ).hexdigest()
