import { BarChart3, Database, WalletCards } from "lucide-react";
import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-panel">
      <header className="border-b border-line bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-5 py-4">
          <Link to="/" className="flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent text-white">
              <BarChart3 size={20} />
            </span>
            <span>
              <span className="block text-base font-semibold tracking-normal text-ink">
                BondRadar
              </span>
              <span className="block text-xs text-slate-500">
                Только информационные статусы
              </span>
            </span>
          </Link>
          <div className="flex items-center gap-3">
            <nav className="flex items-center gap-2 text-sm">
              <NavLink
                to="/live-paper"
                className={({ isActive }) =>
                  `inline-flex items-center gap-2 border px-3 py-2 font-medium transition ${
                    isActive
                      ? "border-accent bg-teal-50 text-accent"
                      : "border-slate-200 bg-white text-slate-700 hover:border-accent hover:text-accent"
                  }`
                }
                style={{ borderRadius: 8 }}
              >
                <WalletCards size={16} />
                <span>Live Paper</span>
              </NavLink>
            </nav>
            <div className="hidden items-center gap-2 text-xs text-slate-500 sm:flex">
              <Database size={16} />
              <span>API сервера через /api</span>
            </div>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-5 py-6">{children}</main>
    </div>
  );
}
