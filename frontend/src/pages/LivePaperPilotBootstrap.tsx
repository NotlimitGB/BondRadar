import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
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
import { ExternalRiskRegimeCard } from "../components/live-paper/ExternalRiskRegimeCard";
import {
  MessageList,
  Section,
  SummaryItem,
} from "../components/live-paper/LivePaperLayout";
import { JsonDetails } from "../components/live-paper/LivePaperJson";
import { EmptyState, ErrorState, LoadingState } from "../components/StateBlocks";
import {
  asRecord,
  asRecordArray,
  formatDateTime,
  formatPlain,
  toNumber,
} from "../utils/livePaperFormat";

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
  name: "Пилот виртуального портфеля 50 000 ₽",
  description: "Виртуальное наблюдение за портфелем",
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

function validateForm(form: PilotFormState): string[] {
  const errors: string[] = [];

  if (!form.name.trim()) {
    errors.push("Название не должно быть пустым.");
  }
  if (!isPositiveNumber(form.model_run_id)) {
    errors.push("ID запуска модели должен быть положительным числом.");
  }
  if (!isPositiveNumber(form.virtual_initial_capital)) {
    errors.push("Виртуальный начальный капитал должен быть положительным.");
  }

  const plannedDuration = toNumber(form.planned_duration_days);
  if (
    plannedDuration === null ||
    plannedDuration < 1 ||
    plannedDuration > 365
  ) {
    errors.push("Плановая длительность должна быть от 1 до 365 дней.");
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
    errors.push("Дата начала должна быть раньше или равна дате окончания.");
  }

  if (!form.next_run_at.trim() || Number.isNaN(Date.parse(form.next_run_at))) {
    errors.push("Следующий запуск обязателен и должен быть корректной датой.");
  }
  if (!isPositiveNumber(form.interval_days)) {
    errors.push("Интервал в днях должен быть положительным.");
  }
  if (form.max_runs.trim() && !isPositiveNumber(form.max_runs)) {
    errors.push("Максимум запусков должен быть положительным, если заполнен.");
  }
  if (!isPositiveNumber(form.top_n)) {
    errors.push("Количество позиций должно быть положительным.");
  }
  if (!isZeroToOne(form.min_probability_positive)) {
    errors.push("Минимальная вероятность позитивного класса должна быть от 0 до 1.");
  }
  if (!isZeroToOne(form.max_position_weight)) {
    errors.push("Максимальный вес позиции должен быть от 0 до 1.");
  }
  if (!isZeroToOne(form.max_issuer_weight)) {
    errors.push("Максимальный вес эмитента должен быть от 0 до 1.");
  }
  if (!isZeroToOne(form.max_high_risk_weight)) {
    errors.push("Максимальный вес высокого риска должен быть от 0 до 1.");
  }
  if (!isNonNegativeNumber(form.transaction_cost_rate)) {
    errors.push("Ставка операционных издержек должна быть неотрицательной.");
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

function ReadinessSummary({
  readiness,
}: {
  readiness: Record<string, unknown> | null;
}) {
  if (!readiness) {
    return <EmptyState label="Отчет готовности не вернулся в ответе." />;
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
        label="Готовность"
        value={scalar(readiness.readiness_status)}
      />
      <SummaryItem label="Проверки" value={gates.length} />
      <SummaryItem label="Проваленные проверки" value={failedGateCount} />
      <SummaryItem label="Проверки с предупреждением" value={warningGateCount} />
      <SummaryItem
        label="Выбранный запуск модели"
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
      <SummaryItem label="Состояние" value={overview.health_status} />
      <SummaryItem label="Расписания" value={overview.schedule_count} />
      <SummaryItem label="Активные расписания" value={overview.active_schedule_count} />
      <SummaryItem label="Портфели" value={overview.portfolio_count} />
      <SummaryItem label="Недавние циклы" value={overview.recent_cycle_count} />
    </div>
  );
}

function ResultPanel({ result }: { result: LivePaperPilotBootstrapResponse }) {
  return (
    <div className="space-y-4">
      <Section
        title="Результат подготовки"
        subtitle="Сводка ответа, отчета готовности и подготовленных данных запросов."
      >
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <span
            className={`rounded-full border px-3 py-1 text-sm font-semibold ${statusTone[result.status]}`}
          >
            {statusLabels[result.status]}
          </span>
          <span className="text-sm text-slate-600">
            HTTP-ответ успешен; статус «заблокировано» означает остановку проверками.
          </span>
        </div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <SummaryItem label="Готовность" value={formatPlain(result.readiness_status)} />
          <SummaryItem
            label="Выбранный запуск модели"
            value={formatPlain(result.selected_model_run_id)}
          />
          <SummaryItem
            label="Созданное расписание"
            value={formatPlain(result.created_schedule_id)}
          />
          <SummaryItem
            label="Виртуальный капитал"
            value={formatPlain(result.virtual_initial_capital)}
          />
          <SummaryItem
            label="Плановая длительность"
            value={`${result.planned_duration_days} дней`}
          />
          <SummaryItem
            label="Следующий запуск"
            value={formatDateTime(result.next_run_at)}
          />
          <SummaryItem
            label="Интервал"
            value={`${result.interval_days} дней`}
          />
          <SummaryItem label="Максимум запусков" value={formatPlain(result.max_runs)} />
        </div>
      </Section>

      <Section title="Сводка готовности">
        <ReadinessSummary readiness={result.readiness} />
      </Section>

      <section className="grid gap-4 xl:grid-cols-2">
        <div className="surface p-4">
          <MessageList
            items={result.warnings}
            title="Предупреждения"
            tone="border-amber-200 bg-amber-50 text-amber-800"
          />
        </div>
        <div className="surface p-4">
          <MessageList
            items={result.errors}
            title="Ошибки"
            tone="border-red-200 bg-red-50 text-red-800"
          />
        </div>
      </section>

      <Section
        title="Следующие шаги"
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
              {step.body ? <JsonDetails data={step.body} title="Тело запроса" /> : null}
            </div>
          ))}
        </div>
      </Section>

      <Section title="Сформированные данные запросов">
        <div className="space-y-3">
          <JsonDetails
            data={result.payloads.readiness_request}
            title="Запрос готовности"
          />
          <JsonDetails data={result.payloads.cycle_request} title="Запрос цикла" />
          <JsonDetails
            data={result.payloads.schedule_request}
            title="Запрос расписания"
          />
        </div>
      </Section>

      {result.monitoring_overview ? (
        <Section title="Сводка мониторинга">
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

  const externalRiskQuery = useQuery({
    queryKey: ["external-risk-regime"],
    queryFn: api.getExternalRiskRegime,
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
            К виртуальному контуру
          </Link>
          <p className="mt-4 text-sm font-semibold uppercase text-accent">
            Пилот виртуального контура
          </p>
          <h1 className="mt-1 text-2xl font-semibold text-ink">
            Подготовка расписания пилота
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-600">
            Форма собирает компактные данные для проверки готовности, цикла и расписания.
            Информационный режим: virtual paper only, no broker, no real money.
          </p>
        </div>
        <div className="surface flex items-center gap-2 px-3 py-2 text-sm text-slate-600">
          <Rocket size={16} />
          Виртуальный пилот на 50 000 ₽
        </div>
      </section>

      {externalRiskQuery.data ? (
        <ExternalRiskRegimeCard compact regime={externalRiskQuery.data} />
      ) : null}
      {externalRiskQuery.isError ? (
        <ErrorState message={normalizeApiError(externalRiskQuery.error)} />
      ) : null}

      <section className="surface flex items-start gap-3 border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        <AlertTriangle className="mt-0.5 shrink-0" size={18} />
        <span>
          Проверка без создания schedule только формирует отчет. Создание
          schedule настраивает virtual paper pilot, но не запускает реальные
          действия. Перед confirmed paper execution нужны quality gate,
          внешний риск и monitoring review.
        </span>
      </section>

      <section className="grid gap-3 md:grid-cols-4">
        <SummaryItem label="Капитал" value={formSummary.capital} />
        <SummaryItem label="Длительность" value={`${formSummary.duration} дней`} />
        <SummaryItem label="Даты" value={formSummary.dates} />
        <SummaryItem label="Следующий запуск" value={formSummary.nextRun} />
      </section>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-5">
          <Section
            title="Основные настройки пилота"
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
                label="Идентификатор запуска модели"
                onChange={(value) => updateField("model_run_id", value)}
                type="number"
                value={form.model_run_id}
              />
              <Field
                label="Метод доходности"
                onChange={(value) => updateField("return_method", value)}
                value={form.return_method}
              />
              <Field
                label="Горизонт, дней"
                onChange={(value) => updateField("horizon_days", value)}
                type="number"
                value={form.horizon_days}
              />
              <Field
                label="Виртуальный начальный капитал"
                onChange={(value) => updateField("virtual_initial_capital", value)}
                value={form.virtual_initial_capital}
              />
              <Field
                label="Плановая длительность, дней"
                onChange={(value) => updateField("planned_duration_days", value)}
                type="number"
                value={form.planned_duration_days}
              />
            </div>
          </Section>

          <Section
            title="Даты эксперимента"
            subtitle="Явный период нужен для стабильных проверок устойчивости."
          >
            <div className="grid gap-4 md:grid-cols-2">
              <Field
                label="Дата начала"
                onChange={(value) => updateField("date_from", value)}
                type="date"
                value={form.date_from}
              />
              <Field
                label="Дата окончания"
                onChange={(value) => updateField("date_to", value)}
                type="date"
                value={form.date_to}
              />
              <div className="md:col-span-2">
                <Field
                  label="Следующий запуск"
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
                label="Интервал, дней"
                onChange={(value) => updateField("interval_days", value)}
                type="number"
                value={form.interval_days}
              />
              <Field
                label="Максимум запусков"
                onChange={(value) => updateField("max_runs", value)}
                placeholder="пусто = без лимита"
                type="number"
                value={form.max_runs}
              />
            </div>
          </Section>

          <Section
            title="Настройки стратегии"
            subtitle="Нейтральные параметры отбора позиций для виртуального портфеля."
          >
            <div className="grid gap-4 md:grid-cols-2">
              <Field
                label="Количество позиций"
                onChange={(value) => updateField("top_n", value)}
                type="number"
                value={form.top_n}
              />
              <Field
                label="Минимальная вероятность позитивного класса"
                onChange={(value) => updateField("min_probability_positive", value)}
                value={form.min_probability_positive}
              />
              <Field
                label="Ставка операционных издержек"
                onChange={(value) => updateField("transaction_cost_rate", value)}
                value={form.transaction_cost_rate}
              />
              <CheckboxField
                checked={form.use_portfolio_constraints}
                label="Использовать портфельные ограничения"
                onChange={(checked) =>
                  updateField("use_portfolio_constraints", checked)
                }
              />
              <Field
                label="Максимальный вес позиции"
                onChange={(value) => updateField("max_position_weight", value)}
                value={form.max_position_weight}
              />
              <Field
                label="Максимальный вес эмитента"
                onChange={(value) => updateField("max_issuer_weight", value)}
                value={form.max_issuer_weight}
              />
              <Field
                label="Максимальный вес высокого риска"
                onChange={(value) => updateField("max_high_risk_weight", value)}
                value={form.max_high_risk_weight}
              />
            </div>
          </Section>

          <Section
            title="Контроль безопасности"
            subtitle="Подготовка не запускает циклы и не выполняет ребалансировку."
          >
            <div className="grid gap-3 md:grid-cols-2">
              <CheckboxField
                checked={form.dry_run_only}
                description="Проверочный режим ничего не создает и только показывает, что будет отправлено."
                label="Только проверочный режим"
                onChange={(checked) => updateField("dry_run_only", checked)}
              />
              <CheckboxField
                checked={form.create_schedule}
                label="Создать расписание"
                onChange={(checked) => updateField("create_schedule", checked)}
              />
              <CheckboxField
                checked={form.allow_readiness_warning}
                label="Разрешить предупреждение готовности"
                onChange={(checked) => updateField("allow_readiness_warning", checked)}
              />
              <CheckboxField
                checked={form.allow_not_ready}
                label="Разрешить статус неготовности"
                onChange={(checked) => updateField("allow_not_ready", checked)}
              />
              <CheckboxField
                checked={form.include_monitoring_overview}
                label="Добавить сводку мониторинга"
                onChange={(checked) =>
                  updateField("include_monitoring_overview", checked)
                }
              />
            </div>
          </Section>
        </div>

        <aside className="space-y-4">
          <section className="surface sticky top-4 p-4">
            <h2 className="text-base font-semibold text-ink">Отправка</h2>
            <p className="mt-1 text-sm text-slate-600">
              Первый шаг безопасно проверяет готовность. Создание расписания требует
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
                label="Я понимаю, что будет создано расписание виртуального контура в локальной системе."
                onChange={setConfirmationChecked}
              />

              <button
                className="text-button w-full justify-center disabled:cursor-not-allowed disabled:opacity-50"
                disabled={!canCreateSchedule}
                onClick={() => submit("create-schedule")}
                type="button"
              >
                Создать schedule для virtual pilot
              </button>
            </div>
          </section>
        </aside>
      </div>

      {bootstrapMutation.isPending ? (
        <LoadingState label="Отправка запроса подготовки" />
      ) : null}

      {latestResult ? <ResultPanel result={latestResult} /> : (
        <section className="surface flex items-start gap-3 p-4 text-sm text-slate-600">
          <FileJson className="mt-0.5 shrink-0" size={18} />
          <span>
            После проверки здесь появятся сводка готовности, сформированные данные
            запросов и следующие шаги.
          </span>
        </section>
      )}

      {latestResult?.status === "scheduled" ? (
        <section className="surface flex items-start gap-3 border-teal-200 bg-teal-50 p-4 text-sm text-teal-800">
          <CheckCircle2 className="mt-0.5 shrink-0" size={18} />
          <span>
            Расписание создано. Эта страница не запускает ожидающие расписания; запуск
            остается отдельным ручным действием.
          </span>
        </section>
      ) : null}
    </div>
  );
}
