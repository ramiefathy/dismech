"""Build a deterministic, standalone dermatology-focused DisMech bundle.

The source disorder YAML is never transformed. Selection is driven by a reviewed
policy, and every copied source file is verified byte-for-byte by SHA-256.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


BUNDLE_FORMAT_VERSION = 1
PROFILE_ARCHIVAL = "archival"
PROFILE_COMPACT = "compact"
VALID_PROFILES = {PROFILE_ARCHIVAL, PROFILE_COMPACT}
REFERENCE_KEYS = {
    "reference",
    "references",
    "reference_id",
    "source_reference",
    "supporting_reference",
}
TIER_ORDER = {
    "MANUAL": 0,
    "BOARD_CORE": 1,
    "PRIMARY_DERMATOLOGY": 2,
    "DERMATOLOGY_COMANAGED": 3,
    "SYNDROMIC_DERMATOLOGY": 4,
    "STRUCTURED_CUTANEOUS_MANIFESTATIONS": 5,
}


class DermatologyBundleError(RuntimeError):
    """Raised when a dermatology bundle cannot be built safely."""


@dataclass(frozen=True)
class DiseaseRecord:
    """One source disorder and the normalized metadata used for scoping."""

    slug: str
    path: Path
    relative_path: str
    data: Mapping[str, Any]
    name: str
    aliases: tuple[str, ...]
    scope_labels: tuple[str, ...]
    disease_ids: tuple[str, ...]


@dataclass(frozen=True)
class ScopeDecision:
    """Auditable inclusion or exclusion decision for one source disorder."""

    slug: str
    name: str
    source_path: str
    included: bool
    tier: str
    domains: tuple[str, ...]
    reasons: tuple[str, ...]
    board_matches: tuple[str, ...]
    cutaneous_phenotype_count: int
    diagnostic_cutaneous_phenotype_count: int
    candidate_score: int
    candidate_reasons: tuple[str, ...]


@dataclass(frozen=True)
class BuildResult:
    """Summary returned by :func:`build_dermatology_bundle`."""

    output_dir: Path
    archive_path: Path | None
    selected_count: int
    candidate_count: int
    source_commit: str
    missing_references: tuple[str, ...]
    missing_modules: tuple[str, ...]


@dataclass(frozen=True)
class BuildPlan:
    """Repository inventory and closure computed before any output mutation."""

    records: tuple[DiseaseRecord, ...]
    decisions: tuple[ScopeDecision, ...]
    selected: tuple[DiseaseRecord, ...]
    modules: tuple[Path, ...]
    associated_files: tuple[Path, ...]
    history_files: tuple[Path, ...]
    research_files: tuple[Path, ...]
    hypothesis_files: tuple[Path, ...]
    reference_ids: tuple[str, ...]
    reference_files: tuple[Path, ...]
    missing_references: tuple[str, ...]
    missing_modules: tuple[str, ...]
    boundary_mondo_ids: tuple[str, ...]
    source_commit: str
    policy_sha256: str


def sha256_file(path: Path) -> str:
    """Return a lowercase SHA-256 digest for *path*."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    """Return a lowercase SHA-256 digest for bytes."""

    return hashlib.sha256(value).hexdigest()


def normalize_label(value: Any) -> str:
    """Normalize a clinical label for conservative exact matching."""

    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.casefold().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def slugify_name(value: str) -> str:
    """Create the repository's common underscore-style disease slug."""

    value = unicodedata.normalize("NFKD", value)
    value = "".join(character for character in value if not unicodedata.combining(character))
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", value)).strip("_")


def load_yaml(path: Path) -> Mapping[str, Any]:
    """Load one YAML mapping with a path-rich error on malformed input."""

    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise DermatologyBundleError(f"Cannot parse YAML {path}: {error}") from error
    if not isinstance(loaded, Mapping):
        raise DermatologyBundleError(f"Expected a YAML mapping in {path}")
    return loaded


def load_scope_policy(path: Path) -> Mapping[str, Any]:
    """Load and minimally validate the reviewed dermatology scope policy."""

    policy = load_yaml(path)
    if policy.get("policy_version") != 1:
        raise DermatologyBundleError(
            f"Unsupported policy_version in {path}: {policy.get('policy_version')!r}"
        )
    required = {"direct_metadata_rules", "comanaged_name_rules", "phenotype_rules"}
    missing = sorted(required - set(policy))
    if missing:
        raise DermatologyBundleError(f"Scope policy is missing required keys: {missing}")
    return policy


def walk_scalars(value: Any, *, key: str | None = None) -> Iterator[tuple[str | None, str]]:
    """Yield ``(parent_key, scalar_text)`` recursively from YAML-compatible data."""

    if isinstance(value, Mapping):
        for child_key, child in value.items():
            yield from walk_scalars(child, key=str(child_key))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            yield from walk_scalars(child, key=key)
    elif value is not None:
        yield key, str(value)


def _string_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(str(item) for item in value if isinstance(item, (str, int, float)))
    return ()


def _nested_string(data: Mapping[str, Any], *keys: str) -> str | None:
    current: Any = data
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return str(current) if isinstance(current, (str, int, float)) else None


def disease_aliases(data: Mapping[str, Any]) -> tuple[str, ...]:
    """Return disease identity labels, excluding categories and parent classes."""

    values: list[str] = []
    values.extend(_string_values(data.get("name")))
    values.extend(_string_values(data.get("synonyms")))
    for path in (
        ("disease_term", "preferred_term"),
        ("disease_term", "term", "label"),
    ):
        value = _nested_string(data, *path)
        if value:
            values.append(value)
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


def disease_scope_labels(data: Mapping[str, Any]) -> tuple[str, ...]:
    """Return reviewed structured fields allowed to drive direct scope rules."""

    values = [*disease_aliases(data)]
    values.extend(_string_values(data.get("category")))
    values.extend(_string_values(data.get("parents")))
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


