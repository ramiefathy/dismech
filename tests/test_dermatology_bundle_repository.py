from __future__ import annotations

from pathlib import Path

import pytest

from dismech.export.dermatology_bundle import plan_dermatology_bundle


# These are source-corpus cache gaps, not files created by the specialty exporter.
# The non-strict plan records them in every audit/manifest. Keep this list exact so
# new gaps remain visible rather than being silently accepted.
EXPECTED_SOURCE_REFERENCE_GAPS = {
    "DOI:10.1111/bjd.19170",
    "DOI:10.1177/2050313x231204197",
    "DOI:10.1186/s12879-024-09937-2",
    "DOI:10.3389/fimmu.2023.1128688",
    "DOI:10.47276/lr.95.1.7",
    "DOI:10.47276/lr.95.2.2024018",
    "MONDO",
    "ORPHA:500",
}


@pytest.mark.kb_data
def test_repository_dermatology_scope_is_nonempty_and_dependency_audited() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    plan = plan_dermatology_bundle(
        repo_root,
        repo_root / "dermatology_kg" / "scope.yaml",
        profile="compact",
        strict=False,
    )

    assert len(plan.records) >= 100
    assert len(plan.selected) >= 25
    assert set(plan.missing_references) == EXPECTED_SOURCE_REFERENCE_GAPS
    assert not plan.missing_modules
    assert all(decision.reasons for decision in plan.decisions if decision.included)
    assert len({record.slug for record in plan.selected}) == len(plan.selected)
