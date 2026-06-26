import { createContext, useContext } from 'react'
import type { TF, SymbolId, Indicators, Bar, VwapPoint, Trade, MarchLayouts, LayoutPanelConfig } from '../types'

export interface AppContextType {
  activeTf: TF
  setActiveTf: (tf: TF) => void
  activeSymbol: SymbolId
  handleSymbolChange: (sym: SymbolId) => void
  modalOpen: boolean
  setModalOpen: (open: boolean) => void
  indicatorsOpen: boolean
  setIndicatorsOpen: (open: boolean) => void
  visibleIds: Set<number>
  indicators: Indicators
  fromDate: string
  toDate: string
  handleApplyRange: (from: string, to: string) => void
  bars: Bar[]
  vwapData: VwapPoint[]
  allTrades: Trade[]
  loadingIds: Set<number>
  toggleId: (id: number) => void
  toggleIndicator: (key: keyof Indicators) => void
  isNq: boolean
  fromTs: number | null
  toTs: number | null
  candleError: any
  selectedBacktestId: number | null
  setSelectedBacktestId: (id: number | null) => void
  activeTab: 'analysis' | 'equity' | 'monte-carlo'
  setActiveTab: (tab: 'analysis' | 'equity' | 'monte-carlo') => void
  marchSymbol: 'nq' | 'es'
  setMarchSymbol: (sym: 'nq' | 'es') => void
  marchTf: TF
  setMarchTf: (tf: TF) => void
  marchStreamStatus: 'loading' | 'live' | 'idle' | 'error'
  setMarchStreamStatus: (s: 'loading' | 'live' | 'idle' | 'error') => void
  // March chart date selection. 'latest' streams bm_nq_ticks live (open-ended
  // `to`, bounded below by marchFromDate); 'range' shows static nq_ history.
  marchMode: 'latest' | 'range'
  marchFromDate: string
  marchToDate: string
  handleMarchApplyRange: (from: string, to: string) => void
  handleMarchLatest: (from: string) => void
  selectedAccountId: number | null
  setSelectedAccountId: (id: number | null) => void
  marchAccountModalOpen: boolean
  setMarchAccountModalOpen: (open: boolean) => void
  marchStrategyModalOpen: boolean
  setMarchStrategyModalOpen: (open: boolean) => void
  visibleTradeStrategies: Set<string>
  toggleTradeStrategy: (strategy: string) => void
  isBottomOpen: boolean
  setIsBottomOpen: (open: boolean) => void
  marchLayout: string
  setMarchLayout: (layout: string) => void
  marchBottomHeight: number
  setMarchBottomHeight: (h: number) => void
  // Per-layout panel configs (symbol / timeframe / date / indicator), persisted
  // to the backend. Keyed by layout id; the array is that layout's panels.
  marchLayouts: MarchLayouts
  updateMarchPanel: (layout: string, index: number, patch: Partial<LayoutPanelConfig>) => void
  // The panel that last opened the Indicators / Backtests modal, so those
  // global modals act on the right panel.
  activeMarchPanel: { layout: string; index: number } | null
  setActiveMarchPanel: (p: { layout: string; index: number } | null) => void
}

export const AppContext = createContext<AppContextType | null>(null)

export function useApp() {
  const context = useContext(AppContext)
  if (!context) {
    throw new Error('useApp must be used within an AppProvider')
  }
  return context
}
