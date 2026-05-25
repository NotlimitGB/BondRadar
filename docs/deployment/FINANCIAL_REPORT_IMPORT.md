# BondRadar Financial Report Import

This guide describes the controlled operator workflow for importing company
financial reports into BondRadar. It is file-based, explicit, and auditable.

It does not scrape external websites, does not run paper execution, does not
activate schedules, and does not deploy anything.

## Supported File Formats

Use CSV or JSON with these fields:

```text
company_id
company_ticker
company_inn
period_year
period_quarter
period_start_date
period_end_date
published_at
document_date
currency
source
source_url
source_file_name
report_type
revenue
ebitda
net_debt
total_debt
cash
equity
short_term_debt
operating_cash_flow
net_profit
interest_expense
debt_to_ebitda
interest_coverage
```

At least one company identifier is required:

- `company_id`;
- `company_ticker`;
- `company_inn`.

`period_quarter=0` means annual report. This follows the existing project
convention where annual reports have priority over Q4 when both are available
for the same period.

Recommended source values for operator imports:

- `operator_csv`;
- `operator_json`;
- `issuer_manual`.

Money values should be normalized to RUB unless `currency` is explicitly
different. Decimal strings are accepted. Missing values should be empty or
`null`, not fake zero values.

Use `source_url` for the original issuer or report page when available. Use
`source_file_name` for the local or original report file name when available.

Synthetic examples live in:

```text
docs/examples/financial_reports/
```

The small example files are synthetic workflow fixtures, not real issuer
financials.

Before collecting real financial reports, review issuer identity:

```text
docs/deployment/ISSUER_IDENTITY_ENRICHMENT.md
```

Financial reports should not be linked to generated names such as `Unknown
issuer for RU...` without identity review.

## Dry-run Validation

Run local validation without calling the ingest endpoint:

```bash
python scripts/financial_report_import.py \
  --input docs/examples/financial_reports/financial_reports_example_small.csv \
  --format csv \
  --source operator_csv \
  --backend-url http://127.0.0.1:8000 \
  --dry-run \
  --json-output logs/financial_reports/import_dry_run.json \
  --markdown-output logs/financial_reports/import_dry_run.md
```

To also preview company matching through the backend without mutating the
database:

```bash
python scripts/financial_report_import.py \
  --input ./data/financial_reports/company_reports_2025.csv \
  --format csv \
  --source operator_csv \
  --backend-url http://127.0.0.1:8000 \
  --dry-run \
  --validate-companies \
  --json-output logs/financial_reports/import_preview.json \
  --markdown-output logs/financial_reports/import_preview.md
```

The preview endpoint is:

```text
POST /api/financial-reports/preview
```

It accepts the same payload shape as `POST /api/financial-reports/ingest` and
returns row-level company matching, likely action, warnings, and errors.

## Confirmed Import

Real import is non-default and requires explicit confirmation:

```bash
python scripts/financial_report_import.py \
  --input ./data/financial_reports/company_reports_2025.csv \
  --format csv \
  --source operator_csv \
  --backend-url http://127.0.0.1:8000 \
  --execute yes \
  --confirm-import yes \
  --rebuild-existing \
  --validate-companies \
  --json-output logs/financial_reports/import_run.json \
  --markdown-output logs/financial_reports/import_run.md
```

The import records:

- `financial_reports`;
- `financial_report_source_documents`;
- `financial_report_import_runs`.

Inspect import runs:

```bash
curl -s http://127.0.0.1:8000/api/financial-reports/import-runs
curl -s http://127.0.0.1:8000/api/financial-reports/source-documents
```

## Coverage Before And After

Use the rehearsal script to capture coverage before and after the import flow:

```bash
python scripts/financial_report_import_rehearsal.py \
  --input ./data/financial_reports/company_reports_2025.csv \
  --format csv \
  --source operator_csv \
  --backend-url http://127.0.0.1:8000 \
  --as-of-date 2026-05-19 \
  --stale-after-days 540 \
  --execute no \
  --json-output logs/financial_reports/rehearsal.json \
  --markdown-output logs/financial_reports/rehearsal.md
```

For a confirmed import through the rehearsal script:

```bash
python scripts/financial_report_import_rehearsal.py \
  --input ./data/financial_reports/company_reports_2025.csv \
  --format csv \
  --source operator_csv \
  --backend-url http://127.0.0.1:8000 \
  --as-of-date 2026-05-19 \
  --execute yes \
  --confirm-import yes \
  --json-output logs/financial_reports/rehearsal_execute.json \
  --markdown-output logs/financial_reports/rehearsal_execute.md
```

Coverage endpoint:

```bash
curl -s "http://127.0.0.1:8000/api/data-readiness/financial-reports/coverage?as_of_date=2026-05-19&active_only=true&stale_after_days=540"
```

Feature ratio coverage remains low until feature snapshots are rebuilt after
the reports are imported.

## Post-Ingest Rebuild Plan

Render the post-ingest plan:

```bash
python scripts/financial_report_post_ingest_rebuild.py \
  --backend-url http://127.0.0.1:8000 \
  --as-of-date-from 2026-05-13 \
  --as-of-date-to 2026-05-19 \
  --dry-run \
  --json-output logs/financial_reports/post_ingest_rebuild_plan.json \
  --markdown-output logs/financial_reports/post_ingest_rebuild_plan.md
```

Recommended sequence:

```text
1. coverage before
2. ingest reports
3. rebuild company credit health snapshots
4. rebuild bond risk assessments
5. rebuild feature snapshots
6. optionally retrain model
7. optionally regenerate predictions
8. run quality gate / readiness
9. only then consider paper dry-run
```

