# BondRadar Issuer Identity Enrichment

Issuer identity cleanup is the review step before collecting real issuer
financial reports. It turns generated names such as `Unknown issuer for
RU000...` into reviewed legal issuer metadata without guessing silently.

This workflow does not scrape external websites, merge companies, change bonds,
activate schedules, or run paper execution.

## Why Identity Comes First

Corporate bonds may involve several entities:

- legal issuer;
- finance subsidiary;
- SPV;
- operating company;
- parent group;
- guarantor.

These are not automatically the same. BondRadar stores legal issuer identity
separately from issuer group/parent identity. Financial reports should be linked
to the legal issuer only after operator review, unless a later explicit mapping
task says otherwise.

## Safe Workflow

```text
diagnostics -> target export -> manual review -> preview -> apply -> coverage recheck
```

Local path policy:

```text
docs/examples/issuer_identity/     = synthetic examples safe for git
data/issuer_identity/private/      = real operator data, ignored by git
data/issuer_identity/staging/      = in-progress review files, ignored by git
data/issuer_identity/reviewed/     = reviewed real data, ignored by git
logs/issuer_identity/              = generated reports, ignored by git
```

Do not commit real reviewed identity CSV files, generated VDS/local logs, smoke
test reports, or private operator files.

Start with diagnostics:

```bash
curl -s "http://127.0.0.1:8000/api/companies/identity/diagnostics?active_only=true&limit=20" \
  -o logs/issuer_identity/diagnostics.json
```

Export cleanup targets:

```bash
python scripts/issuer_identity_target_export.py \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --model-run-id 2 \
  --as-of-date 2026-05-19 \
  --limit 20 \
  --json-output logs/issuer_identity/targets.json \
  --csv-output logs/issuer_identity/targets.csv \
  --markdown-output logs/issuer_identity/targets.md
```

Fill the identity template from official issuer evidence:

```text
docs/examples/issuer_identity/issuer_identity_template.csv
docs/examples/issuer_identity/issuer_identity_template.json
```

Batch rehearsal can generate a target list and review template before any
reviewed file exists:

```bash
python scripts/issuer_identity_batch_rehearsal.py \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --model-run-id 2 \
  --as-of-date 2026-05-19 \
  --limit 20 \
  --review-template-output logs/issuer_identity/priority_identity_review_template_task81.csv \
  --json-output logs/issuer_identity/batch_rehearsal_task81.json \
  --markdown-output logs/issuer_identity/batch_rehearsal_task81.md
```

Expected behavior:

- diagnostics before is fetched;
- targets are exported;
- review template is generated;
- preview/apply is not attempted without reviewed input;
- report status is `passed` or `warning`;
- next steps explain that the operator must fill the reviewed CSV manually.

Dry-run and preview:

```bash
python scripts/issuer_identity_import.py \
  --input data/issuer_identity/staging/issuer_identity_review.csv \
  --format csv \
  --backend-url http://127.0.0.1:8000 \
  --dry-run \
  --json-output logs/issuer_identity/import_preview.json \
  --markdown-output logs/issuer_identity/import_preview.md
```

Confirmed apply is non-default:

```bash
python scripts/issuer_identity_import.py \
  --input data/issuer_identity/reviewed/issuer_identity_review.csv \
  --format csv \
  --backend-url http://127.0.0.1:8000 \
  --execute yes \
  --confirm-apply yes \
  --json-output logs/issuer_identity/import_apply.json \
  --markdown-output logs/issuer_identity/import_apply.md
```

Batch preview without apply:

```bash
python scripts/issuer_identity_batch_rehearsal.py \
  --backend-url http://127.0.0.1:8000 \
  --reviewed-input data/issuer_identity/private/priority_identity_review.csv \
  --format csv \
  --execute-apply no \
  --json-output logs/issuer_identity/batch_preview_task81.json \
  --markdown-output logs/issuer_identity/batch_preview_task81.md
```

Confirmed batch apply requires both explicit flags:

```bash
python scripts/issuer_identity_batch_rehearsal.py \
  --backend-url http://127.0.0.1:8000 \
  --reviewed-input data/issuer_identity/reviewed/priority_identity_review.csv \
  --format csv \
  --execute-apply yes \
  --confirm-apply yes \
  --json-output logs/issuer_identity/batch_apply_task81.json \
  --markdown-output logs/issuer_identity/batch_apply_task81.md
```

Create a PostgreSQL backup before confirmed apply. The script does not perform
automatic rollback.

## MOEX Metadata Preview

MOEX ISS metadata can sometimes provide issuer name or INN for a bond secid.
Use it only as a preview source:

```bash
python scripts/issuer_identity_moex_enrich.py \
  --backend-url http://127.0.0.1:8000 \
  --limit 20 \
  --json-output logs/issuer_identity/moex_preview.json \
  --markdown-output logs/issuer_identity/moex_preview.md
```

This script does not apply changes unless `--execute yes --confirm-apply yes`
is provided.

## Conflict Policy

Preview reports hard conflicts, including:

- same INN linked to another company;
- same OGRN linked to another company;
- incoming INN/OGRN differs from a verified profile;
- incoming legal name differs from a verified profile.

Apply blocks hard conflicts by default. `--allow-conflicts` records conflict
status and notes, but still does not merge companies.

## Duplicate / Same Issuer Review

Duplicate company rows happen when the bond universe contains weak issuer
metadata and BondRadar creates generated placeholders such as `Unknown issuer
for RU000...`. A later reviewed identity can reveal that several company rows
probably refer to the same legal issuer or the same issuer group.

Duplicate review does not merge companies. It only records a reviewed candidate
relationship in `company_identity_duplicate_candidates`. Actual merge or bond
consolidation is a separate future task.

Keep these cases separate during review:

- duplicate legal issuer;
- same issuer group;
- SPV or finance subsidiary;
- parent group or guarantor.

Safe workflow:

