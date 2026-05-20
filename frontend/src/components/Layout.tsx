import {
  BarChart3,
  CalendarClock,
  Database,
  LayoutDashboard,
  ShieldAlert,
  WalletCards,
} from "lucide-react";
import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-panel">
      <header className="border-b border-line bg-white">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-5 py-4 lg:flex-row lg:items-center lg:justify-between">
          <Link to="/" className="flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent text-white">
              <BarChart3 size={20} />
            </span>
            <span>
              <span className="block text-base font-semibold tracking-normal text-ink">
                BondRadar
              </span>
              <span className="block text-xs text-slate-500">
                Информационный режим · virtual paper only
              </span>
            </span>
          </Link>
          <div className="flex w-full flex-col gap-3 lg:w-auto lg:flex-row lg:items-center">
            <nav className="flex flex-wrap items-center gap-2 text-sm">
              <NavLink
                end
                to="/"
                className={({ isActive }) =>
                  `inline-flex items-center gap-2 border px-3 py-2 font-medium transition ${
                    isActive
                      ? "border-accent bg-teal-50 text-accent"
                      : "border-slate-200 bg-white text-slate-700 hover:border-accent hover:text-accent"
                  }`
                }
                style={{ borderRadius: 8 }}
              >
                <LayoutDashboard size={16} />
                <span>Облигации</span>
              </NavLink>
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
                <span>Виртуальный контур</span>
              </NavLink>
              <NavLink
                to="/live-paper/schedules"
                className={({ isActive }) =>
                  `inline-flex items-center gap-2 border px-3 py-2 font-medium transition ${
                    isActive
                      ? "border-accent bg-teal-50 text-accent"
                      : "border-slate-200 bg-white text-slate-700 hover:border-accent hover:text-accent"
                  }`
                }
                style={{ borderRadius: 8 }}
              >
                <CalendarClock size={16} />
                <span>Расписания</span>
              </NavLink>
              <NavLink
                to="/risk/external-regime"
                className={({ isActive }) =>
                  `inline-flex items-center gap-2 border px-3 py-2 font-medium transition ${
                    isActive
                      ? "border-accent bg-teal-50 text-accent"
                      : "border-slate-200 bg-white text-slate-700 hover:border-accent hover:text-accent"
                  }`
                }
                style={{ borderRadius: 8 }}
              >
                <ShieldAlert size={16} />
                <span>Внешний риск</span>
              </NavLink>
            </nav>
            <div className="hidden items-center gap-2 text-xs text-slate-500 sm:flex">
              <Database size={16} />
              <span>no broker · no real money · /api</span>
            </div>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-5 py-6">{children}</main>
    </div>
  );
}