Keep paper schedules paused until coverage, rebuild, readiness, and manual
review are complete. Risk override remains paper-only, explicit, and guarded.

## Post-Import Financial Report Diagnostics

After a report is imported, run read-only diagnostics before any rebuild or
paper workflow. Diagnostics explain which report is selected, which raw fields
are present, which derived metrics can be computed, and why scoring readiness
may still be partial.

For the first TMK canonical report:

```bash
python3 scripts/financial_report_diagnostics.py \
  --backend-url http://127.0.0.1:8000 \
  --company-ids 125 \
  --json-output logs/financial_reports/tmk_financial_report_diagnostics_task90.json \
  --markdown-output logs/financial_reports/tmk_financial_report_diagnostics_task90.md
```

Expected state for the first TMK diagnostics:

```text
has_financial_report = true
latest_report_period_year = 2025
latest_report_period_quarter = 0
signal = insufficient_data
safe_for_feature_pipeline = true
safe_for_risk_scoring = false
risk_scoring_readiness = partial
missing fields include interest_expense and net_debt
fallback net_debt can be computed as total_debt - cash
```

`covered_by_canonical` means collection coverage is satisfied by a canonical
issuer report. `insufficient_data` means the report exists but still lacks
some fields for stronger scoring or risk readiness. These are different
concepts.

The diagnostics script is read-only:

```text
import_executed = false
paper_trading_called = false
read_only = true
paper_schedule_status = not_checked
```

Keep schedule safety as a direct VDS SQL check, not as a diagnostics API call:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
select id, name, status, use_current_date_as_of_date, next_run_at, last_run_at, last_cycle_run_id, run_count
from paper_live_schedules
order by id desc
limit 5;
"
```

## Financial Scoring Preview / Dry Run

After diagnostics, run a read-only financial scoring preview to see which
financial factors would matter for scoring and risk review. This preview does
not mutate company scores, bond scores, predictions, feature snapshots,
financial reports, paper portfolios, or schedules.

The concepts are separate:

```text
coverage_effective_status = covered_by_canonical
means the issuer has a collected canonical report.

risk_scoring_readiness = partial
means the report is visible but lacks fields for full financial-aware risk scoring.

financial scoring preview
shows suggested risk factors but does not mutate scores.
```

Run the Task 91 preview for TMK:

```bash
python3 scripts/financial_scoring_preview.py \
  --backend-url http://127.0.0.1:8000 \
  --company-ids 125 \
  --json-output logs/financial_reports/tmk_financial_scoring_preview_task91_vds.json \
  --markdown-output logs/financial_reports/tmk_financial_scoring_preview_task91_vds.md
```

Expected TMK preview:

```text
has_financial_report = true
risk_scoring_readiness = partial
gross_debt_to_ebitda severity = high
net_debt_to_ebitda_fallback severity = elevated
interest_coverage missing because interest_expense is missing
risk_penalty_points = 0
score_adjustment_points = 0
dry_run_only = true
```

The preview endpoint is:

```text
GET /api/financial-reports/scoring-preview/company/{company_id}
```

Optional query flags:

```text
include_diagnostics=true|false
include_bond_context=true|false
```

## Batch Financial Scoring Preview

Use the batch preview before enabling any financial-aware scoring effects. This
is an observability report only:

```text
This is not production scoring.
This does not mutate bond scores.
This does not mutate company scores.
This does not mutate predictions.
This does not retrain ML.
This does not trigger paper trading.
All suggested score/risk adjustments remain 0 and labelled preview_only.
```

Run a safe target-issuer batch preview:

```bash
python3 scripts/financial_scoring_preview.py \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --model-run-id 2 \
  --as-of-date 2026-05-19 \
  --limit 50 \
  --use-duplicate-mapping \
  --rollup-duplicates \
  --include-duplicate-members \
  --json-output logs/financial_reports/batch_financial_scoring_preview_task92_vds.json \
  --markdown-output logs/financial_reports/batch_financial_scoring_preview_task92_vds.md
```

For this script, `--source mixed` is intentionally limited to safe target
sources: `top-predictions` plus `bond-universe`. It does not use paper
positions and does not call paper-trading endpoints.

The batch report includes:

```text
has_report_count
missing_report_count
ready_count
partial_count
not_ready_count
negative_factor_count
fallback_metric_company_count
preview_only_adjustment_count
missing_fields_summary
risk_factor_summary
top_negative_preview_companies
```

Expected current production state:

```text
financial_reports_count = 1
read_only = true
dry_run_only = true
import_executed = false
paper_trading_called = false
preview_only_adjustment_count = 0
```

Only TMK currently has a real imported official-source report. Most target
issuers are expected to be missing_report / not_ready until more official
financial reports are imported. If TMK is included in the selected targets, it
should appear as partial because interest_expense and net_debt are missing and
fallback net debt metrics are used only for preview.

The batch endpoint is:

```text
POST /api/financial-reports/scoring-preview/batch
```

Request shape:

```json
{
  "company_ids": [125],
  "include_diagnostics": true,
  "include_bond_context": true
}
```

## Financial Report Collection Priority Queue

Use the collection priority queue to choose which issuer financial reports to
collect next. The queue is a read-only planning report:

```text
This queue chooses which issuer financial reports to collect next.
It is not an importer.
It is not production scoring.
It does not mutate scores, predictions, reports, schedules, or paper trading.
Corporate issuers are prioritized.
OFZ/government-like issuers are excluded or heavily de-prioritized.
```

Run the Task 93 queue on VDS:

```bash
python3 scripts/financial_collection_priority_queue.py \
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
  --json-output logs/financial_reports/collection_priority_queue_task93_vds.json \
  --markdown-output logs/financial_reports/collection_priority_queue_task93_vds.md
