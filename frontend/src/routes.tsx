import { lazy, Suspense } from "react";
import { createBrowserRouter, Navigate } from "react-router";

import { AppShell } from "./app/AppShell";
import { SkeletonCard } from "./components/ui/skeleton";

const HomePage = lazy(() => import("./features/home/HomePage"));
const WorkspacePage = lazy(() => import("./features/workspace/WorkspacePage"));
const DecisionsPage = lazy(() => import("./features/decisions/DecisionsPage"));
const PortfolioPage = lazy(() => import("./features/portfolio/PortfolioPage"));
const TrackRecordPage = lazy(
  () => import("./features/track-record/TrackRecordPage"),
);
// P4-02: the PUBLIC track record renders OUTSIDE AppShell (and therefore
// outside AuthGate) — visitors reach the ledger without ever seeing the
// login screen. /track-record stays the operator's in-app view.
const PublicTrackRecordPage = lazy(
  () => import("./features/track-record/PublicTrackRecordPage"),
);
const IntelPage = lazy(() => import("./features/intelligence/IntelPage"));
const SettingsPage = lazy(() => import("./features/settings/SettingsPage"));
const ReportPage = lazy(() => import("./features/report/ReportPage"));
const BacktestPage = lazy(() => import("./features/backtest/BacktestPage"));

const page = (element: React.ReactNode) => (
  <Suspense fallback={<SkeletonCard lines={6} />}>{element}</Suspense>
);

export const router = createBrowserRouter([
  {
    // pre-auth public route: NOT a child of AppShell, so no AuthGate
    path: "/public/track-record",
    element: page(<PublicTrackRecordPage />),
  },
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: page(<HomePage />) },
      { path: "trade", element: <Navigate to="/trade/BTC-USD" replace /> },
      { path: "trade/:symbol", element: page(<WorkspacePage />) },
      { path: "decisions", element: page(<DecisionsPage />) },
      { path: "decisions/:runId", element: page(<DecisionsPage />) },
      { path: "portfolio", element: page(<PortfolioPage />) },
      { path: "backtest", element: page(<BacktestPage />) },
      { path: "track-record", element: page(<TrackRecordPage />) },
      { path: "intel", element: page(<IntelPage />) },
      { path: "settings", element: page(<SettingsPage />) },
      { path: "report", element: page(<ReportPage />) },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);
