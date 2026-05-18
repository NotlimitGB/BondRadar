import type { ReactNode } from "react";

import { EmptyState } from "../StateBlocks";
import { messageFromRecord } from "../../utils/livePaperFormat";

export function Section({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <section className="surface p-4">
      <div className="mb-4">
        <h2 className="text-base font-semibold text-ink">{title}</h2>
        {subtitle ? (
          <p className="mt-1 text-sm text-slate-600">{subtitle}</p>
        ) : null}
      </div>
      {children}
    </section>
  );
}

export function SummaryItem({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-white p-3">
      <div className="text-xs font-semibold uppercase text-slate-500">{label}</div>
      <div className="mt-1 text-sm font-semibold text-ink">{value}</div>
    </div>
  );
}

export function MessageList({
  title,
  items,
  tone,
  emptyLabel = "Нет записей для отображения.",
}: {
  title: string;
  items: unknown[];
  tone: string;
  emptyLabel?: string;
}) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      {items.length ? (
        <div className="mt-2 space-y-2">
          {items.map((item, index) => (
            <div
              className={`rounded-lg border px-3 py-2 text-sm ${tone}`}
              key={`${title}-${index}`}
            >
              {messageFromRecord(item)}
            </div>
          ))}
        </div>
      ) : (
        <EmptyState label={emptyLabel} />
      )}
    </div>
  );
}
