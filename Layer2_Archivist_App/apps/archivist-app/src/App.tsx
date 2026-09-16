// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import "./App.css";
import Layout from "./pages/Layout";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import NoPage from "./pages/NoPage";
import HomePage from "./pages/Home";
import ReviewPage from "./pages/Review";
import CollectionsPage from "./pages/Collections";
import CollectionsItemsPage from "./pages/CollectionsItems";
import CollectionReviewPage from "./pages/CollectionReview";
import AdminPage from "./pages/Admin";
import StatisticsAdminPage from "./pages/StatisticsAdmin";
import RepositoriesPage from "./pages/Repositories";
import RepositoryDetailPage from "./pages/RepositoryDetail";
import DashboardPage from "./pages/Dashboard";
import EpubProcessorPage from "./pages/EpubProcessor";
import DataPipelineMonitor from "./pages/DataPipelineMonitor";
import PipelineErrors from "./pages/DataPipelineMonitor/PipelineErrors";
import PipelineErrorRecords from "./pages/DataPipelineMonitor/PipelineErrorRecords";
import DataIngestionPage from "./pages/DataIngestion";
import IngestionErrors from "./pages/DataIngestion/IngestionErrors";
import IngestionErrorRecords from "./pages/DataIngestion/IngestionErrorRecords";
import ArchitectureDiagrams from "./pages/ArchitectureDiagrams";
import FieldMappingConfigPage from "./pages/FieldMappingConfig";
import CorrectionRequestsPage from "./pages/CorrectionRequests";
import CollectionOcrReportPage from "./pages/CollectionOcrReport";
import PublicResourcesPage from "./pages/PublicResources";
import PublicIngestionPage from "./pages/PublicIngestion";
import CyclopediaPage, { CyclopediaDetail } from "./pages/Cyclopedia";
import MooreChronologyPage from "./pages/MooreChronology";
import MooreChronologyChronologiesPage from "./pages/MooreChronologyChronologies";
import MooreChronologyViewerPage from "./pages/MooreChronologyViewer";
import MooreChronologyLetterPage from "./pages/MooreChronologyLetter";
import GenealogyPapersPage, { GenealogyPapersDetail } from "./pages/GenealogyPapers";
import { AuthProvider } from "./contexts/AuthContext";
import ProtectedRoute from "./components/ProtectedRoute";

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Layout />}>
            {/* Public routes */}
            <Route index element={<DashboardPage />} />
            <Route path="home" element={<HomePage />} />
            <Route path="repositories" element={<RepositoriesPage />} />
            <Route path="repositories/:repository/collections" element={<RepositoryDetailPage />} />
            <Route path="repositories/:repository/collections/:collectionName" element={<CollectionsItemsPage />} />
            <Route path="repositories/:repository/collections/:collectionName/ocr-report" element={<CollectionOcrReportPage />} />
            {/* Review pages - accessible to all, but modify actions require documents.canEdit */}
            <Route path="repositories/:repository/collections/:collectionName/review/:recordid" element={<CollectionReviewPage />} />
            <Route path="review/:id" element={<ReviewPage />} />
            <Route path="collections" element={<CollectionsPage />} />
            <Route path="correction-requests" element={
              <ProtectedRoute permission="documents">
                <CorrectionRequestsPage />
              </ProtectedRoute>
            } />
            <Route path="digital-resources" element={<PublicResourcesPage />} />
            <Route path="digital-resources/cyclopedia" element={<CyclopediaPage />} />
            <Route path="digital-resources/cyclopedia/:itemId" element={<CyclopediaDetail />} />
            <Route path="digital-resources/moore-chronology" element={<MooreChronologyPage />} />
            <Route path="digital-resources/moore-chronology/chronologies" element={<MooreChronologyChronologiesPage />} />
            <Route path="digital-resources/moore-chronology/chronologies/:itemId" element={<MooreChronologyViewerPage />} />
            <Route path="digital-resources/moore-chronology/letter" element={<MooreChronologyLetterPage />} />
            <Route path="digital-resources/moore-chronology/letter/:itemId" element={<MooreChronologyViewerPage />} />
            <Route path="digital-resources/genealogy-papers" element={<GenealogyPapersPage />} />
            <Route path="digital-resources/genealogy-papers/:itemId" element={<GenealogyPapersDetail />} />
            <Route path="admin/statistics" element={<StatisticsAdminPage />} />
            <Route path="help/architecture" element={<ArchitectureDiagrams />} />

            {/* Protected routes - EPUB Processor (Admin only) */}
            <Route path="tools/epub-processor" element={
              <ProtectedRoute adminOnly>
                <EpubProcessorPage />
              </ProtectedRoute>
            } />

            {/* Protected routes - Data Pipeline (requires pipelineMonitor.canEdit) */}
            <Route path="tools/data-pipeline" element={
              <ProtectedRoute permission="pipelineMonitor">
                <DataPipelineMonitor />
              </ProtectedRoute>
            } />
            <Route path="tools/data-pipeline/errors/:stageId" element={
              <ProtectedRoute permission="pipelineMonitor">
                <PipelineErrors />
              </ProtectedRoute>
            } />
            <Route path="tools/data-pipeline/errors/:stageId/records" element={
              <ProtectedRoute permission="pipelineMonitor">
                <PipelineErrorRecords />
              </ProtectedRoute>
            } />

            {/* Protected routes - Data Ingestion (requires dataIngestion.canEdit) */}
            <Route path="tools/public-resources-ingestion" element={
              <ProtectedRoute adminOnly>
                <PublicIngestionPage />
              </ProtectedRoute>
            } />
            <Route path="tools/data-ingestion" element={
              <ProtectedRoute permission="dataIngestion">
                <DataIngestionPage />
              </ProtectedRoute>
            } />
            <Route path="tools/data-ingestion/errors" element={
              <ProtectedRoute permission="dataIngestion">
                <IngestionErrors />
              </ProtectedRoute>
            } />
            <Route path="tools/data-ingestion/errors/records" element={
              <ProtectedRoute permission="dataIngestion">
                <IngestionErrorRecords />
              </ProtectedRoute>
            } />

            {/* Admin routes */}
            <Route path="admin" element={
              <ProtectedRoute adminOnly>
                <AdminPage />
              </ProtectedRoute>
            } />
            <Route path="admin/field-mappings" element={
              <ProtectedRoute adminOnly>
                <FieldMappingConfigPage />
              </ProtectedRoute>
            } />

            {/* Catch-all */}
            <Route path="*" element={<NoPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
