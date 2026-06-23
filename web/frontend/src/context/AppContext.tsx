import { createContext, useContext } from 'react'
import type { TF, SymbolId, Indicators, Bar, VwapPoint, Trade } from '../types'

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
}

export const AppContext = createContext<AppContextType | null>(null)

export function useApp() {
  const context = useContext(AppContext)
  if (!context) {
    throw new Error('useApp must be used within an AppProvider')
  }
  return context
}
