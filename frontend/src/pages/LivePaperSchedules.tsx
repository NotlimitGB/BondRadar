import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  FileJson,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { Link } from "react-router-dom";

import { api, normalizeApiError } from "../api/client";
import type {
  LivePaperCycleMonitoringSummary,
  LivePaperScheduleRead,
  LivePaperScheduleRunDueRequest,
  LivePaperScheduleRunDueResponse,
  LivePaperScheduledRunItem,
} from "../api/types";
import { EmptyState, ErrorState, LoadingState } from "../components/StateBlocks";

type RunDueFormState = {
  now: string;
  limit: string;
  lock_minutes: string;
  dry_run: boolean;
};

type CycleFilterState = {
  status: string;
  limit: string;
};

type LatestResult =
  | {
      kind: "run-due";
      data: LivePaperScheduleRunDueResponse;
    }
  | {
      kind: "single";
      data: LivePaperScheduledRunItem;
    };

const defaultRunDueForm: RunDueFormState = {
  now: "",
  limit: "10",
  lock_minutes: "10",
  dry_run: true,
};

const defaultCycleFilters: CycleFilterState = {
  status: "",
  limit: "50",
};

const statusLabels: Record<string, string> = {
  active: "активно",
  paused: "пауза",
  archived: "архив",
  running: "в работе",
  completed: "завершен",
  blocked: "заблокирован",
  failed: "ошибка",
  skipped: "пропущен",
  dry_run: "dry-run",
  due: "due",
  ready: "готово",
  warning: "внимание",
  not_ready: "не готово",
};

const statusTone: Record<string, string> = {
  active: "border-teal-200 bg-teal-50 text-teal-800",
  completed: "border-teal-200 bg-teal-50 text-teal-800",
  paused: "border-amber-200 bg-amber-50 text-amber-800",
  blocked: "border-amber-200 bg-amber-50 text-amber-800",
  skipped: "border-slate-200 bg-slate-50 text-slate-700",
  dry_run: "border-sky-200 bg-sky-50 text-sky-800",
  failed: "border-red-200 bg-red-50 text-red-800",
  archived: "border-slate-200 bg-slate-50 text-slate-700",
  running: "border-sky-200 bg-sky-50 text-sky-800",
};

function toNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function formatPlain(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "—";
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

function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "medium",
  }).format(parsed);
}

function messageFromItem(item: unknown): string {
  if (!item || typeof item !== "object") {
    return formatPlain(item);
  }
  const record = item as Record<string, unknown>;
  const message = record.message ?? record.detail ?? record.code;
  if (
    typeof message === "string" ||
    typeof message === "number" ||
    typeof message === "boolean"
  ) {
    return String(message);
  }
  return JSON.stringify(record);
}

function validateRunDueForm(form: RunDueFormState): string[] {
  const errors: string[] = [];
  const limit = toNumber(form.limit);
  const lockMinutes = toNumber(form.lock_minutes);

  if (limit === null || limit < 1 || limit > 100) {
    errors.push("Limit должен быть от 1 до 100.");
  }
  if (lockMinutes === null || lockMinutes < 1 || lockMinutes > 120) {
    errors.push("Lock minutes должен быть от 1 до 120.");
  }
  if (form.now.trim() && Number.isNaN(Date.parse(form.now))) {
    errors.push("Now должен быть корректной датой, если заполнен.");
  }

  return errors;
}