```text
diagnostics -> duplicate export -> manual review -> preview -> confirmed apply
```

Duplicate diagnostics:

```bash
curl -sS "http://127.0.0.1:8000/api/companies/identity/duplicates/diagnostics?active_only=true&limit=50&min_score=0.50" \
  -o logs/issuer_identity/duplicate_diagnostics.json
```

Export duplicate candidates:

```bash
python scripts/issuer_identity_duplicate_export.py \
  --backend-url http://127.0.0.1:8000 \
  --limit 50 \
  --min-score 0.50 \
  --json-output logs/issuer_identity/duplicate_candidates.json \
  --csv-output logs/issuer_identity/duplicate_candidates.csv \
  --markdown-output logs/issuer_identity/duplicate_candidates.md
```

Use `--exclude-accepted` when you want only unresolved candidates. The default
export keeps accepted/reviewed rows visible so the operator can see the current
duplicate map.

Preview reviewed duplicate decisions:

```bash
python scripts/issuer_identity_duplicate_review.py \
  --input data/issuer_identity/private/duplicate_review.csv \
  --format csv \
  --backend-url http://127.0.0.1:8000 \
  --dry-run \
  --json-output logs/issuer_identity/duplicate_preview.json \
  --markdown-output logs/issuer_identity/duplicate_preview.md
```

Confirmed apply records only the non-destructive review decision:

```bash
python scripts/issuer_identity_duplicate_review.py \
  --input data/issuer_identity/private/duplicate_review.csv \
  --format csv \
  --backend-url http://127.0.0.1:8000 \
  --execute-apply yes \
  --confirm-apply yes \
  --json-output logs/issuer_identity/duplicate_apply.json \
  --markdown-output logs/issuer_identity/duplicate_apply.md
```

The duplicate workflow never moves bonds, deletes companies, overwrites verified
identity, or applies legal-name guesses automatically.

## Using Duplicate Mapping In Target Exports

Accepted/reviewed duplicate decisions can be used as a read-only canonical
issuer layer for operator reports. This does not merge companies and does not
move bonds; it only rolls duplicate rows up in generated CSV/JSON/Markdown.

Identity targets with canonical rollup:

```bash
python scripts/issuer_identity_target_export.py \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --model-run-id 2 \
  --as-of-date 2026-05-19 \
  --limit 50 \
  --use-duplicate-mapping \
  --rollup-duplicates \
  --include-duplicate-members \
  --compare-rollup \
  --json-output logs/issuer_identity/canonical_identity_targets_task83.json \
  --csv-output logs/issuer_identity/canonical_identity_targets_task83.csv \
  --markdown-output logs/issuer_identity/canonical_identity_targets_task83.md
```

Use this before applying more identity rows so reviewed duplicate candidates are
not handled as separate legal issuers by mistake.

## Uncertain Cases

If evidence is unclear:

- leave identity as `weak` or `unknown`;
- do not fake INN, OGRN, or group identity;
- add review notes;
- collect more source evidence before importing financial reports.

Identity cleanup is an operator review aid. It is not a credit-risk override and
does not change virtual paper schedules.

## Manual Review Quality Checklist

For each reviewed row:

- Did I identify the legal issuer, not just the brand?
- Did I verify INN/OGRN from a reliable source?
- Did I record `source_url` or `source_file_name`?
- Did I avoid using bond short name as legal name?
- If issuer is SPV/subsidiary, did I mark `issuer_role` correctly?
- If parent/group differs from legal issuer, did I fill `issuer_group_name`
  separately?
- If uncertain, did I leave `identity_status` weak/unknown instead of guessing?

## Identity-First Financial Collection Queue

After the financial collection priority queue is generated, run the
identity-first queue before collecting official issuer reports. The priority
queue ranks how valuable a report would be; the identity-first queue decides
whether the issuer identity is strong enough to start financial collection.

```text
Known corporate issuers go to collection_ready.
Unknown issuer rows go to identity_review_required.
Government-like issuers remain excluded/deprioritized.
Already covered issuers like TMK stay in already_covered with missing fields.
```

This step is planning/export only. It does not import identity profiles, apply
duplicate mappings, import financial reports, mutate scores, update
predictions, activate schedules, or call paper-trading endpoints.

Run on VDS:

```bash
python3 scripts/identity_first_collection_queue.py \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --model-run-id 2 \
  --as-of-date 2026-05-19 \
  --limit 50 \
  --use-duplicate-mapping \
  --rollup-duplicates \
  --include-duplicate-members \
  --include-covered \
  --exclude-government-like \
  --json-output logs/financial_reports/identity_first_collection_queue_task94_vds.json \
  --markdown-output logs/financial_reports/identity_first_collection_queue_task94_vds.md \
  --identity-review-csv-output logs/financial_reports/identity_review_required_task94_vds.csv \
  --collection-ready-csv-output logs/financial_reports/collection_ready_task94_vds.csv
```

Review `identity_review_required_task94_vds.csv` first. Rows named
`Unknown issuer for ...`, rows with low classification confidence, weak
identity status, missing legal name, or missing INN/OGRN must go through the
normal identity review workflow before any financial report collection. Use
`collection_ready_task94_vds.csv` for issuers that are known corporate issuers
and ready for official-source report collection.

## Official-Source Collection Pack for Collection-Ready Issuers

After identity-first review, use the official-source collection pack to create
financial collection templates only for `collection_ready` issuers. This keeps
unknown issuers in identity review and prevents report collection for the wrong
legal entity.

```text
Identity-first queue decides who is safe to collect.
Official-source collection pack creates templates/checklists for those issuers.
No financial values are invented.
Real financial values must come from official issuer report/disclosure/auditor report.
Preview must pass before any future import.
```

Run on VDS:

