import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import { fetchDatabaseSummary } from '../api'
import PageShell from '../components/layout/PageShell'

export const Route = createFileRoute('/database')({
  component: DatabaseRouteComponent,
})

function DatabaseRouteComponent() {
  return (
    <PageShell>
      <DatabasePage />
    </PageShell>
  )
}

function UnitedStatesFlag() {
  return (
    <svg width="28" height="28" viewBox="0 0 36 36" aria-hidden="true" className="shrink-0 rounded-full shadow-sm shadow-black/40">
      <defs>
        <clipPath id="us-flag-circle"><circle cx="18" cy="18" r="18" /></clipPath>
      </defs>
      <g clipPath="url(#us-flag-circle)">
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
    <svg width="28" height="28" viewBox="0 0 36 36" aria-hidden="true" className="shrink-0 rounded-full shadow-sm shadow-black/40">
      <defs>
        <clipPath id="id-flag-circle"><circle cx="18" cy="18" r="18" /></clipPath>
      </defs>
      <g clipPath="url(#id-flag-circle)">
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

function FilterIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-gray-400">
      <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3" />
    </svg>
  )
}



function SearchIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-gray-400">
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  )
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unit]}`
}

function formatDateRange(firstDate?: string, lastDate?: string) {
  if (!firstDate || !lastDate) return '—'
  return `${firstDate.slice(0, 10).replaceAll('-', '/')}-${lastDate.slice(0, 10).replaceAll('-', '/')}`
}

type CountryOption = 'All Country' | 'United States' | 'Indonesia'
type TypeOption = 'All Type' | 'Futures' | 'Index' | 'Stock'

export default function DatabasePage() {
  const [selectedCountry, setSelectedCountry] = useState<CountryOption>('All Country')
  const [selectedType, setSelectedType] = useState<TypeOption>('All Type')
  const [searchQuery, setSearchQuery] = useState('')
  const [openTimeframeMenu, setOpenTimeframeMenu] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['database-summary'],
    queryFn: fetchDatabaseSummary,
    staleTime: 60_000,
    refetchOnWindowFocus: true,
  })

  const filteredData = useMemo(() => {
    if (!data) return []
    const query = searchQuery.trim().toLowerCase()
    return data.filter((item) => {
      const matchCountry = selectedCountry === 'All Country' || item.country === selectedCountry
      const matchType = selectedType === 'All Type' || item.type === selectedType
      if (!matchCountry || !matchType) return false
      if (!query) return true

      const sym = item.symbol.toLowerCase()
      const name = item.datasetName.toLowerCase()

      // 1. Symbol exact match or starts with query
      if (sym === query || sym.startsWith(query)) return true

      // 2. Any word in symbol or datasetName starts with query
      const words = `${sym} ${name}`.split(/[\s._\-()]+/)
      return words.some((w) => w.startsWith(query))
    })
  }, [data, selectedCountry, selectedType, searchQuery])

  return (
    <main className="flex-1 min-h-0 overflow-y-auto bg-[#121214] pt-5 pr-5 pb-5 pl-[50px] text-gray-200 select-none">
      <div className="mb-5 flex items-center gap-3 max-w-[940px]">
        <h1 className="text-sm font-semibold tracking-wide text-gray-200">Database</h1>

        {/* Country Filter Dropdown (Apple Liquid Glass & Native Dark Mode) */}
        <div className="liquid-glass-btn liquid-glass-btn-no-grow !rounded-xl relative inline-flex items-center gap-2.5 px-3.5 py-2 text-xs text-gray-200 shadow-sm">
          <span className="font-medium text-gray-200">{selectedCountry}</span>
          <FilterIcon />
          <select
            value={selectedCountry}
            onChange={(e) => setSelectedCountry(e.target.value as CountryOption)}
            className="absolute inset-0 w-full h-full opacity-0 cursor-pointer text-xs"
            style={{ colorScheme: 'dark' }}
            aria-label="Filter Country"
          >
            <option value="All Country">All Country</option>
            <option value="United States">United States</option>
            <option value="Indonesia">Indonesia</option>
          </select>
        </div>

        {/* Type Filter Dropdown (Apple Liquid Glass & Native Dark Mode) */}
        <div className="liquid-glass-btn liquid-glass-btn-no-grow !rounded-xl relative inline-flex items-center gap-2.5 px-3.5 py-2 text-xs text-gray-200 shadow-sm">
          <span className="font-medium text-gray-200">{selectedType}</span>
          <FilterIcon />
          <select
            value={selectedType}
            onChange={(e) => setSelectedType(e.target.value as TypeOption)}
            className="absolute inset-0 w-full h-full opacity-0 cursor-pointer text-xs"
            style={{ colorScheme: 'dark' }}
            aria-label="Filter Type"
          >
            <option value="All Type">All Type</option>
            <option value="Futures">Futures</option>
            <option value="Index">Index</option>
            <option value="Stock">Stock</option>
          </select>
        </div>

        {/* Symbol Text Search Input (Apple Liquid Glass) */}
        <div className="liquid-glass-btn liquid-glass-btn-no-grow !rounded-xl relative inline-flex items-center gap-2 px-3 py-2 text-xs text-gray-200 shadow-sm w-44">
          <SearchIcon />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search symbol..."
            className="bg-transparent text-xs font-medium text-gray-200 placeholder-gray-500 outline-none w-full"
          />
        </div>
      </div>

      <div className="w-full max-w-[940px] border border-[#212124] bg-[#1A1A1E] shadow-2xl shadow-black/40 select-text">
          <table className="min-w-full table-fixed border-collapse text-left text-xs">
            <thead>
              <tr>
                <th className="w-[32%] border-b border-[#212124] bg-[#28282D] py-3 pl-6 text-[10px] font-medium uppercase tracking-wide text-gray-400">Dataset</th>
                <th className="w-[9%] border-b border-[#212124] bg-[#28282D] px-3 py-3 text-[10px] font-medium uppercase tracking-wide text-gray-400">Symbol</th>
                <th className="w-[11%] border-b border-[#212124] bg-[#28282D] px-3 py-3 text-[10px] font-medium uppercase tracking-wide text-gray-400">Type</th>
                <th className="w-[16%] border-b border-[#212124] bg-[#28282D] px-3 py-3 text-[10px] font-medium uppercase tracking-wide text-gray-400">Timeframe</th>
                <th className="w-[22%] border-b border-[#212124] bg-[#28282D] px-3 py-3 text-[10px] font-medium uppercase tracking-wide text-gray-400">Date</th>
                <th className="w-[10%] border-b border-[#212124] bg-[#28282D] px-3 py-3 text-[10px] font-medium uppercase tracking-wide text-gray-400">Size</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-900/40 bg-gray-950/10">
              {isLoading && (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-xs text-gray-500 font-mono">
                    Loading datasets from QuestDB…
                  </td>
                </tr>
              )}
              {!isLoading && filteredData.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-xs text-gray-500 font-mono">
                    No datasets found matching selected filters.
                  </td>
                </tr>
              )}
              {filteredData.map((dataset) => {
                const dateRange = formatDateRange(dataset.firstDate, dataset.lastDate)
                const canShowTimeframeMenu = dataset.availableTimeframes && dataset.availableTimeframes.length > 2
                return (
                  <tr key={dataset.symbol} className="border-b border-[#212124] text-gray-300 last:border-0 hover:bg-[#212126]/50">
                    <td className="py-3.5 pl-6">
                      <div className="flex items-center gap-3">
                        <CountryFlag country={dataset.country} />
                        <div className="min-w-0">
                          <div className="truncate font-semibold text-gray-200" title={dataset.datasetName}>{dataset.datasetName}</div>
                          <div className="mt-0.5 text-[11px] text-gray-500">{dataset.country}</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-3.5 font-mono text-[11px] font-semibold text-gray-300">{dataset.symbol}</td>
                    <td className="px-3 py-3.5 text-[11px] text-gray-300">{dataset.type}</td>
                    <td className="px-3 py-3.5 font-mono text-[11px] text-gray-300">
                      {canShowTimeframeMenu ? (
                        <div className="relative inline-block">
                          <button
                            type="button"
                            className="text-left font-mono text-[11px] text-gray-300 transition-colors hover:text-white"
                            title="Show available timeframes"
                            aria-expanded={openTimeframeMenu === dataset.symbol}
                            onClick={(event) => {
                              event.stopPropagation()
                              setOpenTimeframeMenu(openTimeframeMenu === dataset.symbol ? null : dataset.symbol)
                            }}
                          >
                            {dataset.timeframe}
                          </button>

                          {openTimeframeMenu === dataset.symbol && (
                            <>
                              <div className="fixed inset-0 z-30" onClick={() => setOpenTimeframeMenu(null)} />
                              <div className="absolute left-full top-1/2 z-40 ml-3 w-32 -translate-y-[16px] liquid-glass-dropdown rounded-lg py-1">
                                <div className="absolute -left-[6px] top-[16px] h-2.5 w-2.5 -translate-y-1/2 rotate-45 liquid-glass-dropdown-arrow" />
                                <p className="relative z-10 px-3 pb-1 pt-1.5 text-[9px] font-semibold uppercase tracking-widest text-gray-500">Available</p>
                                {dataset.availableTimeframes.map((timeframe) => (
                                  <div key={timeframe} className="relative z-10 px-3 py-1 text-xs text-gray-200">
                                    {timeframe}
                                  </div>
                                ))}
                              </div>
                            </>
                          )}
                        </div>
                      ) : (
                        dataset.timeframe
                      )}
                    </td>
                    <td className="px-3 py-3.5 font-mono text-[11px] text-gray-300">{dateRange}</td>
                    <td className="px-3 py-3.5 font-mono text-[11px] text-gray-300">
                      {dataset.bytes === undefined ? '—' : formatSize(dataset.bytes)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
      </div>
    </main>
  )
}
