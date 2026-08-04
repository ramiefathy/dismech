from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator, Mapping, Sequence

import yaml

CLASSIFIER_VERSION = "dermkg-scope-1"
TIER_PRIORITY = {"core": 0, "shared-care": 1, "consultative": 2}
IDENTITY_KEYS = {"name", "synonyms", "category", "parents", "disease_term"}
ENDPOINT_KEYS = {
    "disease",
    "diseases",
    "disease_a",
    "disease_b",
    "disease_1",
    "disease_2",
    "source_disease",
    "target_disease",
    "condition",
    "conditions",
    "disorder",
    "disorders",
    "subject",
    "object",
}
ASSOCIATED_COPY_PATHS = (
    "LICENSE",
    "docs/disclaimer.md",
    "docs/explanation/design-decisions.md",
    "conf/oak_config.yaml",
    "conf/reference_validator_config.yaml",
)


@dataclass(frozen=True)
class Classification:
    slug: str
    name: str
    source_path: str
    included: bool
    tier: str | None
    reasons: tuple[str, ...]
    matched_terms: tuple[str, ...]
    structured_score: int
    mondo_id: str | None


@dataclass(frozen=True)
class BuildResult:
    output_dir: Path
    archive_path: Path | None
    source_commit: str
    selected_count: int
    candidate_count: int
    reference_count: int
    module_count: int
    comorbidity_count: int
    history_count: int
    research_file_count: int


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _normalise_slug(value: str) -> str:
    return _normalise(value.replace("_", " "))


def _term_pattern(term: str) -> re.Pattern[str]:
    normalised = _normalise(term.rstrip("*"))
    if not normalised:
        raise ValueError("Scope terms must not be empty")
    escaped = re.escape(normalised).replace(r"\ ", r"\s+")
    suffix = r"[a-z0-9]*" if term.endswith("*") else ""
    return re.compile(rf"(?<![a-z0-9]){escaped}{suffix}(?![a-z0-9])")


def _matching_terms(text: str, terms: Sequence[str]) -> list[str]:
    normalised = _normalise(text)
    return [term for term in terms if _term_pattern(term).search(normalised)]


