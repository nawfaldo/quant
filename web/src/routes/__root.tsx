import { useEffect, useState } from 'react'
import { createRootRoute, Outlet, useRouterState } from '@tanstack/react-router'
import { MarchWorkspace } from './march'
import Sidebar from '../components/navigation/Sidebar'

export const Route = createRootRoute({
  component: RootRouteComponent,
})

function RootRouteComponent() {
  const pathname = useRouterState({ select: state => state.location.pathname })
  const isMarchRoute = pathname === '/march'
  const [hasVisitedMarch, setHasVisitedMarch] = useState(isMarchRoute)

  // March owns a stateful chart and live WebSocket connection. Once visited,
  // keep it mounted while other routes are open so returning does not recreate
  // the chart or download its candle history again.
  useEffect(() => {
    if (isMarchRoute) setHasVisitedMarch(true)
  }, [isMarchRoute])

  return (
    <div className="h-screen bg-gray-950 text-white flex flex-row overflow-hidden">
      <Sidebar />

      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        {(hasVisitedMarch || isMarchRoute) && (
          <div
            className={isMarchRoute ? 'flex flex-1 min-h-0 overflow-hidden' : 'hidden'}
            aria-hidden={!isMarchRoute}
          >
            <MarchWorkspace />
          </div>
        )}
        <Outlet />
      </div>
    </div>
  )
}
