import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import ModalShell from '../ui/ModalShell'
import { fetchDatabaseSymbols, type DatabaseSummaryItem } from '../../api'

export interface SymbolOption {
  symbol: string
  name: string
  country: string
  type: string
  availableTimeframes?: string[]
}

function UnitedStatesFlag() {
  return (
    <svg width="24" height="24" viewBox="0 0 36 36" aria-hidden="true" className="shrink-0 rounded-full shadow-sm shadow-black/40">
      <defs>
        <clipPath id="us-flag-circle-sym"><circle cx="18" cy="18" r="18" /></clipPath>
      </defs>
      <g clipPath="url(#us-flag-circle-sym)">
        <rect width="36" height="36" fill="#fff" />
        {[0, 5.54, 11.08, 16.62, 22.16, 27.7, 33.24].map((y) => <rect key={y} y={y} width="36" height="2.77" fill="#b22234" />)}
        <rect width="16" height="15" fill="#3c3b6e" />
        {[3, 8, 13].flatMap((y) => [3.5, 7.5, 11.5, 14.5].map((x) => <circle key={`${x}-${y}`} cx={x} cy={y} r="0.7" fill="#fff" />))}
      </g>
    </svg>
  )
}

function IndonesiaFlag() {
  return (
    <svg width="24" height="24" viewBox="0 0 36 36" aria-hidden="true" className="shrink-0 rounded-full shadow-sm shadow-black/40">
      <defs>
        <clipPath id="id-flag-circle-sym"><circle cx="18" cy="18" r="18" /></clipPath>
      </defs>
      <g clipPath="url(#id-flag-circle-sym)">
        <rect width="36" height="18" fill="#e70011" />
        <rect y="18" width="36" height="18" fill="#ffffff" />
      </g>
    </svg>
  )
}

function CountryFlag({ country }: { country: string }) {
  if (country === 'Indonesia') return <IndonesiaFlag />
  return <UnitedStatesFlag />
}

function SearchIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-gray-400">
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  )
}

interface SymbolModalProps {
  open: boolean
  onClose: () => void
  selectedSymbol: string
  onSelectSymbol: (symbol: string, defaultTf?: string, availableTfs?: string[]) => void
}

export default function SymbolModal({
  open,
  onClose,
  selectedSymbol,
  onSelectSymbol,
}: SymbolModalProps) {
  const [searchQuery, setSearchQuery] = useState('')

  const { data: dbSummary = [], isLoading } = useQuery<DatabaseSummaryItem[]>({
    queryKey: ['database-symbols'],
    queryFn: fetchDatabaseSymbols,
    enabled: open,
    staleTime: 60000,
  })

  // List ONLY real database datasets
  const allSymbols = useMemo(() => {
    return dbSummary.map((item) => ({
      symbol: item.symbol,
      name: item.datasetName || item.symbol,
      country: item.country || 'United States',
      type: item.type || 'Futures',
      availableTimeframes: item.availableTimeframes,
    }))
  }, [dbSummary])

  // Filtered symbols matching database search algorithm
  const filteredSymbols = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return allSymbols

    return allSymbols.filter((item) => {
      const sym = item.symbol.toLowerCase()
      const name = item.name.toLowerCase()

      if (sym === q || sym.startsWith(q)) return true
      const words = `${sym} ${name}`.split(/[\s._\-()]+/)
      return words.some((w) => w.startsWith(q))
    })
  }, [allSymbols, searchQuery])


  const headerControls = (
    <div className="flex items-center gap-2 flex-1 min-w-0 ml-2">
      {/* Symbol Text Search Input */}
      <div className="liquid-glass-btn liquid-glass-btn-no-grow !rounded-xl relative inline-flex items-center gap-1.5 px-2.5 py-2 text-xs text-gray-200 !shadow-none w-48 shrink-0">
        <SearchIcon />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search..."
          className="bg-transparent text-xs font-medium text-gray-200 placeholder-gray-500 outline-none w-full"
        />
      </div>
    </div>
  )

  return (
    <ModalShell
      open={open}
      onClose={onClose}
      title="Select Symbol"
      closePosition="left"
      headerExtra={headerControls}
      className=""
    >
      <div className="flex-1 min-h-0 flex flex-col pt-2 px-0">
        {/* Symbol List */}
        <div className="overflow-y-auto flex-1 min-h-0">
          <div className="flex flex-col pb-12">
            {isLoading ? (
              <div className="py-8 text-center text-xs text-gray-500 font-mono px-4">
                Loading datasets from QuestDB…
              </div>
            ) : filteredSymbols.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-10 text-gray-500 text-xs px-4">
                <span>No datasets found matching search query.</span>
                <button
                  onClick={() => setSearchQuery('')}
                  className="mt-2 text-blue-400 hover:underline cursor-pointer"
                >
                  Clear search
                </button>
              </div>
            ) : (
              filteredSymbols.map((item) => {
                const isSelected =
                  item.symbol.toLowerCase() === selectedSymbol.toLowerCase()
                return (
                  <div
                    key={item.symbol}
                    onClick={() => {
                      const tfs = item.availableTimeframes
                      const defaultTf = tfs && tfs.length > 0 ? tfs[0] : undefined
                      onSelectSymbol(item.symbol.toLowerCase(), defaultTf, tfs)
                      onClose()
                    }}
                    className="group relative flex items-center justify-between py-3 px-4 hover:bg-[#212126]/50 transition-colors cursor-pointer"
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <CountryFlag country={item.country} />
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="font-bold text-gray-100 text-xs uppercase tracking-wide shrink-0 w-14">
                          {item.symbol}
                        </span>
                        <span className="truncate font-medium text-gray-400 text-xs" title={item.name}>
                          {item.name}
                        </span>
                      </div>
                    </div>

                    {!isSelected && (
                      <button
                        type="button"
                        className="liquid-glass-btn liquid-glass-btn-no-grow liquid-glass-btn-rounded-lg px-3 py-1 text-xs font-semibold text-gray-200 opacity-0 group-hover:opacity-100 transition-opacity shrink-0 ml-3"
                      >
                        Select
                      </button>
                    )}

                    <div className="absolute bottom-0 left-4 right-0 border-b border-white/15 pointer-events-none" />
                  </div>
                )
              })
            )}
          </div>
        </div>
      </div>
    </ModalShell>
  )
}