def _walk(value: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    yield path, value
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield from _walk(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, (*path, str(index)))


def _strings(value: Any) -> Iterator[str]:
    for _, child in _walk(value):
        if isinstance(child, str):
            yield child


def _path_strings(value: Any, key_terms: set[str]) -> list[str]:
    output: list[str] = []
    for path, child in _walk(value):
        if not isinstance(child, str):
            continue
        normalised_path = {_normalise(part) for part in path}
        if normalised_path & key_terms:
            output.append(child)
    return output


def _identity_text(document: Mapping[str, Any]) -> str:
    values: list[str] = []
    for key in IDENTITY_KEYS:
        if key in document:
            values.extend(_strings(document[key]))
    return "\n".join(values)


def _extract_mondo_id(document: Mapping[str, Any]) -> str | None:
    disease_term = document.get("disease_term")
    if isinstance(disease_term, Mapping):
        term = disease_term.get("term")
        if isinstance(term, Mapping):
            identifier = term.get("id")
            if isinstance(identifier, str) and identifier.startswith("MONDO:"):
                return identifier
    for text in _strings(document.get("mappings", {})):
        if text.startswith("MONDO:"):
            return text
    return None


def _scope_list(scope: Mapping[str, Any], key: str) -> list[str]:
    value = scope.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings")
    return list(value)


def load_scope(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Scope file must contain a mapping: {path}")
    if data.get("classifier_version") != CLASSIFIER_VERSION:
        raise ValueError(
            f"Unsupported classifier_version {data.get('classifier_version')!r}; "
            f"expected {CLASSIFIER_VERSION!r}"
        )
    for key in (
        "core_identity_terms",
        "shared_care_identity_terms",
        "consultative_identity_terms",
        "cutaneous_anatomy_terms",
        "high_specificity_phenotype_terms",
        "specialty_management_terms",
        "explicit_include_slugs",
        "explicit_exclude_slugs",
    ):
        _scope_list(data, key)
    return data


def classify_document(
    document: Mapping[str, Any], source_path: Path, scope: Mapping[str, Any]
) -> Classification:
    slug = source_path.stem
    name_value = document.get("name", slug.replace("_", " "))
    name = str(name_value)
    slug_key = _normalise_slug(slug)
    includes = {_normalise_slug(item) for item in _scope_list(scope, "explicit_include_slugs")}
    excludes = {_normalise_slug(item) for item in _scope_list(scope, "explicit_exclude_slugs")}
    mondo_id = _extract_mondo_id(document)

    if slug_key in excludes:
        return Classification(
            slug=slug,
            name=name,
            source_path=source_path.as_posix(),
            included=False,
            tier=None,
            reasons=("explicit exclusion",),
            matched_terms=(),
            structured_score=0,
            mondo_id=mondo_id,
        )

    identity = _identity_text(document)
    core_hits = _matching_terms(identity, _scope_list(scope, "core_identity_terms"))
    shared_hits = _matching_terms(identity, _scope_list(scope, "shared_care_identity_terms"))
    consult_hits = _matching_terms(identity, _scope_list(scope, "consultative_identity_terms"))

    anatomy_text = "\n".join(
        _path_strings(document, {"locations", "location", "anatomy", "anatomical site", "site"})
    )
    phenotype_text = "\n".join(_path_strings(document, {"phenotypes", "phenotype"}))
    management_text = "\n".join(
        _path_strings(
            document,
            {
                "diagnosis",
                "differential diagnoses",
                "differential diagnosis",
                "treatments",
                "treatment",
                "management",
            },
        )
    )
    anatomy_hits = sorted(
        set(_matching_terms(anatomy_text, _scope_list(scope, "cutaneous_anatomy_terms")))
    )
    phenotype_hits = sorted(
        set(
            _matching_terms(
                phenotype_text, _scope_list(scope, "high_specificity_phenotype_terms")
            )
        )
    )
    management_hits = sorted(
        set(
            _matching_terms(
                management_text, _scope_list(scope, "specialty_management_terms")
            )
        )
    )

    policy = scope.get("structured_signal_policy", {})
    if not isinstance(policy, Mapping):
        raise ValueError("structured_signal_policy must be a mapping")
    anatomy_score = int(policy.get("anatomy_score", 2)) if anatomy_hits else 0
    phenotype_score = min(
        len(phenotype_hits) * int(policy.get("phenotype_score_each", 1)),
        int(policy.get("maximum_phenotype_score", 4)),
    )
    management_score = (
        int(policy.get("specialty_management_score", 4)) if management_hits else 0
    )
    structured_score = anatomy_score + phenotype_score + management_score
    minimum_score = int(policy.get("minimum_total_score", 6))
    required_signal = bool(anatomy_hits or management_hits)
    if not bool(policy.get("require_anatomy_or_specialty_management", True)):
        required_signal = True
    structured_include = structured_score >= minimum_score and required_signal

    reasons: list[str] = []
    matched: list[str] = []
    tier: str | None = None
    if slug_key in includes:
        tier = "shared-care"
        reasons.append("explicit inclusion")
    if core_hits:
        tier = "core"
        reasons.append("primary dermatology identity")
        matched.extend(core_hits)
    elif shared_hits and tier != "core":
        tier = tier or "shared-care"
        reasons.append("enumerated dermatology shared-care identity")
        matched.extend(shared_hits)
    elif consult_hits and tier not in {"core", "shared-care"}:
        tier = tier or "consultative"
        reasons.append("enumerated dermatologic diagnostic-consult identity")
        matched.extend(consult_hits)
    if structured_include and tier is None:
        tier = "consultative"
        reasons.append(
            "combined structured dermatologic anatomy/phenotype/management signals"
        )
        matched.extend(anatomy_hits + phenotype_hits + management_hits)

    return Classification(
        slug=slug,
        name=name,
        source_path=source_path.as_posix(),
        included=tier is not None,
        tier=tier,
        reasons=tuple(reasons),
        matched_terms=tuple(sorted(set(matched))),
        structured_score=structured_score,
        mondo_id=mondo_id,
    )


def _load_disorders(repo_root: Path, scope: Mapping[str, Any]) -> tuple[
    dict[str, Mapping[str, Any]], list[Classification]
]:
    disorder_dir = repo_root / "kb" / "disorders"
    if not disorder_dir.is_dir():
        raise FileNotFoundError(f"Missing disorder directory: {disorder_dir}")
    documents: dict[str, Mapping[str, Any]] = {}
    classifications: list[Classification] = []
    for path in sorted(disorder_dir.glob("*.yaml")):
        if path.name.endswith(".history.yaml"):
            continue
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, Mapping):
            raise ValueError(f"Disorder file is not a mapping: {path}")
        classification = classify_document(parsed, path.relative_to(repo_root), scope)
        if classification.slug in documents:
            raise ValueError(f"Duplicate disorder slug: {classification.slug}")
        documents[classification.slug] = parsed
        classifications.append(classification)
    return documents, classifications


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    root_resolved = root.resolve()
    try:
        return resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"Path escapes repository root: {path}") from exc


