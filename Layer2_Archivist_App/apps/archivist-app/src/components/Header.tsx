'use client'

import { Archive, HelpCircle, Clock, LogOut, User, Menu } from 'lucide-react'
import BreadcrumbNav from '@/components/BreadcrumbNav'
import { useLocation } from 'react-router-dom'
import { useAuth } from '@/contexts/AuthContext'
import { signOut } from '@/services/auth'
import { useState } from 'react'

interface HeaderProps {
  currentPage?: string
  onNotificationClick: () => void
  onHelpClick: () => void
  onUserMenuClick: () => void
  onMenuClick?: () => void
}

const Header: React.FC<HeaderProps> = ({
  onHelpClick,
  onUserMenuClick,
  onMenuClick,
}) => {
  const location = useLocation()
  const { user, isAuthenticated, isLoading } = useAuth()
  const [showUserMenu, setShowUserMenu] = useState(false)

  const pathname = location.pathname
  const currentPage =
    pathname === '/' || pathname.startsWith('/review')
      ? 'home'
      : pathname.startsWith('/collections')
      ? 'collections'
      : pathname.startsWith('/repositories')
      ? 'repositories'
      : ''

  const lastSync = new Date().toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  })

  const handleSignOut = () => {
    signOut()
  }

  return (
    <>
      {/* Fixed Top Header */}
      <header
        id="top-header"
        data-component-id="trpl:header"
        className="group/header fixed top-0 z-50 w-full bg-museum-950 text-white backdrop-blur-lg shadow-md"
      >
        <div className="px-6 py-4">
          <div className="flex items-center justify-between">
            {/* Left Section */}
            <div className="flex items-center space-x-6">
              {/* Hamburger Menu Button */}
              <button
                onClick={onMenuClick}
                className="p-2 text-white hover:bg-white/10 rounded-lg transition-colors"
                aria-label="Toggle menu"
              >
                <Menu className="w-6 h-6" />
              </button>
              
              <div className="flex items-center space-x-3">
                <span className="font-semibold tracking-wide text-white">TRPL Archivist</span>
              </div>

              {/* Breadcrumb Navigation */}
              <BreadcrumbNav />
            </div>

            {/* Right Section */}
            <div className="flex items-center space-x-4">
              {/* <div className="hidden md:flex items-center space-x-2 text-sm text-gray-200">
                <Clock className="w-3 h-3" aria-hidden="true" />
                <span>Last sync: {lastSync}</span>
              </div> */}

              {/* <button
                className="p-2 text-gray-200 hover:text-white hover:bg-gray-700/30 rounded-full transition-all duration-300 hover:shadow-[0_0_10px_rgba(255,255,255,0.5)]"
                onClick={onHelpClick}
                aria-label="Help"
              >
                <HelpCircle className="w-5 h-5" />
              </button> */}

              {/* User Menu */}
              {!isLoading && isAuthenticated && user && (
                <div className="relative">
                  <button
                    className="flex items-center justify-center w-5 h-5 rounded-full border-2 border-white text-white hover:bg-white/10 transition-all duration-300 hover:shadow-[0_0_10px_rgba(255,255,255,0.6)]"
                    onClick={() => setShowUserMenu(!showUserMenu)}
                    aria-label="User menu"
                  >
                    <User className="w-3 h-3" />
                    {/* <span className="hidden md:inline text-sm">{user.display_name}</span> */}
                  </button>

                  {/* User Dropdown Menu */}
                  {showUserMenu && (
                    <div className="absolute right-0 mt-2 w-64 bg-white rounded-lg shadow-lg overflow-hidden z-50 border border-gray-200">
                      <div className="flex items-center px-4 py-3 border-b border-gray-200 bg-museum-50">
                      <div className="flex-shrink-0 w-10 h-10 rounded-full bg-black flex items-center justify-center mr-3">
                          <User className="w-5 h-5 text-white" />
                      </div>
                      <div className="px-4 py-3 border-b border-gray-200 bg-museum-50">
                        <p className="text-sm font-medium text-museum-900">{user.name}</p>
                        <p className="text-xs text-museum-600 truncate">{user.email}</p>
                      </div>
                      </div>
                      <button
                        onClick={handleSignOut}
                        className="w-full flex items-center space-x-2 px-4 py-3 text-left text-sm text-gray-700 hover:bg-gray-100 transition-colors"
                      >
                        <LogOut className="w-4 h-4" />
                        <span>Sign Out</span>
                      </button>
                    </div>
                  )}
                </div>
              )}

              {/* Loading State */}
              {isLoading && (
                <div className="flex items-center space-x-2 text-sm text-gray-200">
                  <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white"></div>
                </div>
              )}
            </div>
          </div>
        </div>
      </header>

      
    </>
  )
}

export default Header
