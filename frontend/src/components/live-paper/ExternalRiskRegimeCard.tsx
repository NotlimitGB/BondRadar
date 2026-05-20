import { AlertTriangle, ShieldAlert } from "lucide-react";
import { Link } from "react-router-dom";

import type { ExternalRiskRegime } from "../../api/types";
import { formatDateTime } from "../../utils/livePaperFormat";

const modeLabels: Record<ExternalRiskRegime["mode"], string> = {
  normal: "Обычный режим",
  elevated: "Повышенный внешний риск",
  severe: "Жёсткий внешний риск",
};

const modeTone: Record<ExternalRiskRegime["mode"], string> = {
  normal: "border-teal-200 bg-teal-50 text-teal-800",
  elevated: "border-amber-200 bg-amber-50 text-amber-800",
  severe: "border-red-200 bg-red-50 text-red-800",
};

const modeDescriptions: Record<ExternalRiskRegime["mode"], string> = {
  normal: "Обычный режим: virtual paper monitoring может работать штатно.",
  elevated:
    "Повышенный внешний риск: confirmed paper execution требует manual review.",
  severe:
    "Жёсткий внешний риск: confirmed paper execution блокируется safety overlay по умолчанию.",
};

export function externalRiskModeLabel(
  mode: ExternalRiskRegime["mode"] | null | undefined,
): string {
  return mode ? modeLabels[mode] : "Внешний риск не загружен";
}

export function ExternalRiskRegimeCard({
  regime,
  compact = false,
  showLink = true,
}: {
  regime?: ExternalRiskRegime | null;
  compact?: boolean;
  showLink?: boolean;
}) {
  if (!regime) {
    return null;
  }

  return (
    <section className={`surface border p-4 ${modeTone[regime.mode]}`}>
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div>
          <div className="inline-flex items-center gap-2 rounded-lg border border-current px-2 py-1 text-xs font-semibold uppercase">
            {regime.mode === "normal" ? (
              <ShieldAlert size={15} />
            ) : (
              <AlertTriangle size={15} />
            )}
            {modeLabels[regime.mode]}
          </div>
          <p className="mt-3 text-sm">{modeDescriptions[regime.mode]}</p>
          {!compact ? (
            <p className="mt-2 text-sm">
              {regime.reason || "Внешний режим задан без пояснения."}
            </p>
          ) : null}
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs opacity-85">
            <span>Источник: {regime.source || "manual"}</span>
            <span>До: {formatDateTime(regime.expires_at)}</span>
            <span>virtual paper only</span>
            <span>no broker</span>
            <span>no real money</span>
          </div>
        </div>
        {showLink ? (
          <Link className="text-button shrink-0" to="/risk/external-regime">
            Открыть внешний риск
          </Link>
        ) : null}
      </div>
    </section>
  );
}