def _copy_file(source: Path, destination: Path) -> None:
    if source.is_symlink():
        raise ValueError(f"Symlinks are not copied into the standalone KG: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    if _sha256(source) != _sha256(destination):
        raise RuntimeError(f"Byte-identity check failed while copying {source}")


def _copy_tree(source: Path, destination: Path) -> int:
    count = 0
    for child in sorted(source.rglob("*")):
        if child.is_dir():
            continue
        relative = child.relative_to(source)
        _copy_file(child, destination / relative)
        count += 1
    return count


def _reference_ids(value: Any) -> set[str]:
    references: set[str] = set()
    for path, child in _walk(value):
        if not isinstance(child, str) or not path:
            continue
        key = _normalise(path[-1])
        if key in {"reference", "references"} and re.match(r"^[A-Za-z][A-Za-z0-9_]*:", child):
            references.add(child.strip())
    return references


def _conformance_modules(value: Any) -> set[str]:
    modules: set[str] = set()
    for path, child in _walk(value):
        if not path or _normalise(path[-1]) != "conforms to":
            continue
        values = child if isinstance(child, list) else [child]
        for item in values:
            if isinstance(item, str) and "#" in item:
                modules.add(item.split("#", 1)[0])
    return modules


def _cache_index(repo_root: Path) -> dict[str, Path]:
    cache_dir = repo_root / "references_cache"
    index: dict[str, Path] = {}
    if not cache_dir.is_dir():
        raise FileNotFoundError(f"Missing reference cache: {cache_dir}")
    reference_line = re.compile(r"^reference_id:\s*(.+?)\s*$")
    for path in sorted(cache_dir.glob("*.md")):
        identifier: str | None = None
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for line_number, line in enumerate(stream):
                if line_number > 40:
                    break
                match = reference_line.match(line.rstrip("\n"))
                if match:
                    identifier = match.group(1).strip().strip("\"'")
                    break
        if identifier:
            if identifier in index:
                raise ValueError(
                    f"Duplicate cache reference_id {identifier}: {index[identifier]} and {path}"
                )
            index[identifier] = path
    return index


def _module_index(repo_root: Path) -> dict[str, Path]:
    module_dir = repo_root / "kb" / "modules"
    if not module_dir.is_dir():
        return {}
    return {_normalise_slug(path.stem): path for path in sorted(module_dir.glob("*.yaml"))}


def _associated_module_closure(
    repo_root: Path, documents: Iterable[Mapping[str, Any]]
) -> tuple[dict[str, Mapping[str, Any]], set[str]]:
    index = _module_index(repo_root)
    pending = {_normalise_slug(name) for doc in documents for name in _conformance_modules(doc)}
    modules: dict[str, Mapping[str, Any]] = {}
    references: set[str] = set()
    while pending:
        key = pending.pop()
        if key in modules:
            continue
        path = index.get(key)
        if path is None:
            raise FileNotFoundError(f"Referenced mechanism module is missing: {key}")
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, Mapping):
            raise ValueError(f"Mechanism module is not a mapping: {path}")
        modules[key] = parsed
        references.update(_reference_ids(parsed))
        pending.update(
            _normalise_slug(name)
            for name in _conformance_modules(parsed)
            if _normalise_slug(name) not in modules
        )
    return modules, references


def _selected_identity_tokens(
    classifications: Sequence[Classification], documents: Mapping[str, Mapping[str, Any]]
) -> set[str]:
    tokens: set[str] = set()
    for item in classifications:
        if not item.included:
            continue
        tokens.add(_normalise_slug(item.slug))
        tokens.add(_normalise(item.name))
        if item.mondo_id:
            tokens.add(item.mondo_id.casefold())
        for synonym in _strings(documents[item.slug].get("synonyms", [])):
            tokens.add(_normalise(synonym))
    return {token for token in tokens if token}


def _comorbidity_endpoints(document: Mapping[str, Any]) -> set[str]:
    endpoints: set[str] = set()
    for path, child in _walk(document):
        if not isinstance(child, str) or not path:
            continue
        key = _normalise(path[-1]).replace(" ", "_")
        if key in ENDPOINT_KEYS or "disease" in key or "disorder" in key or "condition" in key:
            endpoints.add(_normalise(child))
            if child.startswith("MONDO:"):
                endpoints.add(child.casefold())
    return {item for item in endpoints if item}


