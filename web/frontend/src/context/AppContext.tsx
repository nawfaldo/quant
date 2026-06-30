import React, { createContext, useContext } from 'react'
import type { TF, Trade, MarchLayouts, LayoutPanelConfig } from '../types'
import type { RunResult, TuneResult } from '../api'

export interface AppContextType {
  modalOpen: boolean
  setModalOpen: (open: boolean) => void
  indicatorsOpen: boolean
  setIndicatorsOpen: (open: boolean) => void
  visibleIds: Set<number>
  loadingIds: Set<number>
  allTrades: Trade[]
  toggleId: (id: number) => void
  marchSymbol: 'nq' | 'es'
  setMarchSymbol: (sym: 'nq' | 'es') => void
  marchTf: TF
  setMarchTf: (tf: TF) => void
  marchStreamStatus: 'loading' | 'live' | 'idle' | 'error'
  setMarchStreamStatus: (s: 'loading' | 'live' | 'idle' | 'error') => void
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
  marchLayouts: MarchLayouts
  updateMarchPanel: (layout: string, index: number, patch: Partial<LayoutPanelConfig>) => void
  activeMarchPanel: { layout: string; index: number } | null
  setActiveMarchPanel: (p: { layout: string; index: number } | null) => void
  selectedBacktestId: number | null
  setSelectedBacktestId: (id: number | null) => void
  activeTab: 'analysis' | 'equity' | 'splicing' | 'monte-carlo'
  setActiveTab: (tab: 'analysis' | 'equity' | 'splicing' | 'monte-carlo') => void
  testResults: Record<string, RunResult>
  setTestResults: React.Dispatch<React.SetStateAction<Record<string, RunResult>>>
  testErrors: Record<string, string>
  setTestErrors: React.Dispatch<React.SetStateAction<Record<string, string>>>
  tuneResults: Record<string, TuneResult>
  setTuneResults: React.Dispatch<React.SetStateAction<Record<string, TuneResult>>>
}

export const AppContext = createContext<AppContextType | null>(null)

export function useApp() {
  const context = useContext(AppContext)
  if (!context) {
    throw new Error('useApp must be used within an AppProvider')
  }
  return context
}