def extract_disease_ids(data: Mapping[str, Any]) -> tuple[str, ...]:
    """Extract disease CURIEs used to bind associations to source entries."""

    values: set[str] = set()
    for _, scalar in walk_scalars(data.get("disease_term", {})):
        if re.fullmatch(r"(?:MONDO|ORPHA|NCIT|ICD10CM|icd11f):[^\s]+", scalar.strip()):
            values.add(scalar.strip())
    mappings = data.get("mappings")
    if isinstance(mappings, Mapping):
        for _, scalar in walk_scalars(mappings):
            if re.fullmatch(r"(?:MONDO|ORPHA|NCIT|ICD10CM|icd11f):[^\s]+", scalar.strip()):
                values.add(scalar.strip())
    return tuple(sorted(values))


def inventory_disorders(repo_root: Path) -> tuple[DiseaseRecord, ...]:
    """Load every source disorder from the canonical ``kb/disorders`` directory."""

    disorder_dir = repo_root / "kb" / "disorders"
    if not disorder_dir.is_dir():
        raise DermatologyBundleError(f"Missing disorder directory: {disorder_dir}")

    records: list[DiseaseRecord] = []
    names: dict[str, Path] = {}
    for path in sorted(disorder_dir.glob("*.yaml")):
        if path.name.endswith(".history.yaml"):
            continue
        data = load_yaml(path)
        name = str(data.get("name") or "").strip()
        if not name:
            raise DermatologyBundleError(f"Disorder has no name: {path}")
        normalized_name = normalize_label(name)
        if normalized_name in names:
            raise DermatologyBundleError(
                f"Duplicate disorder name {name!r}: {names[normalized_name]} and {path}"
            )
        names[normalized_name] = path
        records.append(
            DiseaseRecord(
                slug=path.stem,
                path=path,
                relative_path=path.relative_to(repo_root).as_posix(),
                data=data,
                name=name,
                aliases=disease_aliases(data),
                scope_labels=disease_scope_labels(data),
                disease_ids=extract_disease_ids(data),
            )
        )
    if not records:
        raise DermatologyBundleError(f"No disorder YAML files found in {disorder_dir}")
    return tuple(records)


def _compile_rules(rules: Iterable[Mapping[str, Any]], *, section: str) -> tuple[dict[str, Any], ...]:
    compiled: list[dict[str, Any]] = []
    for position, rule in enumerate(rules):
        if not isinstance(rule, Mapping):
            raise DermatologyBundleError(f"{section}[{position}] must be a mapping")
        rule_id = str(rule.get("id") or "").strip()
        expression = str(rule.get("regex") or "").strip()
        if not rule_id or not expression:
            raise DermatologyBundleError(f"{section}[{position}] needs id and regex")
        try:
            pattern = re.compile(expression, flags=re.IGNORECASE)
        except re.error as error:
            raise DermatologyBundleError(f"Invalid regex for {section}.{rule_id}: {error}") from error
        compiled.append(
            {
                "id": rule_id,
                "pattern": pattern,
                "tier": str(rule.get("tier") or "PRIMARY_DERMATOLOGY"),
                "domains": tuple(sorted(set(_string_values(rule.get("domains"))))),
            }
        )
    return tuple(compiled)


def _manual_lookup(entries: Any, record: DiseaseRecord) -> Mapping[str, Any] | None:
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes, bytearray)):
        return None
    normalized_name = normalize_label(record.name)
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        slug = str(entry.get("slug") or "").strip()
        name = normalize_label(entry.get("name"))
        if (slug and slug == record.slug) or (name and name == normalized_name):
            return entry
    return None


def _board_matches(policy: Mapping[str, Any], record: DiseaseRecord) -> tuple[Mapping[str, Any], ...]:
    normalized_aliases = {normalize_label(alias) for alias in record.aliases}
    normalized_aliases.add(normalize_label(record.name))
    matches: list[Mapping[str, Any]] = []
    for entry in policy.get("board_priority", []) or []:
        if not isinstance(entry, Mapping):
            continue
        aliases = [entry.get("label"), *(entry.get("aliases") or [])]
        if any(normalize_label(alias) in normalized_aliases for alias in aliases if alias):
            matches.append(entry)
    return tuple(matches)


def _phenotype_labels(data: Mapping[str, Any]) -> tuple[tuple[str, bool], ...]:
    labels: list[tuple[str, bool]] = []
    phenotypes = data.get("phenotypes")
    if not isinstance(phenotypes, Sequence) or isinstance(phenotypes, (str, bytes, bytearray)):
        return ()
    for phenotype in phenotypes:
        if not isinstance(phenotype, Mapping):
            continue
        values: list[str] = []
        for key in ("name", "preferred_term"):
            values.extend(_string_values(phenotype.get(key)))
        for path in (
            ("phenotype_term", "preferred_term"),
            ("phenotype_term", "term", "label"),
            ("term", "label"),
        ):
            value = _nested_string(phenotype, *path)
            if value:
                values.append(value)
        diagnostic_value = phenotype.get("diagnostic")
        if diagnostic_value is None:
            diagnostic_value = phenotype.get("is_diagnostic")
        diagnostic = diagnostic_value is True or str(diagnostic_value).casefold() == "true"
        label = " | ".join(dict.fromkeys(value.strip() for value in values if value.strip()))
        if label:
            labels.append((label, diagnostic))
    return tuple(labels)