def _associated_comorbidities(
    repo_root: Path, identity_tokens: set[str]
) -> tuple[list[tuple[Path, Mapping[str, Any]]], set[str]]:
    directory = repo_root / "kb" / "comorbidities"
    selected: list[tuple[Path, Mapping[str, Any]]] = []
    references: set[str] = set()
    if not directory.is_dir():
        return selected, references
    for path in sorted(directory.glob("*.yaml")):
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, Mapping):
            continue
        endpoints = _comorbidity_endpoints(parsed)
        if endpoints & identity_tokens:
            selected.append((path, parsed))
            references.update(_reference_ids(parsed))
    return selected, references


def _copy_histories(repo_root: Path, target_root: Path, selected_slugs: set[str]) -> int:
    directory = repo_root / "history"
    if not directory.is_dir():
        return 0
    count = 0
    normalised_slugs = {_normalise_slug(slug) for slug in selected_slugs}
    for path in sorted(directory.rglob("*.yaml")):
        try:
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        target = parsed.get("target", {}) if isinstance(parsed, Mapping) else {}
        slug = target.get("slug") if isinstance(target, Mapping) else None
        if isinstance(slug, str) and _normalise_slug(slug) in normalised_slugs:
            relative = path.relative_to(repo_root)
            _copy_file(path, target_root / relative)
            count += 1
    return count


def _copy_hypotheses(repo_root: Path, target_root: Path, selected_slugs: set[str]) -> int:
    directory = repo_root / "kb" / "hypotheses"
    if not directory.is_dir():
        return 0
    selected = {_normalise_slug(slug) for slug in selected_slugs}
    count = 0
    for child in sorted(directory.iterdir()):
        if _normalise_slug(child.name) not in selected:
            continue
        relative = child.relative_to(repo_root)
        if child.is_dir():
            count += _copy_tree(child, target_root / relative)
        elif child.is_file():
            _copy_file(child, target_root / relative)
            count += 1
    return count


def _research_path_references(value: Any) -> set[PurePosixPath]:
    paths: set[PurePosixPath] = set()
    pattern = re.compile(r"(?:^|[\s(\[{'\"])(research/[A-Za-z0-9_.\-/]+)")
    for text in _strings(value):
        for match in pattern.finditer(text):
            raw = match.group(1).rstrip(".,;:)]}'\"")
            candidate = PurePosixPath(raw)
            if ".." not in candidate.parts:
                paths.add(candidate)
    return paths


def _copy_research(
    repo_root: Path,
    target_root: Path,
    selected_slugs: set[str],
    selected_documents: Iterable[Mapping[str, Any]],
    associated_documents: Iterable[Mapping[str, Any]],
) -> int:
    directory = repo_root / "research"
    if not directory.is_dir():
        return 0
    copied: set[Path] = set()
    references: set[PurePosixPath] = set()
    for document in [*selected_documents, *associated_documents]:
        references.update(_research_path_references(document))
    for reference in sorted(references, key=str):
        source = repo_root / Path(*reference.parts)
        if not source.exists():
            raise FileNotFoundError(f"Referenced research artifact is missing: {reference}")
        if source.is_dir():
            for child in source.rglob("*"):
                if child.is_file():
                    copied.add(child)
        else:
            copied.add(source)

    slug_keys = {_normalise_slug(slug) for slug in selected_slugs}
    for child in sorted(directory.iterdir()):
        child_key = _normalise_slug(child.name)
        if not any(child_key == slug or child_key.startswith(f"{slug} ") for slug in slug_keys):
            continue
        if child.is_dir():
            copied.update(path for path in child.rglob("*") if path.is_file())
        elif child.is_file():
            copied.add(child)

    for source in sorted(copied):
        relative = _safe_relative(source, repo_root)
        _copy_file(source, target_root / relative)
    return len(copied)


