import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { ArrowLeft, AlertTriangle, BarChart3, ListChecks } from "lucide-react";
import { Link, useParams } from "react-router-dom";
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
  LivePaperMonitoringAlert,
  LivePaperPortfolioMonitoringSummary,
  PaperPortfolioOperation,
  PaperPortfolioPosition,
  PaperPortfolioSnapshot,
  PaperTradingContributionItem,
  PaperTradingEquityPoint,
  PaperTradingPerformanceResponse,
} from "../api/types";
import {
  HealthBadge,
  StatusBadge as StatusPill,
} from "../components/live-paper/LivePaperBadges";
import { EmptyState, ErrorState, LoadingState } from "../components/StateBlocks";
import {
  formatDateOnly,
  formatMoney,
  formatPercent,
  formatPlain,
  toNumber,
} from "../utils/livePaperFormat";

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

const operationLabels: Record<string, string> = {
  open_position: "добавление позиции",
  increase_position: "увеличение позиции",
  reduce_position: "сокращение позиции",
  close_position: "закрытие позиции",
  rebalance: "ребалансировка",
  rebalance_fee: "комиссия ребалансировки",
  fee: "комиссия",
  period_result: "результат периода",
  period_return: "результат периода",
  portfolio_created: "создание виртуального портфеля",
  allocation_increase: "увеличение размещения",
  allocation_decrease: "сокращение размещения",
  allocation_removed: "закрытие размещения",
};

function operationLabel(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  return operationLabels[value] ?? "операция портфеля";
}

function scalarEntries(
  value: Record<string, unknown> | null | undefined,
  limit = 8,
): Array<[string, unknown]> {
  if (!value) {
    return [];
  }
  return Object.entries(value)
    .filter(([, item]) => {
      return (
        item === null ||
        typeof item === "string" ||
        typeof item === "number" ||
        typeof item === "boolean"
      );
    })
    .slice(0, limit);
}

function nestedRecord(
  value: Record<string, unknown> | null | undefined,
  key: string,
): Record<string, unknown> | null {
  const item = value?.[key];
  return item && typeof item === "object" && !Array.isArray(item)
    ? (item as Record<string, unknown>)
    : null;
}

function SectionHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
      <div>
        <h2 className="text-base font-semibold text-ink">{title}</h2>
        {subtitle ? <p className="mt-1 text-sm text-slate-500">{subtitle}</p> : null}
      </div>
      {action}
    </div>
  );
}

function SummaryCards({
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
    ["Запуск модели", portfolio.model_run_id?.toString() ?? "—"],
  ];

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {items.map(([label, value]) => (
        <div key={label} className="surface p-4">
          <div className="text-xs font-semibold uppercase text-slate-500">
            {label}
          </div>
          <div className="mt-2 text-lg font-semibold text-ink">{value}</div>
        </div>
      ))}
    </div>
  );
}

