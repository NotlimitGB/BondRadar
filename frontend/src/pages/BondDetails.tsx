import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Building2, Calculator } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { ApiError, api, normalizeApiError } from "../api/client";
import { ScorePanel } from "../components/ScorePanel";
import { EmptyState, ErrorState, LoadingState } from "../components/StateBlocks";
import { StatusBadge } from "../components/StatusBadge";
import { formatDate, formatNumber } from "../utils/format";

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-line bg-slate-50 px-3 py-2" style={{ borderRadius: 8 }}>
      <div className="text-xs font-semibold uppercase text-slate-500">{label}</div>
      <div className="mt-1 text-sm font-medium text-slate-800">{value}</div>
    </div>
  );
}

export function BondDetails() {
  const params = useParams();
  const bondId = Number(params.bondId);
  const queryClient = useQueryClient();

  const bondQuery = useQuery({
    queryKey: ["bond", bondId],
    queryFn: () => api.getBond(bondId),
    enabled: Number.isFinite(bondId),
  });

  const companyQuery = useQuery({
    queryKey: ["company", bondQuery.data?.company_id],
    queryFn: () => api.getCompany(bondQuery.data!.company_id),
    enabled: Boolean(bondQuery.data?.company_id),
  });

  const scoreQuery = useQuery({
    queryKey: ["bond-score", bondId],
    queryFn: () => api.getBondScore(bondId),
    enabled: Number.isFinite(bondId),
  });

  const calculateMutation = useMutation({
    mutationFn: () => api.calculateBondScore(bondId),
    onSuccess: (score) => {
      queryClient.setQueryData(["bond-score", bondId], score);
    },
  });

  if (bondQuery.isLoading) {
    return <LoadingState label="Загрузка карточки облигации" />;
  }

  if (bondQuery.isError) {
    return <ErrorState message={normalizeApiError(bondQuery.error)} />;
  }

  const bond = bondQuery.data;
  if (!bond) {
    return <EmptyState label="Облигация не найдена." />;
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <Link to="/" className="mb-3 inline-flex items-center gap-2 text-sm text-slate-600 hover:text-accent">
            <ArrowLeft size={16} />
            К списку облигаций
          </Link>
          <h1 className="text-2xl font-semibold text-ink">{bond.name}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <StatusBadge signal={bond.signal} />
            <span className="text-sm text-slate-500">
              {bond.isin ?? bond.secid ?? "Нет рыночного идентификатора"}
            </span>
          </div>
        </div>
        <button
          className="primary-button"
          onClick={() => calculateMutation.mutate()}
          disabled={calculateMutation.isPending}
        >
          <Calculator size={17} />
          <span>
            {calculateMutation.isPending
              ? "Расчет"
              : "Рассчитать скоринг облигации"}
          </span>
        </button>
      </div>

      {calculateMutation.isError ? (
        <ErrorState message={normalizeApiError(calculateMutation.error)} />
      ) : null}

      <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="surface p-4">
          <h2 className="mb-4 text-sm font-semibold uppercase text-slate-500">
            Данные облигации
          </h2>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <Field label="Валюта" value={bond.currency} />
            <Field label="Текущая цена" value={formatNumber(bond.current_price, 3)} />
            <Field label="Ставка купона" value={formatNumber(bond.coupon_rate, 3)} />
            <Field label="Доходность к погашению" value={formatNumber(bond.yield_to_maturity, 3)} />
            <Field label="Дюрация, лет" value={formatNumber(bond.duration_years, 3)} />
            <Field label="Скоринг ликвидности" value={formatNumber(bond.liquidity_score, 0)} />
            <Field label="Объем торгов" value={formatNumber(bond.volume, 0)} />
            <Field label="Дата погашения" value={formatDate(bond.maturity_date)} />
            <Field label="Дата оферты" value={formatDate(bond.offer_date)} />
          </div>
        </div>

        <div className="surface p-4">
          <h2 className="mb-4 flex items-center gap-2 text-sm font-semibold uppercase text-slate-500">
            <Building2 size={17} />
            Эмитент
          </h2>
          {companyQuery.isLoading ? <LoadingState label="Загрузка эмитента" /> : null}
          {companyQuery.isError ? (
            <ErrorState message={normalizeApiError(companyQuery.error)} />
          ) : null}
          {companyQuery.data ? (
            <div className="space-y-3">
              <div>
                <div className="text-base font-semibold text-ink">
                  {companyQuery.data.name}
                </div>
                <div className="text-sm text-slate-500">
                  {companyQuery.data.ticker} · {companyQuery.data.country}
                </div>
              </div>
              <StatusBadge signal={companyQuery.data.signal} />
              <Link
                className="text-button w-full justify-center"
                to={`/companies/${companyQuery.data.id}`}
              >
                Открыть эмитента
              </Link>
            </div>
          ) : null}
        </div>
      </section>

      {scoreQuery.isLoading ? <LoadingState label="Загрузка последнего скоринга" /> : null}
      {scoreQuery.isError && !(scoreQuery.error instanceof ApiError && scoreQuery.error.status === 404) ? (
        <ErrorState message={normalizeApiError(scoreQuery.error)} />
      ) : null}
      {!scoreQuery.isLoading && !scoreQuery.data ? (
        <EmptyState label="Скоринг еще не рассчитывался." />
      ) : null}
      {scoreQuery.data ? (
        <ScorePanel score={scoreQuery.data} title="Скоринг облигации" />
      ) : null}
    </div>
  );
}
