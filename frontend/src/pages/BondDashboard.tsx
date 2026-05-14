import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Filter, Search } from "lucide-react";
import { Link } from "react-router-dom";

import { api, normalizeApiError } from "../api/client";
import type { AnalysisSignal } from "../api/types";
import { EmptyState, ErrorState, LoadingState } from "../components/StateBlocks";
import { StatusBadge } from "../components/StatusBadge";
import { formatDate, formatNumber } from "../utils/format";

const signalOptions: Array<AnalysisSignal | "all"> = [
  "all",
  "interesting_for_analysis",
  "neutral",
  "elevated_risk",
  "increased_risk",
  "high_risk",
  "insufficient_data",
];

export function BondDashboard() {
  const [query, setQuery] = useState("");
  const [signal, setSignal] = useState<AnalysisSignal | "all">("all");
  const [companyId, setCompanyId] = useState<string>("all");

  const bondsQuery = useQuery({ queryKey: ["bonds"], queryFn: api.getBonds });
  const companiesQuery = useQuery({
    queryKey: ["companies"],
    queryFn: api.getCompanies,
  });

  const companyById = useMemo(() => {
    return new Map((companiesQuery.data ?? []).map((company) => [company.id, company]));
  }, [companiesQuery.data]);

  const filteredBonds = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (bondsQuery.data ?? []).filter((bond) => {
      const matchesQuery =
        !needle ||
        bond.name.toLowerCase().includes(needle) ||
        (bond.isin ?? "").toLowerCase().includes(needle) ||
        (bond.secid ?? "").toLowerCase().includes(needle);
      const matchesSignal = signal === "all" || bond.signal === signal;
      const matchesCompany =
        companyId === "all" || String(bond.company_id) === companyId;
      return matchesQuery && matchesSignal && matchesCompany;
    });
  }, [bondsQuery.data, companyId, query, signal]);

  return (
    <div className="space-y-5">
      <section className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-sm font-semibold uppercase text-accent">
            Bonds dashboard
          </p>
          <h1 className="mt-1 text-2xl font-semibold text-ink">
            Bond analytics workspace
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-600">
            Review imported bonds, inspect issuer context, and calculate
            informational scoring snapshots.
          </p>
        </div>
        <div className="surface px-3 py-2 text-sm text-slate-600">
          {filteredBonds.length} of {bondsQuery.data?.length ?? 0} bonds shown
        </div>
      </section>

      <section className="surface p-4">
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_220px_260px]">
          <label className="block">
            <span className="mb-1 flex items-center gap-2 text-xs font-semibold uppercase text-slate-500">
              <Search size={15} />
              Search
            </span>
            <input
              aria-label="Search bonds"
              className="h-10 w-full border border-line bg-white px-3 text-sm outline-none transition focus:border-accent"
              style={{ borderRadius: 8 }}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>

          <label className="block">
            <span className="mb-1 flex items-center gap-2 text-xs font-semibold uppercase text-slate-500">
              <Filter size={15} />
              Signal
            </span>
            <select
              aria-label="Filter by signal"
              className="h-10 w-full border border-line bg-white px-3 text-sm outline-none transition focus:border-accent"
              style={{ borderRadius: 8 }}
              value={signal}
              onChange={(event) =>
                setSignal(event.target.value as AnalysisSignal | "all")
              }
            >
              {signalOptions.map((value) => (
                <option key={value} value={value}>
                  {value === "all" ? "All signals" : value}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="mb-1 text-xs font-semibold uppercase text-slate-500">
              Issuer
            </span>
            <select
              aria-label="Filter by issuer"
              className="h-10 w-full border border-line bg-white px-3 text-sm outline-none transition focus:border-accent"
              style={{ borderRadius: 8 }}
              value={companyId}
              onChange={(event) => setCompanyId(event.target.value)}
            >
              <option value="all">All issuers</option>
              {(companiesQuery.data ?? []).map((company) => (
                <option key={company.id} value={company.id}>
                  {company.name}
                </option>
              ))}
            </select>
          </label>
        </div>
      </section>

      {bondsQuery.isLoading ? <LoadingState label="Loading bonds" /> : null}
      {bondsQuery.isError ? (
        <ErrorState message={normalizeApiError(bondsQuery.error)} />
      ) : null}
      {!bondsQuery.isLoading && !bondsQuery.isError && filteredBonds.length === 0 ? (
        <EmptyState label="No bonds match the current filters." />
      ) : null}

      {filteredBonds.length ? (
        <section className="surface overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full border-separate border-spacing-0 text-left text-sm">
              <thead className="bg-slate-50 text-xs uppercase text-slate-500">
                <tr>
                  <th className="border-b border-line px-4 py-3">Bond</th>
                  <th className="border-b border-line px-4 py-3">Issuer</th>
                  <th className="border-b border-line px-4 py-3">Yield</th>
                  <th className="border-b border-line px-4 py-3">Duration</th>
                  <th className="border-b border-line px-4 py-3">Maturity</th>
                  <th className="border-b border-line px-4 py-3">Signal</th>
                  <th className="border-b border-line px-4 py-3"></th>
                </tr>
              </thead>
              <tbody>
                {filteredBonds.map((bond) => {
                  const company = companyById.get(bond.company_id);
                  return (
                    <tr key={bond.id} className="hover:bg-slate-50">
                      <td className="border-b border-line px-4 py-3">
                        <div className="font-medium text-ink">{bond.name}</div>
                        <div className="text-xs text-slate-500">
                          {bond.isin ?? bond.secid ?? "No market identifier"}
                        </div>
                      </td>
                      <td className="border-b border-line px-4 py-3 text-slate-700">
                        {company?.name ?? `Company #${bond.company_id}`}
                      </td>
                      <td className="border-b border-line px-4 py-3">
                        {formatNumber(bond.yield_to_maturity, 2)}
                      </td>
                      <td className="border-b border-line px-4 py-3">
                        {formatNumber(bond.duration_years, 2)}
                      </td>
                      <td className="border-b border-line px-4 py-3">
                        {formatDate(bond.maturity_date)}
                      </td>
                      <td className="border-b border-line px-4 py-3">
                        <StatusBadge signal={bond.signal} />
                      </td>
                      <td className="border-b border-line px-4 py-3 text-right">
                        <Link
                          className="text-button"
                          to={`/bonds/${bond.id}`}
                          aria-label={`Open ${bond.name}`}
                        >
                          <span>Open</span>
                          <ArrowRight size={16} />
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  );
}
