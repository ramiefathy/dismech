# Dermatology KG expansion and improvement backlog

This backlog separates **distribution engineering** from **scientific curation**. The initial standalone bundle preserves existing DisMech assertions exactly; it does not invent or silently enrich biological or clinical content. New claims should enter the canonical disorder records through the repository's existing exact-snippet, ontology, history, and review workflow.

## Priority 0 — make dermatology presentation and diagnostic reasoning first-class

### 1. Structured morphology, configuration, distribution, and evolution

Add or standardize a dermatology presentation layer that can represent:

- primary and secondary lesion morphology;
- color, surface, border, shape, configuration, and arrangement;
- anatomic distribution, symmetry, photodistribution, dermatomal/acral/intertriginous relationships, and mucosal involvement;
- symptoms, onset, tempo, recurrence, triggers, medication chronology, and evolution;
- age-dependent, pregnancy-associated, immunocompromised-host, and skin-tone-dependent variation.

This is not cosmetic metadata. Dermatologic diagnosis depends on morphology and distribution, and educational data show that explicit training in morphology, configuration, and distribution improves description and diagnostic performance (PMID:34340596; DOI:10.1177/12034754211035093).

### 2. Evidence-bearing differential diagnosis edges

Add an optional clinical-reasoning layer distinct from the causal pathograph:

- `has_differential_diagnosis` edges;
- discriminating positive and negative findings;
- the clinical context in which the differential applies;
- recommended confirmatory tests and reference standard;
- urgency and harm of missing the alternative;
- evidence class, population, skin tone, age, and review state.

A flat disease list is insufficient. Differential-diagnosis systems are evaluated on ranked alternatives rather than a single label, and clinically similar inflammatory, infectious, autoimmune, and neoplastic conditions frequently overlap (PMID:32424212; DOI:10.1038/s41591-020-0842-3).

### 3. Dermatopathology and clinicopathologic correlation

Standardize:

- reaction pattern and compartment;
- epidermal, junctional, dermal, adnexal, vascular, and subcutaneous findings;
- inflammatory-cell composition and depth;
- special stains, direct and indirect immunofluorescence, immunohistochemistry, cytogenetics, and molecular tests;
- disease stage and treated-versus-untreated state;
- clinicopathologic discordance and key mimickers.

Dermoscopy often has direct histopathologic correlates and can improve lesion selection and targeted histologic examination (PMID:30321580; DOI:10.1016/j.jaad.2018.07.072). In inpatient purpura, morphology, distribution, depth, and histopathology jointly determine the useful differential (PMID:32376433).

### 4. Dermoscopy, trichoscopy, onychoscopy, and other imaging

Create modality-specific observation records with standardized terminology, body site, image acquisition context, gold-standard diagnosis, and evidence. Non-neoplastic dermoscopy can distinguish clinically similar papulosquamous, pigmentary, granulomatous, sclerotic, infectious, and inflammatory conditions, including in darker phototypes (PMID:33319764; DOI:10.1684/ejd.2020.3928).

### 5. Skin-tone-aware presentation and evidence

Every clinical or image-derived assertion should be able to record:

- skin-tone or pigmentation context and the method used to characterize it;
- whether erythema, scale, pigment alteration, or vascular signs differ by skin tone;
- study population and geographic context;
- evidence gaps rather than presumed universality.

Diagnostic performance can differ across skin tones even for specialists and can remain unequal after decision support is introduced (PMID:38317019; DOI:10.1038/s41591-023-02728-3). Skin-tone coverage should therefore be a governed evidence dimension, not only an image-gallery attribute.

## Priority 0 — make management content clinically usable without turning the KG into a guideline

### 6. Treatment-context semantics

For each treatment assertion, represent where supported:

- indication and phenotype/endotype targeted;
- line of therapy, monotherapy versus combination, induction versus maintenance;
- route, dose, schedule, duration, and taper strategy;
- age, pregnancy/lactation, renal/hepatic, infection, malignancy, and immunization constraints;
- baseline screening, laboratory monitoring, drug interactions, adverse effects, and stopping rules;
- expected onset, response endpoint, relapse, and durability;
- procedural details and wound-care requirements;
- regulatory status and guideline/source date.

These fields must be evidence-bearing and time-stamped. The resource should distinguish mechanistic plausibility, trial evidence, regulatory approval, guideline recommendation, and common clinical practice rather than collapsing them into one `treatments` list.

### 7. Severity, activity, damage, and response instruments

