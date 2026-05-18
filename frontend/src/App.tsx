import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { LoadingState } from "./components/StateBlocks";

const BondDashboard = lazy(() =>
  import("./pages/BondDashboard").then((module) => ({
    default: module.BondDashboard,
  })),
);

const BondDetails = lazy(() =>
  import("./pages/BondDetails").then((module) => ({
    default: module.BondDetails,
  })),
);

const CompanyDetails = lazy(() =>
  import("./pages/CompanyDetails").then((module) => ({
    default: module.CompanyDetails,
  })),
);

const LivePaperDashboard = lazy(() =>
  import("./pages/LivePaperDashboard").then((module) => ({
    default: module.LivePaperDashboard,
  })),
);

const LivePaperPilotBootstrap = lazy(() =>
  import("./pages/LivePaperPilotBootstrap").then((module) => ({
    default: module.LivePaperPilotBootstrap,
  })),
);

const LivePaperPortfolioDetails = lazy(() =>
  import("./pages/LivePaperPortfolioDetails").then((module) => ({
    default: module.LivePaperPortfolioDetails,
  })),
);

const LivePaperSchedules = lazy(() =>
  import("./pages/LivePaperSchedules").then((module) => ({
    default: module.LivePaperSchedules,
  })),
);

export default function App() {
  return (
    <Layout>
      <Suspense fallback={<LoadingState label="Загрузка страницы" />}>
        <Routes>
          <Route path="/" element={<BondDashboard />} />
          <Route path="/live-paper" element={<LivePaperDashboard />} />
          <Route
            path="/live-paper/pilot-bootstrap"
            element={<LivePaperPilotBootstrap />}
          />
          <Route path="/live-paper/schedules" element={<LivePaperSchedules />} />
          <Route
            path="/live-paper/portfolios/:portfolioId"
            element={<LivePaperPortfolioDetails />}
          />
          <Route path="/bonds/:bondId" element={<BondDetails />} />
          <Route path="/companies/:companyId" element={<CompanyDetails />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </Layout>
  );
}