def classify_disorder(record: DiseaseRecord, policy: Mapping[str, Any]) -> ScopeDecision:
    """Apply the reviewed fail-closed scope policy to one disorder."""

    manual_exclude = _manual_lookup(policy.get("manual_excludes"), record)
    manual_include = _manual_lookup(policy.get("manual_includes"), record)
    metadata_text = " | ".join(record.scope_labels)
    name_text = " | ".join((record.name, *record.aliases))

    reasons: list[str] = []
    domains: set[str] = set()
    tiers: list[str] = []

    board_matches = _board_matches(policy, record)
    for entry in board_matches:
        entry_id = str(entry.get("id") or slugify_name(str(entry.get("label") or "board")))
        reasons.append(f"board_priority:{entry_id}")
        tiers.append("BOARD_CORE")
        domains.update(_string_values(entry.get("domains")))

    direct_rules = _compile_rules(policy.get("direct_metadata_rules") or [], section="direct_metadata_rules")
    for rule in direct_rules:
        if rule["pattern"].search(metadata_text):
            reasons.append(f"direct_metadata:{rule['id']}")
            tiers.append(rule["tier"])
            domains.update(rule["domains"])

    comanaged_rules = _compile_rules(policy.get("comanaged_name_rules") or [], section="comanaged_name_rules")
    for rule in comanaged_rules:
        if rule["pattern"].search(name_text):
            reasons.append(f"comanaged_name:{rule['id']}")
            tiers.append(rule["tier"])
            domains.update(rule["domains"])

    phenotype_config = policy.get("phenotype_rules")
    if not isinstance(phenotype_config, Mapping):
        raise DermatologyBundleError("phenotype_rules must be a mapping")
    phenotype_patterns = _compile_rules(
        phenotype_config.get("patterns") or [], section="phenotype_rules.patterns"
    )
    matched_phenotypes: list[tuple[str, bool]] = []
    for label, diagnostic in _phenotype_labels(record.data):
        if any(rule["pattern"].search(label) for rule in phenotype_patterns):
            matched_phenotypes.append((label, diagnostic))
    cutaneous_count = len(matched_phenotypes)
    diagnostic_count = sum(1 for _, diagnostic in matched_phenotypes if diagnostic)
    minimum = int(phenotype_config.get("minimum_matches", 4))
    diagnostic_minimum = int(phenotype_config.get("minimum_matches_with_diagnostic", 2))
    if cutaneous_count >= minimum or (
        diagnostic_count >= 1 and cutaneous_count >= diagnostic_minimum
    ):
        reasons.append(
            "structured_cutaneous_phenotypes:"
            f"{cutaneous_count}_total_{diagnostic_count}_diagnostic"
        )
        tiers.append("STRUCTURED_CUTANEOUS_MANIFESTATIONS")
        domains.add("medical_dermatology")

    candidate_reasons: list[str] = []
    candidate_score = 0
    description = str(record.data.get("description") or "")
    for rule in _compile_rules(
        policy.get("candidate_description_rules") or [], section="candidate_description_rules"
    ):
        if rule["pattern"].search(description):
            candidate_reasons.append(f"description:{rule['id']}")
            candidate_score += 1
    if cutaneous_count:
        candidate_reasons.append(f"cutaneous_phenotypes:{cutaneous_count}")
        candidate_score += min(cutaneous_count, 5)
    if diagnostic_count:
        candidate_reasons.append(f"diagnostic_cutaneous_phenotypes:{diagnostic_count}")
        candidate_score += 2 * diagnostic_count

    if manual_include:
        reasons.insert(0, f"manual_include:{manual_include.get('reason', 'reviewed')}")
        tiers.insert(0, str(manual_include.get("tier") or "MANUAL"))
        domains.update(_string_values(manual_include.get("domains")))
    included = bool(reasons)

    if manual_exclude:
        included = False
        reasons = [f"manual_exclude:{manual_exclude.get('reason', 'reviewed')}"]
        tiers = []
        domains = set()

    tier = min(tiers, key=lambda value: TIER_ORDER.get(value, 999)) if tiers else "EXCLUDED"
    return ScopeDecision(
        slug=record.slug,
        name=record.name,
        source_path=record.relative_path,
        included=included,
        tier=tier,
        domains=tuple(sorted(domains)),
        reasons=tuple(dict.fromkeys(reasons)),
        board_matches=tuple(
            str(entry.get("id") or slugify_name(str(entry.get("label") or "board")))
            for entry in board_matches
        ),
        cutaneous_phenotype_count=cutaneous_count,
        diagnostic_cutaneous_phenotype_count=diagnostic_count,
        candidate_score=candidate_score,
        candidate_reasons=tuple(dict.fromkeys(candidate_reasons)),
    )


def extract_reference_ids(data: Mapping[str, Any]) -> set[str]:
    """Extract reference identifiers without interpreting the scientific content."""

    references: set[str] = set()
    for key, scalar in walk_scalars(data):
        normalized_key = (key or "").casefold()
        if normalized_key in REFERENCE_KEYS and scalar.strip():
            references.add(scalar.strip())
    return references


def extract_module_slugs(data: Mapping[str, Any]) -> set[str]:
    """Extract module slugs from ``conforms_to`` foreign keys."""

    modules: set[str] = set()
    for key, scalar in walk_scalars(data):
        if (key or "").casefold() != "conforms_to":
            continue
        module_slug = scalar.split("#", 1)[0].strip()
        if module_slug:
            modules.add(module_slug)
    return modules


def _read_frontmatter(path: Path) -> Mapping[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            first = stream.readline().rstrip("\n\r")
            if first != "---":
                return {}
            lines: list[str] = []
            for line in stream:
                if line.rstrip("\n\r") == "---":
                    break
                lines.append(line)
            else:
                return {}
    except (OSError, UnicodeError):
        return {}
    try:
        loaded = yaml.safe_load("".join(lines)) or {}
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, Mapping) else {}


def index_reference_cache(repo_root: Path) -> Mapping[str, Path]:
    """Index tool-generated reference cache files by frontmatter identifier."""

    cache_root = repo_root / "references_cache"
    if not cache_root.is_dir():
        raise DermatologyBundleError(f"Missing references cache: {cache_root}")
    index: dict[str, Path] = {}
    for path in sorted(cache_root.rglob("*.md")):
        reference_id = str(_read_frontmatter(path).get("reference_id") or "").strip()
        if not reference_id:
            continue
        prior = index.get(reference_id)
        if prior and sha256_file(prior) != sha256_file(path):
            raise DermatologyBundleError(
                f"Conflicting cache files for {reference_id}: {prior} and {path}"
            )
        index.setdefault(reference_id, path)
    return index


