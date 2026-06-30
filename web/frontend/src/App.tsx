import { useState, useEffect, useRef, useMemo } from 'react'
import { useQuery, useQueries } from '@tanstack/react-query'
import { TIMEFRAMES, makeDefaultPanelConfig, type TF, type MarchLayouts, type LayoutPanelConfig } from './types'
import { fetchTrades, fetchMarchSettings, saveMarchSettings, fetchMarchLayouts, saveMarchLayouts, type RunResult, type TuneResult } from './api'

import BacktestsModal from './components/BacktestsModal'
import IndicatorsModal from './components/IndicatorsModal'
import StatsPage from './components/StatsPage'
import TestPage from './components/TestPage'
import MarchPage from './components/MarchPage'
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
} from '@tanstack/react-router'

// Pre-load default; the persisted default_timeframe from app.db overrides this
// once settings load (see the settings effect below).
const DEFAULT_TF = TIMEFRAMES.find(t => t.table === '5m') ?? TIMEFRAMES[0]

// --- Route Components ---

function RootRouteComponent() {
  const {
    modalOpen, setModalOpen,
    setIndicatorsOpen,
    indicatorsOpen,
    visibleIds, loadingIds, toggleId,
    marchSymbol,
    marchAccountModalOpen, setMarchAccountModalOpen, setSelectedAccountId,
    selectedAccountId, marchStrategyModalOpen, setMarchStrategyModalOpen,
    marchLayouts, activeMarchPanel, updateMarchPanel,
  } = useApp()

  // The Indicators modal acts on whichever chart panel opened it.
  const activeCfg: LayoutPanelConfig =
    (activeMarchPanel && marchLayouts[activeMarchPanel.layout]?.[activeMarchPanel.index]) ||
    makeDefaultPanelConfig()

  return (
    <div className="h-screen bg-gray-950 text-white flex flex-row overflow-hidden">
      <Sidebar />

      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
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
        isNq={activeCfg.symbol === 'nq'}
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
        activeSymbol={marchSymbol}
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

function TestRouteComponent() {
  return (
    <div className="flex flex-1 overflow-hidden min-h-0">
      <TestPage />
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
  component: MarchRouteComponent,
})

const statsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/stats',
  component: StatsRouteComponent,
})

const testRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/test',
  component: TestRouteComponent,
})

const routeTree = rootRoute.addChildren([indexRoute, statsRoute, testRoute])

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
    () => tradeQueries.flatMap(q => q.data ?? []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tradeQueries.map(q => q.dataUpdatedAt).join(',')]
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

  function handleMarchApplyRange(from: string, to: string) {
    setMarchFromDate(from)
    setMarchToDate(to)
    setMarchMode('range')
  }

  function handleMarchLatest(from: string) {
    setMarchFromDate(from)
    setMarchMode('latest')
  }

  // Load persisted march settings from app.db once on mount, then persist any
  // change back. `marchLoaded` gates the save effect so we don't immediately
  // overwrite the stored values with the component defaults on first render.
  const marchLoaded = useRef(false)
  const { data: savedMarch } = useQuery({
    queryKey: ['marchSettings'],
    queryFn: fetchMarchSettings,
    staleTime: Infinity,
  })

  useEffect(() => {
    if (!savedMarch) return
    setMarchSymbol(savedMarch.symbol)
    const tf = TIMEFRAMES.find(t => t.table === savedMarch.tf)
    if (tf) setMarchTf(tf)
    setMarchFromDate(savedMarch.from)
    setMarchToDate(savedMarch.to)
    setMarchMode(savedMarch.mode)
    if (savedMarch.bottomOpen !== undefined) {
      const isOpen = typeof savedMarch.bottomOpen === 'boolean'
        ? savedMarch.bottomOpen
        : savedMarch.bottomOpen === 'true';
      setIsBottomOpen(isOpen);
    }
    if (savedMarch.layout) {
      setMarchLayout(savedMarch.layout)
    }
    if (savedMarch.bottomHeight !== undefined) {
      const h = parseInt(String(savedMarch.bottomHeight), 10)
      if (!isNaN(h) && h > 0) {
        setMarchBottomHeight(h)
      }
    }
    marchLoaded.current = true
  }, [savedMarch])

  useEffect(() => {
    if (!marchLoaded.current) return
    saveMarchSettings({
      symbol: marchSymbol,
      tf: marchTf.table,
      from: marchFromDate,
      to: marchToDate,
      mode: marchMode,
      bottomOpen: String(isBottomOpen),
      layout: marchLayout,
      bottomHeight: String(marchBottomHeight),
    })
  }, [marchSymbol, marchTf, marchFromDate, marchToDate, marchMode, isBottomOpen, marchLayout, marchBottomHeight])

  // Load persisted per-layout panel configs once; persist any change back.
  const marchLayoutsLoaded = useRef(false)
  const { data: savedMarchLayouts } = useQuery({
    queryKey: ['marchLayouts'],
    queryFn: fetchMarchLayouts,
    staleTime: Infinity,
  })

  useEffect(() => {
    if (!savedMarchLayouts) return
    setMarchLayouts(savedMarchLayouts)
    marchLayoutsLoaded.current = true
  }, [savedMarchLayouts])

  useEffect(() => {
    if (!marchLayoutsLoaded.current) return
    saveMarchLayouts(marchLayouts)
  }, [marchLayouts])

  const [testResults, setTestResults] = useState<Record<string, RunResult>>({})
  const [testErrors, setTestErrors] = useState<Record<string, string>>({})
  const [tuneResults, setTuneResults] = useState<Record<string, TuneResult>>({})

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
      visibleIds, loadingIds, allTrades, toggleId,
      marchSymbol, setMarchSymbol,
      marchTf, setMarchTf,
      marchStreamStatus, setMarchStreamStatus,
      marchMode, marchFromDate, marchToDate,
      handleMarchApplyRange, handleMarchLatest,
      selectedAccountId, setSelectedAccountId,
      marchAccountModalOpen, setMarchAccountModalOpen,
      marchStrategyModalOpen, setMarchStrategyModalOpen,
      visibleTradeStrategies, toggleTradeStrategy,
      isBottomOpen, setIsBottomOpen,
      marchLayout, setMarchLayout,
      marchBottomHeight, setMarchBottomHeight,
      marchLayouts, updateMarchPanel,
      activeMarchPanel, setActiveMarchPanel,
      selectedBacktestId, setSelectedBacktestId,
      activeTab, setActiveTab,
      testResults, setTestResults,
      testErrors, setTestErrors,
      tuneResults, setTuneResults,
    }}>
      <RouterProvider router={router} />
    </AppContext.Provider>
  )
}