```bash
python3 scripts/financial_official_collection_pack.py \
  --mode template \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --model-run-id 2 \
  --as-of-date 2026-05-19 \
  --limit 50 \
  --use-duplicate-mapping \
  --rollup-duplicates \
  --include-duplicate-members \
  --include-covered \
  --exclude-government-like \
  --period-year 2025 \
  --period-quarter 0 \
  --report-type annual \
  --currency RUB \
  --accounting-standard IFRS \
  --consolidation-scope consolidated \
  --value-scale million \
  --max-issuers 2 \
  --financial-template-output data/financial_reports/private/collection_ready_financial_template_task95.csv \
  --evidence-template-output data/financial_reports/private/official_source_evidence_template_task95.json \
  --source-checklist-output logs/financial_reports/official_source_checklist_task95.csv \
  --json-output logs/financial_reports/official_collection_pack_task95.json \
  --markdown-output logs/financial_reports/official_collection_pack_task95.md
```

The generated files are operator artifacts. Keep filled financial values in
ignored private/log paths and run preview mode before any future import task.
Future live BondRadar cycles may refresh market/risk/paper state every 1-2
hours while the exchange is open, but this workflow does not implement or
activate that scheduler.

## Official-Source Evidence Assistant

Once issuers are `collection_ready`, use the official-source evidence assistant
to separate source discovery and evidence-backed value entry from any future
import. The assistant helps operators keep the chain explicit:

```text
known issuer identity
official source candidate
field evidence with page/table/note
candidate file
preview-only validation
separate controlled import later
```

Unknown issuer rows must stay in identity review. Do not collect financial
values for them until the legal issuer, INN/OGRN, and official source are
confirmed.

Create source intake from the Task 95 financial template and checklist:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode source-template \
  --financial-template-input data/financial_reports/private/collection_ready_financial_template_task95.csv \
  --evidence-template-input data/financial_reports/private/official_source_evidence_template_task95.json \
  --source-checklist-input logs/financial_reports/official_source_checklist_task95.csv \
  --source-intake-output data/financial_reports/private/official_source_intake_task96.json \
  --json-output logs/financial_reports/official_source_intake_task96.json \
  --markdown-output logs/financial_reports/official_source_intake_task96.md
```

Validate source URLs before entering values:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode source-validate \
  --source-intake-input data/financial_reports/private/official_source_intake_task96.json \
  --json-output logs/financial_reports/official_source_validation_task96.json \
  --markdown-output logs/financial_reports/official_source_validation_task96.md
```

Fill a candidate file from manual evidence only:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode candidate-fill \
  --financial-template-input data/financial_reports/private/collection_ready_financial_template_task95.csv \
  --source-intake-input data/financial_reports/private/official_source_intake_task96.json \
  --manual-values-json data/financial_reports/private/manual_values_task96.json \
  --candidate-output data/financial_reports/private/collection_ready_financial_candidate_task96.csv \
  --candidate-format csv \
  --evidence-output logs/financial_reports/official_source_evidence_task96.json \
  --json-output logs/financial_reports/official_source_candidate_fill_task96.json \
  --markdown-output logs/financial_reports/official_source_candidate_fill_task96.md
```

Preview without import:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode preview \
  --backend-url http://127.0.0.1:8000 \
  --candidate-input data/financial_reports/private/collection_ready_financial_candidate_task96.csv \
  --format csv \
  --json-output logs/financial_reports/official_source_candidate_preview_task96.json \
  --markdown-output logs/financial_reports/official_source_candidate_preview_task96.md
```

The assistant blocks Wikipedia/wiki, blogs, forums, social media, random
aggregator sources, market capitalization as equity, coupon payments as
interest expense, and suspicious placeholder-zero rows. It does not import or
apply financial reports or identity profiles.

## Official-Source Discovery

Task 97 source discovery can enrich the Task 96 intake with official-looking
source candidates for known collection-ready issuers such as RZD and
Mostotrest. It is still source-only: no financial values are extracted, no PDFs
are parsed, and no import/apply endpoint is called.

Discover source candidates:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode source-discover \
  --source-intake-input data/financial_reports/private/official_source_intake_task96.json \
  --source-intake-output data/financial_reports/private/official_source_intake_discovered_task97.json \
  --json-output logs/financial_reports/official_source_discovery_task97.json \
  --markdown-output logs/financial_reports/official_source_discovery_task97.md
```

Validate discovered source candidates:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode source-validate \
  --source-intake-input data/financial_reports/private/official_source_intake_discovered_task97.json \
  --json-output logs/financial_reports/official_source_validation_discovered_task97.json \
  --markdown-output logs/financial_reports/official_source_validation_discovered_task97.md
```

Discovery candidates are not approved value sources. Unknown issuer rows remain
in identity review, landing pages require operator review, and exact official
annual/audited report evidence is still required before candidate-fill.

## Exact Official Report Document Resolver

Once official-looking sources are discovered, resolve the exact annual/audited
report document metadata before any financial value entry. This resolver works
only with source/document metadata: URLs, titles, dates, file names, status, and
operator review state.

Resolve document candidates:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode document-resolve \
  --source-intake-input data/financial_reports/private/official_source_intake_discovered_task97.json \
  --source-intake-output data/financial_reports/private/official_source_intake_resolved_task98.json \
  --document-output data/financial_reports/private/official_report_documents_task98.json \
  --document-checklist-output logs/financial_reports/official_report_document_checklist_task98.csv \
  --json-output logs/financial_reports/official_report_documents_task98.json \
  --markdown-output logs/financial_reports/official_report_documents_task98.md
```

Validate exact report documents:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode document-validate \
  --document-input data/financial_reports/private/official_report_documents_task98.json \
  --json-output logs/financial_reports/official_report_document_validation_task98.json \
  --markdown-output logs/financial_reports/official_report_document_validation_task98.md
```

The resolver must not invent report PDFs or treat landing pages as final
evidence. Exact report metadata is required before candidate-fill, and unknown
or blocked domains remain outside the financial collection path.

## Operator Exact Document Intake

