import { useState, useEffect } from 'react'
import { Link, useLocation, useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { TIMEFRAMES, SYMBOLS, type TF, type SymbolId } from '../types'
import { fetchBacktests } from '../api'
import { useApp } from '../context/AppContext'

interface Props {
  activeTf: TF
  onTfChange: (tf: TF) => void
  activeSymbol: SymbolId
  onSymbolChange: (sym: SymbolId) => void
  onIndicatorsOpen: () => void
  onResearchOpen: () => void
  fromDate: string
  toDate: string
  onApplyRange: (from: string, to: string) => void
}

function dateToDisplay(iso: string): string {
  if (!iso) return ''
  const [year, month, day] = iso.split('-')
  return `${month}/${day}/${year.slice(2)}`
}

function displayToIso(display: string): string {
  if (!display) return ''
  const parts = display.split('/')
  if (parts.length !== 3) return ''
  const [mm, dd, yy] = parts
  if (!mm || !dd || !yy) return ''
  const year = yy.length === 4 ? yy : `20${yy}`
  return `${year}-${mm.padStart(2, '0')}-${dd.padStart(2, '0')}`
}

export default function Header({
  activeTf, onTfChange, activeSymbol, onSymbolChange,
  onIndicatorsOpen, onResearchOpen,
  fromDate, toDate, onApplyRange,
}: Props) {
  const [draftFrom, setDraftFrom] = useState(dateToDisplay(fromDate))
  const [draftTo, setDraftTo] = useState(dateToDisplay(toDate))
  const location = useLocation()
  const navigate = useNavigate()
  
  const { selectedBacktestId, setSelectedBacktestId, activeTab, setActiveTab } = useApp()
  const { data: backtests } = useQuery({
    queryKey: ['backtests'],
    queryFn: fetchBacktests,
    staleTime: Infinity,
  })

  function handleTabClick(tabId: 'analysis' | 'equity' | 'monte-carlo') {
    setActiveTab(tabId)
    if (location.pathname !== '/stats') {
      navigate({ to: '/stats' })
    }
  }

  useEffect(() => { setDraftFrom(dateToDisplay(fromDate)) }, [fromDate])
  useEffect(() => { setDraftTo(dateToDisplay(toDate)) }, [toDate])

  const dirty = draftFrom !== dateToDisplay(fromDate) || draftTo !== dateToDisplay(toDate)
  const isChartPage = location.pathname === '/'

  function handleApply() {
    onApplyRange(displayToIso(draftFrom), displayToIso(draftTo))
  }

  return (
    <div className="h-[52px] px-5 border-b border-gray-800/60 flex items-center gap-3 bg-gray-950/95 backdrop-blur-sm">

      {/* Page selection */}
      <div className="h-full flex items-stretch gap-2.5 shrink-0">
        <Link
          to="/"
          title="Chart"
          activeProps={{ className: 'text-white' }}
          inactiveProps={{ className: 'text-gray-500 hover:text-gray-200' }}
          className="relative px-1 h-full flex items-center justify-center gap-1.5 transition-all duration-150 text-xs font-medium select-none"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 3v18h18" />
            <path d="m19 9-5 5-4-4-3 3" />
          </svg>
          Chart
          {isChartPage && (
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className="absolute top-0.5 left-1/2 -translate-x-1/2 text-white">
              <line x1="12" y1="2" x2="12" y2="22" />
              <polyline points="19 15 12 22 5 15" />
            </svg>
          )}
        </Link>
        <Link
          to="/stats"
          title="Stats"
          activeProps={{ className: 'text-white' }}
          inactiveProps={{ className: 'text-gray-500 hover:text-gray-200' }}
          className="relative px-1 h-full flex items-center justify-center gap-1.5 transition-all duration-150 text-xs font-medium select-none"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="20" x2="18" y2="10" />
            <line x1="12" y1="20" x2="12" y2="4" />
            <line x1="6" y1="20" x2="6" y2="14" />
          </svg>
          Stats
          {!isChartPage && (
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className="absolute top-0.5 left-1/2 -translate-x-1/2 text-white">
              <line x1="12" y1="2" x2="12" y2="22" />
              <polyline points="19 15 12 22 5 15" />
            </svg>
          )}
        </Link>
      </div>

      {!isChartPage && (
        <>
          {/* Vertical divider */}
          <div className="h-5 w-[1px] bg-gray-800/80 self-center mx-1 shrink-0" />

          {/* Tab selection */}
          <div className="flex items-center gap-0.5 bg-gray-900 rounded-lg p-0.5 border border-gray-800/80 shrink-0">
            <button
              onClick={() => handleTabClick('analysis')}
              className={`px-2.5 py-1 transition-all duration-150 text-xs font-medium rounded-md select-none ${
                activeTab === 'analysis'
                  ? 'bg-gray-700 text-white shadow-sm'
                  : 'text-gray-500 hover:text-gray-200 hover:bg-gray-800/70'
              }`}
            >
              Analysis
            </button>
            <button
              onClick={() => handleTabClick('equity')}
              className={`px-2.5 py-1 transition-all duration-150 text-xs font-medium rounded-md select-none ${
                activeTab === 'equity'
                  ? 'bg-gray-700 text-white shadow-sm'
                  : 'text-gray-500 hover:text-gray-200 hover:bg-gray-800/70'
              }`}
            >
              Equity
            </button>
            <button
              onClick={() => handleTabClick('monte-carlo')}
              className={`px-2.5 py-1 transition-all duration-150 text-xs font-medium rounded-md select-none ${
                activeTab === 'monte-carlo'
                  ? 'bg-gray-700 text-white shadow-sm'
                  : 'text-gray-500 hover:text-gray-200 hover:bg-gray-800/70'
              }`}
            >
              Monte Carlo
            </button>
          </div>
        </>
      )}

      {!isChartPage && (
        <>
          {/* Vertical divider */}
          <div className="h-5 w-[1px] bg-gray-800/80 self-center mx-1 shrink-0" />

          {/* Horizontal scrollable backtests list */}
          <div className="flex-1 h-full flex items-stretch overflow-x-auto no-scrollbar select-none">
            {backtests?.map(bt => (
              <button
                key={bt.id}
                onClick={() => setSelectedBacktestId(bt.id)}
                className={`text-left px-8 h-full flex flex-col justify-center transition-all duration-100 shrink-0 select-none border-b-2 ${
                  selectedBacktestId === bt.id
                    ? 'bg-gray-900/80 text-white border-gray-500'
                    : 'text-gray-400 hover:text-gray-200 hover:bg-gray-900/30 border-transparent'
                }`}
              >
                <div className="text-[11px] font-semibold truncate max-w-[280px] leading-tight">{bt.strategy}</div>
                <div className="text-[9px] text-gray-500 leading-tight mt-0.5">#{bt.id} · {bt.symbol.toUpperCase()}</div>
              </button>
            ))}
          </div>
        </>
      )}

      {isChartPage && (
        <>
          {/* Vertical divider */}
          <div className="h-5 w-[1px] bg-gray-800/80 self-center mx-1 shrink-0" />

          {/* Symbol selector */}
          <select
            value={activeSymbol}
            onChange={e => onSymbolChange(e.target.value as SymbolId)}
            className="bg-gray-900 border border-gray-800/80 text-xs font-medium text-gray-200 rounded-lg px-2.5 py-1.5 outline-none cursor-pointer hover:border-gray-700 transition-colors shrink-0"
          >
            {SYMBOLS.map(sym => (
              <option key={sym.id} value={sym.id}>{sym.label}</option>
            ))}
          </select>

          {/* Date range — between title and TF selector */}
          <div className="flex items-center gap-1 bg-gray-900 rounded-lg p-0.5 px-2 border border-gray-800/80 shrink-0">
            <input
              type="text"
              value={draftFrom}
              onChange={e => setDraftFrom(e.target.value)}
              placeholder="MM/DD/YY"
              className={`bg-transparent text-xs font-mono outline-none w-[58px] py-1 transition-colors duration-200 ${draftFrom ? 'text-gray-200' : 'text-gray-500'
                }`}
            />
            <span className="text-[10px] font-light select-none text-gray-600">—</span>
            <input
              type="text"
              value={draftTo}
              onChange={e => setDraftTo(e.target.value)}
              placeholder="MM/DD/YY"
              className={`bg-transparent text-xs font-mono outline-none w-[58px] py-1 transition-colors duration-200 ${draftTo ? 'text-gray-200' : 'text-gray-500'
                }`}
            />
            <button
              onClick={handleApply}
              disabled={!dirty}
              title="Apply date range"
              className={`ml-1 px-2 py-1 text-[11px] font-medium rounded-md transition-all duration-150 shrink-0 ${dirty
                ? 'bg-blue-600 text-white hover:bg-blue-500'
                : 'bg-gray-800/50 text-gray-600 cursor-default'
                }`}
            >
              Apply
            </button>
          </div>

          {/* Timeframe selector */}
          <div className="flex items-center gap-0.5 bg-gray-900 rounded-lg p-0.5 border border-gray-800/80 shrink-0">
            {TIMEFRAMES.map(tf => (
              <button
                key={tf.label}
                onClick={() => onTfChange(tf)}
                className={`px-2.5 py-1 text-xs font-medium rounded-md transition-all duration-150 ${tf.label === activeTf.label
                  ? 'bg-gray-700 text-white shadow-sm'
                  : 'text-gray-500 hover:text-gray-200 hover:bg-gray-800/70'
                  }`}
              >
                {tf.label}
              </button>
            ))}
          </div>

          {/* Indicators */}
          <button
            onClick={onIndicatorsOpen}
            title="Indicators"
            className="px-2 py-1.5 rounded-md text-gray-500 hover:text-gray-200 hover:bg-gray-800/70 transition-all duration-150 shrink-0 flex items-center gap-1.5"
          >
            <svg width="20" height="18" viewBox="0 0 16 16" fill="none">
              <polyline points="1,7.5 5,3 9,5 14.5,0.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
              <rect x="0.8" y="12" width="3" height="3.5" rx="0.4" stroke="currentColor" strokeWidth="1.1" />
              <rect x="5.5" y="10" width="3" height="5.5" rx="0.4" stroke="currentColor" strokeWidth="1.1" />
              <rect x="10.5" y="7.5" width="3" height="8" rx="0.4" stroke="currentColor" strokeWidth="1.1" />
            </svg>
            <span className="text-xs font-medium">Indicators</span>
          </button>

          {/* Backtests */}
          <button
            onClick={onResearchOpen}
            title="Backtests"
            className="px-2 py-1.5 rounded-md -ml-2 text-gray-500 hover:text-gray-200 hover:bg-gray-800/70 transition-all duration-150 shrink-0 flex items-center gap-1.5"
          >
            <svg width="20" height="18" viewBox="0 0 14 16" fill="none">
              <path d="M1 1h8l4 4v10H1V1z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
              <path d="M9 1v4h4" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
              <line x1="3" y1="7" x2="7" y2="7" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
              <line x1="3" y1="9.5" x2="11" y2="9.5" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
              <line x1="3" y1="12" x2="11" y2="12" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
            </svg>
            <span className="text-xs font-medium">Backtests</span>
          </button>
        </>
      )}

      {/* Spacer */}
      {isChartPage && <div className="flex-1" />}

    </div>
  )
}

