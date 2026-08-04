from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

from dismech.export.dermatology_bundle import (
    DermatologyBundleError,
    build_dermatology_bundle,
    classify_disorder,
    inventory_disorders,
    load_scope_policy,
    plan_dermatology_bundle,
    source_commit,
    write_audit_only,
)


SOURCE_SHA = "1" * 40


def write_yaml(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def write_reference(repo: Path, reference_id: str, body: str = "Cached source text") -> Path:
    safe = reference_id.replace(":", "_").replace("/", "_")
    path = repo / "references_cache" / f"{safe}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nreference_id: {reference_id}\ntitle: Test reference\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def minimal_policy(**overrides: object) -> dict[str, object]:
    policy: dict[str, object] = {
        "policy_version": 1,
        "direct_metadata_rules": [
            {
                "id": "skin_identity",
                "regex": r"\b(?:skin|cutaneous|acne)\b",
                "tier": "PRIMARY_DERMATOLOGY",
                "domains": ["medical_dermatology"],
            }
        ],
        "comanaged_name_rules": [
            {
                "id": "lupus",
                "regex": r"\bsystemic lupus erythematosus\b",
                "tier": "DERMATOLOGY_COMANAGED",
                "domains": ["rheumatologic_dermatology"],
            }
        ],
        "phenotype_rules": {
            "minimum_matches": 4,
            "minimum_matches_with_diagnostic": 2,
            "patterns": [
                {"id": "skin_signs", "regex": r"\b(?:rash|skin ulcer|alopecia|nail dystrophy)\b"}
            ],
        },
        "candidate_description_rules": [
            {"id": "skin_description", "regex": r"\b(?:skin|cutaneous)\b"}
        ],
        "manual_includes": [],
        "manual_excludes": [],
        "board_priority": [
            {
                "id": "acne_vulgaris",
                "label": "Acne vulgaris",
                "aliases": ["Acne"],
                "domains": ["medical_dermatology"],
            }
        ],
    }
    policy.update(overrides)
    return policy


def make_repo(tmp_path: Path, policy: dict[str, object] | None = None) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    (repo / "kb" / "disorders").mkdir(parents=True)
    (repo / "kb" / "modules").mkdir(parents=True)
    (repo / "references_cache").mkdir(parents=True)
    (repo / "src" / "dismech" / "schema").mkdir(parents=True)
    (repo / "src" / "dismech" / "schema" / "dismech.yaml").write_text(
        "id: https://example.org/test\nname: test\n", encoding="utf-8"
    )
    (repo / "LICENSE").write_text("BSD-3-Clause\n", encoding="utf-8")
    policy_path = repo / "dermatology_kg" / "scope.yaml"
    write_yaml(policy_path, policy or minimal_policy())
    return repo, policy_path


def test_classifier_is_fail_closed_and_manual_exclude_wins(tmp_path: Path) -> None:
    policy = minimal_policy(
        manual_excludes=[{"slug": "Acne_Vulgaris", "reason": "test override"}]
    )
    repo, policy_path = make_repo(tmp_path, policy)
    write_yaml(
        repo / "kb" / "disorders" / "Acne_Vulgaris.yaml",
        {
            "name": "Acne Vulgaris",
            "parents": ["Inflammatory skin disease"],
            "description": "A cutaneous disorder.",
        },
    )
    write_yaml(
        repo / "kb" / "disorders" / "Incidental_Rash_Syndrome.yaml",
        {
            "name": "Incidental Rash Syndrome",
            "description": "A systemic disease with occasional skin findings.",
            "phenotypes": [
                {
                    "name": "Rash",
                    "diagnostic": False,
                    "phenotype_term": {"term": {"id": "HP:0000988", "label": "Skin rash"}},
                }
            ],
        },
    )

    loaded_policy = load_scope_policy(policy_path)
    records = {record.slug: record for record in inventory_disorders(repo)}
    acne = classify_disorder(records["Acne_Vulgaris"], loaded_policy)
    incidental = classify_disorder(records["Incidental_Rash_Syndrome"], loaded_policy)

    assert acne.included is False
    assert acne.reasons == ("manual_exclude:test override",)
    assert incidental.included is False
    assert incidental.candidate_score > 0
    assert "description:skin_description" in incidental.candidate_reasons


def test_structured_diagnostic_phenotypes_can_include_systemic_disease(tmp_path: Path) -> None:
    repo, policy_path = make_repo(tmp_path)
    write_yaml(
        repo / "kb" / "disorders" / "Syndrome.yaml",
        {
            "name": "Systemic Syndrome",
            "phenotypes": [
                {"name": "Alopecia", "diagnostic": True},
                {"name": "Nail dystrophy", "diagnostic": False},
            ],
        },
    )
    decision = classify_disorder(inventory_disorders(repo)[0], load_scope_policy(policy_path))
    assert decision.included is True
    assert decision.tier == "STRUCTURED_CUTANEOUS_MANIFESTATIONS"
    assert decision.cutaneous_phenotype_count == 2
    assert decision.diagnostic_cutaneous_phenotype_count == 1


def test_archival_bundle_preserves_source_bytes_and_dependency_closure(tmp_path: Path) -> None:
    repo, policy_path = make_repo(tmp_path)
    disorder_path = repo / "kb" / "disorders" / "Acne_Vulgaris.yaml"
    disorder_bytes = (
        b"name: Acne Vulgaris\n"
        b"parents:\n- Inflammatory skin disease\n"
        b"disease_term:\n  term:\n    id: MONDO:0011438\n    label: acne\n"
        b"pathophysiology:\n- name: Inflammation\n  conforms_to: inflammatory_response#Inflammation\n"
        b"  evidence:\n  - reference: PMID:1\n    snippet: Exact text\n"
    )
    disorder_path.write_bytes(disorder_bytes)
    module_path = repo / "kb" / "modules" / "inflammatory_response.yaml"
    module_path.write_bytes(
        b"name: Inflammatory response\npathophysiology:\n- name: Inflammation\n  evidence:\n  - reference: PMID:2\n"
    )
    ref1 = write_reference(repo, "PMID:1", "Exact text")
    ref2 = write_reference(repo, "PMID:2", "Module text")

    write_yaml(
        repo / "kb" / "comorbidities" / "Acne_Depression.yaml",
        {
            "name": "Acne and depression",
            "disease_a": "Acne Vulgaris",
            "disease_b": "MONDO:0002050",
            "evidence": [{"reference": "PMID:3"}],
        },
    )
    ref3 = write_reference(repo, "PMID:3", "Association text")
    write_yaml(
        repo / "history" / "disorders" / "Acne_Vulgaris" / "2026-01-01.yaml",
        {
            "history_version": 1,
            "target": {
                "kind": "disorder",
                "slug": "Acne_Vulgaris",
                "path": "kb/disorders/Acne_Vulgaris.yaml",
            },
        },
    )
    research = repo / "research" / "Acne_Vulgaris-deep-research.md"
    research.parent.mkdir(parents=True, exist_ok=True)
    research.write_bytes(b"research bytes\n")
    hypothesis = repo / "kb" / "hypotheses" / "Acne_Vulgaris" / "canonical" / "notes.md"
    hypothesis.parent.mkdir(parents=True, exist_ok=True)
    hypothesis.write_bytes(b"hypothesis bytes\n")

    output = tmp_path / "bundle"
    archive = tmp_path / "bundle.tar.gz"
    result = build_dermatology_bundle(
        repo,
        policy_path,
        output,
        profile="archival",
        source_commit_override=SOURCE_SHA,
        archive_path=archive,
    )

    assert result.selected_count == 1
    assert (output / "kb" / "disorders" / disorder_path.name).read_bytes() == disorder_bytes
    assert (output / "kb" / "modules" / module_path.name).read_bytes() == module_path.read_bytes()
    assert (output / ref1.relative_to(repo)).read_bytes() == ref1.read_bytes()
    assert (output / ref2.relative_to(repo)).read_bytes() == ref2.read_bytes()
    assert (output / ref3.relative_to(repo)).read_bytes() == ref3.read_bytes()
    assert (output / research.relative_to(repo)).read_bytes() == research.read_bytes()
    assert (output / hypothesis.relative_to(repo)).read_bytes() == hypothesis.read_bytes()
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["source_commit"] == SOURCE_SHA
    assert manifest["selected_disorders"] == ["Acne_Vulgaris"]
    assert manifest["boundary_mondo_ids"] == ["MONDO:0002050"]
    assert manifest["source_file_sha256"]["kb/disorders/Acne_Vulgaris.yaml"] == hashlib.sha256(
        disorder_bytes
    ).hexdigest()
    assert archive.is_file()
    reports_text = "\n".join(
        report.read_text(encoding="utf-8")
        for report in sorted((output / "reports").glob("*.tsv"))
    )
    assert str(repo) not in reports_text
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
    assert "dismech-dermatology-kg/kb/disorders/Acne_Vulgaris.yaml" in names


def test_strict_mode_fails_on_unresolved_reference(tmp_path: Path) -> None:
    repo, policy_path = make_repo(tmp_path)
    write_yaml(
        repo / "kb" / "disorders" / "Acne.yaml",
        {
            "name": "Acne",
            "parents": ["Skin disease"],
            "evidence": [{"reference": "PMID:404"}],
        },
    )
    with pytest.raises(DermatologyBundleError, match="unresolved references: PMID:404"):
        plan_dermatology_bundle(
            repo,
            policy_path,
            source_commit_override=SOURCE_SHA,
        )


def test_strict_mode_fails_on_unresolved_top_level_reference_list(tmp_path: Path) -> None:
    repo, policy_path = make_repo(tmp_path)
    write_yaml(
        repo / "kb" / "disorders" / "Acne.yaml",
        {
            "name": "Acne",
            "parents": ["Skin disease"],
            "references": ["PMID:405"],
        },
    )
    with pytest.raises(DermatologyBundleError, match="unresolved references: PMID:405"):
        plan_dermatology_bundle(
            repo,
            policy_path,
            source_commit_override=SOURCE_SHA,
        )


def test_deterministic_archives_are_byte_identical(tmp_path: Path) -> None:
    repo, policy_path = make_repo(tmp_path)
    write_yaml(
        repo / "kb" / "disorders" / "Acne.yaml",
        {"name": "Acne", "parents": ["Skin disease"], "evidence": [{"reference": "PMID:1"}]},
    )
    write_reference(repo, "PMID:1")
    archives = []
    for index in (1, 2):
        archive = tmp_path / f"bundle-{index}.tar.gz"
        build_dermatology_bundle(
            repo,
            policy_path,
            tmp_path / f"bundle-{index}",
            source_commit_override=SOURCE_SHA,
            archive_path=archive,
        )
        archives.append(archive.read_bytes())
    assert archives[0] == archives[1]


def test_audit_only_writes_board_and_candidate_reports(tmp_path: Path) -> None:
    repo, policy_path = make_repo(tmp_path)
    write_yaml(
        repo / "kb" / "disorders" / "Acne_Vulgaris.yaml",
        {"name": "Acne Vulgaris", "parents": ["Skin disease"]},
    )
    write_yaml(
        repo / "kb" / "disorders" / "Systemic_Disease.yaml",
        {"name": "Systemic Disease", "description": "Occasional cutaneous findings."},
    )
    output = tmp_path / "audit"
    result = write_audit_only(
        repo,
        policy_path,
        output,
        profile="compact",
        strict=True,
        force=False,
        source_commit_override=SOURCE_SHA,
    )
    assert result.selected_count == 1
    assert result.candidate_count == 1
    assert "PRESENT_SELECTED" in (output / "reports" / "board_priority_gap.tsv").read_text()
    assert "Systemic Disease" in (output / "reports" / "candidate_review.tsv").read_text()
    summary = json.loads((output / "audit_summary.json").read_text())
    assert summary["source_commit"] == SOURCE_SHA


def test_audit_refuses_to_overwrite_repository_source_paths(tmp_path: Path) -> None:
    repo, policy_path = make_repo(tmp_path)
    write_yaml(
        repo / "kb" / "disorders" / "Acne.yaml",
        {"name": "Acne", "parents": ["Skin disease"]},
    )
    with pytest.raises(DermatologyBundleError, match="allowed only below dist/ or tmp/"):
        write_audit_only(
            repo,
            policy_path,
            repo / "dermatology_kg",
            profile="compact",
            strict=True,
            force=True,
            source_commit_override=SOURCE_SHA,
        )


def test_source_commit_rejects_uncommitted_provenance_inputs(tmp_path: Path) -> None:
    repo, policy_path = make_repo(tmp_path)
    write_yaml(
        repo / "kb" / "disorders" / "Acne.yaml",
        {"name": "Acne", "parents": ["Skin disease"]},
    )
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", repo, "config", "user.email", "test@example.org"], check=True
    )
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-qm", "fixture"], check=True)

    clean_commit = source_commit(repo)
    assert len(clean_commit) == 40

    policy_path.write_text(policy_path.read_text() + "\n# dirty\n", encoding="utf-8")
    with pytest.raises(DermatologyBundleError, match="not cryptographically bound to HEAD"):
        source_commit(repo)
