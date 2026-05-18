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

export type LivePaperHealthStatus = "healthy" | "warning" | "critical" | "unknown";

export type LivePaperAlertLevel = "info" | "warning" | "critical";

export interface LivePaperMonitoringAlert {
  level: LivePaperAlertLevel;
  code: string;
  message: string;
  details: Record<string, unknown>;
}

export interface LivePaperScheduleMonitoringSummary {
  id: number;
  name: string;
  status: string;
  next_run_at: string;
  last_run_at: string | null;
  last_cycle_run_id: number | null;
  run_count: number;
  max_runs: number | null;
  interval_days: number;
  is_due: boolean;
  is_locked: boolean;
  lock_expires_at: string | null;
  health_status: LivePaperHealthStatus;
  alerts: LivePaperMonitoringAlert[];
}

export interface LivePaperCycleMonitoringSummary {
  id: number;
  status: string;
  mode: string;
  portfolio_id: number | null;
  schedule_id: number | null;
  client_cycle_key: string | null;
  as_of_date: string | null;
  scheduled_for: string | null;
  readiness_status: string | null;
  selected_model_run_id: number | null;
  started_at: string;
  finished_at: string | null;
  warning_count: number;
  error_count: number;
  summary: Record<string, unknown>;
}

export interface LivePaperPortfolioMonitoringSummary {
  id: number;
  name: string;
  status: string;
  base_currency: string;
  initial_capital: string | number;
  current_value: string | number;
  cash_balance: string | number;
  model_run_id: number | null;
  return_method: string | null;
  horizon_days: number | null;
  last_rebalance_as_of_date: string | null;
  last_rebalanced_at: string | null;
  last_marked_at: string | null;
  active_positions_count: number;
  snapshot_count: number;
  latest_snapshot_date: string | null;
  cumulative_return: string | number | null;
  max_drawdown: string | number | null;
  health_status: LivePaperHealthStatus;
  alerts: LivePaperMonitoringAlert[];
}

export interface LivePaperMonitoringOverviewResponse {
  health_status: LivePaperHealthStatus;
  now: string;

  schedule_count: number;
  active_schedule_count: number;
  due_schedule_count: number;
  locked_schedule_count: number;

  portfolio_count: number;
  active_portfolio_count: number;

  recent_cycle_count: number;
  completed_cycle_count: number;
  blocked_cycle_count: number;
  failed_cycle_count: number;
  running_cycle_count: number;

  schedules: LivePaperScheduleMonitoringSummary[];
  portfolios: LivePaperPortfolioMonitoringSummary[];
  recent_cycles: LivePaperCycleMonitoringSummary[];

  alerts: LivePaperMonitoringAlert[];
}

export interface LivePaperPortfolioMonitoringResponse {
  portfolio: LivePaperPortfolioMonitoringSummary;
  performance: Record<string, unknown> | null;
  equity_curve: Array<Record<string, unknown>>;
  contributions: Record<string, unknown> | null;
  positions: Array<Record<string, unknown>>;
  recent_cycles: LivePaperCycleMonitoringSummary[];
  alerts: LivePaperMonitoringAlert[];
}