Task 99 adds the operator handoff after document resolution. It creates a
fillable exact document intake file, validates reviewed exact report metadata,
and then lets `document-resolve` merge valid documents into the resolved source
intake. It does not extract financial values, import reports, mutate identity
data, or trade.

Create the exact document intake template:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode document-intake-template \
  --document-input data/financial_reports/private/official_report_documents_task98.json \
  --document-intake-output data/financial_reports/private/exact_document_intake_task99.json \
  --document-intake-csv-output data/financial_reports/private/exact_document_intake_task99.csv \
  --json-output logs/financial_reports/exact_document_intake_template_task99.json \
  --markdown-output logs/financial_reports/exact_document_intake_template_task99.md
```

Validate reviewed exact document intake:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode document-intake-validate \
  --document-intake-input data/financial_reports/private/exact_document_intake_filled_task99.json \
  --json-output logs/financial_reports/exact_document_intake_validation_task99.json \
  --markdown-output logs/financial_reports/exact_document_intake_validation_task99.md
```

Resolve documents with the reviewed intake:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode document-resolve \
  --source-intake-input data/financial_reports/private/official_source_intake_discovered_task97.json \
  --document-intake-input data/financial_reports/private/exact_document_intake_filled_task99.json \
  --source-intake-output data/financial_reports/private/official_source_intake_resolved_task99.json \
  --document-output data/financial_reports/private/official_report_documents_resolved_task99.json \
  --document-checklist-output logs/financial_reports/official_report_document_checklist_task99.csv \
  --json-output logs/financial_reports/official_report_documents_resolved_task99.json \
  --markdown-output logs/financial_reports/official_report_documents_resolved_task99.md
```

Use only exact official annual/audited report pages or PDFs. Do not use
landing pages, Wikipedia, blogs, forums, social media, news, or aggregators as
final document evidence.

## Exact Official Document Intake Fill

Task 100 fills exact document metadata from a private reviewed candidate file.
It is source/document metadata only: no financial values are extracted, no PDFs
are OCRed or parsed, no reports are imported, and no trading, scoring, or
identity state is changed. If no reviewed exact candidate file is supplied, the
filled intake remains unfilled with warnings.

Fill exact document intake from reviewed candidates:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode document-intake-fill \
  --document-intake-input data/financial_reports/private/exact_document_intake_task99.json \
  --source-intake-input data/financial_reports/private/official_source_intake_discovered_task97.json \
  --document-output data/financial_reports/private/official_report_documents_task98.json \
  --exact-document-candidates-input data/financial_reports/private/exact_document_candidates_task100.json \
  --document-intake-output data/financial_reports/private/exact_document_intake_filled_task100.json \
  --document-intake-csv-output data/financial_reports/private/exact_document_intake_filled_task100.csv \
  --json-output logs/financial_reports/exact_document_discovery_task100.json \
  --markdown-output logs/financial_reports/exact_document_discovery_task100.md
```

Validate filled exact document intake:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode document-intake-validate \
  --document-intake-input data/financial_reports/private/exact_document_intake_filled_task100.json \
  --json-output logs/financial_reports/exact_document_intake_validation_task100.json \
  --markdown-output logs/financial_reports/exact_document_intake_validation_task100.md
```

Resolve exact documents after validation:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode document-resolve \
  --source-intake-input data/financial_reports/private/official_source_intake_discovered_task97.json \
  --document-intake-input data/financial_reports/private/exact_document_intake_filled_task100.json \
  --source-intake-output data/financial_reports/private/official_source_intake_resolved_task100.json \
  --document-output data/financial_reports/private/official_report_documents_resolved_task100.json \
  --document-checklist-output logs/financial_reports/official_report_document_checklist_task100.csv \
  --json-output logs/financial_reports/official_report_documents_resolved_task100.json \
  --markdown-output logs/financial_reports/official_report_documents_resolved_task100.md
```

Only exact reviewed official report pages or PDFs can fill rows. Unknown domains
remain review-only even with `--allow-unknown-source`, and blocked domains or
financial fields fail validation.

## Exact Document Quality Gate

Task 101 runs the resolve-ready gate before any future financial value
collection. It reuses exact document intake fill, intake validation, and document
resolve, then fails unless every required issuer has exactly one valid reviewed
official document. The gate does not extract values, import reports, mutate
identity state, or trade.

Run the quality gate:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode document-quality-gate \
  --document-intake-input data/financial_reports/private/exact_document_intake_task99.json \
  --source-intake-input data/financial_reports/private/official_source_intake_discovered_task97.json \
  --document-input data/financial_reports/private/official_report_documents_task98.json \
  --exact-document-candidates-input data/financial_reports/private/exact_document_candidates_task101.json \
  --required-company-ids 18,67 \
  --document-intake-output data/financial_reports/private/exact_document_intake_gate_task101.json \
  --document-intake-csv-output data/financial_reports/private/exact_document_intake_gate_task101.csv \
  --source-intake-output data/financial_reports/private/official_source_intake_resolved_gate_task101.json \
  --document-output data/financial_reports/private/official_report_documents_gate_task101.json \
  --document-checklist-output logs/financial_reports/official_report_document_checklist_gate_task101.csv \
  --json-output logs/financial_reports/exact_document_quality_gate_task101.json \
  --markdown-output logs/financial_reports/exact_document_quality_gate_task101.md
