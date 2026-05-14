export type AnalysisSignal =
  | "interesting_for_analysis"
  | "neutral"
  | "elevated_risk"
  | "increased_risk"
  | "high_risk"
  | "insufficient_data";

export interface Bond {
  id: number;
  company_id: number;
  isin: string | null;
  secid: string | null;
  name: string;
  currency: string;
  nominal_value: string | number | null;
  current_price: string | number | null;
  coupon_rate: string | number | null;
  yield_to_maturity: string | number | null;
  duration_years: string | number | null;
  volume: string | number | null;
  maturity_date: string | null;
  offer_date: string | null;
  is_floating_coupon: boolean;
  is_subordinated: boolean;
  is_perpetual: boolean;
  amortization: boolean | null;
  liquidity_score: number | null;
  signal: AnalysisSignal;
  risk_notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface Company {
  id: number;
  name: string;
  ticker: string;
  sector: string | null;
  inn: string | null;
  country: string;
  credit_rating: string | null;
  signal: AnalysisSignal;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export type Explanation = {
  summary?: string;
  positive_factors?: string[];
  negative_factors?: string[];
  missing_data?: string[];
  risk_warnings?: string[];
  scores?: Record<string, number | null>;
  source_data?: Record<string, unknown>;
  ratios?: Record<string, number | null>;
};

export interface BondScore {
  id: number;
  bond_id: number;
  company_score_id: number | null;
  yield_score: number | null;
  duration_score: number | null;
  liquidity_score: number | null;
  spread_score: number | null;
  risk_penalty: number | null;
  final_bond_score: number | null;
  signal: AnalysisSignal;
  explanation: Explanation | null;
  created_at: string;
}

export interface CompanyScore {
  id: number;
  company_id: number;
  report_id: number | null;
  debt_score: number | null;
  profitability_score: number | null;
  liquidity_score: number | null;
  cashflow_score: number | null;
  stability_score: number | null;
  final_company_score: number | null;
  risk_level: string;
  explanation: Explanation | null;
  created_at: string;
}
