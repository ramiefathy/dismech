#!/usr/bin/env python3
"""Build the dermatology bundle with reviewed supplemental scope registries."""

from __future__ import annotations

import json
import re
import tempfile
import unicodedata
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from dismech.export.dermatology_bundle import (
    DermatologyBundleError,
    build_dermatology_bundle,
    build_parser as build_base_parser,
    write_audit_only,
)


def normalize_label(value: Any) -> str:
    """Normalize a scope label for duplicate suppression only."""

    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.casefold().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _load_mapping(path: Path) -> Mapping[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise DermatologyBundleError(f"Cannot load supplemental scope file {path}: {error}") from error
    if not isinstance(data, Mapping):
        raise DermatologyBundleError(f"Expected a YAML mapping in supplemental scope file {path}")
    return data


def merge_scope_policy(base_policy: Path, supplement_dir: Path) -> tuple[dict[str, Any], dict[str, int]]:
    """Merge exact-label scope registries without changing scientific records.

    Duplicate identifiers or labels already present in the base policy are skipped. Extra
    registry metadata is preserved in the merged policy but ignored by the core selector.
    """

    base = dict(_load_mapping(base_policy))
    base_entries = base.get("board_priority") or []
    if not isinstance(base_entries, list):
        raise DermatologyBundleError("base board_priority must be a list")
    merged_entries = [dict(entry) for entry in base_entries if isinstance(entry, Mapping)]

    seen_ids = {str(entry.get("id") or "").strip() for entry in merged_entries}
    seen_labels = {
        normalize_label(entry.get("label"))
        for entry in merged_entries
        if normalize_label(entry.get("label"))
    }

    files = tuple(sorted(supplement_dir.glob("*.yaml"))) if supplement_dir.is_dir() else ()
    added = 0
    skipped_duplicate_id = 0
    skipped_duplicate_label = 0
    for path in files:
        supplement = _load_mapping(path)
        entries = supplement.get("board_priority") or []
        if not isinstance(entries, list):
            raise DermatologyBundleError(f"{path}: board_priority must be a list")
        for raw in entries:
            if not isinstance(raw, Mapping):
                raise DermatologyBundleError(f"{path}: every board_priority entry must be a mapping")
            entry = dict(raw)
            entry_id = str(entry.get("id") or "").strip()
            label_key = normalize_label(entry.get("label"))
            if not entry_id or not label_key:
                raise DermatologyBundleError(f"{path}: every board_priority entry needs id and label")
            if entry_id in seen_ids:
                skipped_duplicate_id += 1
                continue
            if label_key in seen_labels:
                skipped_duplicate_label += 1
                continue
            merged_entries.append(entry)
            seen_ids.add(entry_id)
            seen_labels.add(label_key)
            added += 1

    base["board_priority"] = merged_entries
    base["supplemental_scope"] = {
        "files": [path.as_posix() for path in files],
        "base_entry_count": len(base_entries),
        "added_entry_count": added,
        "merged_entry_count": len(merged_entries),
        "skipped_duplicate_id_count": skipped_duplicate_id,
        "skipped_duplicate_label_count": skipped_duplicate_label,
        "semantics": (
            "Supplemental entries are exact-name scope signals only. They do not add, "
            "rewrite, validate, or endorse disease assertions."
        ),
    }
    return base, {
        "files": len(files),
        "base_entries": len(base_entries),
        "added_entries": added,
        "merged_entries": len(merged_entries),
        "skipped_duplicate_ids": skipped_duplicate_id,
        "skipped_duplicate_labels": skipped_duplicate_label,
    }


def build_parser():
    parser = build_base_parser()
    parser.description = (
        "Build a deterministic standalone dermatology-focused DisMech bundle using "
        "the reviewed base policy plus supplemental exact-label scope registries."
    )
    parser.add_argument(
        "--supplement-dir",
        type=Path,
        default=Path("dermatology_kg/supplemental_scope"),
        help="Directory of supplemental YAML registries containing board_priority entries.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    base_policy = args.policy if args.policy.is_absolute() else repo_root / args.policy
    supplement_dir = (
        args.supplement_dir
        if args.supplement_dir.is_absolute()
        else repo_root / args.supplement_dir
    )

    try:
        merged, merge_summary = merge_scope_policy(base_policy.resolve(), supplement_dir.resolve())
        with tempfile.TemporaryDirectory(prefix="dismech-dermatology-policy-") as temp_dir:
            merged_policy = Path(temp_dir) / "merged-scope.yaml"
            merged_policy.write_text(
                yaml.safe_dump(merged, sort_keys=False, allow_unicode=True, width=1000),
                encoding="utf-8",
            )
            if args.audit_only:
                result = write_audit_only(
                    repo_root,
                    merged_policy,
                    args.output,
                    profile=args.profile,
                    strict=not args.no_strict,
                    force=args.force,
                )
            else:
                result = build_dermatology_bundle(
                    repo_root,
                    merged_policy,
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
                "supplemental_scope": merge_summary,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