def _document_metrics(document: Mapping[str, Any], scope: Mapping[str, Any]) -> dict[str, Any]:
    fields = _scope_list(scope, "curation_field_priorities")
    missing = [field for field in fields if not document.get(field)]
    evidence_items = 0
    pathograph_edges = 0
    ontology_terms: set[str] = set()
    for path, child in _walk(document):
        if path and _normalise(path[-1]) == "evidence" and isinstance(child, list):
            evidence_items += len(child)
        if path and _normalise(path[-1]) == "downstream" and isinstance(child, list):
            pathograph_edges += len(child)
        if path and _normalise(path[-1]) == "id" and isinstance(child, str) and ":" in child:
            ontology_terms.add(child)
    priority_weights = {
        "differential_diagnoses": 5,
        "histopathology": 5,
        "diagnosis": 4,
        "treatments": 4,
        "pathophysiology": 4,
        "phenotypes": 3,
        "prognosis": 2,
        "progression": 2,
        "genetic_basis": 1,
        "clinical_trials": 1,
    }
    opportunity_score = sum(priority_weights.get(field, 1) for field in missing)
    if evidence_items == 0:
        opportunity_score += 10
    if not document.get("differential_diagnoses"):
        opportunity_score += 2
    return {
        "missing_priority_fields": missing,
        "evidence_item_count": evidence_items,
        "reference_count": len(_reference_ids(document)),
        "pathophysiology_node_count": len(document.get("pathophysiology", []) or []),
        "pathograph_edge_count": pathograph_edges,
        "ontology_term_count": len(ontology_terms),
        "opportunity_score": opportunity_score,
    }


