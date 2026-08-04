# Standalone DisMech Dermatology Knowledge Graph

This directory defines a reproducible dermatology-focused distribution of DisMech. It does **not** rewrite or summarize selected disease records. The exporter copies the complete source YAML and its resolvable provenance closure byte-for-byte into a self-contained bundle.

## Scope contract

A disorder is included only when at least one reviewed rule in `scope.yaml` applies:

1. its structured identity metadata directly describes a cutaneous, hair, nail, mucosal, adnexal, or dermatologic disorder;
2. its canonical name matches a reviewed dermatology co-management rule;
3. its canonical name or synonym exactly matches a board-priority condition used for coverage auditing; or
4. its structured phenotypes contain a sufficiently strong cutaneous pattern, using a higher threshold for non-diagnostic manifestations.

Free-text descriptions may nominate a record for review but may not auto-include it. `manual_excludes` overrides every positive rule. This keeps uncertain systemic conditions out of the production subset until a curator makes an explicit decision.

The board-priority registry is derived from the public American Board of Dermatology BASIC Exam Content Outline. It is a coverage-audit aid, not a complete clinical ontology or a treatment guideline. The policy records the outline's 2019 update date so the provenance is not silently presented as newer than it is.

## What the bundle contains

The `archival` profile contains:

- exact copies of all selected `kb/disorders/*.yaml` records;
- all referenced mechanism modules and exact associated non-disorder KB records;
- all resolvable `references_cache` records used by those YAML files;
- matching append-only history records;
- disease-prefixed research artifacts and disease-specific hypothesis artifacts;
- the LinkML schema, ontology/reference-validator configuration when present, and license;
- a complete file manifest, SHA-256 checksums, and deterministic reports.

The `compact` profile omits history, research, and hypothesis artifacts but still preserves selected disorders, modules, associated KB records, reference-cache closure, schema, and provenance.

Association files may point to a non-dermatology condition without importing that condition as a node. Those external endpoints remain explicit boundary references and are enumerated in `manifest.json`; this preserves relevant associations without violating the specialty scope.

## Build commands

```bash
# Validate the actual repository and write reports only.
uv run python scripts/build_dermatology_kg.py \
  --audit-only \
  --profile archival \
  --output /tmp/dismech-dermatology-audit \
  --force

# Build the full standalone directory and deterministic archive.
uv run python scripts/build_dermatology_kg.py \
  --profile archival \
  --output dist/dermatology-kg \
  --archive dist/dismech-dermatology-kg.tar.gz \
  --force
```

Strict mode is the default. The build fails when a referenced module or reference-cache record cannot be resolved. `--no-strict` exists only for investigation and records unresolved closure in the output; it should not be used for a release artifact.

## Audit outputs

| File | Purpose |
|---|---|
| `reports/scope_decisions.tsv` | One explicit inclusion/exclusion decision for every source disorder. |
| `reports/catalog.tsv` | Selected conditions, scope tier, clinical domains, ontology identifiers, and source hash. |
| `reports/clinical_coverage.tsv` | Section-level inventory used to prioritize curation, not to infer scientific correctness. |
| `reports/candidate_review.tsv` | Ambiguous excluded conditions ranked for human review. Candidates never self-promote. |
| `reports/board_priority_gap.tsv` | Board-priority entities matched to the source corpus or flagged for curation/name-resolution review. |
| `manifest.json` | Source commit, policy hash, selected slugs, closure counts, boundary identifiers, and per-file hashes. |
| `SHA256SUMS` | Release-integrity receipt covering every output file except the checksum file itself. |

## Information-preservation guarantees

The exporter uses binary file copies and re-hashes both sides. It does not parse and re-serialize selected source YAML. The archive is built with sorted paths, fixed timestamps, fixed ownership, and fixed modes so identical source bytes and policy produce an identical `.tar.gz`.

The source commit is mandatory, and all provenance-bearing inputs must be clean relative to that commit. A non-git checkout or a checkout with modified/untracked KG, scope, schema, configuration, history, research, or reference-cache inputs cannot create a production bundle. The Python API retains an internal commit override only for isolated unit-test fixtures; the command-line interface does not expose it.

## Maintenance model

`scope.yaml` is the sole approval surface. Automated discovery produces candidates, not decisions. Any policy change should be reviewed separately from scientific content changes so a scope expansion cannot silently modify disease assertions.

Run the audit whenever the scope policy, exporter, source schema, or a specialty-release snapshot changes. A manual GitHub Actions workflow can create the archival artifact without adding generated bundle contents to Git.

## Safety

DisMech is AI-curated. Exact citations and schema validation improve traceability but do not establish clinical correctness. This subset is not medical advice, a diagnostic authority, or a substitute for current clinical guidelines.
