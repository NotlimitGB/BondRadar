export function formatNumber(
  value: string | number | null | undefined,
  digits = 2,
): string {
  if (value === null || value === undefined || value === "") {
    return "нет данных";
  }
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return String(value);
  }
  return new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: digits,
  }).format(numeric);
}

export function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "нет данных";
  }
  return new Intl.DateTimeFormat("ru-RU", {
    year: "numeric",
    month: "short",
    day: "2-digit",
  }).format(new Date(value));
}

const labelMap: Record<string, string> = {
  interesting_for_analysis: "интересна для анализа",
  neutral: "нейтрально",
  elevated_risk: "повышенный риск",
  increased_risk: "повышенный риск",
  high_risk: "высокий риск",
  insufficient_data: "недостаточно данных",
  low: "низкий",
  medium: "средний",
  high: "высокий",
  critical: "критический",
  yield_score: "доходность",
  company_score: "скоринг эмитента",
  liquidity_score: "ликвидность",
  duration_score: "дюрация",
  spread_score: "спред",
  risk_penalty: "штраф за риск",
  final_bond_score: "итоговый скоринг облигации",
  final_company_score: "итоговый скоринг компании",
  debt_score: "долговая нагрузка",
  profitability_score: "рентабельность",
  cashflow_score: "денежный поток",
  stability_score: "устойчивость",
  net_debt_to_ebitda: "чистый долг / EBITDA",
  debt_to_equity: "долг / капитал",
  interest_coverage: "покрытие процентов",
  cash_to_short_term_debt: "денежные средства / краткосрочный долг",
  operating_cash_flow_to_total_debt: "операционный поток / общий долг",
  net_profit_margin: "маржа чистой прибыли",
  yield_to_maturity: "доходность к погашению",
  duration_years: "дюрация, лет",
  volume: "объем торгов",
  offer_date: "дата оферты",
  amortization: "амортизация",
};

export function labelFromKey(value: string): string {
  return labelMap[value] ?? value.replaceAll("_", " ");
}

const textMap: Record<string, string> = {
  "Yield to maturity is missing": "Не указана доходность к погашению",
  "Duration is missing": "Не указана дюрация",
  "Liquidity data is missing": "Нет данных о ликвидности",
  "Company score is missing": "Нет скоринга эмитента",
  "Spread data is missing": "Нет данных о спреде",
  "Bond has an offer date before maturity": "У облигации есть оферта до погашения",
  "Bond has amortization schedule": "У облигации есть график амортизации",
  "Bond not found": "Облигация не найдена",
  "Company not found": "Компания не найдена",
  "Bond score not found": "Скоринг облигации не найден",
  "Financial report for company not found": "Финансовая отчетность компании не найдена",
};

export function translateText(value: string): string {
  return textMap[value] ?? value;
}