```

For this script, `--source mixed` uses only the safe target sources
`top-predictions` and `bond-universe`; it does not call paper-trading
endpoints.

Expected current state:

```text
financial_reports_count = 1
read_only = true
dry_run_only = true
import_executed = false
paper_trading_called = false
would_mutate_scores = false
would_trigger_paper_trading = false
```

Only TMK currently has a report. TMK should appear in
`already_covered` with `risk_scoring_readiness = partial` and next fields such
as `interest_expense` and `net_debt`. Most other target issuers are expected to
be missing reports and ranked in `priority_queue`. OFZ/government-like issuers
should appear in `excluded_or_deprioritized` when
`--exclude-government-like` is used.

The priority endpoint is:

```text
POST /api/financial-reports/collection-priority/batch
```

Request shape:

```json
{
  "company_ids": [125],
  "source_presence": {
    "125": ["manual-id"]
  },
  "include_covered": true,
  "exclude_government_like": true
}
```

## Identity-First Financial Collection Queue

Use the identity-first queue after the collection priority queue. The priority
queue ranks collection value; the identity-first queue decides whether a ranked
issuer is safe for official financial report collection now or needs identity
review first.

```text
Known corporate issuers go to collection_ready.
Unknown issuer rows go to identity_review_required.
Government-like issuers remain excluded/deprioritized.
Already covered issuers like TMK stay in already_covered with missing fields.
```

Run the Task 94 identity-first queue on VDS:

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

For this script, `--source mixed` uses only `top-predictions` and
`bond-universe`. It does not use paper positions and does not call paper
trading endpoints.

Expected current state:

```text
financial_reports_count = 1
read_only = true
dry_run_only = true
import_executed = false
identity_apply_executed = false
paper_trading_called = false
would_mutate_scores = false
would_trigger_paper_trading = false
```

Known corporate issuers such as RZD or Mostotrest should appear in
`collection_ready` only when identity evidence is strong enough: non-generated
issuer name, corporate classification, matched/verified identity or legal name
plus INN/OGRN. Generated `Unknown issuer for ...` rows should appear in
`identity_review_required` and be exported to the identity review CSV before
any financial report collection starts. TMK should remain in `already_covered`
with next fields such as `interest_expense` and `net_debt`.

The identity-first endpoint is:

```text
POST /api/financial-reports/identity-first-collection/batch
```

Request shape:

```json
{
  "company_ids": [18, 67, 125],
  "source_presence": {
    "18": ["top-predictions", "bond-universe"],
    "67": ["top-predictions", "bond-universe"],
    "125": ["manual-id"]
  },
  "include_covered": true,
  "exclude_government_like": true
}
```

## Official-Source Collection Pack for Collection-Ready Issuers

The identity-first queue decides who is safe to collect. The official-source
collection pack creates operator templates and checklists for those issuers.
It does not invent financial values and does not import anything.

Real financial values must come from official issuer reports, official
disclosure systems, exchange disclosure, or auditor reports. Preview must pass
before any future import task.

Run the Task 95 template pack on VDS:

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

Run preview only after the operator fills official-source values:

```bash
python3 scripts/financial_official_collection_pack.py \
  --mode preview \
  --backend-url http://127.0.0.1:8000 \
  --reviewed-input data/financial_reports/private/collection_ready_financial_filled_task95.csv \
  --format csv \
  --json-output logs/financial_reports/official_collection_preview_task95.json \
  --markdown-output logs/financial_reports/official_collection_preview_task95.md
```

Expected current state:

```text
selected issuers include RZD and Mostotrest
Unknown issuer rows are excluded
TMK is excluded by default because it is already covered/partial
financial values are empty in template mode
source URLs are empty/operator_to_find in template mode
read_only = true
dry_run_only = true
import_executed = false
identity_apply_executed = false
paper_trading_called = false
would_mutate_scores = false
would_trigger_paper_trading = false
```

Strategic note: future live BondRadar cycles should refresh market, risk,
paper, and readiness state every 1-2 hours while the exchange is open, not
once per day. This task does not implement or activate that scheduler.

## Official-Source Evidence Assistant

After the official-source collection pack creates empty templates, use the
evidence assistant to collect and validate official source candidates, then
build a preview-only candidate file. This workflow prepares evidence-backed
rows only; it does not import reports, mutate the database, or trade.

Allowed value sources are official issuer reports, official disclosure systems,
exchange disclosure, and auditor reports. Do not use Wikipedia, blogs, forums,
social media, random aggregators, coupon schedules as interest expense, market
capitalization as equity, or placeholder zeros as financial values.

Create source intake from the Task 95 pack:

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

Validate filled source intake before adding values:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode source-validate \
  --source-intake-input data/financial_reports/private/official_source_intake_task96.json \
  --json-output logs/financial_reports/official_source_validation_task96.json \
  --markdown-output logs/financial_reports/official_source_validation_task96.md
```

Fill candidate rows only from evidence-backed manual values:

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

Run preview only. This calls the read-only preview endpoint and never calls
ingest/apply:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode preview \
  --backend-url http://127.0.0.1:8000 \
  --candidate-input data/financial_reports/private/collection_ready_financial_candidate_task96.csv \
  --format csv \
  --json-output logs/financial_reports/official_source_candidate_preview_task96.json \
  --markdown-output logs/financial_reports/official_source_candidate_preview_task96.md
