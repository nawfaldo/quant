import { Link } from "@tanstack/react-router";

export default function Sidebar() {
  return (
    <div className="w-[50px] h-screen bg-gray-950 border-r border-gray-900/60 flex flex-col items-center pt-2 pb-4 select-none shrink-0 z-20">
      {/* Navigation links */}
      <div className="flex flex-col gap-3 w-full items-center">
        <Link to="/">
          {({ isActive }) => (
            <div
              className={`w-9 h-9 flex items-center justify-center rounded-lg transition-all duration-200 ${
                isActive ? "text-white" : "text-gray-500 hover:text-gray-200"
              }`}
              title="Chart"
            >
              <svg
                width="20"
                height="20"
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

        <Link to="/stats">
          {({ isActive }) => (
            <div
              className={`w-9 h-9 flex items-center justify-center rounded-lg transition-all duration-200 ${
                isActive ? "text-white" : "text-gray-500 hover:text-gray-200"
              }`}
              title="Stats"
            >
              <svg
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <line x1="18" y1="20" x2="18" y2="10" />
                <line x1="12" y1="20" x2="12" y2="4" />
                <line x1="6" y1="20" x2="6" y2="14" />
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
      </div>
    </div>
  );
}
