import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  CalendarClock,
  CheckCircle2,
  FileJson,
  Rocket,
} from "lucide-react";
import { Link } from "react-router-dom";

import { api, normalizeApiError } from "../api/client";
import type {
  LivePaperMonitoringOverviewResponse,
  LivePaperPilotBootstrapRequest,
  LivePaperPilotBootstrapResponse,
  LivePaperPilotBootstrapStatus,
} from "../api/types";
import { EmptyState, ErrorState, LoadingState } from "../components/StateBlocks";

type PilotFormState = {
  name: string;
  description: string;
  model_run_id: string;
  return_method: string;
  horizon_days: string;
  virtual_initial_capital: string;
  planned_duration_days: string;
  date_from: string;
  date_to: string;
  next_run_at: string;
  interval_days: string;
  max_runs: string;
  create_schedule: boolean;
  dry_run_only: boolean;
  allow_readiness_warning: boolean;
  allow_not_ready: boolean;
  top_n: string;
  min_probability_positive: string;
  use_portfolio_constraints: boolean;
  max_position_weight: string;
  max_issuer_weight: string;
  max_high_risk_weight: string;
  transaction_cost_rate: string;
  include_monitoring_overview: boolean;
};

const defaultForm: PilotFormState = {
  name: "50k live paper pilot",
  description: "Virtual paper observation pilot",
  model_run_id: "",
  return_method: "risk_adjusted",
  horizon_days: "30",
  virtual_initial_capital: "50000",
  planned_duration_days: "90",
  date_from: "2025-01-10",
  date_to: "2025-03-14",
  next_run_at: "",
  interval_days: "1",
  max_runs: "",
  create_schedule: true,
  dry_run_only: true,
  allow_readiness_warning: false,
  allow_not_ready: false,
  top_n: "5",
  min_probability_positive: "0.50",
  use_portfolio_constraints: true,
  max_position_weight: "0.20",
  max_issuer_weight: "0.30",
  max_high_risk_weight: "0.20",
  transaction_cost_rate: "0.001",
  include_monitoring_overview: true,
};

const statusLabels: Record<LivePaperPilotBootstrapStatus, string> = {
  prepared: "подготовлено",
  scheduled: "расписание создано",
  blocked: "заблокировано проверками",
};

const statusTone: Record<LivePaperPilotBootstrapStatus, string> = {
  prepared: "border-sky-200 bg-sky-50 text-sky-800",
  scheduled: "border-teal-200 bg-teal-50 text-teal-800",
  blocked: "border-amber-200 bg-amber-50 text-amber-800",
};

function toNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function isPositiveNumber(value: unknown): boolean {
  const numeric = toNumber(value);
  return numeric !== null && numeric > 0;
}

function isNonNegativeNumber(value: unknown): boolean {
  const numeric = toNumber(value);
  return numeric !== null && numeric >= 0;
}

function isZeroToOne(value: unknown): boolean {
  const numeric = toNumber(value);
  return numeric !== null && numeric >= 0 && numeric <= 1;
}

function formatPlain(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  if (typeof value === "number") {
    return new Intl.NumberFormat("ru-RU", {
      maximumFractionDigits: 4,
    }).format(value);
  }
  return String(value);
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
}

function asRecordArray(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => asRecord(item))
    .filter((item): item is Record<string, unknown> => item !== null);
}

function scalar(value: unknown): string {
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return String(value);
  }
  return "—";
}

function messageFromRecord(item: Record<string, unknown>): string {
  const message = item.message ?? item.detail ?? item.code;
  if (
    typeof message === "string" ||
    typeof message === "number" ||
    typeof message === "boolean"
  ) {
    return String(message);
  }
  return JSON.stringify(item);
}