```

If no exact reviewed candidate file is supplied, the gate fails safely. Only a
full pass sets `ready_for_value_extraction=true`; `ready_for_import` remains
`false` in all cases.

## Controlled Official Document Candidate Discovery

Task 102 searches only allowlisted official source pages for exact report
document candidates. It does not use search engines, parse PDFs for values,
extract financial values, import reports, mutate identity state, or trade.
Uncertain links remain operator-review candidates and must still pass the exact
document quality gate before any value collection.

Run controlled official candidate discovery:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode document-candidate-discover \
  --document-intake-input data/financial_reports/private/exact_document_intake_task99.json \
  --source-intake-input data/financial_reports/private/official_source_intake_discovered_task97.json \
  --document-input data/financial_reports/private/official_report_documents_task98.json \
  --required-company-ids 18,67 \
  --report-period 2025 \
  --report-type annual \
  --accounting-standard IFRS \
  --candidate-output data/financial_reports/private/exact_document_candidates_task102.json \
  --candidate-csv-output data/financial_reports/private/exact_document_candidates_task102.csv \
  --run-quality-gate true \
  --quality-gate-json-output logs/financial_reports/exact_document_quality_gate_task102.json \
  --quality-gate-markdown-output logs/financial_reports/exact_document_quality_gate_task102.md \
  --json-output logs/financial_reports/exact_document_candidate_discovery_task102.json \
  --markdown-output logs/financial_reports/exact_document_candidate_discovery_task102.md
```

The crawler fetches only allowlisted official seed pages, extracts HTML anchors,
scores document-looking links, and writes candidates compatible with
`document-intake-fill` and `document-quality-gate`. If exact reviewed documents
are not found for every required issuer, the quality gate remains failed and
`ready_for_value_extraction=false`.

## Official Seed Resolver

Task 103 resolves better official navigation seeds before candidate discovery.
It can enrich the seed report with INN/OGRN from the Task 95-like financial
template, but it does not apply identity changes, extract financial values,
import reports, mutate state, or trade.

Run official seed resolution with candidate discovery and the strict gate:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode official-seed-resolve \
  --document-intake-input data/financial_reports/private/exact_document_intake_task99.json \
  --source-intake-input data/financial_reports/private/official_source_intake_discovered_task97.json \
  --document-input data/financial_reports/private/official_report_documents_task98.json \
  --financial-template-input data/financial_reports/private/collection_ready_financial_template_task95.csv \
  --required-company-ids 18,67 \
  --seed-output data/financial_reports/private/official_seed_pack_task103.json \
  --seed-csv-output data/financial_reports/private/official_seed_pack_task103.csv \
  --run-candidate-discovery true \
  --candidate-output data/financial_reports/private/exact_document_candidates_task103.json \
  --candidate-csv-output data/financial_reports/private/exact_document_candidates_task103.csv \
  --run-quality-gate true \
  --quality-gate-json-output logs/financial_reports/exact_document_quality_gate_task103.json \
  --quality-gate-markdown-output logs/financial_reports/exact_document_quality_gate_task103.md \
  --json-output logs/financial_reports/official_seed_resolve_task103.json \
  --markdown-output logs/financial_reports/official_seed_resolve_task103.md
```

Generated issuer paths such as `/investors/` or `/reports/` are only navigation
seeds. They are not exact report evidence and cannot weaken the exact document
quality gate.

## Operator Official Seed Intake

Task 104 lets an operator provide reviewed official navigation seed pages after
identity-first collection targets are known. The workflow records seed metadata
only: company identity fields, seed type, seed URL, review status, context, and
notes. It does not apply identity profiles, duplicate mappings, financial
reports, scores, predictions, schedules, or paper state.

Create an operator-fillable seed template:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode operator-seed-template \
  --seed-input data/financial_reports/private/official_seed_pack_task103.json \
  --document-intake-input data/financial_reports/private/exact_document_intake_task99.json \
  --financial-template-input data/financial_reports/private/collection_ready_financial_template_task95.csv \
  --required-company-ids 18,67 \
  --operator-seed-output data/financial_reports/private/operator_official_seed_task104.json \
  --operator-seed-csv-output data/financial_reports/private/operator_official_seed_task104.csv \
  --json-output logs/financial_reports/operator_official_seed_template_task104.json \
  --markdown-output logs/financial_reports/operator_official_seed_template_task104.md
```

Validate filled operator seeds:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode operator-seed-validate \
  --operator-seed-input data/financial_reports/private/operator_official_seed_filled_task104.json \
  --required-company-ids 18,67 \
  --json-output logs/financial_reports/operator_official_seed_validation_task104.json \
  --markdown-output logs/financial_reports/operator_official_seed_validation_task104.md
```

Resolve official seeds with reviewed operator input, then run discovery/gate:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode official-seed-resolve \
  --document-intake-input data/financial_reports/private/exact_document_intake_task99.json \
  --source-intake-input data/financial_reports/private/official_source_intake_discovered_task97.json \
  --document-input data/financial_reports/private/official_report_documents_task98.json \
  --financial-template-input data/financial_reports/private/collection_ready_financial_template_task95.csv \
  --operator-seed-input data/financial_reports/private/operator_official_seed_filled_task104.json \
  --required-company-ids 18,67 \
  --seed-output data/financial_reports/private/official_seed_pack_task104.json \
  --seed-csv-output data/financial_reports/private/official_seed_pack_task104.csv \
  --run-candidate-discovery true \
  --candidate-output data/financial_reports/private/exact_document_candidates_task104.json \
  --candidate-csv-output data/financial_reports/private/exact_document_candidates_task104.csv \
  --run-quality-gate true \
  --quality-gate-json-output logs/financial_reports/exact_document_quality_gate_task104.json \
  --quality-gate-markdown-output logs/financial_reports/exact_document_quality_gate_task104.md \
  --json-output logs/financial_reports/official_seed_resolve_task104.json \
  --markdown-output logs/financial_reports/official_seed_resolve_task104.md
```

Use only official issuer, disclosure, or official-like exchange pages. Seed
pages remain navigation aids; they do not bypass exact reviewed document
requirements, do not authorize financial value collection, and do not change
`ready_for_value_extraction` unless the strict document quality gate later
passes.

## Operator Official Seed Candidate Helper