def _selected_markers(selected: Sequence[DiseaseRecord]) -> tuple[set[str], set[str]]:
    raw: set[str] = set()
    normalized: set[str] = set()
    for record in selected:
        values = {record.slug, record.name, *record.aliases, *record.disease_ids}
        for value in values:
            if not value:
                continue
            raw.add(value)
            normalized.add(normalize_label(value))
            raw.add(f"kb/disorders/{record.slug}.yaml")
    return raw, normalized


def document_references_selected(
    data: Mapping[str, Any], raw_markers: set[str], normalized_markers: set[str]
) -> bool:
    """Return true only for exact identifiers/labels or canonical source paths."""

    for _, scalar in walk_scalars(data):
        stripped = scalar.strip()
        if stripped in raw_markers or normalize_label(stripped) in normalized_markers:
            return True
        if stripped.replace("\\", "/") in raw_markers:
            return True
    return False


def discover_associated_files(repo_root: Path, selected: Sequence[DiseaseRecord]) -> tuple[Path, ...]:
    """Find non-disorder KB YAML records that explicitly reference selected diseases."""

    kb_root = repo_root / "kb"
    raw_markers, normalized_markers = _selected_markers(selected)
    paths: list[Path] = []
    if not kb_root.is_dir():
        return ()
    for path in sorted(kb_root.rglob("*.yaml")):
        relative = path.relative_to(kb_root)
        if relative.parts[0] in {"disorders", "modules", "hypotheses"}:
            continue
        try:
            data = load_yaml(path)
        except DermatologyBundleError:
            continue
        if document_references_selected(data, raw_markers, normalized_markers):
            paths.append(path)
    return tuple(paths)


def discover_history_files(
    repo_root: Path,
    selected: Sequence[DiseaseRecord],
    module_paths: Sequence[Path],
    associated_paths: Sequence[Path],
) -> tuple[Path, ...]:
    """Find append-only history records whose target is part of the bundle."""

    history_root = repo_root / "history"
    if not history_root.is_dir():
        return ()
    target_paths = {
        path.relative_to(repo_root).as_posix()
        for path in [*(record.path for record in selected), *module_paths, *associated_paths]
    }
    target_slugs = (
        {record.slug for record in selected}
        | {path.stem for path in module_paths}
        | {path.stem for path in associated_paths}
    )
    found: list[Path] = []
    for path in sorted(history_root.rglob("*.yaml")):
        try:
            data = load_yaml(path)
        except DermatologyBundleError:
            continue
        target = data.get("target")
        if not isinstance(target, Mapping):
            continue
        target_path = str(target.get("path") or "").replace("\\", "/")
        target_slug = str(target.get("slug") or "")
        if target_path in target_paths or target_slug in target_slugs:
            found.append(path)
    return tuple(found)


def _artifact_matches_record(relative: Path, record: DiseaseRecord) -> bool:
    prefixes = {record.slug, slugify_name(record.name)}
    for part in relative.parts:
        stem = Path(part).stem
        if any(
            stem == prefix
            or stem.startswith(prefix + "-")
            or stem.startswith(prefix + "_")
            for prefix in prefixes
            if prefix
        ):
            return True
    return False


def discover_research_files(repo_root: Path, selected: Sequence[DiseaseRecord]) -> tuple[Path, ...]:
    """Find research artifacts whose path is explicitly disease-prefixed."""

    research_root = repo_root / "research"
    if not research_root.is_dir():
        return ()
    return tuple(
        path
        for path in sorted(research_root.rglob("*"))
        if path.is_file()
        and any(_artifact_matches_record(path.relative_to(research_root), record) for record in selected)
    )


def discover_hypothesis_files(repo_root: Path, selected: Sequence[DiseaseRecord]) -> tuple[Path, ...]:
    """Find disease-specific mechanistic-hypothesis artifacts."""

    root = repo_root / "kb" / "hypotheses"
    if not root.is_dir():
        return ()
    files: list[Path] = []
    for record in selected:
        candidate = root / record.slug
        if candidate.is_dir():
            files.extend(path for path in sorted(candidate.rglob("*")) if path.is_file())
    return tuple(dict.fromkeys(files))


def source_commit(repo_root: Path) -> str:
    """Resolve a clean source commit, or fail rather than claim false provenance."""

    provenance_paths = (
        "kb",
        "references_cache",
        "history",
        "research",
        "src/dismech/schema",
        "dermatology_kg/scope.yaml",
        "conf",
        "LICENSE",
    )

    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise DermatologyBundleError(
            f"Cannot resolve source git commit for {repo_root}; pass a real git checkout: {error}"
        ) from error
    commit = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise DermatologyBundleError(f"Unexpected git commit value: {commit!r}")

    try:
        status = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--",
                *provenance_paths,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise DermatologyBundleError(
            f"Cannot verify source-tree cleanliness for {repo_root}: {error}"
        ) from error
    dirty = [line for line in status.stdout.splitlines() if line.strip()]
    if dirty:
        preview = "; ".join(dirty[:20])
        suffix = " ..." if len(dirty) > 20 else ""
        raise DermatologyBundleError(
            "Source data are not cryptographically bound to HEAD; commit or discard "
            f"changes before building: {preview}{suffix}"
        )
    return commit


def _collect_closure_yaml(paths: Iterable[Path]) -> tuple[set[str], set[str]]:
    references: set[str] = set()
    modules: set[str] = set()
    for path in paths:
        if path.suffix not in {".yaml", ".yml"}:
            continue
        try:
            data = load_yaml(path)
        except DermatologyBundleError:
            continue
        references.update(extract_reference_ids(data))
        modules.update(extract_module_slugs(data))
    return references, modules