def _board_coverage(
    scope: Mapping[str, Any],
    selected: Sequence[Classification],
    documents: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    probes = scope.get("board_topic_probes", {})
    if not isinstance(probes, Mapping):
        raise ValueError("board_topic_probes must be a mapping")
    identities = {
        item.slug: _identity_text(documents[item.slug])
        for item in selected
        if item.included
    }
    domains: dict[str, list[dict[str, Any]]] = {}
    for domain, topics in probes.items():
        if not isinstance(topics, list):
            raise ValueError(f"board_topic_probes.{domain} must be a list")
        rows: list[dict[str, Any]] = []
        for topic in topics:
            if not isinstance(topic, str):
                raise ValueError(f"Non-string board topic in {domain}")
            matches = [
                slug
                for slug, identity in identities.items()
                if _matching_terms(identity, [topic])
            ]
            rows.append({"topic": topic, "present": bool(matches), "matched_slugs": matches})
        domains[str(domain)] = rows
    return {"domains": domains}


def _write_tsv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            cooked = {
                key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                if isinstance(value, (list, tuple, dict))
                else value
                for key, value in row.items()
            }
            writer.writerow(cooked)


def _git_value(repo_root: Path, *args: str, default: str = "unknown") -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return default
    return result.stdout.strip() or default


def _standalone_validator_text() -> str:
    return '''#!/usr/bin/env python3
"""Verify payload hashes and parse the standalone DisMech dermatology KG."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
errors = []
for item in manifest["files"]:
    path = ROOT / item["path"]
    if not path.is_file():
        errors.append(f"missing: {item['path']}")
        continue
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != item["sha256"]:
        errors.append(f"hash mismatch: {item['path']}")
try:
    import yaml
except ImportError:
    yaml = None
if yaml is not None:
    for path in sorted((ROOT / "kb" / "disorders").glob("*.yaml")):
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            errors.append(f"not a YAML mapping: {path.relative_to(ROOT)}")
if errors:
    raise SystemExit("Standalone KG validation failed:\n- " + "\n- ".join(errors))
print(
    f"Validated {manifest['selection']['selected_condition_count']} conditions and "
    f"{len(manifest['files'])} hashed payload files."
)
'''


def _readme_text(manifest_seed: Mapping[str, Any]) -> str:
    selection = manifest_seed["selection"]
    return f"""# DisMech Dermatology Knowledge Graph

This directory is a self-contained, provenance-bound dermatology projection of
DisMech at source commit `{manifest_seed['source']['commit']}`.

It contains **{selection['selected_condition_count']} complete disorder records**.
Selected `kb/disorders/*.yaml` files are copied byte-for-byte; they are not
summarized, transformed, or clinically rewritten. The bundle also carries the
reachable evidence cache, referenced mechanism modules, disease-specific
research and curation history, hypotheses, and comorbidity records that touch a
selected condition.

## Scope tiers

- **core** — primary diseases of skin, hair, nails, mucosa, cutaneous oncology,
  dermatopathology, or cutaneous infection.
- **shared-care** — systemic/genetic conditions commonly evaluated with another
  specialty because their dermatologic manifestations are diagnostically or
  therapeutically important.
- **consultative** — diseases in which dermatologists may make or materially
  contribute to the diagnosis, admitted only by an enumerated identity rule or
  multiple structured skin-specific signals.

A single nonspecific rash is not sufficient for inclusion. Exact inclusion
reasons are recorded in `metadata/selection.json` and `metadata/catalog.tsv`.
Near-miss candidates are retained in `metadata/candidates.tsv` for human review.

## Files

- `kb/disorders/` — lossless selected source records
- `kb/modules/` — reachable mechanism-module closure
- `kb/comorbidities/` — associations touching selected disorders
- `references_cache/` — exact source evidence used by copied records
- `research/`, `history/`, `kb/hypotheses/` — associated provenance artifacts
- `data/diseases.jsonl` — machine-friendly complete records
- `metadata/catalog.tsv` — condition, tier, MONDO identifier, and inclusion reason
- `metadata/audit.json` and `metadata/audit.md` — completeness and board-topic audit
- `manifest.json` — source commit, scope hash, counts, and SHA-256 for every payload
- `validate.py` — offline integrity verification

Run `python validate.py` to verify every payload hash. PyYAML is optional for
hash validation and enables a parse check for all disorder records.

## Safety and governance

DisMech is AI-curated and is not a clinical-care guideline or patient-level
decision-support system. Evidence presence and exact quotation do not establish
that a claim is scientifically correct. Scope admission likewise indicates
relevance to dermatology, not a recommendation that dermatologists independently
manage every included systemic disease.
"""


def _write_audit_markdown(
    path: Path,
    selected: Sequence[Classification],
    metrics: Sequence[Mapping[str, Any]],
    board: Mapping[str, Any],
) -> None:
    tier_counts = Counter(item.tier for item in selected)
    missing_counts: Counter[str] = Counter()
    for row in metrics:
        missing_counts.update(row["missing_priority_fields"])
    board_rows = [row for rows in board["domains"].values() for row in rows]
    absent = [row["topic"] for row in board_rows if not row["present"]]
    top = sorted(metrics, key=lambda row: (-int(row["opportunity_score"]), str(row["name"])))[:50]
    lines = [
        "# Dermatology KG curation audit",
        "",
        f"Selected conditions: **{len(selected)}**.",
        "",
        "## Scope composition",
        "",
        "| Tier | Count |",
        "|---|---:|",
    ]
    for tier in ("core", "shared-care", "consultative"):
        lines.append(f"| {tier} | {tier_counts.get(tier, 0)} |")
    lines.extend(
        [
            "",
            "## Recurrent structured-content opportunities",
            "",
            "These are missing-field signals, not assertions that existing content is incorrect.",
            "",
            "| Field | Selected records without field |",
            "|---|---:|",
        ]
    )
    for field, count in missing_counts.most_common():
        lines.append(f"| `{field}` | {count} |")
    lines.extend(
        [
            "",
            "## Board-topic probe gaps",
            "",
            "The probe is an editorial coverage checklist, not an official examination blueprint.",
            "",
        ]
    )
    if absent:
        lines.extend(f"- {topic}" for topic in absent)
    else:
        lines.append("No probe topic was absent by name/synonym matching.")
    lines.extend(
        [
            "",
            "## Highest-priority record-level opportunities",
            "",
            "| Condition | Tier | Score | Missing priority fields |",
            "|---|---|---:|---|",
        ]
    )
    for row in top:
        missing = ", ".join(f"`{field}`" for field in row["missing_priority_fields"]) or "—"
        lines.append(
            f"| {row['name']} | {row['tier']} | {row['opportunity_score']} | {missing} |"
        )
    lines.extend(
        [
            "",
            "## Recommended schema/content expansion axes",
            "",
            "1. Add structured morphology, distribution, configuration, symptom, temporal-course, and skin-of-color variation fields rather than burying these diagnostic discriminators in prose.",
            "2. Represent clinicopathologic correlation explicitly: specimen site, biopsy technique, histologic reaction pattern, key positive/negative findings, special studies, and histopathologic mimickers.",
            "3. Expand differential-diagnosis relations with discriminating features and evidence, not unranked name lists.",
            "4. Add severity instruments, treatment-line context, contraindications, monitoring, pregnancy/lactation considerations, urgent red flags, and escalation thresholds as provenance-bearing records.",
            "5. Distinguish guideline-backed standard care, regulatory authorization, common off-label practice, investigational therapy, and mechanistic hypothesis.",
            "6. Add dermatology-specific image and specimen metadata under explicit licensing and redistribution controls; do not infer phenotype validity from an image alone.",
            "7. Track pediatric, pregnancy, immunocompromised, transplant, and diverse-skin-tone applicability for each clinical claim.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _deterministic_zip(source_dir: Path, archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=archive_path.name + ".", dir=archive_path.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(source_dir.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(source_dir).as_posix()
                info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())
        os.replace(temp_path, archive_path)
    finally:
        temp_path.unlink(missing_ok=True)


def build_distribution(
    repo_root: Path,
    output_dir: Path,
    *,
    scope_path: Path | None = None,
    archive_path: Path | None = None,
) -> BuildResult:
    repo_root = repo_root.resolve()
    scope_path = (scope_path or Path(__file__).with_name("scope.yaml")).resolve()
    scope = load_scope(scope_path)
    documents, classifications = _load_disorders(repo_root, scope)
    selected = sorted(
        (item for item in classifications if item.included),
        key=lambda item: (TIER_PRIORITY[item.tier or "consultative"], item.name.casefold()),
    )
    minimum = int(scope.get("minimum_selected_conditions", 1))
    maximum = int(scope.get("maximum_selected_conditions", 10_000))
    if not minimum <= len(selected) <= maximum:
        raise RuntimeError(
            f"Scope selected {len(selected)} conditions; expected {minimum}..{maximum}. "
            "Review the scope rather than silently accepting drift."
        )

    output_dir = output_dir.resolve()
    protected = {
        repo_root,
        (repo_root / "kb").resolve(),
        (repo_root / "kb" / "disorders").resolve(),
        (repo_root / "references_cache").resolve(),
    }
    if output_dir in protected:
        raise ValueError(f"Refusing destructive output path: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=output_dir.name + ".tmp-", dir=output_dir.parent))

    selected_slugs = {item.slug for item in selected}
    selected_documents = [documents[item.slug] for item in selected]
    all_references = set().union(*(_reference_ids(doc) for doc in selected_documents))
    modules, module_references = _associated_module_closure(repo_root, selected_documents)
    all_references.update(module_references)
    identity_tokens = _selected_identity_tokens(selected, documents)
    comorbidities, comorbidity_references = _associated_comorbidities(repo_root, identity_tokens)
    all_references.update(comorbidity_references)

    try:
        for item in selected:
            source = repo_root / item.source_path
            destination = temporary / "kb" / "disorders" / source.name
            _copy_file(source, destination)

        module_paths = _module_index(repo_root)
        for key in sorted(modules):
            source = module_paths[key]
            _copy_file(source, temporary / source.relative_to(repo_root))

        for source, _ in comorbidities:
            _copy_file(source, temporary / source.relative_to(repo_root))

        cache = _cache_index(repo_root)
        missing_references = sorted(reference for reference in all_references if reference not in cache)
        if missing_references:
            preview = ", ".join(missing_references[:20])
            raise FileNotFoundError(
                f"{len(missing_references)} referenced records are absent from references_cache: {preview}"
            )
        for reference in sorted(all_references):
            source = cache[reference]
            _copy_file(source, temporary / source.relative_to(repo_root))

        history_count = _copy_histories(repo_root, temporary, selected_slugs)
        _copy_hypotheses(repo_root, temporary, selected_slugs)
        research_count = _copy_research(
            repo_root,
            temporary,
            selected_slugs,
            selected_documents,
            [doc for _, doc in comorbidities] + list(modules.values()),
        )

        schema_dir = repo_root / "src" / "dismech" / "schema"
        if schema_dir.is_dir():
            _copy_tree(schema_dir, temporary / schema_dir.relative_to(repo_root))
        for relative_text in ASSOCIATED_COPY_PATHS:
            source = repo_root / relative_text
            if source.is_file():
                _copy_file(source, temporary / relative_text)
        _copy_file(scope_path, temporary / "metadata" / "scope.yaml")

        selection_payload = [dataclasses.asdict(item) for item in classifications]
        (temporary / "metadata").mkdir(parents=True, exist_ok=True)
        (temporary / "metadata" / "selection.json").write_text(
            json.dumps(selection_payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        _write_tsv(
            temporary / "metadata" / "catalog.tsv",
            [dataclasses.asdict(item) for item in selected],
            [
                "slug",
                "name",
                "tier",
                "mondo_id",
                "reasons",
                "matched_terms",
                "structured_score",
                "source_path",
            ],
        )
        candidates = [
            item
            for item in classifications
            if not item.included and item.structured_score >= max(1, int(scope["structured_signal_policy"]["minimum_total_score"]) - 2)
        ]
        _write_tsv(
            temporary / "metadata" / "candidates.tsv",
            [dataclasses.asdict(item) for item in sorted(candidates, key=lambda row: (-row.structured_score, row.name.casefold()))],
            ["slug", "name", "mondo_id", "structured_score", "reasons", "matched_terms", "source_path"],
        )

        data_dir = temporary / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        with (data_dir / "diseases.jsonl").open("w", encoding="utf-8") as stream:
            for item in selected:
                stream.write(
                    json.dumps(
                        {
                            "slug": item.slug,
                            "tier": item.tier,
                            "selection_reasons": list(item.reasons),
                            "record": documents[item.slug],
                        },
                        sort_keys=True,
                        ensure_ascii=False,
                    )
                    + "\n"
                )

        metric_rows: list[dict[str, Any]] = []
        for item in selected:
            row = dataclasses.asdict(item)
            row.update(_document_metrics(documents[item.slug], scope))
            metric_rows.append(row)
        board = _board_coverage(scope, selected, documents)
        audit = {
            "classifier_version": CLASSIFIER_VERSION,
            "selected_condition_count": len(selected),
            "tier_counts": dict(Counter(item.tier for item in selected)),
            "field_presence": {
                field: sum(1 for item in selected if documents[item.slug].get(field))
                for field in _scope_list(scope, "curation_field_priorities")
            },
            "records": metric_rows,
            "board_topic_coverage": board,
        }
        (temporary / "metadata" / "audit.json").write_text(
            json.dumps(audit, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        _write_audit_markdown(
            temporary / "metadata" / "audit.md", selected, metric_rows, board
        )

        source_commit = _git_value(repo_root, "rev-parse", "HEAD")
        source_origin = _git_value(repo_root, "config", "--get", "remote.origin.url")
        dirty = _git_value(repo_root, "status", "--porcelain", default="") != ""
        scope_hash = _sha256(scope_path)
        manifest_seed = {
            "format_version": 1,
            "name": scope.get("name", "DisMech Dermatology Knowledge Graph"),
            "classifier_version": CLASSIFIER_VERSION,
            "source": {"origin": source_origin, "commit": source_commit, "dirty": dirty},
            "scope": {"path": "metadata/scope.yaml", "sha256": scope_hash},
            "selection": {
                "selected_condition_count": len(selected),
                "candidate_count": len(candidates),
                "tier_counts": dict(Counter(item.tier for item in selected)),
            },
            "closure": {
                "reference_count": len(all_references),
                "module_count": len(modules),
                "comorbidity_count": len(comorbidities),
                "history_count": history_count,
                "research_file_count": research_count,
            },
        }
        (temporary / "README.md").write_text(_readme_text(manifest_seed), encoding="utf-8")
        validator = temporary / "validate.py"
        validator.write_text(_standalone_validator_text(), encoding="utf-8")
        validator.chmod(0o755)

        payload_files = []
        for path in sorted(temporary.rglob("*")):
            if not path.is_file() or path.name == "manifest.json":
                continue
            payload_files.append(
                {
                    "path": path.relative_to(temporary).as_posix(),
                    "sha256": _sha256(path),
                    "size": path.stat().st_size,
                }
            )
        manifest = {**manifest_seed, "files": payload_files}
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        expected_disorders = {f"{item.slug}.yaml" for item in selected}
        actual_disorders = {path.name for path in (temporary / "kb" / "disorders").glob("*.yaml")}
        if actual_disorders != expected_disorders:
            raise RuntimeError("Standalone output contains an unexpected disorder set")
        for item in selected:
            source = repo_root / item.source_path
            destination = temporary / "kb" / "disorders" / source.name
            if _sha256(source) != _sha256(destination):
                raise RuntimeError(f"Selected source record changed during build: {item.slug}")

        if output_dir.exists():
            if output_dir.is_symlink() or not output_dir.is_dir():
                raise ValueError(f"Refusing to replace non-directory output: {output_dir}")
            shutil.rmtree(output_dir)
        os.replace(temporary, output_dir)
        if archive_path is not None:
            _deterministic_zip(output_dir, archive_path.resolve())
        return BuildResult(
            output_dir=output_dir,
            archive_path=archive_path.resolve() if archive_path else None,
            source_commit=source_commit,
            selected_count=len(selected),
            candidate_count=len(candidates),
            reference_count=len(all_references),
            module_count=len(modules),
            comorbidity_count=len(comorbidities),
            history_count=history_count,
            research_file_count=research_count,
        )
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a standalone, lossless dermatology projection of DisMech."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--scope", type=Path, default=Path(__file__).with_name("scope.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = build_distribution(
        args.repo_root,
        args.output,
        scope_path=args.scope,
        archive_path=args.archive,
    )
    print(
        json.dumps(
            {
                **dataclasses.asdict(result),
                "output_dir": str(result.output_dir),
                "archive_path": str(result.archive_path) if result.archive_path else None,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