Add structured measurement records for instruments such as PASI, BSA, IGA, EASI, SCORAD, SALT, CLASI activity/damage, CDASI, mRSS, HiSCR/IHS4, UAS7, DLQI, itch/pain NRS, and disease-specific quality-of-life tools. Record instrument version, domain, directionality, minimal important difference when established, population, and evidence source.

### 8. Emergency and inpatient dermatology flags

Represent time-sensitive phenotypes and escalation rules for severe cutaneous adverse reactions, necrotizing infection mimics, purpura/vasculopathy, calciphylaxis, blistering emergencies, erythroderma, immunocompromised-host eruptions, and rapidly progressive ulcers. These should be explicit safety annotations with evidence and uncertainty, not diagnosis shortcuts.

## Priority 1 — use generated reports to close condition and content gaps

### 9. Reconcile the board-priority audit

`reports/board_priority_gap.tsv` is the first work queue. For each `NOT_MATCHED_IN_SOURCE` row:

1. determine whether the condition is truly absent, represented under a broader/narrower concept, or missed by naming/ontology resolution;
2. prefer mapping an existing record over creating a duplicate;
3. create a new disease entry only after Mondo/ontology identity and scope are resolved;
4. curate mechanism, clinical presentation, diagnosis, dermatopathology, treatment, and evidence under the normal DisMech workflow.

The registry covers common visual diagnoses, inflammatory and bullous disease, connective-tissue disease, vasculitis, granulomatous/metabolic disease, hair/nail/pigment disorders, infections/infestations, tumors, and pediatric/genodermatology topics from the public ABD outline. It should be reviewed against updated certification materials when the ABD publishes a newer outline.

### 10. Adjudicate candidate systemic diseases

`reports/candidate_review.tsv` contains excluded diseases with limited or ambiguous cutaneous evidence. Review should answer:

- Is the dermatologist routinely responsible for diagnosis, biopsy interpretation, treatment, longitudinal monitoring, or only recognition/referral?
- Are the cutaneous findings diagnostic, prognostic, treatment-defining, or incidental?
- Would importing the full disease record materially improve dermatology utility, or would an association/boundary edge be more accurate?
- Does inclusion create scope leakage into large systemic disease families?

Only reviewed decisions should enter `manual_includes` or `manual_excludes`.

### 11. Prioritize records with thin clinical coverage

Use `reports/clinical_coverage.tsv` to identify selected records lacking diagnosis, dermatopathology, differential, treatment, progression, genetics, clinical-trial, or discussion content. Section counts are triage signals only; they do not measure correctness or evidence quality.

## Priority 1 — specialty taxonomy and export

### 12. Polyhierarchical dermatology tags

Add governed, non-exclusive tags rather than assigning each disease to one first-match bucket. Suggested axes include:

- medical, pediatric, surgical, dermatopathology, inpatient, rheum-derm, mucosal, hair/scalp, nail, pigmentary, photodermatology, contact dermatitis, vascular, infectious, and cutaneous oncology;
- morphology/reaction-pattern families;
- anatomic site and tissue compartment;
- immune, genetic, infectious, neoplastic, drug-induced, environmental, and mechanical etiologies;
- primary dermatology versus co-managed systemic disease.

These tags should supplement, not replace, ontology identity and causal mechanism.

### 13. Optional clinical KGX/CX2 projection

Keep the existing mechanism graph stable. Add a separate opt-in projection for clinical observations, diagnostic tests, differentials, histopathology, severity instruments, and treatments. Export provenance and evidence on every edge. Do not force clinical semantics into causal predicates.

## Priority 2 — image and educational governance

### 14. Licensed image evidence packets

For each image, capture source, license/redistribution permission, consent or de-identification status, body site, modality, skin tone context, diagnosis reference standard, treatment state, and annotation provenance. Do not copy images merely because they are publicly viewable.

### 15. Board-learning overlays

Generate educational views from structured data rather than embedding unsupported mnemonics into disease records:

- high-yield morphology/distribution clues;
- histopathologic pattern and special-study tables;
- closest mimickers and discriminators;
- treatment-selection and monitoring branches;
- emergency red flags;
- image-based recall sets across skin tones.

Educational priority should remain distinct from evidence strength and clinical importance.

## Acceptance criteria for any expansion wave

A scientific expansion is ready only when:

- disease identity and scope are explicit;
- every added claim has source identity, exact evidence, population/context, and review state;
- morphology, histopathology, diagnosis, treatment, and outcome assertions are not conflated;
- negative and conflicting evidence are retained;
- skin-tone and population applicability are recorded when relevant;
- the canonical DisMech validators pass;
- the dermatology audit remains fail-closed and the standalone bundle reproduces byte-for-byte.