function AlertList({ alerts }: { alerts: LivePaperMonitoringAlert[] }) {
  if (!alerts.length) {
    return <EmptyState label="Активных предупреждений по портфелю нет." />;
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

function EquityChart({
  points,
}: {
  points: Array<PaperTradingEquityPoint | Record<string, unknown>>;
}) {
  const data = points
    .map((point) => {
      const record = point as Record<string, unknown>;
      const date =
        typeof record.as_of_date === "string"
          ? record.as_of_date
          : typeof record.date === "string"
            ? record.date
            : typeof record.snapshot_date === "string"
              ? record.snapshot_date
              : "";
      const value =
        toNumber(record.portfolio_value) ??
        toNumber(record.current_value) ??
        toNumber(record.value);
      return { date, portfolioValue: value };
    })
    .filter((point) => point.date && point.portfolioValue !== null);

  if (!data.length) {
    return <EmptyState label="Кривая стоимости пока недоступна." />;
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

function PositionsTable({ positions }: { positions: PaperPortfolioPosition[] }) {
  if (!positions.length) {
    return <EmptyState label="Позиции пока не отображаются." />;
  }
  return (
    <div className="surface overflow-hidden">
      <div className="overflow-x-auto">
        <table className="min-w-full border-separate border-spacing-0 text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="border-b border-line px-4 py-3">Облигация</th>
              <th className="border-b border-line px-4 py-3">Эмитент</th>
              <th className="border-b border-line px-4 py-3">Сумма</th>
              <th className="border-b border-line px-4 py-3">Вес</th>
              <th className="border-b border-line px-4 py-3">Вероятность</th>
              <th className="border-b border-line px-4 py-3">Риск</th>
              <th className="border-b border-line px-4 py-3">Статус</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((position) => (
              <tr key={position.id} className="hover:bg-slate-50">
                <td className="border-b border-line px-4 py-3">
                  {position.bond_id}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {position.company_id ?? "—"}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {formatMoney(position.current_amount)}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {formatPercent(position.allocation_weight)}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {formatPercent(position.probability_positive)}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {position.risk_level ?? "—"}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {position.is_active ? "активна" : "неактивна"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function OperationsTable({ operations }: { operations: PaperPortfolioOperation[] }) {
  if (!operations.length) {
    return <EmptyState label="Операции по портфелю пока не найдены." />;
  }
  return (
    <div className="surface overflow-hidden">
      <div className="overflow-x-auto">
        <table className="min-w-full border-separate border-spacing-0 text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="border-b border-line px-4 py-3">Дата</th>
              <th className="border-b border-line px-4 py-3">Тип операции</th>
              <th className="border-b border-line px-4 py-3">Облигация</th>
              <th className="border-b border-line px-4 py-3">Эмитент</th>
              <th className="border-b border-line px-4 py-3">Сумма</th>
              <th className="border-b border-line px-4 py-3">Количество</th>
              <th className="border-b border-line px-4 py-3">Цена</th>
              <th className="border-b border-line px-4 py-3">Комиссия</th>
              <th className="border-b border-line px-4 py-3">Цикл</th>
              <th className="border-b border-line px-4 py-3">Запуск модели</th>
            </tr>
          </thead>
          <tbody>
            {operations.map((operation) => (
              <tr key={operation.id} className="hover:bg-slate-50">
                <td className="border-b border-line px-4 py-3">
                  {formatDateOnly(operation.as_of_date ?? operation.created_at)}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {operationLabel(operation.operation_type ?? operation.transaction_type)}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {operation.bond_id ?? "—"}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {operation.company_id ?? "—"}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {formatMoney(operation.amount_delta ?? operation.amount)}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {formatPlain(operation.quantity, 4)}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {formatPlain(operation.price, 4)}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {formatMoney(operation.fee_amount)}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {operation.cycle_run_id ?? "—"}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {operation.model_run_id ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SnapshotsTable({ snapshots }: { snapshots: PaperPortfolioSnapshot[] }) {
  if (!snapshots.length) {
    return <EmptyState label="Снимки состояния пока недоступны." />;
  }
  return (
    <div className="surface overflow-hidden">
      <div className="overflow-x-auto">
        <table className="min-w-full border-separate border-spacing-0 text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="border-b border-line px-4 py-3">Дата</th>
              <th className="border-b border-line px-4 py-3">Стоимость</th>
              <th className="border-b border-line px-4 py-3">Свободные средства</th>
              <th className="border-b border-line px-4 py-3">Размещенная сумма</th>
              <th className="border-b border-line px-4 py-3">Результат периода</th>
              <th className="border-b border-line px-4 py-3">Накопленный результат</th>
              <th className="border-b border-line px-4 py-3">Просадка</th>
            </tr>
          </thead>
          <tbody>
            {snapshots.map((snapshot) => {
              const maxDrawdown =
                snapshot.max_drawdown ?? snapshot.metrics_json?.max_drawdown ?? null;
              return (
                <tr key={snapshot.id} className="hover:bg-slate-50">
                  <td className="border-b border-line px-4 py-3">
                    {formatDateOnly(snapshot.as_of_date)}
                  </td>
                  <td className="border-b border-line px-4 py-3">
                    {formatMoney(snapshot.portfolio_value)}
                  </td>
                  <td className="border-b border-line px-4 py-3">
                    {formatMoney(snapshot.cash_balance)}
                  </td>
                  <td className="border-b border-line px-4 py-3">
                    {formatMoney(snapshot.allocated_value)}
                  </td>
                  <td className="border-b border-line px-4 py-3">
                    {formatPercent(snapshot.period_return)}
                  </td>
                  <td className="border-b border-line px-4 py-3">
                    {formatPercent(snapshot.cumulative_return)}
                  </td>
                  <td className="border-b border-line px-4 py-3">
                    {formatPercent(maxDrawdown)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CyclesTable({ cycles }: { cycles: LivePaperCycleMonitoringSummary[] }) {
  if (!cycles.length) {
    return <EmptyState label="Недавних циклов по портфелю пока нет." />;
  }
  return (
    <div className="surface overflow-hidden">
      <div className="overflow-x-auto">
        <table className="min-w-full border-separate border-spacing-0 text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="border-b border-line px-4 py-3">№</th>
              <th className="border-b border-line px-4 py-3">Статус</th>
              <th className="border-b border-line px-4 py-3">Расписание</th>
              <th className="border-b border-line px-4 py-3">Дата</th>
              <th className="border-b border-line px-4 py-3">Готовность</th>
              <th className="border-b border-line px-4 py-3">Запуск модели</th>
              <th className="border-b border-line px-4 py-3">Предупр.</th>
              <th className="border-b border-line px-4 py-3">Ошибки</th>
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

function KeyValueCard({
  title,
  values,
}: {
  title: string;
  values: Array<[string, unknown]>;
}) {
  return (
    <div className="surface p-4">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
        <ListChecks size={17} />
        <span>{title}</span>
      </div>
      {values.length ? (
        <div className="space-y-2 text-sm">
          {values.map(([key, value]) => (
            <div key={key} className="flex justify-between gap-3">
              <span className="text-slate-500">{key}</span>
              <span className="font-medium text-slate-800">
                {typeof value === "number" || typeof value === "string"
                  ? formatPlain(value, 4)
                  : String(value)}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-sm text-slate-500">Данные пока недоступны.</div>
      )}
    </div>
  );
}

function ContributionsTable({
  items,
}: {
  items: PaperTradingContributionItem[];
}) {
  if (!items.length) {
    return <EmptyState label="Вклад по облигациям пока недоступен." />;
  }
  return (
    <div className="surface overflow-hidden">
      <div className="overflow-x-auto">
        <table className="min-w-full border-separate border-spacing-0 text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="border-b border-line px-4 py-3">Облигация</th>
              <th className="border-b border-line px-4 py-3">Эмитент</th>
              <th className="border-b border-line px-4 py-3">Период</th>
              <th className="border-b border-line px-4 py-3">Изменение размещения</th>
              <th className="border-b border-line px-4 py-3">Комиссия</th>
              <th className="border-b border-line px-4 py-3">Текущая сумма</th>
              <th className="border-b border-line px-4 py-3">Статус</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item, index) => (
              <tr key={`${item.bond_id ?? "none"}-${index}`} className="hover:bg-slate-50">
                <td className="border-b border-line px-4 py-3">
                  {item.bond_name ?? item.bond_id ?? "—"}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {item.company_name ?? item.company_id ?? "—"}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {formatMoney(item.period_return_amount)}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {formatMoney(item.net_amount_delta)}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {formatMoney(item.fee_amount)}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {formatMoney(item.current_amount)}
                </td>
                <td className="border-b border-line px-4 py-3">
                  {item.is_active === false ? "неактивна" : "активна"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function performanceValues(
  performance: PaperTradingPerformanceResponse | null | undefined,
): Array<[string, unknown]> {
  if (!performance) {
    return [];
  }
  const metrics = performance.metrics;
  const values: Array<[string, unknown]> = [
    ["Накопленный результат", metrics.cumulative_return],
    ["Макс. просадка", metrics.max_drawdown],
    ["Волатильность", metrics.volatility],
    ["Сумма комиссий", metrics.total_fee_amount],
    ["Активные позиции", metrics.active_positions_count],
    ["Снимки состояния", metrics.snapshot_count],
  ];
  return values.filter(([, value]) => value !== undefined);
}

export function LivePaperPortfolioDetails() {
  const params = useParams();
  const numericPortfolioId = Number(params.portfolioId);
  const validPortfolioId =
    Number.isInteger(numericPortfolioId) && numericPortfolioId > 0;

  const portfolioId = validPortfolioId ? numericPortfolioId : null;

  const portfolioQuery = useQuery({
    queryKey: ["live-paper", "portfolio-detail", portfolioId],
    queryFn: () => api.getLivePaperPortfolio(portfolioId!),
    enabled: portfolioId !== null,
  });

  const positionsQuery = useQuery({
    queryKey: ["paper-portfolio", portfolioId, "positions"],
    queryFn: () => api.getPaperPortfolioPositions(portfolioId!),
    enabled: portfolioId !== null,
  });

  const operationsQuery = useQuery({
    queryKey: ["paper-portfolio", portfolioId, "operations"],
    queryFn: () => api.getPaperPortfolioOperations(portfolioId!),
    enabled: portfolioId !== null,
  });

  const snapshotsQuery = useQuery({
    queryKey: ["paper-portfolio", portfolioId, "snapshots"],
    queryFn: () => api.getPaperPortfolioSnapshots(portfolioId!),
    enabled: portfolioId !== null,
  });

  const performanceQuery = useQuery({
    queryKey: ["paper-portfolio", portfolioId, "performance"],
    queryFn: () => api.getPaperPortfolioPerformance(portfolioId!),
    enabled: portfolioId !== null,
  });

  const equityQuery = useQuery({
    queryKey: ["paper-portfolio", portfolioId, "equity-curve"],
    queryFn: () => api.getPaperPortfolioEquityCurve(portfolioId!),
    enabled: portfolioId !== null,
  });

  const contributionsQuery = useQuery({
    queryKey: ["paper-portfolio", portfolioId, "contributions"],
    queryFn: () => api.getPaperPortfolioContributions(portfolioId!),
    enabled: portfolioId !== null,
  });

  const monitoring = portfolioQuery.data;
  const portfolio = monitoring?.portfolio;
  const positions = positionsQuery.data ?? [];
  const operations = operationsQuery.data ?? [];
  const snapshots = snapshotsQuery.data ?? [];
  const performance = performanceQuery.data;
  const contributions = contributionsQuery.data;

  const equityPoints = useMemo(() => {
    if (equityQuery.data?.length) {
      return equityQuery.data;
    }
    if (performance?.equity_curve?.length) {
      return performance.equity_curve;
    }
    return monitoring?.equity_curve ?? [];
  }, [equityQuery.data, monitoring?.equity_curve, performance?.equity_curve]);

  if (!validPortfolioId) {
    return <ErrorState message="Некорректный ID портфеля" />;
  }

  if (portfolioQuery.isLoading) {
    return <LoadingState label="Загрузка виртуального портфеля" />;
  }

  if (portfolioQuery.isError) {
    return <ErrorState message={normalizeApiError(portfolioQuery.error)} />;
  }

  if (!portfolio) {
    return <EmptyState label="Виртуальный портфель не найден." />;
  }

  return (
    <div className="space-y-5">
      <section className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <Link
            to="/live-paper"
            className="mb-3 inline-flex items-center gap-2 text-sm text-slate-600 hover:text-accent"
          >
            <ArrowLeft size={16} />
            К виртуальному контуру
          </Link>
          <p className="text-sm font-semibold uppercase text-accent">
            Виртуальный портфель #{portfolio.id}
          </p>
          <h1 className="mt-1 text-2xl font-semibold text-ink">
            {portfolio.name}
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-600">
            Детальный просмотр состояния, операций и циклов виртуального контура.
          </p>
          <p className="mt-1 text-sm text-slate-500">
            Информационный режим: без реальных брокерских действий.
          </p>
        </div>
        <div className="surface flex items-center gap-3 px-3 py-2 text-sm text-slate-600">
          <BarChart3 size={17} />
          <span>Состояние</span>
          <HealthBadge status={portfolio.health_status} />
        </div>
      </section>

      <SummaryCards portfolio={portfolio} />

      <section className="surface p-4">
        <SectionHeader
          title="Предупреждения"
          subtitle="Сигналы мониторинга по выбранному портфелю."
        />
        <AlertList alerts={monitoring.alerts} />
      </section>

      <section className="surface p-4">
        <SectionHeader
          title="Кривая стоимости"
          subtitle="Динамика по снимкам состояния."
        />
        {equityQuery.isLoading || performanceQuery.isLoading ? (
          <LoadingState label="Загрузка кривой стоимости" />
        ) : null}
        {equityQuery.isError ? (
          <ErrorState message={normalizeApiError(equityQuery.error)} />
        ) : null}
        {!equityQuery.isLoading && !performanceQuery.isLoading ? (
          <EquityChart points={equityPoints} />
        ) : null}
      </section>

      <section>
        <SectionHeader title="Позиции" subtitle="Состав виртуального портфеля." />
        {positionsQuery.isLoading ? <LoadingState label="Загрузка позиций" /> : null}
        {positionsQuery.isError ? (
          <ErrorState message={normalizeApiError(positionsQuery.error)} />
        ) : null}
        {!positionsQuery.isLoading && !positionsQuery.isError ? (
          <PositionsTable positions={positions} />
        ) : null}
      </section>

      <section>
        <SectionHeader
          title="Операции"
          subtitle="Изменения портфеля и служебные записи виртуального контура."
        />
        {operationsQuery.isLoading ? <LoadingState label="Загрузка операций" /> : null}
        {operationsQuery.isError ? (
          <ErrorState message={normalizeApiError(operationsQuery.error)} />
        ) : null}
        {!operationsQuery.isLoading && !operationsQuery.isError ? (
          <OperationsTable operations={operations} />
        ) : null}
      </section>

      <section>
        <SectionHeader
          title="Снимки состояния"
          subtitle="История стоимости и размещения средств."
        />
        {snapshotsQuery.isLoading ? (
          <LoadingState label="Загрузка снимков состояния" />
        ) : null}
        {snapshotsQuery.isError ? (
          <ErrorState message={normalizeApiError(snapshotsQuery.error)} />
        ) : null}
        {!snapshotsQuery.isLoading && !snapshotsQuery.isError ? (
          <SnapshotsTable snapshots={snapshots} />
        ) : null}
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <KeyValueCard
          title="Показатели"
          values={
            performanceValues(performance).length
              ? performanceValues(performance)
              : scalarEntries(nestedRecord(monitoring.performance, "metrics"))
          }
        />
        <KeyValueCard
          title="Вклад"
          values={scalarEntries(contributions as unknown as Record<string, unknown>)}
        />
      </section>

      <section>
        <SectionHeader
          title="Вклад по инструментам"
          subtitle="Компактная сводка по облигациям, если отчет доступен."
        />
        {contributionsQuery.isLoading ? (
          <LoadingState label="Загрузка вклада по инструментам" />
        ) : null}
        {contributionsQuery.isError ? (
          <ErrorState message={normalizeApiError(contributionsQuery.error)} />
        ) : null}
        {!contributionsQuery.isLoading && !contributionsQuery.isError ? (
          <ContributionsTable items={contributions?.items ?? []} />
        ) : null}
      </section>

      <section>
        <SectionHeader
          title="Недавние циклы"
          subtitle="Циклы виртуального контура, связанные с выбранным портфелем."
        />
        <CyclesTable cycles={monitoring.recent_cycles} />
      </section>
    </div>
  );
}
