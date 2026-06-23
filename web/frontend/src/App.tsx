import { useState, useMemo, useEffect } from 'react'
import { useQuery, useQueries } from '@tanstack/react-query'
import { TIMEFRAMES, type TF, type Indicators, type SymbolId } from './types'
import { fetchCandles, fetchTrades, fetchVwap, fetchSettings, saveSettings } from './api'
import Chart from './components/Chart'
import Header from './components/Header'
import BacktestsModal from './components/BacktestsModal'
import IndicatorsModal from './components/IndicatorsModal'
import StatsPage from './components/StatsPage'
import MarchPage from './components/MarchPage'
import { AppContext, useApp } from './context/AppContext'
import {
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
  Outlet,
} from '@tanstack/react-router'

function dateToTs(dateStr: string): number {
  return Math.floor(new Date(dateStr).getTime() / 1000)
}

// Pre-load default; the persisted default_timeframe from app.db overrides this
// once settings load (see the settings effect below).
const DEFAULT_TF = TIMEFRAMES.find(t => t.table === '5m') ?? TIMEFRAMES[0]

// --- Route Components ---

function RootRouteComponent() {
  const {
    activeTf, setActiveTf,
    activeSymbol, handleSymbolChange,
    setIndicatorsOpen, setModalOpen,
    fromDate, toDate, handleApplyRange,
    modalOpen, visibleIds, loadingIds, toggleId,
    indicatorsOpen, indicators, toggleIndicator, isNq,
    candleError
  } = useApp()

  return (
    <div className="min-h-screen bg-gray-950 text-white flex flex-col">
      <Header
        activeTf={activeTf}
        onTfChange={setActiveTf}
        activeSymbol={activeSymbol}
        onSymbolChange={handleSymbolChange}
        onIndicatorsOpen={() => setIndicatorsOpen(true)}
        onResearchOpen={() => setModalOpen(true)}
        fromDate={fromDate}
        toDate={toDate}
        onApplyRange={handleApplyRange}
      />

      <Outlet />

      {candleError && (
        <div className="text-sm text-red-400 text-center py-4">
          Failed to load data: {(candleError as Error).message}
        </div>
      )}

      <BacktestsModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        visibleIds={visibleIds}
        loadingIds={loadingIds}
        onToggle={toggleId}
        activeSymbol={activeSymbol}
      />
      <IndicatorsModal
        open={indicatorsOpen}
        onClose={() => setIndicatorsOpen(false)}
        indicators={indicators}
        onToggle={toggleIndicator}
        isNq={isNq}
      />
    </div>
  )
}

function ChartRouteComponent() {
  const {
    bars, activeTf, allTrades, vwapData, indicators, isNq, fromTs, toTs
  } = useApp()

  return (
    <div className="flex flex-1 overflow-hidden">
      <Chart
        bars={bars}
        activeTf={activeTf}
        allTrades={allTrades}
        vwapData={vwapData}
        indicators={isNq ? indicators : { vwap: false, openingRange: false }}
        fromTs={fromTs}
        toTs={toTs}
      />
    </div>
  )
}

function StatsRouteComponent() {
  return (
    <div className="flex flex-1 overflow-hidden">
      <StatsPage />
    </div>
  )
}

function MarchRouteComponent() {
  return (
    <div className="flex flex-1 overflow-hidden">
      <MarchPage />
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
  component: ChartRouteComponent,
})

const statsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/stats',
  component: StatsRouteComponent,
})

const marchRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/march',
  component: MarchRouteComponent,
})

const routeTree = rootRoute.addChildren([indexRoute, statsRoute, marchRoute])

const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

// --- Main App Component ---

