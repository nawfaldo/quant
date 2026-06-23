import { useQuery } from '@tanstack/react-query'
import { fetchBacktests, fetchTrades, fetchMonteCarloData } from '../api'
import EquityChart from './EquityChart'
import MonteCarloChart from './MonteCarloChart'
import { useApp } from '../context/AppContext'


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

function MonteCarloTab({ backtestId }: { backtestId: number }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['montecarlo', backtestId],
    queryFn: () => fetchMonteCarloData(backtestId),
    staleTime: Infinity,
    retry: false,
  })

  const isNotFound = (error as { code?: string })?.code === 'not_found'

  if (isLoading) return (
    <div className="flex-1 flex items-center justify-center">
      <span className="text-sm text-gray-400">Loading Monte Carlo...</span>
    </div>
  )
  if (isError) return (
    <div className="flex-1 flex items-center justify-center">
      {isNotFound
        ? <span className="text-sm text-gray-600">No Monte Carlo simulation for this backtest.</span>
        : <span className="text-sm text-red-400">Error loading Monte Carlo data.</span>
      }
    </div>
  )
  if (!data) return null

  return (
    <div className="flex-1 min-h-0 w-full relative">
      <MonteCarloChart data={data} />
    </div>
  )
}

export default function StatsPage() {
  const { selectedBacktestId, activeTab } = useApp()

  const { data: backtests } = useQuery({
    queryKey: ['backtests'],
    queryFn: fetchBacktests,
    staleTime: Infinity,
  })

  const b = selectedBacktestId !== null ? backtests?.find(bt => bt.id === selectedBacktestId) : null

  const { data: trades, isLoading: loadingTrades, isError: tradesError, error: errorObj } = useQuery({
    queryKey: ['trades', selectedBacktestId],
    queryFn: () => fetchTrades(selectedBacktestId!),
    enabled: selectedBacktestId !== null && activeTab === 'equity',
    staleTime: Infinity,
  })

  return (
    <div className="flex-1 bg-gray-950 flex flex-col min-h-0">
      {selectedBacktestId === null ? (
        <div className="flex-1 flex items-center justify-center">
          <span className="text-sm text-gray-600">Select a backtest</span>
        </div>
      ) : (
        <>

          {/* Content */}
          <div className="flex-1 min-h-0 bg-gray-950 flex flex-col">
            {activeTab === 'analysis' && b && (
              <div className="px-8 py-8 overflow-y-auto flex-1">
                <div className="grid grid-cols-2 gap-6 max-w-5xl mx-auto">

                  {/* Left Column */}
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

                  {/* Right Column */}
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

            {activeTab === 'analysis' && selectedBacktestId !== null && !b && (
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
                ) : tradesError ? (
                  <div className="absolute inset-0 flex items-center justify-center text-red-400 text-xs">
                    Error loading trades: {errorObj?.message}
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
              <MonteCarloTab backtestId={b.id} />
            )}
          </div>
        </>
      )}
    </div>
  )
}
