'use client'

const Footer: React.FC = () => {
  const handleLinkClick = (link: string) => {
    console.log('Navigate to:', link)
  }

  return (
    <footer className=" bottom-0 left-0 right-0 z-50 w-full bg-museum-950 text-white backdrop-blur-lg shadow-md py-4 px-6">
      <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between space-y-4 lg:space-y-0">
        <div className="flex items-center space-x-6">
          <span
            className="text-sm text-white hover:text-gray-300 transition-colors cursor-pointer"
            onClick={() => handleLinkClick('privacy')}
            role="button"
            tabIndex={0}
          >
            Privacy Policy
          </span>
          <span
            className="text-sm text-white hover:text-gray-300 transition-colors cursor-pointer"
            onClick={() => handleLinkClick('terms')}
            role="button"
            tabIndex={0}
          >
            Terms of Service
          </span>
          <span
            className="text-sm text-white hover:text-gray-300 transition-colors cursor-pointer"
            onClick={() => handleLinkClick('help')}
            role="button"
            tabIndex={0}
          >
            Help
          </span>
        </div>
        {/* <div className="flex items-center space-x-4">
          <div className="text-sm text-white">
            System Status: <span className="text-green-400 font-medium">All systems operational</span>
          </div>
          <div className="w-px h-4 bg-white/50" aria-hidden="true"></div>
          <div className="text-sm text-white">
            Version 2.1.4
          </div>
        </div> */}
      </div>
    </footer>
  )
}

export default Footer