```

Task 96 prepares and validates evidence-backed candidate values. It does not
import reports, mutate identities, change scores or predictions, activate
schedules, or run paper trading. Keep filled private CSV/JSON values outside
git-tracked paths.

## Official-Source Discovery

Use source discovery after Task 96 source-template when the intake file has
empty URLs. This step is source-only: it may add official-looking issuer or
disclosure landing page candidates, but it does not approve them as financial
value evidence and does not extract numbers.

Discover official source candidates:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode source-discover \
  --source-intake-input data/financial_reports/private/official_source_intake_task96.json \
  --source-intake-output data/financial_reports/private/official_source_intake_discovered_task97.json \
  --json-output logs/financial_reports/official_source_discovery_task97.json \
  --markdown-output logs/financial_reports/official_source_discovery_task97.md
```

Validate discovered candidates:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode source-validate \
  --source-intake-input data/financial_reports/private/official_source_intake_discovered_task97.json \
  --json-output logs/financial_reports/official_source_validation_discovered_task97.json \
  --markdown-output logs/financial_reports/official_source_validation_discovered_task97.md
```

`needs_operator_review` and `discovered_candidate` mean the source is useful for
manual navigation only. Exact official annual/audited report URL, document
title, and evidence notes are still required before `candidate-fill`. The
workflow blocks Wikipedia/wiki, blogs, forums, social media, news/aggregator
sources, and does not call import/apply endpoints.

## Exact Official Report Document Resolver

After source discovery, resolve exact official report document metadata before
entering any financial values. This step handles report URL/title/date/file
metadata only. It does not extract values, OCR PDFs, parse report tables, import
reports, or trade.

Resolve document candidates from discovered source intake:

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

Validate exact official report documents:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode document-validate \
  --document-input data/financial_reports/private/official_report_documents_task98.json \
  --json-output logs/financial_reports/official_report_document_validation_task98.json \
  --markdown-output logs/financial_reports/official_report_document_validation_task98.md
```

Landing pages and disclosure homepages are navigation aids, not exact report
evidence. Candidate-fill still requires an exact official annual/audited report
page or PDF URL, document title, report period, source type, and operator review.
Unknown domains remain review-only even with `--allow-unknown-source`.

## Operator Exact Document Intake

Task 99 creates and validates an operator-fillable exact document intake file.
This is still document metadata only: no financial values are entered, no PDFs are
OCRed or parsed, no reports are imported, and no trading or scoring state is
changed.

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

Validate a filled exact document intake:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode document-intake-validate \
  --document-intake-input data/financial_reports/private/exact_document_intake_filled_task99.json \
  --json-output logs/financial_reports/exact_document_intake_validation_task99.json \
  --markdown-output logs/financial_reports/exact_document_intake_validation_task99.md
```

Resolve documents using reviewed exact intake:

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

The template intentionally leaves `document_url`, `document_title`, document
date, and file name empty. Operators must paste exact official annual/audited
report page or PDF metadata, mark the row reviewed, and validate it before any
future candidate-fill step.

## Exact Official Document Intake Fill

Task 100 fills exact document metadata from a private reviewed candidate file.
It does not discover the open web, extract financial values, OCR or parse PDFs,
import reports, or trade. If no reviewed exact candidate file is supplied, the
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
state, or trade.

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
extract financial values, import reports, mutate state, or trade. Uncertain
links remain operator-review candidates and must still pass the exact document
quality gate before any value collection.

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
It uses only existing local official-source data, optional Task 95 identity
fields, and optional operator-reviewed seed files. It does not invent exact
report URLs, extract values, import reports, mutate state, or trade.

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

The seed pack may include issuer home, investor/reporting, disclosure home, and
operator-reviewed disclosure profile seeds. Generated issuer paths such as
`/investors/` or `/reports/` are navigation seeds only; they are not exact
document evidence and cannot make the quality gate pass by themselves.

## Operator Official Seed Intake

Task 104 adds an operator-fillable seed intake for exact official navigation
pages: issuer investor/reporting pages, official disclosure profile/report
pages, and official-like exchange issuer pages. This improves navigation for
candidate discovery only. It does not extract values, import reports, mutate
identity data, score bonds, activate schedules, or trade. The exact document
quality gate remains strict.

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

Operator seed intake accepts only official seed metadata. Do not paste financial
figures, OCR output, parsed table values, search results, news, blogs, forums,
social pages, or random aggregators. Unknown domains remain review-only even
with `--allow-unknown-source` and cannot become high-confidence valid seeds.

## Operator Official Seed Candidate Helper

Task 105 proposes official seed candidates for the Task 104 operator seed
template. It scans only allowlisted official seed/source pages, writes candidate
metadata, can optionally autofill high-confidence rows, and can run
`operator-seed-validate` on the autofill file. It does not extract financial
values, import reports, mutate state, or trade. Candidate seeds do not bypass
the exact document quality gate.

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

The helper must not use broad search results or invent e-disclosure profile IDs.
If no high-confidence official candidates are found, autofill keeps rows empty
and validation fails safely until an operator fills reviewed seed URLs.

## Operator Seed Candidate Ranking and Noise Filter

Task 106 keeps the candidate helper operator-friendly by ranking seed-page
candidates and filtering noisy official-site navigation. Official domain alone
is not enough: investor, reporting, annual-report, disclosure, profile, or
financial-results signals must appear in the candidate title or path. Passenger,
ticket, train, station, contact, history, generic activity, project, search,
news, blog, forum, and social pages are filtered out by default.

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

Use `--operator-seed-candidate-include-filtered true` only for diagnostics.
Filtered rows include `filter_status`, `filter_reasons`, `raw_score`, and
`final_score`. Autofill remains conservative and capped; filtered/noise rows
never enter autofill. Candidate seeds still do not extract values, import
reports, trade, or bypass the exact document quality gate.

## Operator Seed Review Promotion

Task 107 lets an operator approve ranked official seed candidates without hand
editing JSON seed files. The review step creates a private checklist from Task
106 candidates. The promotion step converts only explicit `approve` decisions
into reviewed operator seed rows. It still approves navigation seed pages only:
no values are extracted, no reports are imported, no trades run, and
`ready_for_value_extraction` remains controlled by the exact document quality
gate.

Create the review checklist:

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

The operator edits the private review file and sets `operator_decision =
approve` only for verified official issuer/disclosure/reporting seed pages.
RZD or any other unresolved issuer should remain `pending` unless an official
reviewed URL is provided.

Promote approved rows and validate the promoted seed file:

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

Feed reviewed seeds into the existing strict resolver and gate:

```bash
python3 scripts/financial_official_source_evidence_assistant.py \
  --mode official-seed-resolve \
  --document-intake-input data/financial_reports/private/exact_document_intake_task99.json \
  --source-intake-input data/financial_reports/private/official_source_intake_discovered_task97.json \
  --document-input data/financial_reports/private/official_report_documents_task98.json \
  --financial-template-input data/financial_reports/private/collection_ready_financial_template_task95.csv \
  --operator-seed-input data/financial_reports/private/operator_official_seed_promoted_task107.json \
  --required-company-ids 18,67 \
  --seed-output data/financial_reports/private/official_seed_pack_task107.json \
  --seed-csv-output data/financial_reports/private/official_seed_pack_task107.csv \
  --run-candidate-discovery true \
  --candidate-output data/financial_reports/private/exact_document_candidates_task107.json \
  --candidate-csv-output data/financial_reports/private/exact_document_candidates_task107.csv \
  --run-quality-gate true \
  --quality-gate-json-output logs/financial_reports/exact_document_quality_gate_task107.json \
  --quality-gate-markdown-output logs/financial_reports/exact_document_quality_gate_task107.md \
  --json-output logs/financial_reports/official_seed_resolve_task107.json \
  --markdown-output logs/financial_reports/official_seed_resolve_task107.md