function buildRunDuePayload(
  form: RunDueFormState,
  dryRun: boolean,
): LivePaperScheduleRunDueRequest {
  return {
    now: form.now.trim() || null,
    limit: Number(form.limit),
    dry_run: dryRun,
    lock_minutes: Number(form.lock_minutes),
  };
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex rounded-full border px-2 py-1 text-xs font-semibold ${
        statusTone[status] ?? "border-slate-200 bg-slate-50 text-slate-700"
      }`}
    >
      {statusLabels[status] ?? status}
    </span>
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
  items: unknown[];
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
              {messageFromItem(item)}
            </div>
          ))}
        </div>
      ) : (
        <EmptyState label="Нет записей для отображения." />
      )}
    </div>
  );
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

function cyclePortfolioId(cycle: LivePaperScheduledRunItem["cycle"]): number | null {
  const value = cycle?.portfolio_id;
  return typeof value === "number" ? value : null;
}

function RunDuePanel({
  form,
  setForm,
  validationErrors,
  executeConfirmed,
  setExecuteConfirmed,
  isPending,
  onDryRun,
  onExecute,
}: {
  form: RunDueFormState;
  setForm: (form: RunDueFormState) => void;
  validationErrors: string[];
  executeConfirmed: boolean;
  setExecuteConfirmed: (checked: boolean) => void;
  isPending: boolean;
  onDryRun: () => void;
  onExecute: () => void;
}) {
  return (
    <Section
      title="Due schedules"
      subtitle="Проверка и выполнение due schedules через DB-backed scheduler."
    >
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <label className="block text-sm">
            <span className="font-medium text-slate-700">Now</span>
            <input
              className="mt-1 w-full rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent"
              onChange={(event) => setForm({ ...form, now: event.target.value })}
              placeholder="2025-03-15T10:00:00Z"
              value={form.now}
            />
          </label>
          <label className="block text-sm">
            <span className="font-medium text-slate-700">Limit</span>
            <input
              className="mt-1 w-full rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent"
              onChange={(event) => setForm({ ...form, limit: event.target.value })}
              type="number"
              value={form.limit}
            />
          </label>
          <label className="block text-sm">
            <span className="font-medium text-slate-700">Lock minutes</span>
            <input
              className="mt-1 w-full rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent"
              onChange={(event) =>
                setForm({ ...form, lock_minutes: event.target.value })
              }
              type="number"
              value={form.lock_minutes}
            />
          </label>
          <label className="flex items-center gap-2 rounded-lg border border-line bg-white px-3 py-2 text-sm">
            <input
              checked={form.dry_run}
              onChange={(event) =>
                setForm({ ...form, dry_run: event.target.checked })
              }
              type="checkbox"
            />
            Dry-run only
          </label>
        </div>

        <div className="space-y-3">
          <button
            className="text-button w-full justify-center"
            disabled={isPending}
            onClick={onDryRun}
            type="button"
          >
            Проверить due schedules
          </button>
          <label className="flex items-start gap-2 rounded-lg border border-line bg-white p-3 text-sm text-slate-700">
            <input
              checked={executeConfirmed}
              className="mt-1"
              onChange={(event) => setExecuteConfirmed(event.target.checked)}
              type="checkbox"
            />
            <span>
              Я понимаю, что будут созданы виртуальные scheduled cycles в
              локальной системе.
            </span>
          </label>
          <button
            className="primary-button w-full justify-center disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!executeConfirmed || isPending}
            onClick={onExecute}
            type="button"
          >
            Выполнить due schedules
          </button>
        </div>
      </div>

      {validationErrors.length ? (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          <div className="flex items-center gap-2 font-semibold">
            <AlertTriangle size={16} />
            Проверьте параметры
          </div>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {validationErrors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </Section>
  );
}

function SchedulesTable({
  schedules,
  confirmedRuns,
  runNow,
  setRunNow,
  setConfirmedRuns,
  onToggleStatus,
  onRunSchedule,
  pendingScheduleId,
}: {
  schedules: LivePaperScheduleRead[];
  confirmedRuns: Record<number, boolean>;
  runNow: string;
  setRunNow: (value: string) => void;
  setConfirmedRuns: (value: Record<number, boolean>) => void;
  onToggleStatus: (schedule: LivePaperScheduleRead) => void;
  onRunSchedule: (schedule: LivePaperScheduleRead) => void;
  pendingScheduleId: number | null;
}) {
  if (!schedules.length) {
    return <EmptyState label="Live paper расписаний пока нет." />;
  }

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-line text-sm">
        <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
          <tr>
            <th className="px-3 py-2">ID</th>
            <th className="px-3 py-2">Название</th>
            <th className="px-3 py-2">Статус</th>
            <th className="px-3 py-2">Следующий запуск</th>
            <th className="px-3 py-2">Последний запуск</th>
            <th className="px-3 py-2">Run count</th>
            <th className="px-3 py-2">Max runs</th>
            <th className="px-3 py-2">Interval</th>
            <th className="px-3 py-2">Locked</th>
            <th className="px-3 py-2">Last cycle</th>
            <th className="px-3 py-2">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line bg-white">
          {schedules.map((schedule) => {
            const confirmed = Boolean(confirmedRuns[schedule.id]);
            const canToggle =
              schedule.status === "active" || schedule.status === "paused";
            return (
              <tr key={schedule.id}>
                <td className="px-3 py-3 font-mono text-xs text-slate-600">
                  {schedule.id}
                </td>
                <td className="px-3 py-3">
                  <div className="font-medium text-ink">{schedule.name}</div>
                  <div className="text-xs text-slate-500">{schedule.mode}</div>
                </td>
                <td className="px-3 py-3">
                  <StatusBadge status={schedule.status} />
                </td>
                <td className="px-3 py-3 text-slate-600">
                  {formatDateTime(schedule.next_run_at)}
                </td>
                <td className="px-3 py-3 text-slate-600">
                  {formatDateTime(schedule.last_run_at)}
                </td>
                <td className="px-3 py-3 text-slate-600">
                  {schedule.run_count}
                </td>
                <td className="px-3 py-3 text-slate-600">
                  {formatPlain(schedule.max_runs)}
                </td>
                <td className="px-3 py-3 text-slate-600">
                  {schedule.interval_days} д.
                </td>
                <td className="px-3 py-3 text-slate-600">
                  {schedule.locked_at ? (
                    <span>
                      да до {formatDateTime(schedule.lock_expires_at)}
                    </span>
                  ) : (
                    "нет"
                  )}
                </td>
                <td className="px-3 py-3 text-slate-600">
                  {formatPlain(schedule.last_cycle_run_id)}
                </td>
                <td className="px-3 py-3">
                  <div className="flex min-w-56 flex-col gap-2">
                    <details className="rounded-lg border border-line bg-slate-50 p-2">
                      <summary className="cursor-pointer text-xs font-semibold text-ink">
                        Открыть JSON
                      </summary>
                      <pre className="mt-2 max-h-60 overflow-auto rounded bg-slate-950 p-2 text-xs text-slate-100">
                        {JSON.stringify(schedule, null, 2)}
                      </pre>
                    </details>
                    <button
                      className="text-button justify-center disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={!canToggle || pendingScheduleId === schedule.id}
                      onClick={() => onToggleStatus(schedule)}
                      type="button"
                    >
                      {schedule.status === "active" ? "Пауза" : "Активировать"}
                    </button>
                    <input
                      className="w-full rounded-lg border border-line bg-white px-3 py-2 text-xs text-ink outline-none focus:border-accent"
                      onChange={(event) => setRunNow(event.target.value)}
                      placeholder="optional now"
                      value={runNow}
                    />
                    <label className="flex items-start gap-2 rounded-lg border border-line bg-white p-2 text-xs text-slate-700">
                      <input
                        checked={confirmed}
                        className="mt-0.5"
                        onChange={(event) =>
                          setConfirmedRuns({
                            ...confirmedRuns,
                            [schedule.id]: event.target.checked,
                          })
                        }
                        type="checkbox"
                      />
                      Подтверждаю запуск этого schedule
                    </label>
                    <button
                      className="primary-button justify-center disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={!confirmed || pendingScheduleId === schedule.id}
                      onClick={() => onRunSchedule(schedule)}
                      type="button"
                    >
                      Запустить schedule
                    </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function RunDueResultTable({ data }: { data: LivePaperScheduleRunDueResponse }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-line text-sm">
        <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
          <tr>
            <th className="px-3 py-2">Schedule ID</th>
            <th className="px-3 py-2">Schedule name</th>
            <th className="px-3 py-2">Status</th>
            <th className="px-3 py-2">Scheduled for</th>
            <th className="px-3 py-2">Cycle ID</th>
            <th className="px-3 py-2">Cycle status</th>
            <th className="px-3 py-2">Portfolio</th>
            <th className="px-3 py-2">Warnings</th>
            <th className="px-3 py-2">Errors</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line bg-white">
          {data.results.map((item, index) => {
            const portfolioId = cyclePortfolioId(item.cycle);
            return (
              <tr key={`${item.schedule.id}-${item.scheduled_for}-${index}`}>
                <td className="px-3 py-3 font-mono text-xs text-slate-600">
                  {item.schedule.id}
                </td>
                <td className="px-3 py-3 text-ink">{item.schedule.name}</td>
                <td className="px-3 py-3">
                  <StatusBadge status={item.status} />
                </td>
                <td className="px-3 py-3 text-slate-600">
                  {formatDateTime(item.scheduled_for)}
                </td>
                <td className="px-3 py-3 text-slate-600">
                  {formatPlain(item.cycle?.id)}
                </td>
                <td className="px-3 py-3">
                  {item.cycle?.status ? (
                    <StatusBadge status={item.cycle.status} />
                  ) : (
                    "—"
                  )}
                </td>
                <td className="px-3 py-3">
                  {portfolioId ? (
                    <Link
                      className="text-accent hover:underline"
                      to={`/live-paper/portfolios/${portfolioId}`}
                    >
                      #{portfolioId}
                    </Link>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="px-3 py-3 text-slate-600">
                  {item.warnings.length}
                </td>
                <td className="px-3 py-3 text-slate-600">{item.errors.length}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SingleRunResult({ data }: { data: LivePaperScheduledRunItem }) {
  const portfolioId = cyclePortfolioId(data.cycle);
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <SummaryItem label="Schedule" value={`#${data.schedule.id}`} />
        <SummaryItem label="Status" value={<StatusBadge status={data.status} />} />
        <SummaryItem
          label="Scheduled for"
          value={formatDateTime(data.scheduled_for)}
        />
        <SummaryItem label="Cycle" value={formatPlain(data.cycle?.id)} />
        <SummaryItem
          label="Cycle status"
          value={
            data.cycle?.status ? <StatusBadge status={data.cycle.status} /> : "—"
          }
        />
      </div>
      {portfolioId ? (
        <Link
          className="text-button inline-flex"
          to={`/live-paper/portfolios/${portfolioId}`}
        >
          Открыть портфель #{portfolioId}
        </Link>
      ) : null}
      <div className="grid gap-4 md:grid-cols-2">
        <MessageList
          items={data.warnings}
          title="Warnings"
          tone="border-amber-200 bg-amber-50 text-amber-800"
        />
        <MessageList
          items={data.errors}
          title="Errors"
          tone="border-red-200 bg-red-50 text-red-800"
        />
      </div>
    </div>
  );
}