Task 105 helps the operator fill official seed URLs after the Task 104 template
is created. It proposes seed-page candidates from allowlisted official pages and
identity context only. It does not apply identity changes, import financial
reports, extract values, score bonds, alter predictions, activate schedules, or
run paper trading.

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode operator-seed-candidate-discover \
  --operator-seed-input data/financial_reports/private/operator_official_seed_task104.json \
  --seed-input data/financial_reports/private/official_seed_pack_task103.json \
  --financial-template-input data/financial_reports/private/collection_ready_financial_template_task95.csv \
  --required-company-ids 18,67 \
  --operator-seed-candidate-output data/financial_reports/private/operator_official_seed_candidates_task105.json \
  --operator-seed-candidate-csv-output data/financial_reports/private/operator_official_seed_candidates_task105.csv \
  --operator-seed-autofill-output data/financial_reports/private/operator_official_seed_autofill_task105.json \
  --operator-seed-autofill-csv-output data/financial_reports/private/operator_official_seed_autofill_task105.csv \
  --run-operator-seed-validate true \
  --operator-seed-validation-json-output logs/financial_reports/operator_official_seed_validation_autofill_task105.json \
  --operator-seed-validation-markdown-output logs/financial_reports/operator_official_seed_validation_autofill_task105.md \
  --json-output logs/financial_reports/operator_official_seed_candidate_discovery_task105.json \
  --markdown-output logs/financial_reports/operator_official_seed_candidate_discovery_task105.md
```

Only official seed metadata is written. High-confidence autofill still feeds the
strict `operator-seed-validate` and later exact-document quality gate workflows;
it does not authorize value extraction by itself.

## Operator Seed Candidate Ranking and Noise Filter

Task 106 adds ranking and noise filtering to the candidate helper. Official
domain alone is not enough for a useful seed candidate: investor, reporting,
annual-report, disclosure, profile, or financial-results signals must carry the
row. Passenger services, ticketing, train routes, station boards, contact,
history, generic activity, project, search, news, blog, forum, and social links
are filtered by default.

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode operator-seed-candidate-discover \
  --operator-seed-input data/financial_reports/private/operator_official_seed_task104.json \
  --seed-input data/financial_reports/private/official_seed_pack_task103.json \
  --financial-template-input data/financial_reports/private/collection_ready_financial_template_task95.csv \
  --required-company-ids 18,67 \
  --operator-seed-candidate-top-n-per-issuer 20 \
  --operator-seed-candidate-top-n-per-type 5 \
  --operator-seed-candidate-include-filtered false \
  --operator-seed-candidate-noise-filter true \
  --operator-seed-candidate-max-autofill-review-needed 3 \
  --operator-seed-candidate-output data/financial_reports/private/operator_official_seed_candidates_task106.json \
  --operator-seed-candidate-csv-output data/financial_reports/private/operator_official_seed_candidates_task106.csv \
  --operator-seed-autofill-output data/financial_reports/private/operator_official_seed_autofill_task106.json \
  --operator-seed-autofill-csv-output data/financial_reports/private/operator_official_seed_autofill_task106.csv \
  --run-operator-seed-validate true \
  --operator-seed-validation-json-output logs/financial_reports/operator_official_seed_validation_autofill_task106.json \
  --operator-seed-validation-markdown-output logs/financial_reports/operator_official_seed_validation_autofill_task106.md \
  --json-output logs/financial_reports/operator_official_seed_candidate_discovery_task106.json \
  --markdown-output logs/financial_reports/operator_official_seed_candidate_discovery_task106.md
```

Use `--operator-seed-candidate-include-filtered true` to inspect rejected rows.
Autofill only uses kept ranked candidates and caps review-needed rows with
`--operator-seed-candidate-max-autofill-review-needed`. The helper remains
source metadata only and does not change identities, reports, scores,
predictions, schedules, paper state, or value-extraction readiness.

## Operator Seed Review Promotion

Task 107 turns ranked seed candidates into an operator review checklist and then
promotes only explicit approvals into reviewed operator seed rows. The workflow
is still metadata-only: it does not apply identity changes, extract values,
import reports, score bonds, alter predictions, activate schedules, or run
paper trading.

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode operator-seed-review-template \
  --operator-seed-candidate-input data/financial_reports/private/operator_official_seed_candidates_task106.json \
  --operator-seed-input data/financial_reports/private/operator_official_seed_task104.json \
  --required-company-ids 18,67 \
  --operator-seed-review-output data/financial_reports/private/operator_official_seed_review_task107.json \
  --operator-seed-review-csv-output data/financial_reports/private/operator_official_seed_review_task107.csv \
  --json-output logs/financial_reports/operator_official_seed_review_template_task107.json \
  --markdown-output logs/financial_reports/operator_official_seed_review_template_task107.md
```

After operator review, promote only approved official seed pages:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode operator-seed-promote-reviewed \
  --operator-seed-review-input data/financial_reports/private/operator_official_seed_review_filled_task107.json \
  --operator-seed-input data/financial_reports/private/operator_official_seed_task104.json \
  --required-company-ids 18,67 \
  --operator-seed-output data/financial_reports/private/operator_official_seed_promoted_task107.json \
  --operator-seed-csv-output data/financial_reports/private/operator_official_seed_promoted_task107.csv \
  --run-operator-seed-validate true \
  --operator-seed-validation-json-output logs/financial_reports/operator_official_seed_validation_promoted_task107.json \
  --operator-seed-validation-markdown-output logs/financial_reports/operator_official_seed_validation_promoted_task107.md \
  --json-output logs/financial_reports/operator_official_seed_promote_task107.json \
  --markdown-output logs/financial_reports/operator_official_seed_promote_task107.md
```

Then use the promoted seed file with `official-seed-resolve` and the existing
candidate discovery / document quality gate flow. Approved seed pages are not
exact report evidence, and they do not make `ready_for_value_extraction` true.
Pending, rejected, unknown, blocked, or financial-value-bearing review rows are
not promoted.

## Exact Document Discovery From Reviewed Seeds

