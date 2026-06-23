import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchBacktests, fetchTrades } from '../api'
import EquityChart from './EquityChart'

interface Props {
  id: number | null
  onClose: () => void
}

type Tab = 'analysis' | 'equity' | 'monte-carlo'

const TABS = [
  { id: 'analysis', label: 'Analysis' },
  { id: 'equity', label: 'Equity' },
  { id: 'monte-carlo', label: 'Monte Carlo' },
] as const

function fmt$(v: number) {
  return '$' + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function fmtPct(v: number, decimals = 2) {
  return v.toFixed(decimals) + '%'
}
function fmtDate(ts: string) {
  return ts.split(' ')[0]
}

function StatRow({ label, value, color }: { label: string; value: React.ReactNode; color?: string }) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-gray-800/40 last:border-b-0">
      <span className="text-xs text-gray-500">{label}</span>
      <span className={`text-xs font-mono font-medium ${color ?? 'text-gray-200'}`}>{value}</span>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-[11px] font-semibold tracking-widest uppercase text-gray-600 mb-2">{title}</h3>
      <div className="bg-gray-900/40 rounded-lg border border-gray-800/50 px-4 py-1">
        {children}
      </div>
    </div>
  )
}

export default function StatsModal({ id, onClose }: Props) {
  const [activeTab, setActiveTab] = useState<Tab>('analysis')

  const { data: backtests } = useQuery({
    queryKey: ['backtests'],
    queryFn: fetchBacktests,
    enabled: id !== null,
    staleTime: Infinity,
  })

  const b = backtests?.find(bt => bt.id === id)

  const { data: trades, isLoading: loadingTrades } = useQuery({
    queryKey: ['trades', id],
    queryFn: () => fetchTrades(id!),
    enabled: id !== null && activeTab === 'equity',
    staleTime: Infinity,
  })

  useEffect(() => {
    if (id === null) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [id, onClose])

  if (id === null) return null

  return (
    <div className="fixed inset-0 z-[60] bg-gray-950 flex flex-col text-white">
      {/* Header */}
      <div className="px-6 py-4 border-b border-gray-800/80 flex items-center gap-4">
        <button
          onClick={onClose}
          className="text-white hover:text-white/80 p-1.5 rounded-lg hover:bg-gray-900 transition-colors flex items-center justify-center shrink-0"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="19" y1="12" x2="5" y2="12" />
            <polyline points="12 19 5 12 12 5" />
          </svg>
        </button>

        <div className="flex items-center gap-2 pr-2 shrink-0">
          <span className="text-white font-medium text-sm">Stats:</span>
          <span className="text-white font-medium text-sm">
            {b ? b.strategy : '...'}
          </span>
          <span className="text-xs text-white font-mono">#{id}</span>
        </div>

        <div className="flex items-center gap-0.5 bg-gray-900 rounded-lg p-0.5 border border-gray-800/80 shrink-0">
          {TABS.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-3 py-1 text-xs font-medium rounded-md transition-all duration-150 ${
                activeTab === tab.id
                  ? 'bg-gray-700 text-white shadow-sm'
                  : 'text-gray-500 hover:text-gray-200 hover:bg-gray-800/70'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 bg-gray-950 flex flex-col">
        {activeTab === 'analysis' && b && (
          <div className="px-8 py-8 overflow-y-auto flex-1">
            <div className="grid grid-cols-2 gap-6 max-w-5xl mx-auto">

              {/* Left Column — Overview + Balance + Average Returns + Performance Ratios */}
              <div className="space-y-6">
                <Section title="Overview">
                  <StatRow label="Symbol" value={b.symbol.toUpperCase()} />
                  <StatRow label="Instrument" value={b.instrument} />
                  <StatRow label="Period" value={`${fmtDate(b.first_ts)} → ${fmtDate(b.last_ts)}`} />
                  <StatRow label="Total Days" value={String(b.total_days)} />
                  <StatRow label="Number of Trades" value={String(b.num_trades)} />
                </Section>

                <Section title="Balance">
                  <StatRow label="Initial Balance" value={fmt$(b.initial_bal)} />
                  <StatRow label="Final Balance" value={`${fmt$(b.final_bal)} (${b.net_growth >= 0 ? '+' : ''}${fmtPct(b.net_growth)})`} color={b.final_bal >= b.initial_bal ? 'text-emerald-400' : 'text-red-400'} />
                </Section>

                <Section title="Average Returns">
                  <StatRow label="Weekly" value={`${fmt$(b.avg_weekly)} (${fmtPct(b.avg_weekly_pct)})`} color={b.avg_weekly >= 0 ? 'text-emerald-400' : 'text-red-400'} />
                  <StatRow label="Monthly" value={`${fmt$(b.avg_monthly)} (${fmtPct(b.avg_monthly_pct)})`} color={b.avg_monthly >= 0 ? 'text-emerald-400' : 'text-red-400'} />
                </Section>

                <Section title="Performance Ratios">
                  <StatRow label="Sharpe Ratio" value={b.sharpe.toFixed(2)} color={b.sharpe >= 1 ? 'text-emerald-400' : b.sharpe >= 0 ? 'text-gray-200' : 'text-red-400'} />
                  <StatRow label="Profit Factor" value={b.profit_factor.toFixed(2)} color={b.profit_factor >= 1 ? 'text-emerald-400' : 'text-red-400'} />
                  <StatRow label="Expectancy" value={fmt$(b.expectancy)} color={b.expectancy >= 0 ? 'text-emerald-400' : 'text-red-400'} />
                </Section>
              </div>

              {/* Right Column — Win/Loss + Position Sizing + Drawdown & Loss */}
              <div className="space-y-6">
                <Section title="Win / Loss">
                  <StatRow label="Win Rate" value={`${fmtPct(b.win_rate, 1)} (${b.win_count}/${b.num_trades})`} color={b.win_rate >= 50 ? 'text-emerald-400' : 'text-red-400'} />
                  <StatRow label="Total Wins" value={fmt$(b.total_win)} color="text-emerald-400" />
                  <StatRow label="Total Losses" value={fmt$(b.total_loss)} color="text-red-400" />
                  <StatRow label="Max Losing Streak" value={String(b.max_lose_streak)} />
                </Section>

                <Section title="Position Sizing">
                  <StatRow label="Size" value={`${b.avg_size.toFixed(1)} (Min: ${b.min_size.toFixed(1)} / Max: ${b.max_size.toFixed(1)})`} />
                </Section>

                <Section title="Drawdown & Loss">
                  <StatRow
                    label="Max Drawdown"
                    value={
                      <>
                        <span className="text-red-400">{fmtPct(b.max_drawdown)} ({fmt$(b.max_drawdown_dollars)})</span>
                        {b.max_drawdown_peak_date && (
                          <span className="text-white"> [{fmtDate(b.max_drawdown_peak_date)} → {fmtDate(b.max_drawdown_trough_date)}]</span>
                        )}
                      </>
                    }
                  />
                  <StatRow label="Avg Drawdown" value={`${fmtPct(b.avg_drawdown)} (${fmt$(b.avg_drawdown_dollars)})`} color="text-red-400" />
                  <StatRow
                    label="Max Intraday DD"
                    value={
                      <>
                        <span className="text-red-400">{fmtPct(b.max_intraday_drawdown)} ({fmt$(b.max_intraday_drawdown_dollars)})</span>
                        {b.max_intraday_drawdown_date && (
                          <span className="text-white"> [{fmtDate(b.max_intraday_drawdown_date)}]</span>
                        )}
                      </>
                    }
                  />
                  <StatRow label="Avg Intraday DD" value={`${fmtPct(b.avg_intraday_drawdown)} (${fmt$(b.avg_intraday_drawdown_dollars)})`} color="text-red-400" />
                  <StatRow
                    label="Max Daily Loss"
                    value={
                      <>
                        <span className="text-red-400">{fmt$(b.max_daily_loss)}</span>
                        {b.max_daily_loss_date && (
                          <span className="text-white"> [{fmtDate(b.max_daily_loss_date)}]</span>
                        )}
                      </>
                    }
                  />
                  <StatRow label="Avg Daily Loss" value={fmt$(b.avg_daily_loss)} color="text-red-400" />
                </Section>

              </div>

            </div>
          </div>
        )}

        {activeTab === 'analysis' && !b && (
          <div className="flex-1 flex items-center justify-center py-20">
            <span className="text-sm text-gray-600">Loading...</span>
          </div>
        )}

        {activeTab === 'equity' && b && (
          <div className="flex-1 min-h-0 w-full relative">
            {loadingTrades ? (
              <div className="absolute inset-0 flex items-center justify-center">
                <span className="text-sm text-gray-400">Loading trades...</span>
              </div>
            ) : trades ? (
              <EquityChart trades={trades} initialBalance={b.initial_bal} />
            ) : (
              <div className="absolute inset-0 flex items-center justify-center">
                <span className="text-sm text-gray-500">No trades data available.</span>
              </div>
            )}
          </div>
        )}

        {activeTab === 'monte-carlo' && b && (
          <div className="flex-1 flex flex-col items-center justify-center p-8 text-center bg-gray-950">
            <div className="max-w-md space-y-3">
              <div className="text-gray-400 font-medium text-sm">Monte Carlo Simulation</div>
              <p className="text-xs text-gray-600 leading-relaxed">
                Select parameters and run a Monte Carlo simulation to analyze potential outcome distributions and risk of ruin.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
