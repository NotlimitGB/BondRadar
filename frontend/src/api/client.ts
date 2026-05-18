import type {
  Bond,
  BondScore,
  Company,
  CompanyScore,
  LivePaperCycleMonitoringListResponse,
  LivePaperMonitoringOverviewResponse,
  LivePaperPilotBootstrapRequest,
  LivePaperPilotBootstrapResponse,
  LivePaperPortfolioMonitoringResponse,
  LivePaperScheduleRead,
  LivePaperScheduleRunDueRequest,
  LivePaperScheduleRunDueResponse,
  LivePaperScheduleUpdateRequest,
  LivePaperScheduledRunItem,
  PaperPortfolioOperation,
  PaperPortfolioPosition,
  PaperPortfolioSnapshot,
  PaperTradingContributionsResponse,
  PaperTradingEquityPoint,
  PaperTradingPerformanceResponse,
} from "./types";
import { translateText } from "../utils/format";

type LivePaperScheduleListParams = {
  limit?: number;
  status?: string | null;
};

type LivePaperCyclesParams = {
  schedule_id?: number | null;
  portfolio_id?: number | null;
  status?: string | null;
  limit?: number;
};

type LivePaperScheduleRunOnceParams = {
  now?: string | null;
};

function buildQuery(params: Record<string, string | number | boolean | null | undefined>): string {
  const searchParams = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== "") {
      searchParams.set(key, String(value));
    }
  });
  const query = searchParams.toString();
  return query ? `?${query}` : "";
}

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(normalizeApiError(detail, status));
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: {
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      detail = await response.json();
    } catch {
      detail = response.statusText;
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

export function normalizeApiError(detail: unknown, status?: number): string {
  if (typeof detail === "string") {
    return translateText(detail);
  }

  if (detail && typeof detail === "object" && "detail" in detail) {
    const value = (detail as { detail: unknown }).detail;
    if (typeof value === "string") {
      return translateText(value);
    }
    if (Array.isArray(value)) {
      return value
        .map((item) => {
          if (item && typeof item === "object" && "msg" in item) {
            return translateText(String((item as { msg: unknown }).msg));
          }
          return JSON.stringify(item);
        })
        .join("; ");
    }
    return JSON.stringify(value);
  }

  if (status) {
    return `Ошибка API, статус ${status}`;
  }

  return "Ошибка API";
}

export const api = {
  getBonds: () => request<Bond[]>("/api/bonds"),
  getBond: (bondId: number) => request<Bond>(`/api/bonds/${bondId}`),
  getBondScore: async (bondId: number) => {
    try {
      return await request<BondScore>(`/api/bonds/${bondId}/score`);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        return null;
      }
      throw error;
    }
  },
  calculateBondScore: (bondId: number) =>
    request<BondScore>(`/api/bonds/${bondId}/calculate-score`, {
      method: "POST",
    }),
  getCompanies: () => request<Company[]>("/api/companies"),
  getCompany: (companyId: number) =>
    request<Company>(`/api/companies/${companyId}`),
  calculateCompanyScore: (companyId: number) =>
    request<CompanyScore>(`/api/companies/${companyId}/calculate-score`, {
      method: "POST",
    }),
  getLivePaperOverview: () =>
    request<LivePaperMonitoringOverviewResponse>(
      "/api/paper-trading/live/monitoring/overview",
    ),
  getLivePaperPortfolio: (portfolioId: number) =>
    request<LivePaperPortfolioMonitoringResponse>(
      `/api/paper-trading/live/monitoring/portfolios/${portfolioId}`,
    ),
  getLivePaperSchedules: (params: LivePaperScheduleListParams = {}) =>
    request<LivePaperScheduleRead[]>(
      `/api/paper-trading/live/schedules${buildQuery({
        limit: params.limit ?? 100,
        status: params.status,
      })}`,
    ),
  getLivePaperSchedule: (scheduleId: number) =>
    request<LivePaperScheduleRead>(
      `/api/paper-trading/live/schedules/${scheduleId}`,
    ),
  updateLivePaperSchedule: (
    scheduleId: number,
    payload: LivePaperScheduleUpdateRequest,
  ) =>
    request<LivePaperScheduleRead>(
      `/api/paper-trading/live/schedules/${scheduleId}`,
      {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      },
    ),
  runLivePaperScheduleOnce: (
    scheduleId: number,
    params: LivePaperScheduleRunOnceParams = {},
  ) =>
    request<LivePaperScheduledRunItem>(
      `/api/paper-trading/live/schedules/${scheduleId}/run${buildQuery({
        now: params.now,
      })}`,
      {
        method: "POST",
      },
    ),
  runDueLivePaperSchedules: (payload: LivePaperScheduleRunDueRequest) =>
    request<LivePaperScheduleRunDueResponse>(
      "/api/paper-trading/live/schedules/run-due",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      },
    ),
  getLivePaperCycles: (params: LivePaperCyclesParams = {}) =>
    request<LivePaperCycleMonitoringListResponse>(
      `/api/paper-trading/live/monitoring/cycles${buildQuery({
        schedule_id: params.schedule_id,
        portfolio_id: params.portfolio_id,
        status: params.status,
        limit: params.limit ?? 50,
      })}`,
    ),
  bootstrapLivePaperPilot: (payload: LivePaperPilotBootstrapRequest) =>
    request<LivePaperPilotBootstrapResponse>(
      "/api/paper-trading/live/pilots/bootstrap",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      },
    ),
  getPaperPortfolioPositions: (portfolioId: number) =>
    request<PaperPortfolioPosition[]>(
      `/api/paper-trading/portfolios/${portfolioId}/positions`,
    ),
  getPaperPortfolioOperations: (portfolioId: number) =>
    request<PaperPortfolioOperation[]>(
      `/api/paper-trading/portfolios/${portfolioId}/transactions`,
    ),
  getPaperPortfolioSnapshots: (portfolioId: number) =>
    request<PaperPortfolioSnapshot[]>(
      `/api/paper-trading/portfolios/${portfolioId}/snapshots`,
    ),
  getPaperPortfolioPerformance: (portfolioId: number) =>
    request<PaperTradingPerformanceResponse>(
      `/api/paper-trading/portfolios/${portfolioId}/performance`,
    ),
  getPaperPortfolioEquityCurve: (portfolioId: number) =>
    request<PaperTradingEquityPoint[]>(
      `/api/paper-trading/portfolios/${portfolioId}/equity-curve`,
    ),
  getPaperPortfolioContributions: (portfolioId: number) =>
    request<PaperTradingContributionsResponse>(
      `/api/paper-trading/portfolios/${portfolioId}/contributions`,
    ),
};