function ResultPanel({ result }: { result: LatestResult }) {
  if (result.kind === "single") {
    return (
      <Section title="Single schedule result">
        <SingleRunResult data={result.data} />
      </Section>
    );
  }

  return (
    <Section title="Run-due result">
      <div className="mb-4 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <SummaryItem label="Now" value={formatDateTime(result.data.now)} />
        <SummaryItem
          label="Mode"
          value={result.data.dry_run ? "dry-run" : "execution"}
        />
        <SummaryItem label="Due schedules" value={result.data.due_schedule_count} />
        <SummaryItem label="Executed" value={result.data.executed_count} />
        <SummaryItem label="Skipped" value={result.data.skipped_count} />
      </div>
      <div className="mb-4 grid gap-4 md:grid-cols-2">
        <MessageList
          items={result.data.warnings}
          title="Warnings"
          tone="border-amber-200 bg-amber-50 text-amber-800"
        />
        <MessageList
          items={result.data.errors}
          title="Errors"
          tone="border-red-200 bg-red-50 text-red-800"
        />
      </div>
      {result.data.results.length ? (
        <RunDueResultTable data={result.data} />
      ) : (
        <EmptyState label="Run-due result не содержит schedule items." />
      )}
    </Section>
  );
}

function RecentCyclesTable({ cycles }: { cycles: LivePaperCycleMonitoringSummary[] }) {
  if (!cycles.length) {
    return <EmptyState label="Недавних live paper cycles пока нет." />;
  }

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-line text-sm">
        <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
          <tr>
            <th className="px-3 py-2">ID</th>
            <th className="px-3 py-2">Status</th>
            <th className="px-3 py-2">Schedule</th>
            <th className="px-3 py-2">Portfolio</th>
            <th className="px-3 py-2">As of date</th>
            <th className="px-3 py-2">Scheduled for</th>
            <th className="px-3 py-2">Readiness</th>
            <th className="px-3 py-2">Model run</th>
            <th className="px-3 py-2">Warnings</th>
            <th className="px-3 py-2">Errors</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line bg-white">
          {cycles.map((cycle) => (
            <tr key={cycle.id}>
              <td className="px-3 py-3 font-mono text-xs text-slate-600">
                {cycle.id}
              </td>
              <td className="px-3 py-3">
                <StatusBadge status={cycle.status} />
              </td>
              <td className="px-3 py-3 text-slate-600">
                {formatPlain(cycle.schedule_id)}
              </td>
              <td className="px-3 py-3">
                {cycle.portfolio_id ? (
                  <Link
                    className="text-accent hover:underline"
                    to={`/live-paper/portfolios/${cycle.portfolio_id}`}
                  >
                    #{cycle.portfolio_id}
                  </Link>
                ) : (
                  "—"
                )}
              </td>
              <td className="px-3 py-3 text-slate-600">
                {formatDate(cycle.as_of_date)}
              </td>
              <td className="px-3 py-3 text-slate-600">
                {formatDateTime(cycle.scheduled_for)}
              </td>
              <td className="px-3 py-3 text-slate-600">
                {cycle.readiness_status ? (
                  <StatusBadge status={cycle.readiness_status} />
                ) : (
                  "—"
                )}
              </td>
              <td className="px-3 py-3 text-slate-600">
                {formatPlain(cycle.selected_model_run_id)}
              </td>
              <td className="px-3 py-3 text-slate-600">
                {cycle.warning_count}
              </td>
              <td className="px-3 py-3 text-slate-600">{cycle.error_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function LivePaperSchedules() {
  const queryClient = useQueryClient();
  const [runDueForm, setRunDueForm] = useState<RunDueFormState>(defaultRunDueForm);
  const [runDueErrors, setRunDueErrors] = useState<string[]>([]);
  const [executeConfirmed, setExecuteConfirmed] = useState(false);
  const [singleRunNow, setSingleRunNow] = useState("");
  const [confirmedRuns, setConfirmedRuns] = useState<Record<number, boolean>>({});
  const [pendingScheduleId, setPendingScheduleId] = useState<number | null>(null);
  const [latestResult, setLatestResult] = useState<LatestResult | null>(null);
  const [cycleFilters, setCycleFilters] =
    useState<CycleFilterState>(defaultCycleFilters);

  const cycleLimit = toNumber(cycleFilters.limit);

  const schedulesQuery = useQuery({
    queryKey: ["live-paper", "schedules"],
    queryFn: () => api.getLivePaperSchedules({ limit: 100 }),
  });

  const cyclesQuery = useQuery({
    queryKey: ["live-paper", "cycles", cycleFilters],
    queryFn: () =>
      api.getLivePaperCycles({
        status: cycleFilters.status || null,
        limit: cycleLimit && cycleLimit > 0 ? cycleLimit : 50,
      }),
  });

  const schedules = schedulesQuery.data ?? [];
  const cycleResponse = cyclesQuery.data;

  const refreshLivePaper = () => {
    queryClient.invalidateQueries({ queryKey: ["live-paper", "schedules"] });
    queryClient.invalidateQueries({ queryKey: ["live-paper", "overview"] });
    queryClient.invalidateQueries({ queryKey: ["live-paper", "cycles"] });
  };

  const runDueMutation = useMutation({
    mutationFn: api.runDueLivePaperSchedules,
    onSuccess: (data) => {
      setLatestResult({ kind: "run-due", data });
      refreshLivePaper();
    },
  });

  const updateScheduleMutation = useMutation({
    mutationFn: ({
      scheduleId,
      status,
    }: {
      scheduleId: number;
      status: "active" | "paused";
    }) => api.updateLivePaperSchedule(scheduleId, { status }),
    onSuccess: () => {
      refreshLivePaper();
    },
    onSettled: () => {
      setPendingScheduleId(null);
    },
  });

  const singleRunMutation = useMutation({
    mutationFn: ({
      scheduleId,
      now,
    }: {
      scheduleId: number;
      now: string | null;
    }) => api.runLivePaperScheduleOnce(scheduleId, { now }),
    onSuccess: (data) => {
      setLatestResult({ kind: "single", data });
      setConfirmedRuns((current) => ({
        ...current,
        [data.schedule.id]: false,
      }));
      refreshLivePaper();
    },
    onSettled: () => {
      setPendingScheduleId(null);
    },
  });

  const activeScheduleCount = useMemo(
    () => schedules.filter((schedule) => schedule.status === "active").length,
    [schedules],
  );

  function submitRunDue(dryRun: boolean) {
    const errors = validateRunDueForm(runDueForm);
    setRunDueErrors(errors);
    if (errors.length) {
      return;
    }
    runDueMutation.mutate(buildRunDuePayload(runDueForm, dryRun));
  }

  function toggleScheduleStatus(schedule: LivePaperScheduleRead) {
    const nextStatus = schedule.status === "active" ? "paused" : "active";
    setPendingScheduleId(schedule.id);
    updateScheduleMutation.mutate({
      scheduleId: schedule.id,
      status: nextStatus,
    });
  }

  function runSchedule(schedule: LivePaperScheduleRead) {
    if (singleRunNow.trim() && Number.isNaN(Date.parse(singleRunNow))) {
      setRunDueErrors(["Now должен быть корректной датой, если заполнен."]);
      return;
    }
    setRunDueErrors([]);
    setPendingScheduleId(schedule.id);
    singleRunMutation.mutate({
      scheduleId: schedule.id,
      now: singleRunNow.trim() || null,
    });
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
            Scheduler Controls
          </p>
          <h1 className="mt-1 text-2xl font-semibold text-ink">
            Расписания Live Paper
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-600">
            Контроль расписаний, dry-run проверок и виртуальных scheduled cycles.
          </p>
        </div>
        <div className="surface flex items-start gap-2 px-3 py-2 text-sm text-slate-600">
          <ShieldCheck className="mt-0.5 shrink-0" size={16} />
          Информационный режим: действия создают только виртуальные paper-записи.
        </div>
      </section>

      <section className="grid gap-3 md:grid-cols-3">
        <SummaryItem label="Schedules" value={schedules.length} />
        <SummaryItem label="Active" value={activeScheduleCount} />
        <SummaryItem
          label="Recent cycles"
          value={cycleResponse?.total_returned ?? 0}
        />
      </section>

      <RunDuePanel
        executeConfirmed={executeConfirmed}
        form={runDueForm}
        isPending={runDueMutation.isPending}
        onDryRun={() => submitRunDue(true)}
        onExecute={() => submitRunDue(false)}
        setExecuteConfirmed={setExecuteConfirmed}
        setForm={setRunDueForm}
        validationErrors={runDueErrors}
      />

      {runDueMutation.isError ? (
        <ErrorState message={normalizeApiError(runDueMutation.error)} />
      ) : null}
      {updateScheduleMutation.isError ? (
        <ErrorState message={normalizeApiError(updateScheduleMutation.error)} />
      ) : null}
      {singleRunMutation.isError ? (
        <ErrorState message={normalizeApiError(singleRunMutation.error)} />
      ) : null}

      {runDueMutation.isPending ||
      updateScheduleMutation.isPending ||
      singleRunMutation.isPending ? (
        <LoadingState label="Обновление live paper schedules" />
      ) : null}

      <Section
        title="Schedule list"
        subtitle="Список live paper расписаний и безопасные действия с подтверждением."
      >
        {schedulesQuery.isLoading ? (
          <LoadingState label="Загрузка live paper schedules" />
        ) : null}
        {schedulesQuery.isError ? (
          <ErrorState message={normalizeApiError(schedulesQuery.error)} />
        ) : null}
        {schedulesQuery.data ? (
          <SchedulesTable
            confirmedRuns={confirmedRuns}
            onRunSchedule={runSchedule}
            onToggleStatus={toggleScheduleStatus}
            pendingScheduleId={pendingScheduleId}
            runNow={singleRunNow}
            schedules={schedulesQuery.data}
            setConfirmedRuns={setConfirmedRuns}
            setRunNow={setSingleRunNow}
          />
        ) : null}
      </Section>

      {latestResult ? (
        <ResultPanel result={latestResult} />
      ) : (
        <section className="surface flex items-start gap-3 p-4 text-sm text-slate-600">
          <FileJson className="mt-0.5 shrink-0" size={18} />
          <span>
            После dry-run или подтвержденного выполнения здесь появится результат
            scheduler operation.
          </span>
        </section>
      )}

      <Section
        title="Recent cycles"
        subtitle="Monitoring endpoint показывает недавние live paper cycles."
      >
        <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-end">
          <label className="block text-sm">
            <span className="font-medium text-slate-700">Status</span>
            <select
              className="mt-1 w-full rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent"
              onChange={(event) =>
                setCycleFilters({
                  ...cycleFilters,
                  status: event.target.value,
                })
              }
              value={cycleFilters.status}
            >
              <option value="">all</option>
              <option value="running">running</option>
              <option value="completed">completed</option>
              <option value="blocked">blocked</option>
              <option value="failed">failed</option>
            </select>
          </label>
          <label className="block text-sm">
            <span className="font-medium text-slate-700">Limit</span>
            <input
              className="mt-1 w-full rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-accent"
              onChange={(event) =>
                setCycleFilters({
                  ...cycleFilters,
                  limit: event.target.value,
                })
              }
              type="number"
              value={cycleFilters.limit}
            />
          </label>
          <button
            className="text-button inline-flex items-center gap-2"
            onClick={() =>
              queryClient.invalidateQueries({ queryKey: ["live-paper", "cycles"] })
            }
            type="button"
          >
            <RefreshCw size={16} />
            Обновить
          </button>
        </div>

        {cyclesQuery.isLoading ? (
          <LoadingState label="Загрузка live paper cycles" />
        ) : null}
        {cyclesQuery.isError ? (
          <ErrorState message={normalizeApiError(cyclesQuery.error)} />
        ) : null}
        {cycleResponse ? <RecentCyclesTable cycles={cycleResponse.cycles} /> : null}
        {cycleResponse?.alerts.length ? (
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <MessageList
              items={cycleResponse.alerts}
              title="Cycle monitoring alerts"
              tone="border-amber-200 bg-amber-50 text-amber-800"
            />
          </div>
        ) : null}
      </Section>

      <section className="surface flex items-start gap-3 border-teal-200 bg-teal-50 p-4 text-sm text-teal-800">
        <CheckCircle2 className="mt-0.5 shrink-0" size={18} />
        <span>
          Эта страница вызывает только scheduler endpoints и monitoring cycles;
          низкоуровневые portfolio-действия напрямую не вызываются.
        </span>
      </section>

    </div>
  );
}