```

Pending, rejected, missing, blocked, unknown, or financial-value-bearing review
rows are not promoted. Unknown domains cannot become reviewed seeds even with
`--allow-unknown-source`.

## Exact Document Discovery From Reviewed Seeds

Task 108 uses reviewed official seed pages to discover exact official report
document candidates. It scans only allowlisted reviewed seed pages, extracts
HTML anchors, scores annual IFRS/consolidated/audited report links, and filters
presentations, news, prospectuses, quarterly documents, and generic navigation
pages. It does not parse PDFs, OCR documents, extract financial values, import
reports, trade, or change readiness outside the strict document quality gate.

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

The output candidate JSON/CSV is compatible with `document-intake-fill`.
`--exact-document-probe-urls` and `--exact-document-download-documents` are
disabled by default. If downloads are enabled, save only to private/ignored
directories and never commit downloaded documents. If one required issuer is
still unresolved, the quality gate must fail and `ready_for_value_extraction`
must remain `false`.

## Exact Document Discovery: Second-Level Crawl and Legal PDF Filter

Task 109 keeps the same `exact-document-discover-from-seeds` mode, but adds a
document-kind classifier and a controlled second-level crawl from official
reporting category pages. Privacy, cookie, user-agreement, legal-policy,
presentation, prospectus, quarterly/interim, news, and generic navigation links
are diagnostics only. They are never exact report evidence and never flow into
`document-intake-fill`.

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

Category pages such as annual reports, accounting statements, disclosure
reports, and financial-results pages can be followed when they are official,
same-domain, score above the category threshold, and stay within the configured
depth/page limits. Category pages do not pass the quality gate by themselves.
Only `document_kind = exact_report_document` with `filter_status = kept` can be
fed to document intake. The mode still does not parse PDFs, OCR documents,
extract values, import reports, trade, or mutate any database state.

## First Real Data Pack Workflow

Real issuer data should be collected by the operator from official reports and
stored outside git-tracked example files.

Use these ignored local directories:

```text
data/financial_reports/private/
data/financial_reports/staging/
```

The first data pack should be small and reviewed. Do not invent real issuer
values. Do not enter fake zeros.

### 1. Export Target Issuers

```bash
python scripts/financial_report_target_issuers.py \
  --source mixed \
  --backend-url http://127.0.0.1:8000 \
  --limit 50 \
  --json-output logs/financial_reports/target_issuers.json \
  --csv-output logs/financial_reports/target_issuers.csv \
  --markdown-output logs/financial_reports/target_issuers.md
```

After duplicate review, use accepted/reviewed duplicate mapping to avoid
collecting the same issuer report multiple times:

```bash
python scripts/financial_report_target_issuers.py \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --model-run-id 2 \
  --as-of-date 2026-05-19 \
  --limit 50 \
  --use-duplicate-mapping \
  --rollup-duplicates \
  --include-duplicate-members \
  --compare-rollup \
  --json-output logs/financial_reports/canonical_targets_task83.json \
  --csv-output logs/financial_reports/canonical_targets_task83.csv \
  --markdown-output logs/financial_reports/canonical_targets_task83.md \
  --collection-template-output logs/financial_reports/canonical_collection_template_task83.csv
