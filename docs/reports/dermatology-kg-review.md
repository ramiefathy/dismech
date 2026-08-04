# DisMech dermatology KG review and expansion plan

## Executive finding

DisMech is a strong mechanistic source corpus but is not yet a dermatology-native
clinical reasoning model. Its one-file-per-disorder LinkML architecture, exact
reference snippets, ontology bindings, evidence grading, causal pathographs, and
module-conformance model are suitable foundations for a specialty projection.
The correct derivative architecture is therefore a lossless, provenance-bound
projection—not an independently edited copy of selected conditions.

The `dermatology_kg/` implementation applies that architecture. It preserves each
selected disorder YAML byte-for-byte, computes the associated reference/module/
research/history/comorbidity closure, emits an exact inclusion rationale for every
condition, and packages a deterministic standalone archive with cryptographic
integrity checks.

## Repository strengths retained

- Mechanism-first disease records with explicit causal nodes and directed edges.
- Human-readable source YAML validated against a shared LinkML schema.
- HPO, MONDO, GO, Cell Ontology, UBERON, ChEBI, HGNC, NCIT, and related ontology
  bindings rather than locally invented identifiers.
- Exact evidence snippets linked to resolvable references and cached source text.
- Evidence direction and source-type distinctions, including partial and refuting
  evidence.
- Reusable pathophysiology modules implemented as conformance targets rather than
  silent inheritance.
- Existing HTML, CX2, and KGX export infrastructure that can consume the canonical
  records.

## Material limitations for dermatology

### 1. Clinical morphology is mostly prose

Dermatologic diagnosis depends on structured morphology, primary/secondary lesion,
configuration, border, surface, color, palpability, symptoms, distribution,
symmetry, anatomic predilection, evolution, and treatment-modified appearance.
These are not interchangeable with generic HPO phenotype labels. They should be
first-class, evidence-bearing observations with applicability and uncertainty.

Recommended schema entities:

- `CutaneousFindingDescriptor`
- `MorphologyDescriptor`
- `DistributionDescriptor`
- `ConfigurationDescriptor`
- `SurfaceDescriptor`
- `ColorDescriptor`
- `TemporalCourseDescriptor`
- `SymptomDescriptor`
- `SkinToneApplicability`

Each should distinguish canonical teaching description, measured cohort finding,
and expert-consensus pattern.

### 2. Differential diagnosis is not sufficiently relational

A useful dermatology graph should not merely attach an unranked list of mimickers.
It should represent:

- the competing diagnosis;
- clinical/histologic resemblance domain;
- findings favoring each side;
- findings arguing against each side;
- decisive or high-yield tests;
- context dependence, including age, site, medication, immune status, pregnancy,
  and skin tone;
- evidence and confidence.

This is especially important for board preparation, consult reasoning, and virtual
case generation.

### 3. Dermatopathology needs a clinicopathologic contract

Add structured specimen and interpretation fields:

- biopsy site and lesion age;
- punch, shave, excision, curettage, cytology, or nail/hair sampling method;
- lesional versus perilesional tissue;
- reaction pattern and compartment;
- key positive and negative microscopic findings;
- special stains, immunohistochemistry, DIF, IIF, salt-split skin, culture, PCR,
  and molecular studies;
- histopathologic mimickers and clinicopathologic discordance;
- sampling limitations and when repeat biopsy is warranted.

### 4. Treatment records require clinical context

Mechanism and treatment names are insufficient for practical or board-relevant
use. Add provenance-bearing fields for:

- role/line of therapy and target phenotype;
- induction versus maintenance;
- expected response interval and treatment-failure definition;
- contraindications, major interactions, baseline screening, ongoing monitoring,
  and discontinuation criteria;
- pregnancy, lactation, pediatric, geriatric, hepatic, renal, oncologic, transplant,
  and infection-risk context;
- regulatory status, guideline status, off-label status, and investigational status;
- urgent escalation and inpatient-level management thresholds.

### 5. Severity, outcomes, and longitudinal state are under-modeled

Add structured instruments and thresholds where relevant, including disease-area,
activity, damage, symptom, quality-of-life, and investigator/patient global
measures. The graph should distinguish activity from damage and transient response
from durable control.

### 6. Images require explicit governance

Clinical, dermoscopic, trichoscopic, histologic, immunofluorescence, and radiologic
images can materially improve utility but require:

- source identity and stable location;
- license and redistribution permission;
- patient-consent/de-identification status where applicable;
- modality, magnification, stain, body site, diagnosis, and disease stage;
- skin-tone and population metadata;
- whether the image is representative, atypical, a mimicker, or an educational
  schematic;
- curation/review state and uncertainty.

No image should be treated as independent proof of a disease claim.

## Condition-coverage expansion worklist

The generated board-topic audit marks each item PRESENT or ABSENT against the
current source commit. High-value additions or expansions, when absent or shallow,
include the following.

### Inflammatory and papulosquamous

Periorificial dermatitis, acne fulminans, acne conglobata, gram-negative
folliculitis, dissecting cellulitis of the scalp, folliculitis decalvans,
pityriasis rubra pilaris, pityriasis lichenoides, lichen nitidus, lichen spinulosus,
porokeratosis subtypes, confluent and reticulated papillomatosis, and reactive
perforating disorders.