function validateForm(form: PilotFormState): string[] {
  const errors: string[] = [];

  if (!form.name.trim()) {
    errors.push("Название не должно быть пустым.");
  }
  if (!isPositiveNumber(form.model_run_id)) {
    errors.push("Model run ID должен быть положительным числом.");
  }
  if (!isPositiveNumber(form.virtual_initial_capital)) {
    errors.push("Virtual initial capital должен быть положительным.");
  }

  const plannedDuration = toNumber(form.planned_duration_days);
  if (
    plannedDuration === null ||
    plannedDuration < 1 ||
    plannedDuration > 365
  ) {
    errors.push("Planned duration days должен быть от 1 до 365.");
  }

  const dateFrom = Date.parse(`${form.date_from}T00:00:00`);
  const dateTo = Date.parse(`${form.date_to}T00:00:00`);
  if (
    !form.date_from ||
    !form.date_to ||
    Number.isNaN(dateFrom) ||
    Number.isNaN(dateTo) ||
    dateFrom > dateTo
  ) {
    errors.push("Date from должен быть раньше или равен Date to.");
  }

  if (!form.next_run_at.trim() || Number.isNaN(Date.parse(form.next_run_at))) {
    errors.push("Next run at обязателен и должен быть корректной датой.");
  }
  if (!isPositiveNumber(form.interval_days)) {
    errors.push("Interval days должен быть положительным.");
  }
  if (form.max_runs.trim() && !isPositiveNumber(form.max_runs)) {
    errors.push("Max runs должен быть положительным, если заполнен.");
  }
  if (!isPositiveNumber(form.top_n)) {
    errors.push("Top N должен быть положительным.");
  }
  if (!isZeroToOne(form.min_probability_positive)) {
    errors.push("Minimum probability positive должен быть от 0 до 1.");
  }
  if (!isZeroToOne(form.max_position_weight)) {
    errors.push("Max position weight должен быть от 0 до 1.");
  }
  if (!isZeroToOne(form.max_issuer_weight)) {
    errors.push("Max issuer weight должен быть от 0 до 1.");
  }
  if (!isZeroToOne(form.max_high_risk_weight)) {
    errors.push("Max high risk weight должен быть от 0 до 1.");
  }
  if (!isNonNegativeNumber(form.transaction_cost_rate)) {
    errors.push("Transaction cost rate должен быть неотрицательным.");
  }

  return errors;
}

function buildPayload(
  form: PilotFormState,
  mode: "dry-run" | "create-schedule",
): LivePaperPilotBootstrapRequest {
  return {
    name: form.name.trim(),
    description: form.description.trim() || null,
    model_run_id: Number(form.model_run_id),
    return_method: form.return_method.trim() || "risk_adjusted",
    horizon_days: Number(form.horizon_days),
    virtual_initial_capital: form.virtual_initial_capital,
    planned_duration_days: Number(form.planned_duration_days),
    date_from: form.date_from,
    date_to: form.date_to,
    next_run_at: form.next_run_at,
    interval_days: Number(form.interval_days),
    max_runs: form.max_runs.trim() ? Number(form.max_runs) : null,
    create_schedule: true,
    dry_run_only: mode === "dry-run",
    allow_readiness_warning: form.allow_readiness_warning,
    allow_not_ready: form.allow_not_ready,
    top_n: Number(form.top_n),
    min_probability_positive: form.min_probability_positive,
    use_portfolio_constraints: form.use_portfolio_constraints,
    max_position_weight: form.max_position_weight,
    max_issuer_weight: form.max_issuer_weight,
    max_high_risk_weight: form.max_high_risk_weight,
    transaction_cost_rate: form.transaction_cost_rate,
    include_monitoring_overview: form.include_monitoring_overview,
  };
}

function Section({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <section className="surface p-4">
      <div className="mb-4">
        <h2 className="text-base font-semibold text-ink">{title}</h2>
        {subtitle ? (
          <p className="mt-1 text-sm text-slate-600">{subtitle}</p>
        ) : null}
      </div>
      {children}
    </section>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  placeholder?: string;
}) {
  return (
    <label className="block text-sm">
      <span className="font-medium text-slate-700">{label}</span>
      <input
        className="mt-1 w-full rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent"
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        type={type}
        value={value}
      />
    </label>
  );
}

