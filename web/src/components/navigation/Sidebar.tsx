import { Link } from "@tanstack/react-router";

export default function Sidebar() {
  return (
    <div className="w-[50px] h-screen bg-[#121214] border-r border-[#212124] flex flex-col items-center pt-2 pb-4 select-none shrink-0 z-20">
      {/* Navigation links */}
      <div className="flex flex-col gap-3 w-full items-center">
        <Link to="/">
          {({ isActive }) => (
            <div
              className={`w-9 h-9 flex items-center justify-center rounded-lg transition-all duration-200 ${
                isActive ? "text-white" : "text-gray-500 hover:text-gray-200"
              }`}
              title="Home"
            >
              <svg
                width="20"
                height="20"
                className="w-5 h-5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
                <polyline points="9 22 9 12 15 12 15 22" />
              </svg>
            </div>
          )}
        </Link>

        <Link to="/march">
          {({ isActive }) => (
            <div
              className={`w-9 h-9 flex items-center justify-center rounded-lg transition-all duration-200 ${
                isActive ? "text-white" : "text-gray-500 hover:text-gray-200"
              }`}
              title="Chart"
            >
              <svg
                width="24"
                height="24"
                className="w-6 h-6"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M4 7c1-2 4-2 6 0" />
                <path d="M14 7c1-2 4-2 6 0" />
                <ellipse cx="7" cy="13.5" rx="4" ry="5.5" />
                <ellipse cx="17" cy="13.5" rx="4" ry="5.5" />
                <ellipse
                  cx="5.5"
                  cy="13.5"
                  rx="2"
                  ry="3.5"
                  fill="currentColor"
                  stroke="none"
                />
                <ellipse
                  cx="15.5"
                  cy="13.5"
                  rx="2"
                  ry="3.5"
                  fill="currentColor"
                  stroke="none"
                />
              </svg>
            </div>
          )}
        </Link>

        <Link to="/database">
          {({ isActive }) => (
            <div
              className={`w-9 h-9 flex items-center justify-center rounded-lg transition-all duration-200 ${
                isActive ? "text-white" : "text-gray-500 hover:text-gray-200"
              }`}
              title="Database"
            >
              <svg
                width="20"
                height="20"
                className="w-5 h-5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <ellipse cx="12" cy="5" rx="9" ry="3" />
                <path d="M3 5V19A9 3 0 0 0 21 19V5" />
                <path d="M3 12A9 3 0 0 0 21 12" />
              </svg>
            </div>
          )}
        </Link>

        <Link to="/test">
          {({ isActive }) => (
            <div
              className={`w-9 h-9 flex items-center justify-center rounded-lg transition-all duration-200 ${
                isActive ? "text-white" : "text-gray-500 hover:text-gray-200"
              }`}
              title="Test"
            >
              <svg
                width="20"
                height="20"
                className="w-5 h-5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M12 20h9" />
                <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
              </svg>
            </div>
          )}
        </Link>

        <Link to="/environment">
          {({ isActive }) => (
            <div
              className={`w-9 h-9 flex items-center justify-center rounded-lg transition-all duration-200 ${
                isActive ? "text-white" : "text-gray-500 hover:text-gray-200"
              }`}
              title="Environment"
            >
              <svg
                width="20"
                height="20"
                className="w-5 h-5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
                <line x1="8" y1="21" x2="16" y2="21" />
                <line x1="12" y1="17" x2="12" y2="21" />
              </svg>
            </div>
          )}
        </Link>

        <Link to="/code">
          {({ isActive }) => (
            <div
              className={`w-9 h-9 flex items-center justify-center rounded-lg transition-all duration-200 ${
                isActive ? "text-white" : "text-gray-500 hover:text-gray-200"
              }`}
              title="Code"
            >
              <svg
                width="20"
                height="20"
                className="w-5 h-5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="m8 7-5 5 5 5" />
                <line x1="14" y1="5" x2="10" y2="19" />
                <path d="m16 7 5 5-5 5" />
              </svg>
            </div>
          )}
        </Link>
      </div>
    </div>
  );
}
