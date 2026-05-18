import type { LivePaperHealthStatus } from "../../api/types";
import { statusLabel } from "../../utils/livePaperFormat";

const healthTone: Record<LivePaperHealthStatus, string> = {
  healthy: "border-teal-200 bg-teal-50 text-teal-800",
  warning: "border-amber-200 bg-amber-50 text-amber-800",
  critical: "border-red-200 bg-red-50 text-red-800",
  unknown: "border-slate-200 bg-slate-50 text-slate-700",
};

const healthLabels: Record<LivePaperHealthStatus, string> = {
  healthy: "Р·РґРѕСЂРѕРІРѕ",
  warning: "РІРЅРёРјР°РЅРёРµ",
  critical: "РєСЂРёС‚РёС‡РЅРѕ",
  unknown: "РЅРµС‚ РґР°РЅРЅС‹С…",
};

const statusTone: Record<string, string> = {
  active: "border-teal-200 bg-teal-50 text-teal-800",
  completed: "border-teal-200 bg-teal-50 text-teal-800",
  ready: "border-teal-200 bg-teal-50 text-teal-800",
  scheduled: "border-teal-200 bg-teal-50 text-teal-800",
  prepared: "border-sky-200 bg-sky-50 text-sky-800",
  running: "border-sky-200 bg-sky-50 text-sky-800",
  dry_run: "border-sky-200 bg-sky-50 text-sky-800",
  blocked: "border-amber-200 bg-amber-50 text-amber-800",
  warning: "border-amber-200 bg-amber-50 text-amber-800",
  paused: "border-amber-200 bg-amber-50 text-amber-800",
  skipped: "border-slate-200 bg-slate-50 text-slate-700",
  failed: "border-red-200 bg-red-50 text-red-800",
  critical: "border-red-200 bg-red-50 text-red-800",
  archived: "border-slate-200 bg-slate-50 text-slate-700",
};

export function HealthBadge({ status }: { status: LivePaperHealthStatus }) {
  return (
    <span
      className={`inline-flex items-center border px-2 py-1 text-xs font-semibold ${healthTone[status]}`}
      style={{ borderRadius: 8 }}
    >
      {healthLabels[status]}
    </span>
  );
}

export function StatusBadge({
  status,
}: {
  status: string | null | undefined;
}) {
  const value = status ?? "unknown";
  return (
    <span
      className={`inline-flex items-center border px-2 py-1 text-xs font-semibold ${
        statusTone[value] ?? "border-slate-200 bg-slate-50 text-slate-700"
      }`}
      style={{ borderRadius: 8 }}
    >
      {statusLabel(value)}
    </span>
  );
}