function CheckboxField({
  label,
  checked,
  onChange,
  description,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  description?: string;
}) {
  return (
    <label className="flex items-start gap-3 rounded-lg border border-line bg-white p-3 text-sm">
      <input
        checked={checked}
        className="mt-1"
        onChange={(event) => onChange(event.target.checked)}
        type="checkbox"
      />
      <span>
        <span className="block font-medium text-slate-700">{label}</span>
        {description ? (
          <span className="mt-1 block text-xs text-slate-500">{description}</span>
        ) : null}
      </span>
    </label>
  );
}

function SummaryItem({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-white p-3">
      <div className="text-xs font-semibold uppercase text-slate-500">{label}</div>
      <div className="mt-1 text-sm font-semibold text-ink">{value}</div>
    </div>
  );
}

function JsonDetails({ title, data }: { title: string; data: unknown }) {
  if (data === null || data === undefined) {
    return (
      <details className="rounded-lg border border-line bg-white p-3">
        <summary className="cursor-pointer text-sm font-semibold text-ink">
          {title}
        </summary>
        <div className="mt-3 text-sm text-slate-500">Нет данных</div>
      </details>
    );
  }

  return (
    <details className="rounded-lg border border-line bg-white p-3">
      <summary className="cursor-pointer text-sm font-semibold text-ink">
        {title}
      </summary>
      <pre className="mt-3 max-h-96 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-100">
        {JSON.stringify(data, null, 2)}
      </pre>
    </details>
  );
}

function MessageList({
  title,
  items,
  tone,
}: {
  title: string;
  items: Array<Record<string, unknown>>;
  tone: string;
}) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      {items.length ? (
        <div className="mt-2 space-y-2">
          {items.map((item, index) => (
            <div
              className={`rounded-lg border px-3 py-2 text-sm ${tone}`}
              key={`${title}-${index}`}
            >
              {messageFromRecord(item)}
            </div>
          ))}
        </div>
      ) : (
        <EmptyState label="Нет записей для отображения." />
      )}
    </div>
  );
}

function ReadinessSummary({
  readiness,
}: {
  readiness: Record<string, unknown> | null;
}) {
  if (!readiness) {
    return <EmptyState label="Readiness-отчет не вернулся в ответе." />;
  }

  const gates = asRecordArray(readiness.gates);
  const failedGateCount = gates.filter((gate) => gate.status === "failed").length;
  const warningGateCount = gates.filter(
    (gate) => gate.status === "warning",
  ).length;
  const selectedCandidate = asRecord(readiness.selected_candidate);

  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
      <SummaryItem
        label="Readiness"
        value={scalar(readiness.readiness_status)}
      />
      <SummaryItem label="Gates" value={gates.length} />
      <SummaryItem label="Failed gates" value={failedGateCount} />
      <SummaryItem label="Warning gates" value={warningGateCount} />
      <SummaryItem
        label="Selected model run"
        value={scalar(
          selectedCandidate?.model_run_id ?? readiness.selected_model_run_id,
        )}
      />
    </div>
  );
}

function MonitoringSummary({
  overview,
}: {
  overview: LivePaperMonitoringOverviewResponse;
}) {
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
      <SummaryItem label="Health" value={overview.health_status} />
      <SummaryItem label="Schedules" value={overview.schedule_count} />
      <SummaryItem label="Active schedules" value={overview.active_schedule_count} />
      <SummaryItem label="Portfolios" value={overview.portfolio_count} />
      <SummaryItem label="Recent cycles" value={overview.recent_cycle_count} />
    </div>
  );
}

