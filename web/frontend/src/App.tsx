import { useState, useEffect, useMemo } from 'react'
import { useQuery, useQueries } from '@tanstack/react-query'
import { TIMEFRAMES, makeDefaultPanelConfig, type TF, type MarchLayouts, type LayoutPanelConfig } from './types'
import { fetchBacktests, fetchTrades, fetchTradesFx, type RunResult, type TuneResult } from './api'

import BacktestsModal from './components/BacktestsModal'
import IndicatorsModal from './components/IndicatorsModal'
import DatabasePage from './pages/DatabasePage'
import TestPage from './pages/TestPage'
import MarchPage from './pages/MarchPage'
import HomePage from './pages/HomePage'
import EnvironmentPage from './pages/EnvironmentPage'
import CodePage from './pages/CodePage'
import AccountModal from './components/AccountModal'
import StrategyModal from './components/StrategyModal'
import Sidebar from './components/Sidebar'
import { AppContext, useApp } from './context/AppContext'
import {
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
  Outlet,
  useRouterState,
} from '@tanstack/react-router'

// Pre-load default; the persisted default_timeframe from app.db overrides this
// once settings load (see the settings effect below).
const DEFAULT_TF = TIMEFRAMES.find(t => t.table === '5m') ?? TIMEFRAMES[0]

// --- Route Components ---

function RootRouteComponent() {
  const pathname = useRouterState({ select: state => state.location.pathname })
  const isMarchRoute = pathname === '/march'
  const [hasVisitedMarch, setHasVisitedMarch] = useState(isMarchRoute)

  const {
    modalOpen, setModalOpen,
    setIndicatorsOpen,
    indicatorsOpen,
    visibleIds, loadingIds, toggleId,
    marchAccountModalOpen, setMarchAccountModalOpen, setSelectedAccountId,
    selectedAccountId, marchStrategyModalOpen, setMarchStrategyModalOpen,
    marchLayouts, activeMarchPanel, updateMarchPanel, selectedEnvironmentId,
  } = useApp()

  // March owns a stateful chart and live WebSocket connection. Once visited,
  // keep it mounted while other routes are open so returning to the page does
  // not recreate the chart or download its candle history again.
  useEffect(() => {
    if (isMarchRoute) setHasVisitedMarch(true)
  }, [isMarchRoute])

  // The Indicators modal acts on whichever chart panel opened it.
  const activeCfg: LayoutPanelConfig =
    (activeMarchPanel && marchLayouts[activeMarchPanel.layout]?.[activeMarchPanel.index]) ||
    makeDefaultPanelConfig()

  return (
    <div className="h-screen bg-gray-950 text-white flex flex-row overflow-hidden">
      <Sidebar />

      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        {(hasVisitedMarch || isMarchRoute) && (
          <div
            className={isMarchRoute ? 'flex flex-1 min-h-0 overflow-hidden' : 'hidden'}
            aria-hidden={!isMarchRoute}
          >
            <MarchPage />
          </div>
        )}
        <Outlet />
      </div>

      <IndicatorsModal
        open={indicatorsOpen}
        onClose={() => setIndicatorsOpen(false)}
        indicators={activeCfg.indicators}
        onToggle={(key) => {
          if (!activeMarchPanel) return
          updateMarchPanel(activeMarchPanel.layout, activeMarchPanel.index, {
            indicators: { ...activeCfg.indicators, [key]: !activeCfg.indicators[key] },
          })
        }}
      />
      <AccountModal
        open={marchAccountModalOpen}
        onClose={() => setMarchAccountModalOpen(false)}
        onAdded={(id) => setSelectedAccountId(id)}
      />
      <StrategyModal
        open={marchStrategyModalOpen}
        onClose={() => setMarchStrategyModalOpen(false)}
        accountId={selectedAccountId}
      />
      <BacktestsModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        visibleIds={visibleIds}
        loadingIds={loadingIds}
        onToggle={toggleId}
        activeSymbol={activeCfg.symbol}
        environmentId={selectedEnvironmentId}
      />
    </div>
  )
}

function HomeRouteComponent() {
  return (
    <div className="flex flex-1 overflow-hidden">
      <HomePage />
    </div>
  )
}

