import { useEffect, useState } from 'react'
import { createFileRoute, Outlet, useRouterState, useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { fetchEnvironments } from '../api'
import PageShell from '../components/layout/PageShell'
import EnvironmentPage from './environment.index'
import EnvironmentDetailPage from './environment.$environmentId'

export const Route = createFileRoute('/environment')({
  component: EnvironmentLayout,
})

function EnvironmentLayout() {
  const routerState = useRouterState()
  const navigate = useNavigate()
  const pathname = routerState.location.pathname
  const matchDetail = pathname.match(/^\/environment\/(\d+)/)
  const isDetail = !!matchDetail
  const envId = matchDetail ? Number(matchDetail[1]) : null

  const [activeEnvId, setActiveEnvId] = useState<number | null>(envId)

  useEffect(() => {
    if (envId !== null) {
      setActiveEnvId(envId)
      sessionStorage.setItem('last_visited_environment_id', String(envId))
    } else if (pathname === '/environment' || pathname === '/environment/') {
      const lastId = sessionStorage.getItem('last_visited_environment_id')
      if (lastId) {
        void navigate({
          to: '/environment/$environmentId',
          params: { environmentId: lastId },
        })
      }
    }
  }, [envId, pathname, navigate])

  const { data: environments = [], isLoading, isError, error } = useQuery({
    queryKey: ['environments'],
    queryFn: fetchEnvironments,
  })

  const selectedEnv = environments.find((env) => env.id === activeEnvId)

  return (
    <PageShell>
      <div className="flex-1 flex flex-row relative overflow-hidden bg-[#121214]">
        {/* List Page Container */}
        <div
          className={`absolute inset-0 flex flex-col transition-all duration-150 ease-out ${
            isDetail ? 'opacity-50 pointer-events-none' : 'opacity-100'
          }`}
        >
          <EnvironmentPage />
        </div>

        {/* Detail Page Container */}
        <div
          className={`absolute inset-0 flex flex-col bg-[#121214] transition-all duration-150 ease-out ${
            isDetail ? 'translate-x-0 opacity-100' : 'translate-x-full opacity-0 pointer-events-none'
          }`}
        >
          {isLoading ? (
            <div className="flex flex-1 pt-5 pl-[50px] text-sm text-gray-500">Loading environment…</div>
          ) : isError || (activeEnvId !== null && !selectedEnv) ? (
            <div className="flex flex-1 flex-col items-start gap-3 pt-5 pl-[50px]">
              <p className="text-sm text-red-400">
                {isError
                  ? `Could not load environment: ${error instanceof Error ? error.message : 'unknown error'}`
                  : 'Environment not found.'}
              </p>
              <button
                type="button"
                className="liquid-glass-btn liquid-glass-btn-no-grow liquid-glass-btn-rounded-lg px-3 py-1.5 text-xs font-bold"
                onClick={() => {
                  sessionStorage.removeItem('last_visited_environment_id')
                  void navigate({ to: '/environment' })
                }}
              >
                Back to environments
              </button>
            </div>
          ) : selectedEnv ? (
            <EnvironmentDetailPage
              selectedEnv={selectedEnv}
              onBack={() => {
                sessionStorage.removeItem('last_visited_environment_id')
                void navigate({ to: '/environment' })
              }}
            />
          ) : null}
        </div>
      </div>
      {/* Hidden Outlet to keep route matching active and happy in TanStack Router */}
      <div className="hidden" aria-hidden="true">
        <Outlet />
      </div>
    </PageShell>
  )
}