```

Duplicate mapping is export-only. It does not merge companies, move bonds,
change `financial_reports.company_id`, or apply reports to duplicate rows.
Financial reports should usually be collected for the canonical legal issuer.
If a report is attached only to a duplicate candidate, the export reports
`financial_report_attached_to_duplicate_candidate` so the operator can review
the data before rebuilding downstream layers.

If the target export marks `needs_identity_review=true`, run issuer identity
diagnostics and preview first:

```bash
python scripts/issuer_identity_target_export.py \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --limit 50 \
  --json-output logs/issuer_identity/targets.json \
  --csv-output logs/issuer_identity/targets.csv \
  --markdown-output logs/issuer_identity/targets.md
```

Then use `scripts/issuer_identity_import.py` in dry-run mode and apply only
after operator review.

Supported target sources:

- `paper-positions`;
- `top-predictions`;
- `bond-universe`;
- `mixed`.

The export is a collection target list only. It is not an issuer allowlist and
does not change risk logic.

### 2. Fill Collection CSV Manually

Start from:

```text
docs/examples/financial_reports/financial_reports_collection_template.csv
docs/examples/financial_reports/financial_reports_collection_template.json
```

Real working files should live under:

```text
data/financial_reports/staging/
data/financial_reports/private/
```

### 3. Normalize Collection File

```bash
python scripts/financial_report_collection_normalize.py \
  --input data/financial_reports/staging/company_reports_2025_collection.csv \
  --format csv \
  --output logs/financial_reports/normalized_collection.csv \
  --output-format csv \
  --json-report logs/financial_reports/normalized_collection.json \
  --markdown-report logs/financial_reports/normalized_collection.md
```

The normalizer converts `value_scale`:

```text
raw -> unchanged
thousand -> multiply by 1000
million -> multiply by 1000000
billion -> multiply by 1000000000
```

It computes `debt_to_ebitda` and `interest_coverage` only when the ratio is
missing and all required values are present.

### 4. Dry-run Import

```bash
python scripts/financial_report_import.py \
  --input logs/financial_reports/normalized_collection.csv \
  --format csv \
  --source operator_collection \
  --backend-url http://127.0.0.1:8000 \
  --dry-run \
  --json-output logs/financial_reports/import_dry_run_collection.json \
  --markdown-output logs/financial_reports/import_dry_run_collection.md
```

### 5. Preview Backend Matching

```bash
python scripts/financial_report_import.py \
  --input logs/financial_reports/normalized_collection.csv \
  --format csv \
  --source operator_collection \
  --backend-url http://127.0.0.1:8000 \
  --dry-run \
  --validate-companies \
  --json-output logs/financial_reports/import_preview_collection.json \
  --markdown-output logs/financial_reports/import_preview_collection.md
```

### 6. Rehearsal With Coverage

```bash
python scripts/financial_report_data_pack_rehearsal.py \
  --input data/financial_reports/staging/company_reports_2025_collection.csv \
  --format csv \
  --backend-url http://127.0.0.1:8000 \
  --as-of-date 2026-05-19 \
  --normalized-output logs/financial_reports/normalized_data_pack.csv \
  --execute-import no \
  --json-output logs/financial_reports/data_pack_rehearsal.json \
  --markdown-output logs/financial_reports/data_pack_rehearsal.md
```

### 7. Confirmed Import Only After Review

```bash
python scripts/financial_report_data_pack_rehearsal.py \
  --input data/financial_reports/staging/company_reports_2025_collection.csv \
  --format csv \
  --backend-url http://127.0.0.1:8000 \
  --as-of-date 2026-05-19 \
  --normalized-output logs/financial_reports/normalized_data_pack.csv \
  --execute-import yes \
  --confirm-import yes \
  --rebuild-existing \
  --json-output logs/financial_reports/data_pack_import.json \
  --markdown-output logs/financial_reports/data_pack_import.md
```

### 8. Render Post-Ingest Rebuild Plan

```bash
python scripts/financial_report_post_ingest_rebuild.py \
  --backend-url http://127.0.0.1:8000 \
  --as-of-date-from 2026-05-13 \
  --as-of-date-to 2026-05-19 \
  --dry-run \
  --json-output logs/financial_reports/post_ingest_rebuild_plan.json \
  --markdown-output logs/financial_reports/post_ingest_rebuild_plan.md