def _boundary_mondo_ids(
    associated_files: Sequence[Path], selected: Sequence[DiseaseRecord]
) -> tuple[str, ...]:
    selected_ids = {identifier for record in selected for identifier in record.disease_ids}
    found: set[str] = set()
    for path in associated_files:
        data = load_yaml(path)
        for _, scalar in walk_scalars(data):
            found.update(re.findall(r"MONDO:\d+", scalar))
    return tuple(sorted(found - selected_ids))


def plan_dermatology_bundle(
    repo_root: Path,
    policy_path: Path,
    *,
    profile: str = PROFILE_ARCHIVAL,
    strict: bool = True,
    source_commit_override: str | None = None,
) -> BuildPlan:
    """Compute selection and dependency closure without writing bundle output."""

    repo_root = repo_root.resolve()
    policy_path = policy_path.resolve()
    if profile not in VALID_PROFILES:
        raise DermatologyBundleError(f"Unknown profile {profile!r}; choose from {sorted(VALID_PROFILES)}")
    policy = load_scope_policy(policy_path)
    records = inventory_disorders(repo_root)
    decisions = tuple(classify_disorder(record, policy) for record in records)
    by_slug = {record.slug: record for record in records}
    selected = tuple(by_slug[decision.slug] for decision in decisions if decision.included)
    if not selected:
        raise DermatologyBundleError("Scope policy selected zero disorders")

    module_slugs = set()
    reference_ids = set()
    for record in selected:
        module_slugs.update(extract_module_slugs(record.data))
        reference_ids.update(extract_reference_ids(record.data))

    module_paths: list[Path] = []
    missing_modules: set[str] = set()
    visited_modules: set[str] = set()
    queue = sorted(module_slugs)
    while queue:
        slug = queue.pop(0)
        if slug in visited_modules:
            continue
        visited_modules.add(slug)
        path = repo_root / "kb" / "modules" / f"{slug}.yaml"
        if not path.is_file():
            missing_modules.add(slug)
            continue
        module_paths.append(path)
        data = load_yaml(path)
        reference_ids.update(extract_reference_ids(data))
        queue.extend(sorted(extract_module_slugs(data) - visited_modules))

    associated_files = discover_associated_files(repo_root, selected)
    associated_refs, associated_modules = _collect_closure_yaml(associated_files)
    reference_ids.update(associated_refs)
    unresolved_associated_modules = associated_modules - visited_modules
    if unresolved_associated_modules:
        queue = sorted(unresolved_associated_modules)
        while queue:
            slug = queue.pop(0)
            if slug in visited_modules:
                continue
            visited_modules.add(slug)
            path = repo_root / "kb" / "modules" / f"{slug}.yaml"
            if not path.is_file():
                missing_modules.add(slug)
                continue
            module_paths.append(path)
            data = load_yaml(path)
            reference_ids.update(extract_reference_ids(data))
            queue.extend(sorted(extract_module_slugs(data) - visited_modules))

    history_files: tuple[Path, ...] = ()
    research_files: tuple[Path, ...] = ()
    hypothesis_files: tuple[Path, ...] = ()
    if profile == PROFILE_ARCHIVAL:
        history_files = discover_history_files(repo_root, selected, module_paths, associated_files)
        research_files = discover_research_files(repo_root, selected)
        hypothesis_files = discover_hypothesis_files(repo_root, selected)
        ancillary_refs, _ = _collect_closure_yaml([*history_files, *hypothesis_files])
        reference_ids.update(ancillary_refs)

    reference_index = index_reference_cache(repo_root)
    indexed_ids = set(reference_index)
    for record in selected:
        reference_ids.update(
            scalar.strip()
            for _, scalar in walk_scalars(record.data)
            if scalar.strip() in indexed_ids
        )
    for path in [*module_paths, *associated_files, *history_files, *hypothesis_files]:
        if path.suffix not in {".yaml", ".yml"}:
            continue
        try:
            data = load_yaml(path)
        except DermatologyBundleError:
            continue
        reference_ids.update(
            scalar.strip()
            for _, scalar in walk_scalars(data)
            if scalar.strip() in indexed_ids
        )
    missing_references = tuple(sorted(reference_ids - indexed_ids))
    reference_files = tuple(reference_index[reference] for reference in sorted(reference_ids & set(reference_index)))
    if strict and (missing_references or missing_modules):
        problems: list[str] = []
        if missing_references:
            problems.append(f"unresolved references: {', '.join(missing_references[:20])}")
        if missing_modules:
            problems.append(f"unresolved modules: {', '.join(sorted(missing_modules))}")
        raise DermatologyBundleError("; ".join(problems))

    commit = source_commit_override or source_commit(repo_root)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise DermatologyBundleError(f"source_commit_override must be a 40-character SHA: {commit!r}")
    return BuildPlan(
        records=records,
        decisions=decisions,
        selected=selected,
        modules=tuple(sorted(set(module_paths))),
        associated_files=associated_files,
        history_files=history_files,
        research_files=research_files,
        hypothesis_files=hypothesis_files,
        reference_ids=tuple(sorted(reference_ids)),
        reference_files=reference_files,
        missing_references=missing_references,
        missing_modules=tuple(sorted(missing_modules)),
        boundary_mondo_ids=_boundary_mondo_ids(associated_files, selected),
        source_commit=commit,
        policy_sha256=sha256_file(policy_path),
    )


