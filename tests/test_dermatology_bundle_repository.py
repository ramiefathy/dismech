from __future__ import annotations

from pathlib import Path

import pytest

from dismech.export.dermatology_bundle import plan_dermatology_bundle


@pytest.mark.kb_data
def test_repository_dermatology_scope_is_nonempty_and_dependency_closed() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    plan = plan_dermatology_bundle(
        repo_root,
        repo_root / "dermatology_kg" / "scope.yaml",
        profile="compact",
        strict=True,
    )

    assert len(plan.records) >= 100
    assert len(plan.selected) >= 25
    assert not plan.missing_references
    assert not plan.missing_modules
    assert all(decision.reasons for decision in plan.decisions if decision.included)
    assert len({record.slug for record in plan.selected}) == len(plan.selected)
