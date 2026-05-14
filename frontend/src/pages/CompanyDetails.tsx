import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, Calculator } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { api, normalizeApiError } from "../api/client";
import type { CompanyScore } from "../api/types";
import { ScorePanel } from "../components/ScorePanel";
import { EmptyState, ErrorState, LoadingState } from "../components/StateBlocks";
import { StatusBadge } from "../components/StatusBadge";

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-line bg-slate-50 px-3 py-2" style={{ borderRadius: 8 }}>
      <div className="text-xs font-semibold uppercase text-slate-500">{label}</div>
      <div className="mt-1 text-sm font-medium text-slate-800">{value}</div>
    </div>
  );
}

export function CompanyDetails() {
  const params = useParams();
  const companyId = Number(params.companyId);
  const [score, setScore] = useState<CompanyScore | null>(null);

  const companyQuery = useQuery({
    queryKey: ["company", companyId],
    queryFn: () => api.getCompany(companyId),
    enabled: Number.isFinite(companyId),
  });

  const calculateMutation = useMutation({
    mutationFn: () => api.calculateCompanyScore(companyId),
    onSuccess: setScore,
  });

  if (companyQuery.isLoading) {
    return <LoadingState label="Загрузка карточки компании" />;
  }

  if (companyQuery.isError) {
    return <ErrorState message={normalizeApiError(companyQuery.error)} />;
  }

  const company = companyQuery.data;
  if (!company) {
    return <EmptyState label="Компания не найдена." />;
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <Link to="/" className="mb-3 inline-flex items-center gap-2 text-sm text-slate-600 hover:text-accent">
            <ArrowLeft size={16} />
            К списку облигаций
          </Link>
          <h1 className="text-2xl font-semibold text-ink">{company.name}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <StatusBadge signal={company.signal} />
            <span className="text-sm text-slate-500">
              {company.ticker} · {company.country}
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
              : "Рассчитать скоринг компании"}
          </span>
        </button>
      </div>

      {calculateMutation.isError ? (
        <ErrorState message={normalizeApiError(calculateMutation.error)} />
      ) : null}

      <section className="surface p-4">
        <h2 className="mb-4 text-sm font-semibold uppercase text-slate-500">
          Данные компании
        </h2>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <Field label="Тикер" value={company.ticker} />
          <Field label="ИНН" value={company.inn ?? "нет данных"} />
          <Field label="Сектор" value={company.sector ?? "нет данных"} />
          <Field label="Страна" value={company.country} />
          <Field label="Кредитный рейтинг" value={company.credit_rating ?? "нет данных"} />
          <Field label="Заметки" value={company.notes ?? "нет данных"} />
        </div>
      </section>

      {!score ? (
        <EmptyState label="Скоринг компании появится после ручного расчета." />
      ) : (
        <ScorePanel score={score} title="Скоринг компании" />
      )}
    </div>
  );
}