```

Do not use the imported data in paper pilot review until downstream rebuild and
readiness checks have been reviewed.

## First Canonical Financial Reports Pack

Task 84 introduces a canonical issuer workflow for the first reviewed financial
report pack. It is designed for a small operator-filled data pack and does not
invent, scrape, or import financial values by default.

First canonical target issuers:

```text
18  РЖД
67  Мостотрест
125 ТМК
```

Financial reports must be attached to canonical company IDs only. Do not attach
reports to duplicate candidate company rows such as `Unknown issuer for RU...`.

Safety defaults:

- `targets` and `template` modes do not mutate data;
- `preview` validates, normalizes, and calls preview/dry-run paths only;
- `apply` requires `--execute-import yes --confirm-import yes`;
- `paper-positions` is blocked in this canonical pack script;
- `mixed` uses `top-predictions` and `bond-universe`, not paper endpoints;
- non-official sources are blocked in `apply` unless
  `--allow-non-official-source` is explicitly passed.

### Laptop Setup

From a fresh laptop clone:

```bash
git pull
python -m venv .venv
source .venv/Scripts/activate  # Git Bash on Windows
pip install -r requirements.txt
cd frontend && npm install && cd ..
python -m compileall backend/app scripts
python -m pytest backend/tests/test_financial_report_data_pack_scripts.py -q
python -m pytest backend/tests/test_financial_report_canonical_targets.py -q
```

If the backend is not running locally, use only script modes that generate
targets or templates. Preview and apply modes require `--backend-url` to point
at a running backend.

### Manual Source Collection

Preferred sources, in order:

1. official issuer annual IFRS report;
2. official issuer interim IFRS report;
3. official disclosure center or issuer documents;
4. RAS only if IFRS is unavailable;
5. management presentation only if explicitly labeled.

Operator warnings:

- do not use Wikipedia as a financial source;
- do not enter market cap as equity;
- do not enter coupon payments as interest expense;
- do not convert million RUB twice;
- do not enter dashes as zero;
- do not mix annual and quarterly figures in one row;
- do not attach duplicate issuer reports to duplicate company IDs;
- use the canonical company ID from the collection template.

### Generate First Canonical Template

```bash
python scripts/financial_report_canonical_pack.py \
  --mode template \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --model-run-id 2 \
  --as-of-date 2026-05-19 \
  --company-ids 18,67,125 \
  --use-duplicate-mapping \
  --rollup-duplicates \
  --include-duplicate-members \
  --collection-template-output data/financial_reports/private/canonical_first3_reports_task84.csv \
  --json-output logs/financial_reports/canonical_pack_template_task84.json \
  --markdown-output logs/financial_reports/canonical_pack_template_task84.md
```

The generated template pre-fills canonical issuer identity and duplicate
context, but leaves financial values empty. Fill real values manually from
official reports only.

The same selection can be requested by name:

```bash
python scripts/financial_report_canonical_pack.py \
  --mode template \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --company-names РЖД,Мостотрест,ТМК \
  --use-duplicate-mapping \
  --rollup-duplicates \
  --include-duplicate-members \
  --collection-template-output data/financial_reports/private/canonical_first3_reports_task84.csv
```

Name matching fails safely if a name is ambiguous or missing.

### Preview Filled Collection

After the operator fills real values:

```bash
python scripts/financial_report_canonical_pack.py \
  --mode preview \
  --backend-url http://127.0.0.1:8000 \
  --reviewed-input data/financial_reports/private/canonical_first3_reports_task84.csv \
  --format csv \
  --normalized-output logs/financial_reports/canonical_first3_normalized_task84.csv \
  --normalized-format csv \
  --json-output logs/financial_reports/canonical_pack_preview_task84.json \
  --markdown-output logs/financial_reports/canonical_pack_preview_task84.md
```

Preview validates source evidence, normalizes `value_scale`, runs import
dry-run with backend company preview, and records coverage before/after. It
does not call the ingest endpoint.

### First Real Canonical Pack: Preview Only

Use this operation for the first real values after the private template has
been filled from official issuer reports. Keep the reviewed CSV under
`data/financial_reports/private/`.

1. Generate the template on VDS:

```bash
python3 scripts/financial_report_canonical_pack.py \
  --mode template \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --model-run-id 2 \
  --as-of-date 2026-05-19 \
  --company-ids 18,67,125 \
  --use-duplicate-mapping \
  --rollup-duplicates \
  --include-duplicate-members \
  --collection-template-output data/financial_reports/private/canonical_first3_reports_task84.csv \
  --json-output logs/financial_reports/canonical_pack_template_task84_vds.json \
  --markdown-output logs/financial_reports/canonical_pack_template_task84_vds.md
```

2. Fill only from official issuer annual/interim reports or official
disclosure documents. Do not use Wikipedia or unofficial summaries.

3. Run preview only:

```bash
python3 scripts/financial_report_canonical_pack.py \
  --mode preview \
  --backend-url http://127.0.0.1:8000 \
  --reviewed-input data/financial_reports/private/canonical_first3_reports_task84.csv \
  --format csv \
  --normalized-output logs/financial_reports/canonical_first3_normalized_task85.csv \
  --normalized-format csv \
  --json-output logs/financial_reports/canonical_pack_preview_task85_vds.json \
  --markdown-output logs/financial_reports/canonical_pack_preview_task85_vds.md
```

Do not run apply/import until the preview report is reviewed and a PostgreSQL
backup exists.

### Task 86: Official-Source Assisted Preview Only

Task 86 adds an offline helper for filling a private canonical collection CSV
from operator-copied official report values. The helper does not scrape sites
and never calls backend import/apply endpoints.

Use only official issuer annual/interim reports, official disclosure documents,
or locally provided official files. If a value is not found, leave it empty and
add an operator note. Do not use Wikipedia, market cap as equity, coupon
payments as interest expense, dashes as zero, or mixed annual/quarterly values.

#### Workflow A: Manual JSON -> CSV -> Preview

Create a private manual-values JSON from an official report, then fill the
private reviewed CSV:

```bash
python3 scripts/financial_report_official_source_fill.py \
  --template-input data/financial_reports/private/canonical_first3_reports_task85.csv \
  --manual-values-json data/financial_reports/private/rzd_2024_manual_values.json \
  --output data/financial_reports/private/canonical_first3_reports_task86_preview.csv \
  --evidence-output logs/financial_reports/rzd_2024_evidence_task86.json \
  --markdown-output logs/financial_reports/rzd_2024_evidence_task86.md
