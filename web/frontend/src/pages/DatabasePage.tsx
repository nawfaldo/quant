import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchDatabaseSummary } from '../api'

const datasets = [
  { name: 'ES', datasetName: 'S&P 500 Futures', country: 'United States', type: 'Futures', timeframe: '1m...1d', availableTimeframes: ['1m', '5m', '15m', '30m', '1h', '4h', '1d'] },
  { name: 'NQ', datasetName: 'Nasdaq-100 Futures', country: 'United States', type: 'Futures', timeframe: '1m...1d', availableTimeframes: ['1m', '5m', '15m', '30m', '1h', '4h', '1d'] },
  { name: 'VIX', datasetName: 'CBOE Volatility Index', country: 'United States', type: 'Index', timeframe: '1d', availableTimeframes: ['1d'] },
] as const

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

export default function DatabasePage() {
  const [openTimeframeMenu, setOpenTimeframeMenu] = useState<string | null>(null)
  const { data, isLoading } = useQuery({
    queryKey: ['database-summary'],
    queryFn: fetchDatabaseSummary,
    staleTime: 60_000,
    refetchOnWindowFocus: true,
  })

  return (
    <main className="flex-1 min-h-0 overflow-y-auto bg-[#121214] pt-5 pr-5 pb-5 pl-[50px] text-gray-200 select-none">
      <div className="mb-5 flex items-center gap-5">
        <h1 className="text-sm font-semibold tracking-wide text-gray-200">Database</h1>
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
              {datasets.map((dataset) => {
                const bytes = data?.find((item) => item.name === dataset.name)?.bytes
                const dateRange = formatDateRange(
                  data?.find((item) => item.name === dataset.name)?.firstDate,
                  data?.find((item) => item.name === dataset.name)?.lastDate,
                )
                const canShowTimeframeMenu = dataset.availableTimeframes.length > 2
                return (
                  <tr key={dataset.name} className="border-b border-[#212124] text-gray-300 last:border-0">
                    <td className="py-3.5 pl-6">
                      <div className="flex items-center gap-3">
                        <UnitedStatesFlag />
                        <div className="min-w-0">
                          <div className="truncate font-semibold text-gray-200" title={dataset.datasetName}>{dataset.datasetName}</div>
                          <div className="mt-0.5 text-[11px] text-gray-500">{dataset.country}</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-3.5 font-mono text-[11px] font-semibold text-gray-300">{dataset.name}</td>
                    <td className="px-3 py-3.5 text-[11px] text-gray-300">{dataset.type}</td>
                    <td className="px-3 py-3.5 font-mono text-[11px] text-gray-300">
                      {canShowTimeframeMenu ? <div className="relative inline-block">
                        <button
                          type="button"
                          className="text-left font-mono text-[11px] text-gray-300 transition-colors hover:text-white"
                          title="Show available timeframes"
                          aria-expanded={openTimeframeMenu === dataset.name}
                          onClick={(event) => {
                            event.stopPropagation()
                            setOpenTimeframeMenu(openTimeframeMenu === dataset.name ? null : dataset.name)
                          }}
                        >
                          {dataset.timeframe}
                        </button>

                        {openTimeframeMenu === dataset.name && (
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
                      </div> : dataset.timeframe}
                    </td>
                    <td className="px-3 py-3.5 font-mono text-[11px] text-gray-300">{isLoading ? 'Loading…' : dateRange}</td>
                    <td className="px-3 py-3.5 font-mono text-[11px] text-gray-300">
                      {isLoading ? 'Loading…' : bytes === undefined ? '—' : formatSize(bytes)}
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
