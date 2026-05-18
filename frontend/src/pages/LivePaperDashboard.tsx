import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  CalendarClock,
  LineChart as LineChartIcon,
  WalletCards,
} from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api, normalizeApiError } from "../api/client";
import type {
  LivePaperAlertLevel,
  LivePaperCycleMonitoringSummary,
  LivePaperHealthStatus,
  LivePaperMonitoringAlert,
  LivePaperPortfolioMonitoringSummary,
  LivePaperScheduleMonitoringSummary,
} from "../api/types";
import { EmptyState, ErrorState, LoadingState } from "../components/StateBlocks";

const healthTone: Record<LivePaperHealthStatus, string> = {
  healthy: "border-teal-200 bg-teal-50 text-teal-800",
  warning: "border-amber-200 bg-amber-50 text-amber-800",
  critical: "border-red-200 bg-red-50 text-red-800",
  unknown: "border-slate-200 bg-slate-50 text-slate-700",
};

const alertTone: Record<LivePaperAlertLevel, string> = {
  info: "border-slate-200 bg-slate-50 text-slate-700",
  warning: "border-amber-200 bg-amber-50 text-amber-800",
  critical: "border-red-200 bg-red-50 text-red-800",
};

const alertLabels: Record<string, string> = {
  no_active_schedules: "Нет активных расписаний",
  schedule_due: "Расписание ожидает запуска",
  schedule_locked: "Расписание заблокировано активным запуском",
  schedule_lock_stale: "Блокировка расписания выглядит устаревшей",
  schedule_max_runs_reached: "Достигнут максимум запусков",
  last_cycle_failed: "Последний цикл завершился ошибкой",
  last_cycle_blocked: "Последний цикл был заблокирован проверками",
  recent_failed_cycles: "В недавних циклах есть ошибки",
  recent_blocked_cycles: "В недавних циклах есть заблокированные результаты",
  stale_running_cycle: "Запущенный цикл выглядит зависшим",
  portfolio_no_snapshots: "У портфеля нет снимков состояния",
  portfolio_no_active_positions: "У портфеля нет активных позиций",
  portfolio_archived: "Портфель в архиве",
};

const healthLabels: Record<LivePaperHealthStatus, string> = {
  healthy: "здорово",
  warning: "внимание",
  critical: "критично",
  unknown: "нет данных",
};

const statusLabels: Record<string, string> = {
  active: "активно",
  paused: "пауза",
  archived: "архив",
  running: "в работе",
  completed: "завершен",
  blocked: "заблокирован",
  failed: "ошибка",
  ready: "готово",
  warning: "внимание",
  not_ready: "не готово",
};

function toNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function formatPlain(value: unknown, digits = 2): string {
  const numeric = toNumber(value);
  if (numeric === null) {
    return value === null || value === undefined || value === "" ? "—" : String(value);
  }
  return new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: digits,
  }).format(numeric);
}

function formatMoney(value: unknown, currency = "RUB"): string {
  const numeric = toNumber(value);
  if (numeric === null) {
    return "—";
  }
  try {
    return new Intl.NumberFormat("ru-RU", {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    }).format(numeric);
  } catch {
    return `${formatPlain(numeric, 0)} ${currency}`;
  }
}

