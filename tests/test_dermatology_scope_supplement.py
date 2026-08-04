from __future__ import annotations

from pathlib import Path

import yaml

from scripts.build_dermatology_kg_v2 import merge_scope_policy, normalize_label


def test_supplemental_diagnosis_registry_is_unique_and_traceable() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    path = repo_root / "dermatology_kg" / "supplemental_scope" / "diagnosis_registry.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = data["board_priority"]

    assert data["registry_version"] >= 2
    assert len(entries) >= 75

    ids = [entry["id"] for entry in entries]
    labels = [normalize_label(entry["label"]) for entry in entries]
    assert len(ids) == len(set(ids))
    assert len(labels) == len(set(labels))
    assert all(entry.get("domains") for entry in entries)
    assert data["source_basis"]


def test_merged_policy_deduplicates_base_labels_and_preserves_base_policy() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    base_path = repo_root / "dermatology_kg" / "scope.yaml"
    supplement_dir = repo_root / "dermatology_kg" / "supplemental_scope"

    base = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    merged, summary = merge_scope_policy(base_path, supplement_dir)

    assert merged["policy_version"] == base["policy_version"]
    assert len(merged["board_priority"]) >= len(base["board_priority"])
    assert summary["files"] >= 1
    assert summary["added_entries"] >= 50

    labels = [normalize_label(entry["label"]) for entry in merged["board_priority"]]
    ids = [entry["id"] for entry in merged["board_priority"]]
    assert len(labels) == len(set(labels))
    assert len(ids) == len(set(ids))