Task 108 starts from the reviewed seed pack and proposes exact official report
document candidates. It stays read-only and metadata-only: no identity rows,
reports, scores, predictions, schedules, paper state, OCR output, parsed tables,
or financial values are changed.

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode exact-document-discover-from-seeds \
  --seed-input data/financial_reports/private/official_seed_pack_task107.json \
  --document-intake-input data/financial_reports/private/exact_document_intake_task99.json \
  --required-company-ids 18,67 \
  --report-period 2025 \
  --report-type annual \
  --accounting-standard IFRS \
  --exact-document-candidate-output data/financial_reports/private/exact_document_candidates_from_seeds_task108.json \
  --exact-document-candidate-csv-output data/financial_reports/private/exact_document_candidates_from_seeds_task108.csv \
  --run-document-intake-fill true \
  --document-intake-output data/financial_reports/private/exact_document_intake_filled_task108.json \
  --document-intake-csv-output data/financial_reports/private/exact_document_intake_filled_task108.csv \
  --run-document-intake-validate true \
  --document-intake-validation-json-output logs/financial_reports/exact_document_intake_validation_task108.json \
  --document-intake-validation-markdown-output logs/financial_reports/exact_document_intake_validation_task108.md \
  --run-document-quality-gate true \
  --quality-gate-json-output logs/financial_reports/exact_document_quality_gate_task108.json \
  --quality-gate-markdown-output logs/financial_reports/exact_document_quality_gate_task108.md \
  --json-output logs/financial_reports/exact_document_discover_from_seeds_task108.json \
  --markdown-output logs/financial_reports/exact_document_discover_from_seeds_task108.md
```

Reviewed seed pages are navigation sources only. Exact document candidates must
still pass document intake validation and the strict quality gate. If required
issuers are missing exact reviewed documents, `ready_for_value_extraction`
remains `false`.

## Exact Document Discovery: Second-Level Crawl and Legal PDF Filter

Task 109 refines the same `exact-document-discover-from-seeds` command. The
mode classifies every link by `document_kind`, filters privacy/cookie/user
agreement/legal-policy PDFs, and follows only official same-domain reporting
category pages within a fixed depth and page budget.

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode exact-document-discover-from-seeds \
  --seed-input data/financial_reports/private/official_seed_pack_task107.json \
  --document-intake-input data/financial_reports/private/exact_document_intake_task99.json \
  --required-company-ids 18,67 \
  --report-period 2025 \
  --report-type annual \
  --accounting-standard IFRS \
  --exact-document-second-level-crawl true \
  --exact-document-max-crawl-depth 2 \
  --exact-document-filter-legal-documents true \
  --exact-document-filter-policy-documents true \
  --exact-document-filter-generic-pdfs true \
  --exact-document-include-category-pages false \
  --exact-document-candidate-output data/financial_reports/private/exact_document_candidates_from_seeds_task109.json \
  --exact-document-candidate-csv-output data/financial_reports/private/exact_document_candidates_from_seeds_task109.csv \
  --run-document-intake-fill true \
  --document-intake-output data/financial_reports/private/exact_document_intake_filled_task109.json \
  --document-intake-csv-output data/financial_reports/private/exact_document_intake_filled_task109.csv \
  --run-document-intake-validate true \
  --document-intake-validation-json-output logs/financial_reports/exact_document_intake_validation_task109.json \
  --document-intake-validation-markdown-output logs/financial_reports/exact_document_intake_validation_task109.md \
  --run-document-quality-gate true \
  --quality-gate-json-output logs/financial_reports/exact_document_quality_gate_task109.json \
  --quality-gate-markdown-output logs/financial_reports/exact_document_quality_gate_task109.md \
  --json-output logs/financial_reports/exact_document_discover_from_seeds_task109.json \
  --markdown-output logs/financial_reports/exact_document_discover_from_seeds_task109.md
```

Privacy/cookie/user-agreement/legal PDFs are never exact report documents.
Category/reporting pages can be followed to discover exact annual IFRS/МСФО
report documents, but category pages do not pass the document quality gate by
themselves. The workflow remains metadata-only: no PDF parsing, OCR, financial
value extraction, report import, trading, identity mutation, score mutation, or
schedule mutation.

## Exact Document Discovery: Strict Period and Report-Type Gate

Task 110 adds target-period, annual-report, and accounting-standard checks to
`exact-document-discover-from-seeds`. For a 2025 annual IFRS request, old IFRS
files, half-year/interim/quarterly files, and RAS/РСБУ files stay diagnostic
only and cannot be passed into document intake.

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode exact-document-discover-from-seeds \
  --seed-input data/financial_reports/private/official_seed_pack_task107.json \
  --document-intake-input data/financial_reports/private/exact_document_intake_task99.json \
  --required-company-ids 18,67 \
  --report-period 2025 \
  --report-type annual \
  --accounting-standard IFRS \
  --exact-document-second-level-crawl true \
  --exact-document-max-crawl-depth 2 \
  --exact-document-filter-legal-documents true \
  --exact-document-filter-policy-documents true \
  --exact-document-filter-generic-pdfs true \
  --exact-document-filter-wrong-period true \
  --exact-document-filter-interim-for-annual true \
  --exact-document-filter-wrong-report-type true \
  --exact-document-filter-wrong-standard true \
  --exact-document-period-policy target-only \
  --exact-document-target-period-required true \
  --exact-document-allow-prior-year-fallback false \
  --exact-document-include-category-pages false \
  --exact-document-candidate-output data/financial_reports/private/exact_document_candidates_from_seeds_task110.json \
  --exact-document-candidate-csv-output data/financial_reports/private/exact_document_candidates_from_seeds_task110.csv \
  --run-document-intake-fill true \
  --document-intake-output data/financial_reports/private/exact_document_intake_filled_task110.json \
  --document-intake-csv-output data/financial_reports/private/exact_document_intake_filled_task110.csv \
  --run-document-intake-validate true \
  --document-intake-validation-json-output logs/financial_reports/exact_document_intake_validation_task110.json \
  --document-intake-validation-markdown-output logs/financial_reports/exact_document_intake_validation_task110.md \
  --run-document-quality-gate true \
  --quality-gate-json-output logs/financial_reports/exact_document_quality_gate_task110.json \
  --quality-gate-markdown-output logs/financial_reports/exact_document_quality_gate_task110.md \
  --json-output logs/financial_reports/exact_document_discover_from_seeds_task110.json \
  --markdown-output logs/financial_reports/exact_document_discover_from_seeds_task110.md