```

Then run preview only:

```bash
python3 scripts/financial_report_canonical_pack.py \
  --mode preview \
  --backend-url http://127.0.0.1:8000 \
  --reviewed-input data/financial_reports/private/canonical_first3_reports_task86_preview.csv \
  --format csv \
  --normalized-output logs/financial_reports/canonical_first3_normalized_task86.csv \
  --normalized-format csv \
  --json-output logs/financial_reports/canonical_pack_preview_task86_vds.json \
  --markdown-output logs/financial_reports/canonical_pack_preview_task86_vds.md
```

#### Workflow B: Empty Template -> Manual CSV -> Preview

You can also fill
`data/financial_reports/private/canonical_first3_reports_task85.csv` directly
from official issuer reports. Keep the file private, leave unknown values empty,
fill `source_url` or `source_file_name`, and then run the same preview command
above. Do not run apply/import from this workflow.

#### Task 86 VDS Smoke

Generate a synthetic helper output only:

```bash
python3 scripts/financial_report_official_source_fill.py \
  --template-input data/financial_reports/private/canonical_first3_reports_task85.csv \
  --manual-values-json docs/examples/financial_reports/canonical_financial_report_manual_values_example_synthetic.json \
  --output logs/financial_reports/canonical_first3_reports_task86_synthetic_preview.csv \
  --evidence-output logs/financial_reports/synthetic_evidence_task86.json \
  --markdown-output logs/financial_reports/synthetic_evidence_task86.md
```

Preview is allowed against synthetic output only if the referenced canonical
company IDs exist and the output is clearly treated as synthetic. Do not import.

Check the database count before and after preview:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select count(*) from financial_reports;"
```

The count must remain unchanged.

### Confirmed Apply

Before confirmed import on VDS, create a PostgreSQL backup. Then run apply only
after reviewing the preview report:

```bash
python scripts/financial_report_canonical_pack.py \
  --mode apply \
  --backend-url http://127.0.0.1:8000 \
  --reviewed-input data/financial_reports/private/canonical_first3_reports_task84.csv \
  --format csv \
  --normalized-output logs/financial_reports/canonical_first3_normalized_task84.csv \
  --normalized-format csv \
  --execute-import yes \
  --confirm-import yes \
  --json-output logs/financial_reports/canonical_pack_apply_task84.json \
  --markdown-output logs/financial_reports/canonical_pack_apply_task84.md
```

This script does not perform automatic rollback. To rollback, restore the
backup or manually review rows in:

- `financial_reports`;
- `financial_report_source_documents`.

Do not rebuild ML/features automatically after this task. Use the post-ingest
rebuild plan separately after coverage and import reports are reviewed.

### Task 84 VDS Smoke Checklist

Health:

```bash
curl -i http://127.0.0.1:8000/api/health
```

Generate first canonical report template only:

```bash
python3 scripts/financial_report_canonical_pack.py \
  --mode template \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --model-run-id 2 \
  --as-of-date 2026-05-19 \
  --company-ids 18,67,125 \
  --use-duplicate-mapping \
  --rollup-duplicates \
  --include-duplicate-members \
  --collection-template-output data/financial_reports/private/canonical_first3_reports_task84.csv \
  --json-output logs/financial_reports/canonical_pack_template_task84_vds.json \
  --markdown-output logs/financial_reports/canonical_pack_template_task84_vds.md
```

Preview only after operator fills real data:

```bash
python3 scripts/financial_report_canonical_pack.py \
  --mode preview \
  --backend-url http://127.0.0.1:8000 \
  --reviewed-input data/financial_reports/private/canonical_first3_reports_task84.csv \
  --format csv \
  --normalized-output logs/financial_reports/canonical_first3_normalized_task84.csv \
  --normalized-format csv \
  --json-output logs/financial_reports/canonical_pack_preview_task84_vds.json \
  --markdown-output logs/financial_reports/canonical_pack_preview_task84_vds.md
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

Expected Task 84 smoke result:

```text
health = 200 OK
template generation works
no import executed
financial_reports count does not change
schedule remains paused
```

## VDS Canonical Target Smoke

```bash
curl -i http://127.0.0.1:8000/api/health
```

```bash
python3 scripts/financial_report_target_issuers.py \
  --backend-url http://127.0.0.1:8000 \
  --source mixed \
  --model-run-id 2 \
  --as-of-date 2026-05-19 \
  --limit 50 \
  --use-duplicate-mapping \
  --rollup-duplicates \
  --include-duplicate-members \
  --compare-rollup \
  --json-output logs/financial_reports/canonical_targets_task83_vds.json \
  --csv-output logs/financial_reports/canonical_targets_task83_vds.csv \
  --markdown-output logs/financial_reports/canonical_targets_task83_vds.md \
  --collection-template-output logs/financial_reports/canonical_collection_template_task83_vds.csv
```

Expected:

```text
health = 200 OK
canonical financial report target export is written
canonical issuers include accepted duplicate member IDs
paper schedule remains paused
no import or apply endpoint is called
```

## Source Quality Guidance

Preferred sources:

1. consolidated IFRS annual report;
2. consolidated IFRS quarterly or interim report;
3. RAS standalone report only if IFRS is unavailable;
4. management presentation only when explicitly labeled and sourced.

Record source quality fields:

```text
source_url
source_file_name
source_page
source_table
accounting_standard
consolidation_scope
currency
value_scale
```

Operator checks:

- do not mix million RUB and billion RUB without `value_scale`;
- do not enter dashes as zero;
- do not use market capitalization as equity;
- do not use coupon payments as interest expense;
- do not use revenue instead of EBITDA;
- do not use net debt if the report only provides total debt unless it is
  clearly calculated and documented.