### Rheumatologic, neutrophilic, and vasculopathic

Clinically amyopathic dermatomyositis, anti-MDA5 dermatomyositis, anti-TIF1-gamma
phenotype, antisynthetase cutaneous disease, Rowell syndrome, chilblain lupus,
lupus panniculitis, Degos disease, livedoid vasculopathy, calciphylaxis, rheumatoid
neutrophilic dermatosis, bowel-associated dermatosis-arthritis syndrome,
subcorneal pustular dermatosis, neutrophilic eccrine hidradenitis, and palisaded
neutrophilic granulomatous dermatitis.

### Immunobullous

Pemphigus foliaceus, paraneoplastic pemphigus, pemphigus herpetiformis, anti-p200
pemphigoid, pemphigoid gestationis, lichen planus pemphigoides, bullous lupus,
epidermolysis bullosa acquisita variants, linear IgA disease by age group, and
bullous drug eruptions with immunopathologic differentiation.

### Genodermatoses and pediatric dermatology

Ectodermal dysplasia subtypes, pachyonychia congenita, Hailey-Hailey disease,
Darier disease, CHILD syndrome, Netherton syndrome, peeling skin syndromes,
Kindler epidermolysis bullosa, focal dermal hypoplasia, epidermal nevus syndromes,
PIK3CA-related overgrowth, congenital melanocytic nevus syndromes, aplasia cutis,
subcutaneous fat necrosis of the newborn, neonatal lupus, and juvenile
xanthogranuloma.

### Hair, nail, and mucosa

Central centrifugal cicatricial alopecia, frontal fibrosing alopecia, traction
alopecia, trichotillomania, tinea capitis inflammatory variants, loose anagen
syndrome, monilethrix, trichorrhexis nodosa, twenty-nail dystrophy, nail-unit
melanoma, onychopapilloma, retronychia, median canaliform dystrophy, erosive oral
lichen planus, mucous membrane pemphigoid by site, vulvovaginal gingival syndrome,
plasma-cell mucositis, and complex aphthosis.

### Infection and infestation

Ecthyma, ecthyma gangrenosum, erythrasma, trichomycosis axillaris, pitted
keratolysis, atypical mycobacterial skin infection, deep fungal infection,
chromoblastomycosis, sporotrichosis, mycetoma, cutaneous amebiasis, tungiasis,
cutaneous larva migrans, swimmer's itch, rickettsial eschars, disseminated zoster,
and immunocompromised-host viral eruptions.

### Neoplasia and dermatologic surgery

Keratinocyte carcinoma risk states, field cancerization, keratoacanthoma variants,
adnexal carcinoma subtypes, sebaceous carcinoma and Muir-Torre workup, atypical
fibroxanthoma, pleomorphic dermal sarcoma, microcystic adnexal carcinoma, eccrine
porocarcinoma, primary cutaneous B-cell lymphoma subtypes, primary cutaneous
CD30-positive lymphoproliferative disorders, angiosarcoma, dermatofibrosarcoma
protuberans variants, and procedural margin/recurrence concepts.

### Drug reactions and emergencies

Generalized bullous fixed drug eruption, symmetrical drug-related intertriginous
and flexural exanthema, drug-induced lupus, drug-induced pemphigus/pemphigoid,
EGFR-inhibitor eruption, immune-checkpoint-inhibitor toxicities, neutrophilic
reactions to targeted therapy, acute graft-versus-host disease, calciphylaxis,
purpura fulminans, necrotizing soft-tissue infection, and erythroderma etiologies.

## Prioritized implementation sequence

### P0 — Distribution integrity

Maintain byte identity, exact source commit, complete reference and module closure,
no undeclared disorders, deterministic packaging, and a curator-visible scope
catalog. These controls are implemented in `dermatology_kg/`.

### P1 — Dermatology clinical schema

Add morphology/distribution, differential discriminators, clinicopathologic
correlation, severity/outcomes, treatment-context, red-flag, and population/
skin-tone applicability records to the canonical schema.

### P2 — Content depth and board utility

Use the generated audit to prioritize core disorders lacking differential,
histopathology, diagnosis, or treatment structure. Expand condition coverage by
subspecialty with expert review and exact-source evidence.

### P3 — Query and interface layer

Provide specialty views for clinical differential, histopathologic reaction
pattern, body site, morphology, medication reaction, immunofluorescence pattern,
genetic syndrome, treatment mechanism, and urgent escalation. Expose provenance
and uncertainty at every result edge.

## Acceptance criteria for future dermatology curation

A new or expanded condition should not be considered complete merely because it
has a name, phenotype list, and treatment list. At minimum, it should establish a
mechanistic pathograph; structured clinical pattern; meaningful mimickers and
discriminators; diagnostic and histopathologic strategy where applicable;
treatment context and safety; prognosis/complications; ontology bindings; exact
source evidence; and explicit knowledge gaps. Claims that vary by population,
body site, disease stage, or skin tone must state that scope.