```

Wrong-year documents are diagnostics, not exact target-period evidence.
Interim, half-year, and quarterly reports do not satisfy annual report requests.
Prior-year fallback is disabled by default and, if explicitly enabled, remains
operator-review-only support that cannot pass the strict target-period quality
gate.

## Target Reporting Period Availability Policy

Task 111 adds a diagnostics-only availability policy to the reviewed-seed exact
document discovery report. It explains why the strict target-period annual IFRS
gate remains closed without accepting older, interim, RAS/РСБУ, ambiguous, or
placeholder documents as evidence.

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode exact-document-discover-from-seeds \
  --seed-input data/financial_reports/private/official_seed_pack_task107.json \
  --document-intake-input data/financial_reports/private/exact_document_intake_task99.json \
  --required-company-ids 18,67 \
  --report-period 2025 \
  --report-type annual \
  --accounting-standard IFRS \
  --exact-document-period-policy target-only \
  --exact-document-target-period-required true \
  --exact-document-availability-policy-name annual_ifrs_grace_window \
  --exact-document-annual-ifrs-grace-days 180 \
  --exact-document-availability-current-date 2026-05-25 \
  --json-output logs/financial_reports/exact_document_discover_from_seeds_task111.json \
  --markdown-output logs/financial_reports/exact_document_discover_from_seeds_task111.md
```

Each required issuer gets a `target_reporting_period_availability` row with the
target period, required report type, required standard, reason codes, counts for
exact target documents, historical annual IFRS documents, interim/quarterly
documents, wrong-standard documents, placeholders, and operator-review-required
candidates. The default annual IFRS window is 180 days after December 31 of the
target period, and `--exact-document-availability-current-date` makes smoke
replays and tests deterministic.

Historical reports are visible only as `diagnostic_only` fallback metadata.
They do not pass the target-period quality gate and cannot make
`ready_for_value_extraction` or `ready_for_import` true.

## VDS Smoke Checklist

Health:

```bash
curl -i http://127.0.0.1:8000/api/health
```

Diagnostics:

```bash
curl -sS "http://127.0.0.1:8000/api/companies/identity/diagnostics?active_only=true&limit=20" \
  -o logs/issuer_identity/diagnostics_task81_vds.json
```

Batch rehearsal target/template generation only:

```bash
python3 scripts/issuer_identity_batch_rehearsal.py \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --model-run-id 2 \
  --as-of-date 2026-05-19 \
  --limit 20 \
  --review-template-output logs/issuer_identity/priority_identity_review_template_task81_vds.csv \
  --json-output logs/issuer_identity/batch_rehearsal_task81_vds.json \
  --markdown-output logs/issuer_identity/batch_rehearsal_task81_vds.md
```

Confirm no schedule was activated:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
select
  id,
  name,
  status,
  use_current_date_as_of_date,
  next_run_at,
  last_run_at,
  last_cycle_run_id,
  run_count
from paper_live_schedules
order by id desc
limit 5;
"
```

Expected:

```text
/api/health = 200 OK
identity diagnostics returns JSON
batch rehearsal returns warning/passed, not failed
review template file is generated
paper schedule remains paused
identity apply is not executed
```

## VDS Duplicate Smoke Checklist

Health:

```bash
curl -i http://127.0.0.1:8000/api/health
```

Duplicate diagnostics:

```bash
curl -sS "http://127.0.0.1:8000/api/companies/identity/duplicates/diagnostics?active_only=true&limit=20&min_score=0.50" \
  -o logs/issuer_identity/duplicates_diagnostics_task82_vds.json
```

Export duplicate candidates:

```bash
python3 scripts/issuer_identity_duplicate_export.py \
  --backend-url http://127.0.0.1:8000 \
  --limit 20 \
  --min-score 0.50 \
  --json-output logs/issuer_identity/duplicate_candidates_task82_vds.json \
  --csv-output logs/issuer_identity/duplicate_candidates_task82_vds.csv \
  --markdown-output logs/issuer_identity/duplicate_candidates_task82_vds.md
```

Confirm the schedule remains paused:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
select id, name, status, use_current_date_as_of_date, next_run_at, last_run_at, last_cycle_run_id, run_count
from paper_live_schedules
order by id desc
limit 5;
"
```

Expected:

```text
health = 200 OK
duplicate diagnostics returns JSON
duplicate export writes reports
no apply executed
schedule remains paused
```

## VDS Canonical Rollup Smoke Checklist

Health:

```bash
curl -i http://127.0.0.1:8000/api/health
```

Identity targets with duplicate rollup:

```bash
python3 scripts/issuer_identity_target_export.py \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --model-run-id 2 \
  --as-of-date 2026-05-19 \
  --limit 50 \
  --use-duplicate-mapping \
  --rollup-duplicates \
  --include-duplicate-members \
  --compare-rollup \
  --json-output logs/issuer_identity/canonical_identity_targets_task83_vds.json \
  --csv-output logs/issuer_identity/canonical_identity_targets_task83_vds.csv \
  --markdown-output logs/issuer_identity/canonical_identity_targets_task83_vds.md
```

Schedule safety:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
select id, name, status, use_current_date_as_of_date, next_run_at, last_run_at, last_cycle_run_id, run_count
from paper_live_schedules
order by id desc
limit 5;
"
```

Expected:

```text
health = 200 OK
canonical identity target export is written
accepted duplicate members appear under canonical issuers
no apply executed
schedule remains paused
```