def _write_tsv(path: Path, fieldnames: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _section_count(data: Mapping[str, Any], *keys: str) -> int:
    """Count the first present section, allowing schema-compatible aliases."""

    for key in keys:
        if key not in data:
            continue
        value = data.get(key)
        if isinstance(value, Mapping):
            return len(value)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return len(value)
        return int(value is not None and value != "")
    return 0


def _write_scope_reports(output: Path, plan: BuildPlan, policy: Mapping[str, Any]) -> None:
    decision_rows = []
    for decision in sorted(plan.decisions, key=lambda item: (not item.included, item.name.casefold())):
        decision_rows.append(
            {
                "slug": decision.slug,
                "name": decision.name,
                "included": str(decision.included).lower(),
                "tier": decision.tier,
                "domains": ";".join(decision.domains),
                "reasons": ";".join(decision.reasons),
                "board_matches": ";".join(decision.board_matches),
                "cutaneous_phenotype_count": decision.cutaneous_phenotype_count,
                "diagnostic_cutaneous_phenotype_count": decision.diagnostic_cutaneous_phenotype_count,
                "candidate_score": decision.candidate_score,
                "candidate_reasons": ";".join(decision.candidate_reasons),
                "source_path": decision.source_path,
            }
        )
    _write_tsv(
        output / "reports" / "scope_decisions.tsv",
        (
            "slug",
            "name",
            "included",
            "tier",
            "domains",
            "reasons",
            "board_matches",
            "cutaneous_phenotype_count",
            "diagnostic_cutaneous_phenotype_count",
            "candidate_score",
            "candidate_reasons",
            "source_path",
        ),
        decision_rows,
    )

    candidates = [
        row
        for row in decision_rows
        if row["included"] == "false" and int(row["candidate_score"]) > 0
    ]
    candidates.sort(key=lambda row: (-int(row["candidate_score"]), str(row["name"]).casefold()))
    _write_tsv(
        output / "reports" / "candidate_review.tsv",
        (
            "slug",
            "name",
            "candidate_score",
            "candidate_reasons",
            "cutaneous_phenotype_count",
            "diagnostic_cutaneous_phenotype_count",
            "source_path",
        ),
        candidates,
    )

    decisions_by_slug = {decision.slug: decision for decision in plan.decisions}
    catalog_rows = []
    coverage_rows = []
    for record in sorted(plan.selected, key=lambda item: item.name.casefold()):
        decision = decisions_by_slug[record.slug]
        catalog_rows.append(
            {
                "slug": record.slug,
                "name": record.name,
                "tier": decision.tier,
                "domains": ";".join(decision.domains),
                "reasons": ";".join(decision.reasons),
                "board_matches": ";".join(decision.board_matches),
                "disease_ids": ";".join(record.disease_ids),
                "source_path": record.relative_path,
                "source_sha256": sha256_file(record.path),
            }
        )
        coverage_rows.append(
            {
                "slug": record.slug,
                "name": record.name,
                "pathophysiology": _section_count(record.data, "pathophysiology"),
                "phenotypes": _section_count(record.data, "phenotypes"),
                "diagnosis": _section_count(record.data, "diagnosis", "diagnostic"),
                "histopathology": _section_count(record.data, "histopathology", "histology"),
                "differential_diagnoses": _section_count(
                    record.data,
                    "differential_diagnoses",
                    "differential_diagnosis",
                    "differentials",
                ),
                "treatments": _section_count(record.data, "treatments", "treatment"),
                "genetic": _section_count(
                    record.data, "genetic", "genetics", "genetic_basis"
                ),
                "progression": _section_count(
                    record.data, "progression", "natural_history"
                ),
                "discussions": _section_count(record.data, "discussions"),
                "clinical_trials": _section_count(
                    record.data, "clinical_trials", "trials"
                ),
                "references": len(extract_reference_ids(record.data)),
            }
        )
    _write_tsv(
        output / "reports" / "catalog.tsv",
        (
            "slug",
            "name",
            "tier",
            "domains",
            "reasons",
            "board_matches",
            "disease_ids",
            "source_path",
            "source_sha256",
        ),
        catalog_rows,
    )
    _write_tsv(
        output / "reports" / "clinical_coverage.tsv",
        (
            "slug",
            "name",
            "pathophysiology",
            "phenotypes",
            "diagnosis",
            "histopathology",
            "differential_diagnoses",
            "treatments",
            "genetic",
            "progression",
            "discussions",
            "clinical_trials",
            "references",
        ),
        coverage_rows,
    )

    selected_by_board: dict[str, list[str]] = {}
    source_by_board: dict[str, list[str]] = {}
    for decision in plan.decisions:
        for match in decision.board_matches:
            source_by_board.setdefault(match, []).append(decision.slug)
            if decision.included:
                selected_by_board.setdefault(match, []).append(decision.slug)
    board_rows = []
    for entry in policy.get("board_priority", []) or []:
        if not isinstance(entry, Mapping):
            continue
        entry_id = str(entry.get("id") or slugify_name(str(entry.get("label") or "board")))
        selected_matches = sorted(selected_by_board.get(entry_id, []))
        source_matches = sorted(source_by_board.get(entry_id, []))
        if selected_matches:
            status = "PRESENT_SELECTED"
        elif source_matches:
            status = "PRESENT_EXCLUDED_REVIEW_REQUIRED"
        else:
            status = "NOT_MATCHED_IN_SOURCE"
        board_rows.append(
            {
                "id": entry_id,
                "label": str(entry.get("label") or ""),
                "domains": ";".join(_string_values(entry.get("domains"))),
                "status": status,
                "matched_slugs": ";".join(selected_matches or source_matches),
                "aliases": ";".join(_string_values(entry.get("aliases"))),
            }
        )
    _write_tsv(
        output / "reports" / "board_priority_gap.tsv",
        ("id", "label", "domains", "status", "matched_slugs", "aliases"),
        board_rows,
    )


def _copy_verified(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    source_hash = sha256_file(source)
    destination_hash = sha256_file(destination)
    if source_hash != destination_hash:
        raise DermatologyBundleError(
            f"Information-preservation failure copying {source} to {destination}"
        )
    return source_hash


def _write_bundle_readme(path: Path, plan: BuildPlan, profile: str) -> None:
    text = f"""# DisMech Dermatology Knowledge Graph bundle

This is a deterministic, standalone subset of DisMech selected for dermatology.
It was generated from source commit `{plan.source_commit}` with policy SHA-256
`{plan.policy_sha256}` using the `{profile}` profile.

- Selected disorders: {len(plan.selected)}
- Referenced mechanism modules: {len(plan.modules)}
- Associated non-disorder KB records: {len(plan.associated_files)}
- Reference-cache records: {len(plan.reference_files)}
- History records: {len(plan.history_files)}
- Research artifacts: {len(plan.research_files)}
- Hypothesis artifacts: {len(plan.hypothesis_files)}

The files under `kb/`, `references_cache/`, `history/`, and `research/` are copied
byte-for-byte from the source checkout. `manifest.json` and `SHA256SUMS` bind the
bundle to those exact bytes. `reports/scope_decisions.tsv` explains every inclusion
and exclusion. Ambiguous conditions remain excluded and appear in
`reports/candidate_review.tsv`; automated candidate discovery never self-approves.

This resource is AI-curated and is not medical advice. It is not a clinical-care
guideline or a diagnostic decision-support authority.
"""
    path.write_text(text, encoding="utf-8", newline="\n")


def _relative_source_path(path: Path, repo_root: Path) -> PurePosixPath:
    try:
        return PurePosixPath(path.relative_to(repo_root).as_posix())
    except ValueError as error:
        raise DermatologyBundleError(f"Source path is outside repository: {path}") from error


def _copy_plan_files(staging: Path, repo_root: Path, plan: BuildPlan, profile: str) -> dict[str, str]:
    source_hashes: dict[str, str] = {}
    source_paths: list[Path] = [
        *(record.path for record in plan.selected),
        *plan.modules,
        *plan.associated_files,
        *plan.reference_files,
    ]
    if profile == PROFILE_ARCHIVAL:
        source_paths.extend(plan.history_files)
        source_paths.extend(plan.research_files)
        source_paths.extend(plan.hypothesis_files)
    for source in sorted(set(source_paths)):
        relative = _relative_source_path(source, repo_root)
        source_hashes[relative.as_posix()] = _copy_verified(source, staging / relative)

    for path in (
        repo_root / "LICENSE",
        repo_root / "conf" / "oak_config.yaml",
        repo_root / "conf" / "reference_validator_config.yaml",
    ):
        if path.is_file():
            relative = _relative_source_path(path, repo_root)
            source_hashes[relative.as_posix()] = _copy_verified(path, staging / relative)
    schema_root = repo_root / "src" / "dismech" / "schema"
    if not schema_root.is_dir():
        raise DermatologyBundleError(f"Missing schema directory: {schema_root}")
    for source in sorted(path for path in schema_root.rglob("*") if path.is_file()):
        relative = _relative_source_path(source, repo_root)
        source_hashes[relative.as_posix()] = _copy_verified(source, staging / relative)
    return source_hashes


def _file_manifest(root: Path, *, omit: set[str] | None = None) -> list[dict[str, Any]]:
    omit = omit or set()
    rows: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in omit:
            continue
        rows.append(
            {
                "path": relative,
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    return rows


def _write_manifest(
    staging: Path,
    plan: BuildPlan,
    profile: str,
    source_hashes: Mapping[str, str],
) -> None:
    manifest = {
        "bundle_format_version": BUNDLE_FORMAT_VERSION,
        "bundle_name": "dismech-dermatology-kg",
        "source_repository": "ramiefathy/dismech",
        "source_commit": plan.source_commit,
        "scope_policy_sha256": plan.policy_sha256,
        "profile": profile,
        "selected_disorder_count": len(plan.selected),
        "selected_disorders": [record.slug for record in sorted(plan.selected, key=lambda item: item.slug)],
        "module_count": len(plan.modules),
        "associated_file_count": len(plan.associated_files),
        "reference_id_count": len(plan.reference_ids),
        "reference_cache_file_count": len(plan.reference_files),
        "history_file_count": len(plan.history_files),
        "research_file_count": len(plan.research_files),
        "hypothesis_file_count": len(plan.hypothesis_files),
        "missing_references": list(plan.missing_references),
        "missing_modules": list(plan.missing_modules),
        "boundary_mondo_ids": list(plan.boundary_mondo_ids),
        "source_file_sha256": dict(sorted(source_hashes.items())),
        "files": _file_manifest(staging, omit={"manifest.json", "SHA256SUMS"}),
    }
    (staging / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    sums = _file_manifest(staging, omit={"SHA256SUMS"})
    (staging / "SHA256SUMS").write_text(
        "".join(f"{row['sha256']}  {row['path']}\n" for row in sums),
        encoding="utf-8",
        newline="\n",
    )


def create_deterministic_tar_gz(source_dir: Path, archive_path: Path) -> None:
    """Write a byte-reproducible gzip-compressed tar archive."""

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    root_name = "dismech-dermatology-kg"
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path in sorted([source_dir, *source_dir.rglob("*")], key=lambda item: item.as_posix()):
                    relative = path.relative_to(source_dir)
                    arcname = PurePosixPath(root_name, relative.as_posix())
                    info = archive.gettarinfo(str(path), arcname=arcname.as_posix())
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mode = 0o755 if path.is_dir() else 0o644
                    if path.is_file():
                        with path.open("rb") as stream:
                            archive.addfile(info, stream)
                    else:
                        archive.addfile(info)


def _replace_output(staging: Path, output_dir: Path, *, force: bool) -> None:
    if output_dir.exists():
        if not force:
            raise DermatologyBundleError(f"Output already exists; use --force: {output_dir}")
        if output_dir.is_dir():
            shutil.rmtree(output_dir)
        else:
            output_dir.unlink()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging.replace(output_dir)


def _validate_output_location(repo_root: Path, output_dir: Path) -> None:
    """Permit repository-local output only under explicitly generated roots."""

    if output_dir == repo_root:
        raise DermatologyBundleError(f"Unsafe output location: {output_dir}")
    try:
        relative = output_dir.relative_to(repo_root)
    except ValueError:
        return
    if not relative.parts or relative.parts[0] not in {"dist", "tmp"}:
        raise DermatologyBundleError(
            "Repository-local output is allowed only below dist/ or tmp/; "
            f"refusing to overwrite source path {output_dir}"
        )


def build_dermatology_bundle(
    repo_root: Path,
    policy_path: Path,
    output_dir: Path,
    *,
    profile: str = PROFILE_ARCHIVAL,
    strict: bool = True,
    force: bool = False,
    archive_path: Path | None = None,
    source_commit_override: str | None = None,
) -> BuildResult:
    """Build a standalone dermatology distribution atomically."""

    repo_root = repo_root.resolve()
    policy_path = policy_path.resolve()
    output_dir = output_dir.resolve()
    _validate_output_location(repo_root, output_dir)
    if output_dir.exists() and not force:
        raise DermatologyBundleError(f"Output already exists; use --force: {output_dir}")
    if archive_path is not None:
        archive_path = archive_path.resolve()
        if archive_path == output_dir or output_dir in archive_path.parents:
            raise DermatologyBundleError(
                f"Archive must be outside the bundle directory: {archive_path}"
            )
        if archive_path.exists() and not force:
            raise DermatologyBundleError(
                f"Archive already exists; use --force: {archive_path}"
            )

    plan = plan_dermatology_bundle(
        repo_root,
        policy_path,
        profile=profile,
        strict=strict,
        source_commit_override=source_commit_override,
    )
    policy = load_scope_policy(policy_path)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.staging-", dir=output_dir.parent))
    try:
        source_hashes = _copy_plan_files(staging, repo_root, plan, profile)
        scope_destination = staging / "scope" / policy_path.name
        source_hashes[
            _relative_source_path(policy_path, repo_root).as_posix()
        ] = _copy_verified(policy_path, scope_destination)
        _write_scope_reports(staging, plan, policy)
        _write_bundle_readme(staging / "README.md", plan, profile)
        _write_manifest(staging, plan, profile, source_hashes)
        _replace_output(staging, output_dir, force=force)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    if archive_path is not None:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{archive_path.name}.staging-",
            dir=archive_path.parent,
        )
        os.close(descriptor)
        temporary_archive = Path(temporary_name)
        try:
            temporary_archive.unlink()
            create_deterministic_tar_gz(output_dir, temporary_archive)
            if archive_path.exists():
                archive_path.unlink()
            temporary_archive.replace(archive_path)
        except Exception:
            temporary_archive.unlink(missing_ok=True)
            raise

    candidates = tuple(
        decision for decision in plan.decisions if not decision.included and decision.candidate_score > 0
    )
    return BuildResult(
        output_dir=output_dir,
        archive_path=archive_path,
        selected_count=len(plan.selected),
        candidate_count=len(candidates),
        source_commit=plan.source_commit,
        missing_references=plan.missing_references,
        missing_modules=plan.missing_modules,
    )


def write_audit_only(
    repo_root: Path,
    policy_path: Path,
    output_dir: Path,
    *,
    profile: str,
    strict: bool,
    force: bool,
    source_commit_override: str | None = None,
) -> BuildResult:
    """Write scope and closure reports without duplicating the corpus."""

    repo_root = repo_root.resolve()
    policy_path = policy_path.resolve()
    output_dir = output_dir.resolve()
    _validate_output_location(repo_root, output_dir)
    plan = plan_dermatology_bundle(
        repo_root,
        policy_path,
        profile=profile,
        strict=strict,
        source_commit_override=source_commit_override,
    )
    summary = {
        "source_commit": plan.source_commit,
        "policy_sha256": plan.policy_sha256,
        "profile": profile,
        "selected_disorder_count": len(plan.selected),
        "candidate_count": sum(
            1
            for decision in plan.decisions
            if not decision.included and decision.candidate_score > 0
        ),
        "module_count": len(plan.modules),
        "associated_file_count": len(plan.associated_files),
        "reference_id_count": len(plan.reference_ids),
        "reference_cache_file_count": len(plan.reference_files),
        "history_file_count": len(plan.history_files),
        "research_file_count": len(plan.research_files),
        "hypothesis_file_count": len(plan.hypothesis_files),
        "missing_references": list(plan.missing_references),
        "missing_modules": list(plan.missing_modules),
        "boundary_mondo_ids": list(plan.boundary_mondo_ids),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.staging-", dir=output_dir.parent))
    try:
        _write_scope_reports(staging, plan, load_scope_policy(policy_path))
        (staging / "audit_summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        _replace_output(staging, output_dir, force=force)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return BuildResult(
        output_dir=output_dir,
        archive_path=None,
        selected_count=len(plan.selected),
        candidate_count=summary["candidate_count"],
        source_commit=plan.source_commit,
        missing_references=plan.missing_references,
        missing_modules=plan.missing_modules,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a deterministic standalone dermatology-focused DisMech bundle."
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument(
        "--policy", type=Path, default=Path("dermatology_kg/scope.yaml")
    )
    parser.add_argument("--output", type=Path, default=Path("dist/dermatology-kg"))
    parser.add_argument("--profile", choices=sorted(VALID_PROFILES), default=PROFILE_ARCHIVAL)
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--no-strict", action="store_true", help="Report unresolved closure instead of failing.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--archive",
        type=Path,
        default=None,
        help="Optional deterministic .tar.gz output path (full build only).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.audit_only:
            result = write_audit_only(
                args.repo_root,
                args.policy,
                args.output,
                profile=args.profile,
                strict=not args.no_strict,
                force=args.force,
            )
        else:
            result = build_dermatology_bundle(
                args.repo_root,
                args.policy,
                args.output,
                profile=args.profile,
                strict=not args.no_strict,
                force=args.force,
                archive_path=args.archive,
            )
    except DermatologyBundleError as error:
        print(f"ERROR: {error}")
        return 2
    print(
        json.dumps(
            {
                **asdict(result),
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
