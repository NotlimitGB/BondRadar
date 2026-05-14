import type { AnalysisSignal } from "../api/types";
import { labelFromKey } from "../utils/format";

const toneBySignal: Record<AnalysisSignal, string> = {
  interesting_for_analysis: "border-teal-200 bg-teal-50 text-teal-800",
  neutral: "border-slate-200 bg-slate-50 text-slate-700",
  elevated_risk: "border-amber-200 bg-amber-50 text-amber-800",
  increased_risk: "border-orange-200 bg-orange-50 text-orange-800",
  high_risk: "border-red-200 bg-red-50 text-red-800",
  insufficient_data: "border-slate-200 bg-white text-slate-500",
};

export function StatusBadge({
  signal,
  label = "Signal",
}: {
  signal: AnalysisSignal | string | null | undefined;
  label?: string;
}) {
  if (!signal) {
    return (
      <span className="inline-flex items-center border border-slate-200 bg-white px-2 py-1 text-xs font-medium text-slate-500">
        {label}: n/a
      </span>
    );
  }

  const knownSignal = signal as AnalysisSignal;
  const tone =
    toneBySignal[knownSignal] ?? "border-slate-200 bg-white text-slate-600";

  return (
    <span
      className={`inline-flex items-center border px-2 py-1 text-xs font-semibold ${tone}`}
      style={{ borderRadius: 8 }}
    >
      {label}: {labelFromKey(signal)}
    </span>
  );
}
