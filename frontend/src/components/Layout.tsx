import { BarChart3, Database } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-panel">
      <header className="border-b border-line bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-5 py-4">
          <Link to="/" className="flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent text-white">
              <BarChart3 size={20} />
            </span>
            <span>
              <span className="block text-base font-semibold tracking-normal text-ink">
                BondRadar
              </span>
              <span className="block text-xs text-slate-500">
                Analytical statuses only
              </span>
            </span>
          </Link>
          <div className="hidden items-center gap-2 text-xs text-slate-500 sm:flex">
            <Database size={16} />
            <span>Backend API via /api</span>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-5 py-6">{children}</main>
    </div>
  );
}
