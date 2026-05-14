import { Activity, ListChecks } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { BondScore, CompanyScore, Explanation } from "../api/types";
import { formatNumber, labelFromKey, translateText } from "../utils/format";
import { StatusBadge } from "./StatusBadge";

type ScoreLike = BondScore | CompanyScore;

function isBondScore(score: ScoreLike): score is BondScore {
  return "final_bond_score" in score;
}

function scoreValue(score: ScoreLike): number | null {
  return isBondScore(score)
    ? score.final_bond_score
    : score.final_company_score;
}

function explanation(score: ScoreLike): Explanation | null {
  return score.explanation ?? null;
}

function chartData(score: ScoreLike) {
  if (isBondScore(score)) {
    return [
      { name: "Доходность", value: score.yield_score },
      { name: "Эмитент", value: score.explanation?.scores?.company_score ?? null },
      { name: "Ликвидность", value: score.liquidity_score },
      { name: "Дюрация", value: score.duration_score },
      { name: "Спред", value: score.spread_score },
    ].filter((item) => item.value !== null && item.value !== undefined);
  }

  return [
    { name: "Долг", value: score.debt_score },
    { name: "Прибыль", value: score.profitability_score },
    { name: "Ликвидность", value: score.liquidity_score },
    { name: "Денежный поток", value: score.cashflow_score },
    { name: "Устойчивость", value: score.stability_score },
  ].filter((item) => item.value !== null && item.value !== undefined);
}

function FactorList({ title, items }: { title: string; items?: string[] }) {
  if (!items?.length) {
    return null;
  }

  return (
    <section>
      <h4 className="mb-2 text-xs font-semibold uppercase text-slate-500">
        {title}
      </h4>
      <ul className="space-y-2 text-sm text-slate-700">
        {items.map((item) => (
          <li key={item} className="border-l-2 border-line pl-3">
            {translateText(item)}
          </li>
        ))}
      </ul>
    </section>
  );
}

function KeyValueGrid({
  title,
  values,
}: {
  title: string;
  values?: Record<string, unknown>;
}) {
  if (!values || Object.keys(values).length === 0) {
    return null;
  }

  return (
    <section>
      <h4 className="mb-2 text-xs font-semibold uppercase text-slate-500">
        {title}
      </h4>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {Object.entries(values).map(([key, value]) => (
          <div
            key={key}
            className="flex items-center justify-between gap-3 border border-line bg-slate-50 px-3 py-2 text-sm"
            style={{ borderRadius: 8 }}
          >
            <span className="text-slate-500">{labelFromKey(key)}</span>
            <span className="font-medium text-slate-800">
              {typeof value === "number"
                ? formatNumber(value, 3)
                : value === null || value === undefined
                  ? "нет данных"
                  : typeof value === "boolean"
                    ? value
                      ? "да"
                      : "нет"
                    : translateText(String(value))}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

export function ScorePanel({
  score,
  title,
}: {
  score: ScoreLike;
  title: string;
}) {
  const value = scoreValue(score);
  const details = explanation(score);
  const data = chartData(score);

  return (
    <div className="surface overflow-hidden">
      <div className="flex flex-col gap-3 border-b border-line px-4 py-4 md:flex-row md:items-center md:justify-between">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-ink">
            <Activity size={18} />
            <span>{title}</span>
          </div>
          <p className="mt-1 text-sm text-slate-500">Объяснение скоринга</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="border border-line bg-slate-50 px-3 py-2 text-sm font-semibold text-slate-900">
            Итоговый балл: {value ?? "нет данных"}
          </div>
          {isBondScore(score) ? (
            <StatusBadge signal={score.signal} />
          ) : (
            <span className="inline-flex items-center border border-slate-200 bg-slate-50 px-2 py-1 text-xs font-semibold text-slate-700">
              Уровень риска: {labelFromKey(score.risk_level)}
            </span>
          )}
        </div>
      </div>

      <div className="grid gap-5 p-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-4">
          {details?.summary ? (
            <div className="border border-line bg-slate-50 p-3 text-sm text-slate-700">
              {translateText(details.summary)}
            </div>
          ) : null}

          <div className="grid gap-4 md:grid-cols-2">
            <FactorList title="Положительные факторы" items={details?.positive_factors} />
            <FactorList title="Отрицательные факторы" items={details?.negative_factors} />
            <FactorList title="Недостающие данные" items={details?.missing_data} />
            <FactorList title="Предупреждения о риске" items={details?.risk_warnings} />
          </div>

          <KeyValueGrid title="Компоненты скоринга" values={details?.scores} />
          <KeyValueGrid title="Коэффициенты" values={details?.ratios} />
          <KeyValueGrid title="Исходные данные" values={details?.source_data} />
        </div>

        <div className="border border-line bg-white p-3" style={{ borderRadius: 8 }}>
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <ListChecks size={17} />
            <span>Компоненты скоринга</span>
          </div>
          {data.length ? (
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data} layout="vertical" margin={{ left: 12 }}>
                  <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                  <XAxis type="number" domain={[0, 100]} />
                  <YAxis dataKey="name" type="category" width={70} />
                  <Tooltip />
                  <Bar dataKey="value" fill="#0f766e" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="text-sm text-slate-500">Компоненты скоринга пока отсутствуют.</div>
          )}
        </div>
      </div>
    </div>
  );
}
