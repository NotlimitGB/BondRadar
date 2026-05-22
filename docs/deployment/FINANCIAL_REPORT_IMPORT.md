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
