const EMPTY_VALUE = "вЂ”";

const statusLabels: Record<string, string> = {
  active: "Р°РєС‚РёРІРЅРѕ",
  paused: "РїР°СѓР·Р°",
  archived: "Р°СЂС…РёРІ",
  running: "РІ СЂР°Р±РѕС‚Рµ",
  completed: "Р·Р°РІРµСЂС€РµРЅ",
  blocked: "Р·Р°Р±Р»РѕРєРёСЂРѕРІР°РЅ",
  failed: "РѕС€РёР±РєР°",
  skipped: "РїСЂРѕРїСѓС‰РµРЅ",
  dry_run: "dry-run",
  due: "due",
  ready: "РіРѕС‚РѕРІРѕ",
  warning: "РІРЅРёРјР°РЅРёРµ",
  not_ready: "РЅРµ РіРѕС‚РѕРІРѕ",
};

export function toNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

export function formatPlain(value: unknown, digits = 2): string {
  const numeric = toNumber(value);
  if (numeric === null) {
    return value === null || value === undefined || value === ""
      ? EMPTY_VALUE
      : String(value);
  }
  return new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: digits,
  }).format(numeric);
}

export function formatMoney(value: unknown, currency = "RUB"): string {
  const numeric = toNumber(value);
  if (numeric === null) {
    return EMPTY_VALUE;
  }
  try {
    return new Intl.NumberFormat("ru-RU", {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    }).format(numeric);
  } catch {
    return `${formatPlain(numeric, 0)} ${currency}`;
  }
}

export function formatPercent(value: unknown): string {
  const numeric = toNumber(value);
  if (numeric === null) {
    return EMPTY_VALUE;
  }
  return new Intl.NumberFormat("ru-RU", {
    style: "percent",
    maximumFractionDigits: 2,
  }).format(numeric);
}

export function formatDateOnly(value: string | null | undefined): string {
  if (!value) {
    return EMPTY_VALUE;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(parsed);
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return EMPTY_VALUE;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

export function statusLabel(value: string | null | undefined): string {
  if (!value) {
    return EMPTY_VALUE;
  }
  return statusLabels[value] ?? value.replaceAll("_", " ");
}

export function messageFromRecord(item: unknown): string {
  if (!item || typeof item !== "object") {
    return formatPlain(item);
  }
  const record = item as Record<string, unknown>;
  const message = record.message ?? record.detail ?? record.code;
  if (
    typeof message === "string" ||
    typeof message === "number" ||
    typeof message === "boolean"
  ) {
    return String(message);
  }
  return JSON.stringify(record);
}

export function asRecord(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
}

export function asRecordArray(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => asRecord(item))
    .filter((item): item is Record<string, unknown> => item !== null);
}