function formatPercent(value: unknown): string {
  const numeric = toNumber(value);
  if (numeric === null) {
    return "—";
  }
  return new Intl.NumberFormat("ru-RU", {
    style: "percent",
    maximumFractionDigits: 2,
  }).format(numeric);
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatDateOnly(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
}

function statusLabel(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  return statusLabels[value] ?? value.replaceAll("_", " ");
}

function recordValue(
  record: Record<string, unknown>,
  keys: string[],
): unknown {
  for (const key of keys) {
    const value = record[key];
    if (value !== null && value !== undefined && value !== "") {
      return value;
    }
  }
  return null;
}

function HealthBadge({ status }: { status: LivePaperHealthStatus }) {
  return (
    <span
      className={`inline-flex items-center border px-2 py-1 text-xs font-semibold ${healthTone[status]}`}
      style={{ borderRadius: 8 }}
    >
      {healthLabels[status]}
    </span>
  );
}

function StatusPill({ status }: { status: string | null | undefined }) {
  const value = status ?? "unknown";
  const tone =
    value === "completed" || value === "ready" || value === "active"
      ? "border-teal-200 bg-teal-50 text-teal-800"
      : value === "blocked" || value === "warning" || value === "paused"
        ? "border-amber-200 bg-amber-50 text-amber-800"
        : value === "failed" || value === "critical"
          ? "border-red-200 bg-red-50 text-red-800"
          : "border-slate-200 bg-slate-50 text-slate-700";
  return (
    <span
      className={`inline-flex items-center border px-2 py-1 text-xs font-semibold ${tone}`}
      style={{ borderRadius: 8 }}
    >
      {statusLabel(value)}
    </span>
  );
}

function MetricCard({
  title,
  value,
  detail,
  icon,
  status,
}: {
  title: string;
  value: string;
  detail: string;
  icon: ReactNode;
  status?: LivePaperHealthStatus;
}) {
  return (
    <div className="surface p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase text-slate-500">
            {title}
          </div>
          <div className="mt-2 text-2xl font-semibold text-ink">{value}</div>
        </div>
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-slate-50 text-accent">
          {icon}
        </div>
      </div>
      <div className="mt-3 flex items-center justify-between gap-3 text-sm text-slate-500">
        <span>{detail}</span>
        {status ? <HealthBadge status={status} /> : null}
      </div>
    </div>
  );
}

function SectionHeader({
  title,
  subtitle,
}: {
  title: string;
  subtitle?: string;
}) {
  return (
    <div className="mb-3">
      <h2 className="text-base font-semibold text-ink">{title}</h2>
      {subtitle ? <p className="mt-1 text-sm text-slate-500">{subtitle}</p> : null}
    </div>
  );
}

function AlertList({ alerts }: { alerts: LivePaperMonitoringAlert[] }) {
  if (!alerts.length) {
    return <EmptyState label="Активных предупреждений нет." />;
  }
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {alerts.map((alert, index) => (
        <div
          key={`${alert.code}-${index}`}
          className={`border p-3 ${alertTone[alert.level]}`}
          style={{ borderRadius: 8 }}
        >
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 shrink-0" size={17} />
            <div>
              <div className="text-sm font-semibold">
                {alertLabels[alert.code] ?? alert.message}
              </div>
              <div className="mt-1 text-xs opacity-80">{alert.code}</div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function SchedulesTable({
  schedules,
}: {
  schedules: LivePaperScheduleMonitoringSummary[];
}) {
  if (!schedules.length) {
    return <EmptyState label="Расписания пока не настроены." />;
  }
  return (
    <div className="surface overflow-hidden">
      <div className="overflow-x-auto">
        <table className="min-w-full border-separate border-spacing-0 text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="border-b border-line px-4 py-3">ID</th>
              <th className="border-b border-line px-4 py-3">Название</th>
              <th className="border-b border-line px-4 py-3">Статус</th>
              <th className="border-b border-line px-4 py-3">Следующий запуск</th>
              <th className="border-b border-line px-4 py-3">Последний запуск</th>
              <th className="border-b border-line px-4 py-3">Запусков</th>
              <th className="border-b border-line px-4 py-3">Интервал</th>
              <th className="border-b border-line px-4 py-3">Состояние</th>
            </tr>
          </thead>
          <tbody>
            {schedules.map((schedule) => (
              <tr key={schedule.id} className="hover:bg-slate-50">
                <td className="border-b border-line px-4 py-3 text-slate-500">
                  {schedule.id}
                </td>
                <td className="border-b border-line px-4 py-3 font-medium text-ink">
                  {schedule.name}
                </td>
                <td className="border-b border-line px-4 py-3">
                  <StatusPill status={schedule.status} />
                </td>
                <td className="border-b border-line px-4 py-3">
                  {formatDateTime(schedule.next_run_at)}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {formatDateTime(schedule.last_run_at)}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {schedule.run_count}
                  {schedule.max_runs !== null ? ` / ${schedule.max_runs}` : ""}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {schedule.interval_days} дн.
                </td>
                <td className="border-b border-line px-4 py-3">
                  <HealthBadge status={schedule.health_status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PortfolioSelector({
  portfolios,
  selectedPortfolioId,
  onSelect,
}: {
  portfolios: LivePaperPortfolioMonitoringSummary[];
  selectedPortfolioId: number | null;
  onSelect: (portfolioId: number) => void;
}) {
  if (!portfolios.length) {
    return <EmptyState label="Виртуальные портфели пока не созданы." />;
  }
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {portfolios.map((portfolio) => {
        const selected = portfolio.id === selectedPortfolioId;
        return (
          <button
            key={portfolio.id}
            type="button"
            className={`border bg-white p-3 text-left transition hover:border-accent ${
              selected ? "border-accent ring-1 ring-accent" : "border-line"
            }`}
            style={{ borderRadius: 8 }}
            onClick={() => onSelect(portfolio.id)}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="font-medium text-ink">{portfolio.name}</div>
                <div className="mt-1 text-xs text-slate-500">
                  ID {portfolio.id} · model run {portfolio.model_run_id ?? "—"}
                </div>
              </div>
              <HealthBadge status={portfolio.health_status} />
            </div>
            <div className="mt-3 text-sm text-slate-600">
              {formatMoney(portfolio.current_value, portfolio.base_currency)}
            </div>
          </button>
        );
      })}
    </div>
  );
}

function PortfolioSummary({
  portfolio,
}: {
  portfolio: LivePaperPortfolioMonitoringSummary;
}) {
  const items = [
    ["Текущая стоимость", formatMoney(portfolio.current_value, portfolio.base_currency)],
    ["Свободные средства", formatMoney(portfolio.cash_balance, portfolio.base_currency)],
    ["Начальный капитал", formatMoney(portfolio.initial_capital, portfolio.base_currency)],
    ["Накопленный результат", formatPercent(portfolio.cumulative_return)],
    ["Макс. просадка", formatPercent(portfolio.max_drawdown)],
    ["Активные позиции", String(portfolio.active_positions_count)],
    ["Снимки состояния", String(portfolio.snapshot_count)],
    ["Последний снимок", formatDateOnly(portfolio.latest_snapshot_date)],
    ["Model run", portfolio.model_run_id?.toString() ?? "—"],
  ];

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {items.map(([label, value]) => (
        <div
          key={label}
          className="border border-line bg-slate-50 px-3 py-2"
          style={{ borderRadius: 8 }}
        >
          <div className="text-xs font-semibold uppercase text-slate-500">
            {label}
          </div>
          <div className="mt-1 text-sm font-medium text-slate-800">{value}</div>
        </div>
      ))}
    </div>
  );
}

function EquityChart({ points }: { points: Array<Record<string, unknown>> }) {
  const data = points
    .map((point) => ({
      date: typeof point.as_of_date === "string" ? point.as_of_date : "",
      portfolioValue: toNumber(point.portfolio_value),
    }))
    .filter((point) => point.date && point.portfolioValue !== null);

  if (!data.length) {
    return <EmptyState label="Точек кривой стоимости пока нет." />;
  }

  return (
    <div className="h-72">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ left: 8, right: 12, top: 10, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="date" tickFormatter={formatDateOnly} />
          <YAxis tickFormatter={(value) => formatPlain(value, 0)} width={72} />
          <Tooltip
            labelFormatter={(value) => formatDateOnly(String(value))}
            formatter={(value) => [formatMoney(value), "Стоимость"]}
          />
          <Line
            type="monotone"
            dataKey="portfolioValue"
            stroke="#0f766e"
            strokeWidth={2}
            dot={{ r: 3 }}
            activeDot={{ r: 5 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function PositionsTable({ positions }: { positions: Array<Record<string, unknown>> }) {
  if (!positions.length) {
    return <EmptyState label="Активные позиции пока не отображаются." />;
  }
  return (
    <div className="surface overflow-hidden">
      <div className="overflow-x-auto">
        <table className="min-w-full border-separate border-spacing-0 text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="border-b border-line px-4 py-3">Bond ID</th>
              <th className="border-b border-line px-4 py-3">Company ID</th>
              <th className="border-b border-line px-4 py-3">Current amount</th>
              <th className="border-b border-line px-4 py-3">Current weight</th>
              <th className="border-b border-line px-4 py-3">Probability</th>
              <th className="border-b border-line px-4 py-3">Risk level</th>
              <th className="border-b border-line px-4 py-3">Status</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((position, index) => (
              <tr key={`${recordValue(position, ["id"])}-${index}`} className="hover:bg-slate-50">
                <td className="border-b border-line px-4 py-3">
                  {formatPlain(recordValue(position, ["bond_id"]), 0)}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {formatPlain(recordValue(position, ["company_id"]), 0)}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {formatMoney(recordValue(position, ["current_amount", "allocation_amount"]))}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {formatPercent(recordValue(position, ["current_weight", "allocation_weight"]))}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {formatPercent(recordValue(position, ["probability_positive"]))}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {String(recordValue(position, ["risk_level"]) ?? "—")}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {recordValue(position, ["is_active"]) === false ? "неактивна" : "активна"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CyclesTable({ cycles }: { cycles: LivePaperCycleMonitoringSummary[] }) {
  if (!cycles.length) {
    return <EmptyState label="Недавних циклов пока нет." />;
  }
  return (
    <div className="surface overflow-hidden">
      <div className="overflow-x-auto">
        <table className="min-w-full border-separate border-spacing-0 text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="border-b border-line px-4 py-3">ID</th>
              <th className="border-b border-line px-4 py-3">Статус</th>
              <th className="border-b border-line px-4 py-3">Портфель</th>
              <th className="border-b border-line px-4 py-3">Расписание</th>
              <th className="border-b border-line px-4 py-3">Дата</th>
              <th className="border-b border-line px-4 py-3">Readiness</th>
              <th className="border-b border-line px-4 py-3">Model run</th>
              <th className="border-b border-line px-4 py-3">Warnings</th>
              <th className="border-b border-line px-4 py-3">Errors</th>
            </tr>
          </thead>
          <tbody>
            {cycles.map((cycle) => (
              <tr key={cycle.id} className="hover:bg-slate-50">
                <td className="border-b border-line px-4 py-3 text-slate-500">
                  {cycle.id}
                </td>
                <td className="border-b border-line px-4 py-3">
                  <StatusPill status={cycle.status} />
                </td>
                <td className="border-b border-line px-4 py-3">
                  {cycle.portfolio_id ?? "—"}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {cycle.schedule_id ?? "—"}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {formatDateOnly(cycle.as_of_date ?? cycle.scheduled_for)}
                </td>
                <td className="border-b border-line px-4 py-3">
                  <StatusPill status={cycle.readiness_status} />
                </td>
                <td className="border-b border-line px-4 py-3">
                  {cycle.selected_model_run_id ?? "—"}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {cycle.warning_count}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {cycle.error_count}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function LivePaperDashboard() {
  const [selectedPortfolioId, setSelectedPortfolioId] = useState<number | null>(null);

  const overviewQuery = useQuery({
    queryKey: ["live-paper", "overview"],
    queryFn: api.getLivePaperOverview,
  });

  useEffect(() => {
    const portfolios = overviewQuery.data?.portfolios ?? [];
    if (!portfolios.length) {
      if (selectedPortfolioId !== null) {
        setSelectedPortfolioId(null);
      }
      return;
    }
    if (
      selectedPortfolioId === null ||
      !portfolios.some((portfolio) => portfolio.id === selectedPortfolioId)
    ) {
      setSelectedPortfolioId(portfolios[0].id);
    }
  }, [overviewQuery.data, selectedPortfolioId]);

  const portfolioQuery = useQuery({
    queryKey: ["live-paper", "portfolio", selectedPortfolioId],
    queryFn: () => api.getLivePaperPortfolio(selectedPortfolioId!),
    enabled: selectedPortfolioId !== null,
  });

  const selectedPortfolio = useMemo(() => {
    return overviewQuery.data?.portfolios.find(
      (portfolio) => portfolio.id === selectedPortfolioId,
    );
  }, [overviewQuery.data?.portfolios, selectedPortfolioId]);

  const overview = overviewQuery.data;
  const portfolioDetail = portfolioQuery.data;

  return (
    <div className="space-y-5">
      <section className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-sm font-semibold uppercase text-accent">Live Paper</p>
          <h1 className="mt-1 text-2xl font-semibold text-ink">
            Виртуальный контур наблюдения за paper-портфелем
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-600">
            Информационный режим: без реальных операций и брокерских действий.
          </p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <Link className="primary-button" to="/live-paper/pilot-bootstrap">
            Подготовить pilot schedule
          </Link>
          {overview ? (
            <div className="surface px-3 py-2 text-sm text-slate-600">
              Обновлено: {formatDateTime(overview.now)}
            </div>
          ) : null}
        </div>
      </section>

      {overviewQuery.isLoading ? (
        <LoadingState label="Загрузка live paper мониторинга" />
      ) : null}
      {overviewQuery.isError ? (
        <ErrorState message={normalizeApiError(overviewQuery.error)} />
      ) : null}

      {overview ? (
        <>
          <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <MetricCard
              title="Состояние системы"
              value={healthLabels[overview.health_status]}
              detail={`${overview.failed_cycle_count} ошибок в недавних циклах`}
              icon={<Activity size={20} />}
              status={overview.health_status}
            />
            <MetricCard
              title="Активные расписания"
              value={`${overview.active_schedule_count} / ${overview.schedule_count}`}
              detail={`${overview.due_schedule_count} ожидают запуска`}
              icon={<CalendarClock size={20} />}
            />
            <MetricCard
              title="Виртуальные портфели"
              value={`${overview.active_portfolio_count} / ${overview.portfolio_count}`}
              detail={`${overview.locked_schedule_count} активных блокировок`}
              icon={<WalletCards size={20} />}
            />
            <MetricCard
              title="Последние циклы"
              value={`${overview.completed_cycle_count} / ${overview.recent_cycle_count}`}
              detail={`${overview.blocked_cycle_count} заблокированы`}
              icon={<LineChartIcon size={20} />}
            />
          </section>

          <section className="surface p-4">
            <SectionHeader
              title="Предупреждения"
              subtitle="Сигналы мониторинга по расписаниям, циклам и портфелям."
            />
            <AlertList alerts={overview.alerts} />
          </section>

          <section>
            <SectionHeader
              title="Расписания"
              subtitle="Текущие настройки автоматизированного paper-наблюдения."
            />
            <SchedulesTable schedules={overview.schedules} />
          </section>

          <section className="surface p-4">
            <SectionHeader
              title="Виртуальные портфели"
              subtitle="Выберите портфель для просмотра деталей."
            />
            <PortfolioSelector
              portfolios={overview.portfolios}
              selectedPortfolioId={selectedPortfolioId}
              onSelect={setSelectedPortfolioId}
            />
          </section>

          {selectedPortfolio ? (
            <section className="surface p-4">
              <SectionHeader
                title={selectedPortfolio.name}
                subtitle={`Портфель #${selectedPortfolio.id}`}
              />
              <div className="mb-3">
                <Link
                  className="text-button"
                  to={`/live-paper/portfolios/${selectedPortfolio.id}`}
                >
                  Открыть детали
                </Link>
              </div>
              <PortfolioSummary portfolio={selectedPortfolio} />
            </section>
          ) : null}

          {selectedPortfolioId !== null ? (
            <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
              <div className="surface p-4">
                <SectionHeader
                  title="Кривая стоимости"
                  subtitle="Динамика виртуального портфеля по снимкам состояния."
                />
                {portfolioQuery.isLoading ? (
                  <LoadingState label="Загрузка кривой стоимости" />
                ) : null}
                {portfolioQuery.isError ? (
                  <ErrorState message={normalizeApiError(portfolioQuery.error)} />
                ) : null}
                {portfolioDetail ? (
                  <EquityChart points={portfolioDetail.equity_curve} />
                ) : null}
              </div>

              <div className="surface p-4">
                <SectionHeader
                  title="Детали портфеля"
                  subtitle="Сводка из мониторинга и отчетов paper-контура."
                />
                {portfolioDetail ? (
                  <div className="space-y-3 text-sm text-slate-600">
                    <div className="flex justify-between gap-3">
                      <span>Состояние</span>
                      <HealthBadge status={portfolioDetail.portfolio.health_status} />
                    </div>
                    <div className="flex justify-between gap-3">
                      <span>Позиции</span>
                      <span>{portfolioDetail.positions.length}</span>
                    </div>
                    <div className="flex justify-between gap-3">
                      <span>Недавние циклы</span>
                      <span>{portfolioDetail.recent_cycles.length}</span>
                    </div>
                    <div className="flex justify-between gap-3">
                      <span>Предупреждения</span>
                      <span>{portfolioDetail.alerts.length}</span>
                    </div>
                  </div>
                ) : portfolioQuery.isLoading ? null : (
                  <EmptyState label="Детали портфеля пока недоступны." />
                )}
              </div>
            </section>
          ) : null}

          {portfolioDetail ? (
            <section>
              <SectionHeader
                title="Позиции"
                subtitle="Компактный состав выбранного виртуального портфеля."
              />
              <PositionsTable positions={portfolioDetail.positions} />
            </section>
          ) : null}

          <section>
            <SectionHeader
              title="Недавние циклы"
              subtitle="Последние результаты live paper процесса."
            />
            <CyclesTable cycles={overview.recent_cycles} />
          </section>
        </>
      ) : null}
    </div>
  );
}