function ResultPanel({ result }: { result: LivePaperPilotBootstrapResponse }) {
  return (
    <div className="space-y-4">
      <Section
        title="Результат bootstrap"
        subtitle="Сводка ответа, readiness-отчета и подготовленных payloads."
      >
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <span
            className={`rounded-full border px-3 py-1 text-sm font-semibold ${statusTone[result.status]}`}
          >
            {statusLabels[result.status]}
          </span>
          <span className="text-sm text-slate-600">
            HTTP-ответ успешен; статус blocked означает остановку проверками.
          </span>
        </div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <SummaryItem label="Readiness" value={formatPlain(result.readiness_status)} />
          <SummaryItem
            label="Selected model run"
            value={formatPlain(result.selected_model_run_id)}
          />
          <SummaryItem
            label="Created schedule"
            value={formatPlain(result.created_schedule_id)}
          />
          <SummaryItem
            label="Virtual capital"
            value={formatPlain(result.virtual_initial_capital)}
          />
          <SummaryItem
            label="Planned duration"
            value={`${result.planned_duration_days} дней`}
          />
          <SummaryItem
            label="Next run"
            value={formatDateTime(result.next_run_at)}
          />
          <SummaryItem
            label="Interval"
            value={`${result.interval_days} дней`}
          />
          <SummaryItem label="Max runs" value={formatPlain(result.max_runs)} />
        </div>
      </Section>

      <Section title="Readiness summary">
        <ReadinessSummary readiness={result.readiness} />
      </Section>

      <section className="grid gap-4 xl:grid-cols-2">
        <div className="surface p-4">
          <MessageList
            items={result.warnings}
            title="Warnings"
            tone="border-amber-200 bg-amber-50 text-amber-800"
          />
        </div>
        <div className="surface p-4">
          <MessageList
            items={result.errors}
            title="Errors"
            tone="border-red-200 bg-red-50 text-red-800"
          />
        </div>
      </section>

      <Section
        title="Next steps"
        subtitle="Команды показаны только как подсказки; эта страница их не выполняет."
      >
        <div className="grid gap-3 lg:grid-cols-2">
          {result.next_steps.map((step, index) => (
            <div
              className="rounded-lg border border-line bg-white p-4"
              key={`${step.path}-${index}`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-full bg-slate-100 px-2 py-1 text-xs font-semibold text-slate-700">
                  {step.method}
                </span>
                <span className="text-sm font-semibold text-ink">{step.label}</span>
              </div>
              <div className="mt-2 break-all font-mono text-xs text-accent">
                {step.path}
              </div>
              <p className="mt-2 text-sm text-slate-600">{step.description}</p>
              {step.body ? <JsonDetails data={step.body} title="Body" /> : null}
            </div>
          ))}
        </div>
      </Section>

      <Section title="Generated payloads">
        <div className="space-y-3">
          <JsonDetails
            data={result.payloads.readiness_request}
            title="readiness_request"
          />
          <JsonDetails data={result.payloads.cycle_request} title="cycle_request" />
          <JsonDetails
            data={result.payloads.schedule_request}
            title="schedule_request"
          />
        </div>
      </Section>

      {result.monitoring_overview ? (
        <Section title="Monitoring overview">
          <MonitoringSummary overview={result.monitoring_overview} />
        </Section>
      ) : null}
    </div>
  );
}

export function LivePaperPilotBootstrap() {
  const [form, setForm] = useState<PilotFormState>(defaultForm);
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [confirmationChecked, setConfirmationChecked] = useState(false);

  const bootstrapMutation = useMutation({
    mutationFn: api.bootstrapLivePaperPilot,
  });

  const latestResult = bootstrapMutation.data;
  const canCreateSchedule = confirmationChecked && !bootstrapMutation.isPending;

  const formSummary = useMemo(
    () => ({
      capital: form.virtual_initial_capital || "—",
      duration: form.planned_duration_days || "—",
      dates: `${form.date_from || "—"} → ${form.date_to || "—"}`,
      nextRun: form.next_run_at || "—",
    }),
    [form],
  );

  function updateField<K extends keyof PilotFormState>(
    key: K,
    value: PilotFormState[K],
  ) {
    setForm((current) => ({
      ...current,
      [key]: value,
    }));
  }

  function setTomorrowAtTen() {
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    tomorrow.setHours(10, 0, 0, 0);
    updateField("next_run_at", tomorrow.toISOString().replace(".000Z", "Z"));
  }

  function submit(mode: "dry-run" | "create-schedule") {
    const errors = validateForm(form);
    setValidationErrors(errors);
    if (errors.length) {
      return;
    }
    bootstrapMutation.mutate(buildPayload(form, mode));
  }

  return (
    <div className="space-y-5">
      <section className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <Link className="text-button inline-flex items-center gap-2" to="/live-paper">
            <ArrowLeft size={16} />
            К Live Paper
          </Link>
          <p className="mt-4 text-sm font-semibold uppercase text-accent">
            Live Paper Pilot
          </p>
          <h1 className="mt-1 text-2xl font-semibold text-ink">
            Подготовка pilot schedule
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-600">
            Форма собирает compact payload для readiness, cycle и schedule.
            Информационный режим: без реальных брокерских действий.
          </p>
        </div>
        <div className="surface flex items-center gap-2 px-3 py-2 text-sm text-slate-600">
          <Rocket size={16} />
          50 000 RUB virtual pilot
        </div>
      </section>

      <section className="grid gap-3 md:grid-cols-4">
        <SummaryItem label="Capital" value={formSummary.capital} />
        <SummaryItem label="Duration" value={`${formSummary.duration} дней`} />
        <SummaryItem label="Dates" value={formSummary.dates} />
        <SummaryItem label="Next run" value={formSummary.nextRun} />
      </section>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-5">
          <Section
            title="Basic pilot settings"
            subtitle="Основные параметры будущего виртуального наблюдения."
          >
            <div className="grid gap-4 md:grid-cols-2">
              <Field
                label="Название"
                onChange={(value) => updateField("name", value)}
                value={form.name}
              />
              <Field
                label="Описание"
                onChange={(value) => updateField("description", value)}
                value={form.description}
              />
              <Field
                label="Model run ID"
                onChange={(value) => updateField("model_run_id", value)}
                type="number"
                value={form.model_run_id}
              />
              <Field
                label="Return method"
                onChange={(value) => updateField("return_method", value)}
                value={form.return_method}
              />
              <Field
                label="Horizon days"
                onChange={(value) => updateField("horizon_days", value)}
                type="number"
                value={form.horizon_days}
              />
              <Field
                label="Virtual initial capital"
                onChange={(value) => updateField("virtual_initial_capital", value)}
                value={form.virtual_initial_capital}
              />
              <Field
                label="Planned duration days"
                onChange={(value) => updateField("planned_duration_days", value)}
                type="number"
                value={form.planned_duration_days}
              />
            </div>
          </Section>

          <Section
            title="Experiment dates"
            subtitle="Явный период нужен для стабильных robustness diagnostics."
          >
            <div className="grid gap-4 md:grid-cols-2">
              <Field
                label="Date from"
                onChange={(value) => updateField("date_from", value)}
                type="date"
                value={form.date_from}
              />
              <Field
                label="Date to"
                onChange={(value) => updateField("date_to", value)}
                type="date"
                value={form.date_to}
              />
              <div className="md:col-span-2">
                <Field
                  label="Next run at"
                  onChange={(value) => updateField("next_run_at", value)}
                  placeholder="2025-03-15T10:00:00Z"
                  value={form.next_run_at}
                />
                <button
                  className="text-button mt-2 inline-flex items-center gap-2"
                  onClick={setTomorrowAtTen}
                  type="button"
                >
                  <CalendarClock size={16} />
                  Поставить запуск на завтра 10:00
                </button>
              </div>
              <Field
                label="Interval days"
                onChange={(value) => updateField("interval_days", value)}
                type="number"
                value={form.interval_days}
              />
              <Field
                label="Max runs"
                onChange={(value) => updateField("max_runs", value)}
                placeholder="пусто = без лимита"
                type="number"
                value={form.max_runs}
              />
            </div>
          </Section>

          <Section
            title="Strategy settings"
            subtitle="Нейтральные параметры отбора позиций для paper-портфеля."
          >
            <div className="grid gap-4 md:grid-cols-2">
              <Field
                label="Top N"
                onChange={(value) => updateField("top_n", value)}
                type="number"
                value={form.top_n}
              />
              <Field
                label="Minimum probability positive"
                onChange={(value) => updateField("min_probability_positive", value)}
                value={form.min_probability_positive}
              />
              <Field
                label="Transaction cost rate"
                onChange={(value) => updateField("transaction_cost_rate", value)}
                value={form.transaction_cost_rate}
              />
              <CheckboxField
                checked={form.use_portfolio_constraints}
                label="Use portfolio constraints"
                onChange={(checked) =>
                  updateField("use_portfolio_constraints", checked)
                }
              />
              <Field
                label="Max position weight"
                onChange={(value) => updateField("max_position_weight", value)}
                value={form.max_position_weight}
              />
              <Field
                label="Max issuer weight"
                onChange={(value) => updateField("max_issuer_weight", value)}
                value={form.max_issuer_weight}
              />
              <Field
                label="Max high risk weight"
                onChange={(value) => updateField("max_high_risk_weight", value)}
                value={form.max_high_risk_weight}
              />
            </div>
          </Section>

          <Section
            title="Safety controls"
            subtitle="Bootstrap не запускает cycles и не выполняет rebalance."
          >
            <div className="grid gap-3 md:grid-cols-2">
              <CheckboxField
                checked={form.dry_run_only}
                description="Dry-run режим ничего не создает и только показывает, что будет отправлено."
                label="Dry run only"
                onChange={(checked) => updateField("dry_run_only", checked)}
              />
              <CheckboxField
                checked={form.create_schedule}
                label="Create schedule"
                onChange={(checked) => updateField("create_schedule", checked)}
              />
              <CheckboxField
                checked={form.allow_readiness_warning}
                label="Allow readiness warning"
                onChange={(checked) => updateField("allow_readiness_warning", checked)}
              />
              <CheckboxField
                checked={form.allow_not_ready}
                label="Allow not ready"
                onChange={(checked) => updateField("allow_not_ready", checked)}
              />
              <CheckboxField
                checked={form.include_monitoring_overview}
                label="Include monitoring overview"
                onChange={(checked) =>
                  updateField("include_monitoring_overview", checked)
                }
              />
            </div>
          </Section>
        </div>

        <aside className="space-y-4">
          <section className="surface sticky top-4 p-4">
            <h2 className="text-base font-semibold text-ink">Submit</h2>
            <p className="mt-1 text-sm text-slate-600">
              Первый шаг безопасно проверяет readiness. Создание schedule требует
              отдельного подтверждения.
            </p>

            {validationErrors.length ? (
              <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
                <div className="flex items-center gap-2 font-semibold">
                  <AlertTriangle size={16} />
                  Проверьте форму
                </div>
                <ul className="mt-2 list-disc space-y-1 pl-5">
                  {validationErrors.map((error) => (
                    <li key={error}>{error}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {bootstrapMutation.isError ? (
              <div className="mt-4">
                <ErrorState message={normalizeApiError(bootstrapMutation.error)} />
              </div>
            ) : null}

            <div className="mt-4 space-y-3">
              <button
                className="primary-button w-full justify-center"
                disabled={bootstrapMutation.isPending}
                onClick={() => submit("dry-run")}
                type="button"
              >
                Проверить без создания schedule
              </button>

              <CheckboxField
                checked={confirmationChecked}
                label="Я понимаю, что будет создано live paper расписание в локальной системе."
                onChange={setConfirmationChecked}
              />

              <button
                className="text-button w-full justify-center disabled:cursor-not-allowed disabled:opacity-50"
                disabled={!canCreateSchedule}
                onClick={() => submit("create-schedule")}
                type="button"
              >
                Создать schedule
              </button>
            </div>
          </section>
        </aside>
      </div>

      {bootstrapMutation.isPending ? (
        <LoadingState label="Отправка bootstrap-запроса" />
      ) : null}

      {latestResult ? <ResultPanel result={latestResult} /> : (
        <section className="surface flex items-start gap-3 p-4 text-sm text-slate-600">
          <FileJson className="mt-0.5 shrink-0" size={18} />
          <span>
            После проверки здесь появятся readiness summary, generated payloads и
            next steps.
          </span>
        </section>
      )}

      {latestResult?.status === "scheduled" ? (
        <section className="surface flex items-start gap-3 border-teal-200 bg-teal-50 p-4 text-sm text-teal-800">
          <CheckCircle2 className="mt-0.5 shrink-0" size={18} />
          <span>
            Schedule создан. Эта страница не запускает due schedules; запуск
            остается отдельным ручным действием.
          </span>
        </section>
      ) : null}
    </div>
  );
}
