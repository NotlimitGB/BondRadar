import type {
  Bond,
  BondScore,
  Company,
  CompanyScore,
  LivePaperMonitoringOverviewResponse,
  LivePaperPortfolioMonitoringResponse,
} from "./types";
import { translateText } from "../utils/format";

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
};
