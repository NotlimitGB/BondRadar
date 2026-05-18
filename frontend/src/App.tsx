import { Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { BondDashboard } from "./pages/BondDashboard";
import { BondDetails } from "./pages/BondDetails";
import { CompanyDetails } from "./pages/CompanyDetails";
import { LivePaperDashboard } from "./pages/LivePaperDashboard";
import { LivePaperPortfolioDetails } from "./pages/LivePaperPortfolioDetails";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<BondDashboard />} />
        <Route path="/live-paper" element={<LivePaperDashboard />} />
        <Route
          path="/live-paper/portfolios/:portfolioId"
          element={<LivePaperPortfolioDetails />}
        />
        <Route path="/bonds/:bondId" element={<BondDetails />} />
        <Route path="/companies/:companyId" element={<CompanyDetails />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}
