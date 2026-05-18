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

export interface LivePaperCycleMonitoringListResponse {
  total_returned: number;
  cycles: LivePaperCycleMonitoringSummary[];
  alerts: LivePaperMonitoringAlert[];
}

export type LivePaperScheduleStatus = "active" | "paused" | "archived";

export interface LivePaperCycleRunRead extends Record<string, unknown> {
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
  selected_model_run_ids_json: number[] | null;
  request_json: Record<string, unknown>;
  readiness_json: Record<string, unknown>;
  mark_period_result_json: Record<string, unknown> | null;
  rebalance_result_json: Record<string, unknown> | null;
  summary_json: Record<string, unknown>;
  warnings_json: Array<Record<string, unknown>>;
  errors_json: Array<Record<string, unknown>>;
  started_at: string;
  finished_at: string | null;
  created_at: string;
}

export interface LivePaperScheduleRead {
  id: number;
  name: string;
  status: LivePaperScheduleStatus | string;
  mode: string;
  cycle_request_json: Record<string, unknown>;
  next_run_at: string;
  last_run_at: string | null;
  last_cycle_run_id: number | null;
  interval_days: number;
  max_runs: number | null;
  run_count: number;
  use_current_date_as_of_date: boolean;
  locked_at: string | null;
  lock_expires_at: string | null;
  lock_token: string | null;
  summary_json: Record<string, unknown>;
  warnings_json: Array<Record<string, unknown>>;
  errors_json: Array<Record<string, unknown>>;
  created_at: string;
  updated_at: string;
}

export interface LivePaperScheduleUpdateRequest {
  name?: string;
  status?: LivePaperScheduleStatus;
  next_run_at?: string;
  interval_days?: number;
  max_runs?: number | null;
  use_current_date_as_of_date?: boolean;
}

export interface LivePaperScheduleRunDueRequest {
  now?: string | null;
  limit?: number;
  dry_run: boolean;
  lock_minutes?: number;
}

export interface LivePaperScheduledRunItem {
  schedule: LivePaperScheduleRead;
  status: string;
  scheduled_for: string;
  cycle: LivePaperCycleRunRead | null;
  warnings: Array<Record<string, unknown>>;
  errors: Array<Record<string, unknown>>;
}

export interface LivePaperScheduleRunDueResponse {
  now: string;
  dry_run: boolean;
  due_schedule_count: number;
  executed_count: number;
  skipped_count: number;
  results: LivePaperScheduledRunItem[];
  warnings: Array<Record<string, unknown>>;
  errors: Array<Record<string, unknown>>;
}

export type LivePaperPilotBootstrapStatus =
  | "prepared"
  | "scheduled"
  | "blocked";

export interface LivePaperPilotBootstrapRequest {
  name: string;
  description?: string | null;

  model_run_id: number;
  return_method: string;
  horizon_days: number;

  virtual_initial_capital: string | number;
  planned_duration_days: number;

  date_from: string;
  date_to: string;

  next_run_at: string;
  interval_days: number;
  max_runs?: number | null;

  create_schedule: boolean;
  dry_run_only: boolean;

  allow_readiness_warning: boolean;
  allow_not_ready: boolean;

  top_n: number;
  min_probability_positive: string | number;

  use_portfolio_constraints: boolean;
  max_position_weight: string | number;
  max_issuer_weight: string | number;
  max_high_risk_weight: string | number;

  transaction_cost_rate: string | number;

  include_monitoring_overview: boolean;
}

export interface LivePaperPilotBootstrapPayloads {
  readiness_request: Record<string, unknown>;
  cycle_request: Record<string, unknown>;
  schedule_request: Record<string, unknown> | null;
}

export interface LivePaperPilotBootstrapNextStep {
  label: string;
  method: string;
  path: string;
  body: Record<string, unknown> | null;
  description: string;
}

export interface LivePaperPilotBootstrapResponse {
  status: LivePaperPilotBootstrapStatus;
  created_schedule_id: number | null;
  readiness_status: string | null;
  selected_model_run_id: number | null;