export default function App() {
  const [activeTf, setActiveTf] = useState<TF>(DEFAULT_TF)
  const [activeSymbol, setActiveSymbol] = useState<SymbolId>('nq')
  const [modalOpen, setModalOpen] = useState(false)
  const [indicatorsOpen, setIndicatorsOpen] = useState(false)
  const [visibleIds, setVisibleIds] = useState<Set<number>>(new Set())
  const [indicators, setIndicators] = useState<Indicators>({ vwap: false, openingRange: false })
  const [selectedBacktestId, setSelectedBacktestId] = useState<number | null>(null)
  const [activeTab, setActiveTab] = useState<'analysis' | 'equity' | 'monte-carlo'>('analysis')
  const [marchSymbol, setMarchSymbol] = useState<'nq' | 'es'>('nq')
  const [marchTf, setMarchTf] = useState<TF>(DEFAULT_TF)
  const [marchStreamStatus, setMarchStreamStatus] = useState<'loading' | 'live' | 'idle' | 'error'>('idle')

  // Default until the DB responds; overwritten once by the settings query effect below.
  const [fromDate, setFromDate] = useState('2026-01-01')
  const [toDate, setToDate] = useState('2026-04-30')

  const fromTs = fromDate ? dateToTs(fromDate) : null
  // +86399 so the "to" date is inclusive through end of day
  const toTs = toDate ? dateToTs(toDate) + 86399 : null

  // Load persisted date range from app.db once on mount.
  const { data: savedSettings } = useQuery({
    queryKey: ['settings'],
    queryFn: fetchSettings,
    staleTime: Infinity,
  })

  useEffect(() => {
    if (savedSettings) {
      setFromDate(savedSettings.from_date)
      setToDate(savedSettings.to_date)
      const tf = TIMEFRAMES.find(t => t.table === savedSettings.default_timeframe)
      if (tf) setActiveTf(tf)
    }
  }, [savedSettings])

  // Commit a date range only when the user clicks Apply (Header holds the draft).
  function handleApplyRange(from: string, to: string) {
    setFromDate(from)
    setToDate(to)
    saveSettings(from, to)
  }

  const isNq = activeSymbol === 'nq'

  function handleSymbolChange(sym: SymbolId) {
    setActiveSymbol(sym)
    setVisibleIds(new Set())
    if (sym !== 'nq') setIndicators({ vwap: false, openingRange: false })
  }

  const { data: bars = [], error: candleError } = useQuery({
    queryKey: ['candles', activeTf.label, activeSymbol, fromDate, toDate],
    queryFn: () => fetchCandles(activeTf, activeSymbol, fromDate, toDate),
    staleTime: Infinity,
  })

  const { data: vwapData = [] } = useQuery({
    queryKey: ['vwap'],
    queryFn: fetchVwap,
    staleTime: Infinity,
  })

  const visibleIdsArray = [...visibleIds]
  const tradeQueries = useQueries({
    queries: visibleIdsArray.map(id => ({
      queryKey: ['trades', id] as const,
      queryFn: () => fetchTrades(id),
      staleTime: Infinity,
    }))
  })

  const loadingIds = new Set(
    visibleIdsArray.filter((_, i) => tradeQueries[i]?.isLoading)
  )

  const allTrades = useMemo(
    () => tradeQueries.flatMap(q => q.data ?? []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tradeQueries.map(q => q.dataUpdatedAt).join(',')]
  )

  function toggleId(id: number) {
    setVisibleIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleIndicator(key: keyof Indicators) {
    setIndicators(prev => ({ ...prev, [key]: !prev[key] }))
  }

  return (
    <AppContext.Provider value={{
      activeTf, setActiveTf,
      activeSymbol, handleSymbolChange,
      modalOpen, setModalOpen,
      indicatorsOpen, setIndicatorsOpen,
      visibleIds, indicators,
      fromDate, toDate, handleApplyRange,
      bars, vwapData, allTrades, loadingIds,
      toggleId, toggleIndicator, isNq, fromTs, toTs, candleError,
      selectedBacktestId, setSelectedBacktestId,
      activeTab, setActiveTab,
      marchSymbol, setMarchSymbol,
      marchTf, setMarchTf,
      marchStreamStatus, setMarchStreamStatus,
    }}>
      <RouterProvider router={router} />
    </AppContext.Provider>
  )
}

