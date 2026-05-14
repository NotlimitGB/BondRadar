import { AlertTriangle, Loader2 } from "lucide-react";

export function LoadingState({ label = "Loading data" }: { label?: string }) {
  return (
    <div className="surface flex items-center gap-3 px-4 py-5 text-sm text-slate-600">
      <Loader2 className="animate-spin" size={18} />
      <span>{label}</span>
    </div>
  );
}

export function EmptyState({ label }: { label: string }) {
  return (
    <div className="surface px-4 py-5 text-sm text-slate-500">{label}</div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="surface flex items-start gap-3 border-red-200 bg-red-50 px-4 py-4 text-sm text-red-800">
      <AlertTriangle className="mt-0.5 shrink-0" size={18} />
      <span>{message}</span>
    </div>
  );
}
