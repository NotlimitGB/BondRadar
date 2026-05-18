export function JsonDetails({ title, data }: { title: string; data: unknown }) {
  if (data === null || data === undefined) {
    return (
      <details className="rounded-lg border border-line bg-white p-3">
        <summary className="cursor-pointer text-sm font-semibold text-ink">
          {title}
        </summary>
        <div className="mt-3 text-sm text-slate-500">Нет данных</div>
      </details>
    );
  }

  return (
    <details className="rounded-lg border border-line bg-white p-3">
      <summary className="cursor-pointer text-sm font-semibold text-ink">
        {title}
      </summary>
      <pre className="mt-3 max-h-96 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-100">
        {JSON.stringify(data, null, 2)}
      </pre>
    </details>
  );
}