function DatabaseRouteComponent() {
  return (
    <div className="flex flex-1 overflow-hidden">
      <DatabasePage />
    </div>
  )
}

function MarchRouteComponent() {
  // MarchPage is kept alive by RootRouteComponent so its chart survives route
  // navigation. This route only controls whether that persistent page is shown.
  return null
}

function TestRouteComponent() {
  return (
    <div className="flex flex-1 overflow-hidden min-h-0">
      <TestPage />
    </div>
  )
}

function EnvironmentRouteComponent() {
  return (
    <div className="flex flex-1 overflow-hidden">
      <EnvironmentPage />
    </div>
  )
}

function CodeRouteComponent() {
  return (
    <div className="flex flex-1 overflow-hidden">
      <CodePage />
    </div>
  )
}

// --- Router Definition ---

const rootRoute = createRootRoute({
  component: RootRouteComponent,
})

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: HomeRouteComponent,
})

const marchRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/march',
  component: MarchRouteComponent,
})

const databaseRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/database',
  component: DatabaseRouteComponent,
})

const testRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/test',
  component: TestRouteComponent,
})

const environmentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/environment',
  component: EnvironmentRouteComponent,
})

const codeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/code',
  component: CodeRouteComponent,
})

const routeTree = rootRoute.addChildren([indexRoute, marchRoute, databaseRoute, testRoute, environmentRoute, codeRoute])

const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

// --- Main App Component ---

