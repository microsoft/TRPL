import Header from "@/components/Header";
import Footer from "@/components/Footer";
import Sidebar from "@/components/Sidebar";
import { Outlet } from "react-router-dom";
import { useState } from "react";

const Layout = () => {
  // On large screens, sidebar is always visible via CSS. On mobile, it's toggleable.
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <div className="flex flex-col min-h-screen bg-gray-300 text-gray-950 w-full">
      {/* Header (fixed or top element) */}
      <div className="bg-white z-10">
        <Header
          currentPage="home"
          onNotificationClick={() => {}}
          onHelpClick={() => {}}
          onUserMenuClick={() => {}}
          onMenuClick={() => setSidebarOpen(!sidebarOpen)}
        />
      </div>

      {/* Main content area with sidebar */}
      <div className="flex flex-1 overflow-hidden pt-16 bg-white" style={{ height: 'calc(100vh - 16rem)' }}>
        {/* Sidebar */}
        <Sidebar isOpen={sidebarOpen} onClose={() => setSidebarOpen(false)} />

        {/* Main scrollable content area */}
        <main className="flex-1 bg-white overflow-y-auto custom-scrollbar transition-all duration-300 h-full relative z-0">
          <Outlet />
        </main>
      </div>

      {/* Footer (sticks to bottom when short, moves down when content grows) */}
      <Footer />
    </div>
  );
};

export default Layout;
