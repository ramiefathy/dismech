# Review of `ramiefathy/dismech` for a dermatology-focused derivative

## Executive assessment

The fork is a strong substrate for a specialty derivative because the canonical records are structured LinkML/YAML, evidence items are source-addressable, ontology bindings are validated, and mechanism modules are explicit. The dermatology distribution should therefore be an **exact specialty projection**, not a second independently edited disease corpus.

The implementation in this change follows that model: selection and packaging are separate from scientific curation, source YAML is copied byte-for-byte, dependency closure is explicit, and ambiguous candidates are quarantined for review.

## Strengths preserved

1. **One canonical disease record per YAML file.** A specialty bundle can select complete records without field-level truncation.
2. **Evidence traceability.** Exact snippets and cached source records permit integrity checks beyond bibliographic existence.
3. **Ontology-bound concepts.** HPO, Mondo, Gene Ontology, Cell Ontology, Uberon, NCIT, and related identifiers support downstream graph use.
4. **Mechanistic pathographs and reusable modules.** The derivative can preserve causal structure and module conformance without flattening the model.
5. **Negative and partial evidence states.** The source model can retain refutation, absence of evidence, and uncertainty rather than presenting every edge as established.
6. **Existing validation and history mechanisms.** The dermatology exporter can add specialty-scope integrity without replacing repository-wide QC.

## Load-bearing issues addressed by the derivative

### Specialty scope is not currently explicit

DisMech is intentionally cross-disease. There is no authoritative dermatology membership field that can be used as a complete filter. A naive keyword search would both leak systemic records into the subset and omit eponymous/common dermatologic diseases. The new scope policy uses multiple conservative signals and writes one decision per source disorder.

### Incidental skin findings can cause scope leakage

Many systemic diseases mention rash, pigment change, ulcers, hair loss, or nail findings. The exporter does not auto-include from description text. A structured phenotype route requires multiple cutaneous findings or a diagnostic finding plus another matched phenotype. Borderline records remain in `candidate_review.tsv`.

### A filtered disease directory alone is not standalone

Disorder records reference cached literature, mechanism modules, history, research artifacts, and other KB records. The archival bundle resolves and copies those dependencies, records external association endpoints as boundaries, and fails when required reference/module closure is incomplete.

### Corpus-size and documentation drift need cryptographic provenance

The repository evolves rapidly and generated pages can lag source counts. The bundle binds itself to an exact Git commit and scope-policy hash; it never relies on a displayed website count as release provenance. The source commit must be resolvable before a production bundle is created.

### Clinical/boards utility is uneven across records

Mechanistic richness does not guarantee clinically useful morphology, distribution, differential diagnosis, dermatopathology, treatment monitoring, or severity content. The generated clinical-coverage and board-gap reports make those deficits auditable without assigning unsupported quality scores.

## Deliberately excluded from this change

- No disease assertions are added or edited.
- No automatic candidate becomes a production member without a policy rule.
- No new clinical causal edges are inferred from co-occurrence or board relevance.
- No image is copied or redistributed.
- No board outline is treated as a clinical guideline.
- No generated bundle is committed to Git; release artifacts are reproducibly built from source.

## Recommended next review sequence

1. Run the strict archival audit on the current `main` commit.
2. Review every board-gap row for ontology/name-resolution errors before declaring a condition absent.
3. Review highest-scoring quarantined candidates and record explicit include/exclude decisions.
4. Rank selected records by missing clinical sections and evidence coverage.
5. Propose schema changes for presentation, differential diagnosis, dermatopathology, imaging, treatment safety, and skin-tone applicability as a separate PR.
6. Curate conditions in bounded thematic waves with normal DisMech evidence and history requirements.