export default function App() {
  const [modalOpen, setModalOpen] = useState(false)
  const [indicatorsOpen, setIndicatorsOpen] = useState(false)
  const [visibleIds, setVisibleIds] = useState<Set<number>>(new Set())

  function toggleId(id: number) {
    setVisibleIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const { data: backtests } = useQuery({
    queryKey: ['backtests'],
    queryFn: fetchBacktests,
  })

  const visibleIdsArray = [...visibleIds]
  const tradeQueries = useQueries({
    queries: visibleIdsArray.map(id => ({
      queryKey: ['trades', id] as const,
      queryFn: () => fetchTrades(id),
      staleTime: Infinity,
    }))
  })
  const loadingIds = new Set(visibleIdsArray.filter((_, i) => tradeQueries[i]?.isLoading))
  const allTrades = useMemo(
    () => tradeQueries.flatMap((q, idx) => {
      const id = visibleIdsArray[idx];
      const bt = backtests?.find(b => b.id === id);
      const symbol = bt?.symbol ?? 'nq';
      return (q.data ?? []).map(t => ({ ...t, symbol }));
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tradeQueries.map(q => q.dataUpdatedAt).join(','), backtests, visibleIdsArray]
  )
  // FX-execution trades for the same toggled-on backtests, re-priced from
  // fx_nq_ticks — overlaid on the fx_nq candle pane when that overlay is shown.
  const fxTradeQueries = useQueries({
    queries: visibleIdsArray.map(id => ({
      queryKey: ['tradesFx', id] as const,
      queryFn: () => fetchTradesFx(id),
      staleTime: Infinity,
    }))
  })
  const allFxTrades = useMemo(
    () => fxTradeQueries.flatMap((q, idx) => {
      const id = visibleIdsArray[idx];
      const bt = backtests?.find(b => b.id === id);
      const symbol = bt?.symbol ?? 'nq';
      return (q.data ?? []).map(t => ({ ...t, symbol }));
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [fxTradeQueries.map(q => q.dataUpdatedAt).join(','), backtests, visibleIdsArray]
  )
  const [selectedBacktestId, setSelectedBacktestId] = useState<number | null>(null)
  const [activeTab, setActiveTab] = useState<'analysis' | 'equity' | 'splicing' | 'monte-carlo'>('analysis')
  const [marchSymbol, setMarchSymbol] = useState<'nq' | 'es'>('nq')
  const [marchTf, setMarchTf] = useState<TF>(DEFAULT_TF)
  const [marchStreamStatus, setMarchStreamStatus] = useState<'loading' | 'live' | 'idle' | 'error'>('idle')
  const [isBottomOpen, setIsBottomOpen] = useState(true)
  const [marchLayout, setMarchLayout] = useState('single')
  const [marchBottomHeight, setMarchBottomHeight] = useState(400)
  const [marchLayouts, setMarchLayouts] = useState<MarchLayouts>({})
  const [activeMarchPanel, setActiveMarchPanel] = useState<{ layout: string; index: number } | null>(null)

  function updateMarchPanel(layout: string, index: number, patch: Partial<LayoutPanelConfig>) {
    setMarchLayouts(prev => {
      const next = { ...prev }
      const arr = next[layout] ? [...next[layout]] : []
      while (arr.length <= index) arr.push(makeDefaultPanelConfig())
      arr[index] = { ...arr[index], ...patch }
      next[layout] = arr
      return next
    })
  }

  // March chart date selection. Default to live "Latest" mode with a recent
  // (7-day) lower bound so the chart opens on current price and streams. `to`
  // defaults to today so the range-mode Apply button is usable immediately.
  const today = new Date().toISOString().slice(0, 10)
  const recentFrom = (() => {
    const d = new Date()
    d.setDate(d.getDate() - 7)
    return d.toISOString().slice(0, 10)
  })()
  const [marchMode, setMarchMode] = useState<'latest' | 'range'>('latest')
  const [marchFromDate, setMarchFromDate] = useState(recentFrom)
  const [marchToDate, setMarchToDate] = useState(today)
  const [selectedEnvironmentId, setSelectedEnvironmentId] = useState<number | null>(() => {
    const saved = sessionStorage.getItem('selected_environment_id')
    return saved ? Number(saved) : null
  })

  function handleMarchApplyRange(from: string, to: string) {
    setMarchFromDate(from)
    setMarchToDate(to)
    setMarchMode('range')
  }

  function handleMarchLatest(from: string) {
    setMarchFromDate(from)
    setMarchMode('latest')
  }

  const [testResults, setTestResults] = useState<Record<string, RunResult>>({})
  const [testErrors, setTestErrors] = useState<Record<string, string>>({})
  const [tuneResults, setTuneResults] = useState<Record<string, TuneResult>>({})
  // Test runs continue while the user switches test tabs or navigates elsewhere.
  // Keep their UI state above the route so it is not discarded on unmount.
  const [testLoading, setTestLoading] = useState<Record<string, boolean>>({})
  const [testTuneProgress, setTestTuneProgress] = useState<Record<string, { progress: number; total: number }>>({})

  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null)
  const [marchAccountModalOpen, setMarchAccountModalOpen] = useState(false)
  const [marchStrategyModalOpen, setMarchStrategyModalOpen] = useState(false)
  const [visibleTradeStrategies, setVisibleTradeStrategies] = useState<Set<string>>(new Set())

  function toggleTradeStrategy(strategy: string) {
    setVisibleTradeStrategies(prev => {
      const next = new Set(prev)
      if (next.has(strategy)) {
        next.delete(strategy)
      } else {
        next.add(strategy)
      }
      return next
    })
  }

  return (
    <AppContext.Provider value={{
      modalOpen, setModalOpen,
      indicatorsOpen, setIndicatorsOpen,
      visibleIds, loadingIds, allTrades, allFxTrades, toggleId, setVisibleIds,
      marchSymbol, setMarchSymbol,
      marchTf, setMarchTf,
      marchStreamStatus, setMarchStreamStatus,
      marchMode, marchFromDate, marchToDate,
      handleMarchApplyRange, handleMarchLatest,
      selectedEnvironmentId, setSelectedEnvironmentId,
      selectedAccountId, setSelectedAccountId,
      marchAccountModalOpen, setMarchAccountModalOpen,
      marchStrategyModalOpen, setMarchStrategyModalOpen,
      visibleTradeStrategies, toggleTradeStrategy,
      isBottomOpen, setIsBottomOpen,
      marchLayout, setMarchLayout,
      marchBottomHeight, setMarchBottomHeight,
      marchLayouts, setMarchLayouts, updateMarchPanel,
      activeMarchPanel, setActiveMarchPanel,
      selectedBacktestId, setSelectedBacktestId,
      activeTab, setActiveTab,
      testResults, setTestResults,
      testErrors, setTestErrors,
      tuneResults, setTuneResults,
      testLoading, setTestLoading,
      testTuneProgress, setTestTuneProgress,
    }}>
      <RouterProvider router={router} />
    </AppContext.Provider>
  )
}
