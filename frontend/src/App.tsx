import { Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { BondDashboard } from "./pages/BondDashboard";
import { BondDetails } from "./pages/BondDetails";
import { CompanyDetails } from "./pages/CompanyDetails";
import { LivePaperDashboard } from "./pages/LivePaperDashboard";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<BondDashboard />} />
        <Route path="/live-paper" element={<LivePaperDashboard />} />
        <Route path="/bonds/:bondId" element={<BondDetails />} />
        <Route path="/companies/:companyId" element={<CompanyDetails />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}