  virtual_initial_capital: string | number;
  planned_duration_days: number;
  next_run_at: string;
  interval_days: number;
  max_runs: number | null;

  readiness: Record<string, unknown> | null;
  schedule: Record<string, unknown> | null;
  monitoring_overview: LivePaperMonitoringOverviewResponse | null;

  payloads: LivePaperPilotBootstrapPayloads;
  next_steps: LivePaperPilotBootstrapNextStep[];
  warnings: Array<Record<string, unknown>>;
  errors: Array<Record<string, unknown>>;
}

export interface PaperPortfolioPosition {
  id: number;
  portfolio_id: number;
  bond_id: number;
  company_id: number | null;
  as_of_date: string;
  allocation_weight: string | number;
  allocation_amount: string | number;
  current_amount: string | number;
  probability_positive: string | number | null;
  predicted_label: string | null;
  yield_to_maturity: string | number | null;
  liquidity_score: number | null;
  decision_status: string | null;
  risk_level: string | null;
  is_active: boolean;
  source_model_run_id: number | null;
  source_prediction_id: number | null;
  source_details_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface PaperPortfolioOperation {
  id: number;
  portfolio_id: number;
  bond_id: number | null;
  company_id?: number | null;
  transaction_type?: string | null;
  operation_type?: string | null;
  amount_delta?: string | number | null;
  weight_delta?: string | number | null;
  amount?: string | number | null;
  quantity?: string | number | null;
  price?: string | number | null;
  weight?: string | number | null;
  fee_amount?: string | number | null;
  portfolio_value_before?: string | number | null;
  portfolio_value_after?: string | number | null;
  as_of_date?: string | null;
  executed_at?: string | null;
  created_at?: string | null;
  cycle_run_id?: number | null;
  model_run_id?: number | null;
  details_json?: Record<string, unknown>;
  metadata?: Record<string, unknown> | null;
}

export interface PaperPortfolioSnapshot {
  id: number;
  portfolio_id: number;
  as_of_date: string;
  portfolio_value?: string | number | null;
  cash_balance?: string | number | null;
  allocated_value?: string | number | null;
  allocated_weight?: string | number | null;
  unallocated_weight?: string | number | null;
  positions_count?: number | null;
  active_positions_count?: number | null;
  cumulative_return?: string | number | null;
  period_return?: string | number | null;
  max_drawdown?: string | number | null;
  metrics_json?: Record<string, unknown>;
  warnings_json?: Array<Record<string, unknown>>;
  created_at?: string | null;
}

export interface PaperTradingEquityPoint {
  as_of_date: string;
  portfolio_value: string | number;
  cash_balance: string | number;
  allocated_value: string | number;
  allocated_weight: string | number;
  unallocated_weight: string | number;
  cumulative_return: string | number;
  period_return: string | number | null;
  drawdown: string | number;
  active_positions_count: number;
}

export interface PaperTradingPerformanceResponse {
  portfolio_id: number;
  name: string;
  status: string;
  base_currency: string;
  model_run_id: number | null;
  return_method: string | null;
  horizon_days: number | null;
  date_from: string | null;
  date_to: string | null;
  metrics: Record<string, unknown>;
  equity_curve: PaperTradingEquityPoint[];
  warnings: Array<Record<string, unknown>>;
}

export interface PaperTradingContributionItem {
  bond_id: number | null;
  bond_name: string | null;
  isin: string | null;
  secid: string | null;
  company_id: number | null;
  company_name: string | null;
  period_return_amount: string | number;
  allocation_increase_amount: string | number;
  allocation_decrease_amount: string | number;
  removed_amount: string | number;
  fee_amount: string | number;
  net_amount_delta: string | number;
  transaction_count: number;
  current_amount: string | number | null;
  current_weight: string | number | null;
  is_active: boolean | null;
}

export interface PaperTradingContributionsResponse {
  portfolio_id: number;
  date_from: string | null;
  date_to: string | null;
  items: PaperTradingContributionItem[];
  warnings: Array<Record<string, unknown>>;
}
