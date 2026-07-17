import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchBacktestFx, fetchMonteCarloData, fetchTrades } from '../../api'
import { isFuturesInstrument, type Backtest, type MonteCarloData } from '../../types'
import EquityChart from '../charts/EquityChart'
import MonteCarloChart from '../charts/MonteCarloChart'
import Splicing from './Splicing'
import Volatility from './Volatility'
import LiquidGlassTabs from '../navigation/LiquidGlassTabs'

type ResultTab = 'analysis' | 'equity' | 'splicing' | 'volatility' | 'monte-carlo'

const tabs: { id: ResultTab; label: string }[] = [
  { id: 'analysis', label: 'Analysis' },
  { id: 'equity', label: 'Equity' },
  { id: 'splicing', label: 'Splicing' },
  { id: 'volatility', label: 'Volatility' },
  { id: 'monte-carlo', label: 'Monte Carlo' },
]

function fmt$(value: number) {
  return '$' + value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtPct(value: number, decimals = 2) {
  return value.toFixed(decimals) + '%'
}

function fmtDate(timestamp: string) {
  return timestamp.split(' ')[0]
}

function StatRow({ label, value, color }: { label: string; value: React.ReactNode; color?: string }) {
  return (
    <div className="flex items-start justify-between gap-4 py-2 border-b border-gray-800/40 last:border-b-0">
      <span className="text-xs text-gray-500 shrink-0">{label}</span>
      <span className={`text-right text-xs font-mono font-medium ${color ?? 'text-gray-200'}`}>{value}</span>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 text-[10px] font-semibold tracking-widest uppercase text-gray-500">{title}</h3>
      <div className="border border-[#212124] bg-[#1A1A1E] px-4 py-1">{children}</div>
    </section>
  )
}

interface BacktestResultSidebarProps {
  backtest: Backtest
  onDelete?: () => void
}

export default function BacktestResultSidebar({ backtest, onDelete }: BacktestResultSidebarProps) {
  const [activeTab, setActiveTab] = useState<ResultTab>('analysis')
  const [execView, setExecView] = useState<'native' | 'fx'>('native')
  const isFutures = isFuturesInstrument(backtest.instrument)
  const shouldLoadTrades = activeTab === 'equity' || activeTab === 'splicing' || activeTab === 'volatility'
  const { data: trades, isLoading: isLoadingTrades, isError: isTradesError } = useQuery({
    queryKey: ['trades', backtest.id],
    queryFn: () => fetchTrades(backtest.id),
    enabled: shouldLoadTrades,
    staleTime: Infinity,
  })
  const { data: monteCarlo, isLoading: isLoadingMonteCarlo, isError: isMonteCarloError } = useQuery({
    queryKey: ['montecarlo', backtest.id],
    queryFn: () => fetchMonteCarloData(backtest.id),
    enabled: activeTab === 'monte-carlo',
    staleTime: Infinity,
    retry: false,
  })
  const fxQuery = useQuery({
    queryKey: ['backtest-fx', backtest.id],
    queryFn: () => fetchBacktestFx(backtest.id),
    enabled: !isFutures && execView === 'fx',
    staleTime: Infinity,
    retry: false,
  })
  const showFx = !isFutures && execView === 'fx'
  const fxData = fxQuery.data ?? null
  const report = showFx ? fxData?.report ?? null : backtest
  const viewTrades = showFx ? fxData?.trades : trades
  const isLoadingViewTrades = showFx ? fxQuery.isLoading : isLoadingTrades
  const isViewTradesError = showFx ? fxQuery.isError : isTradesError

  return (
    <aside
      className="fixed inset-y-4 right-4 z-50 flex w-[860px] max-w-[calc(100vw-2rem)] flex-col overflow-hidden rounded-xl bg-[#121214] shadow-[0_28px_90px_rgba(0,0,0,0.82)]"
      aria-label={`Backtest ${backtest.id} results`}
    >
      <div className="absolute top-6 left-8 z-10 shrink-0">
        <LiquidGlassTabs
          items={tabs}
          activeId={activeTab}
          onChange={(id) => setActiveTab(id as ResultTab)}
          label="Result views"
        />
      </div>

      <div className="absolute top-6 right-8 z-10 shrink-0 flex items-center gap-3">
        {!isFutures && (
          <LiquidGlassTabs
            items={[
              ['native', 'Native'],
              ['fx', 'March'],
            ].map(([id, label]) => ({ id, label }))}
            activeId={execView}
            onChange={(id) => setExecView(id as 'native' | 'fx')}
            label="Execution view"
          />
        )}
        {onDelete && (
          <button
            onClick={onDelete}
            className="w-7 h-7 flex items-center justify-center text-gray-400 hover:text-red-400 transition-colors cursor-pointer"
            title="Delete Backtest"
          >
            <svg
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polyline points="3 6 5 6 21 6" />
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
              <line x1="10" y1="11" x2="10" y2="17" />
              <line x1="14" y1="11" x2="14" y2="17" />
            </svg>
          </button>
        )}
      </div>

      {showFx && !fxData && (
        <EmptyState message={fxQuery.isLoading ? 'Loading March execution…' : 'No March execution for this backtest.'} />
      )}

      {!showFx || fxData ? <>
      {activeTab === 'analysis' && report && (
        <div className="flex-1 overflow-y-auto px-8 pb-8 pt-20">
          <div className="grid grid-cols-2 gap-6 max-w-5xl mx-auto">
            <div className="space-y-6">
              <Section title="Overview">
                <StatRow label="Symbol" value={report.symbol.toUpperCase()} />
                <StatRow label="Instrument" value={report.instrument} />
                <StatRow label="Period" value={`${fmtDate(report.first_ts)} → ${fmtDate(report.last_ts)}`} />
                <StatRow label="Total Days" value={String(report.total_days)} />
                <StatRow label="Number of Trades" value={String(report.num_trades)} />
              </Section>

              <Section title="Balance">
                <StatRow label="Initial Balance" value={fmt$(report.initial_bal)} />
                <StatRow
                  label="Final Balance"
                  value={`${fmt$(report.final_bal)} (${report.net_growth >= 0 ? '+' : ''}${fmtPct(report.net_growth)})`}
                  color={report.final_bal >= report.initial_bal ? 'text-emerald-400' : 'text-red-400'}
                />
              </Section>

              <Section title="Average Returns">
                <StatRow label="Weekly" value={`${fmt$(report.avg_weekly)} (${fmtPct(report.avg_weekly_pct)})`} color={report.avg_weekly >= 0 ? 'text-emerald-400' : 'text-red-400'} />
                <StatRow label="Monthly" value={`${fmt$(report.avg_monthly)} (${fmtPct(report.avg_monthly_pct)})`} color={report.avg_monthly >= 0 ? 'text-emerald-400' : 'text-red-400'} />
              </Section>

              <Section title="Performance Ratios">
                <StatRow label="Sharpe Ratio" value={report.sharpe.toFixed(2)} color={report.sharpe >= 1 ? 'text-emerald-400' : report.sharpe >= 0 ? 'text-gray-200' : 'text-red-400'} />
                <StatRow label="Profit Factor" value={report.profit_factor.toFixed(2)} color={report.profit_factor >= 1 ? 'text-emerald-400' : 'text-red-400'} />
                <StatRow label="Expectancy" value={fmt$(report.expectancy)} color={report.expectancy >= 0 ? 'text-emerald-400' : 'text-red-400'} />
              </Section>
            </div>

            <div className="space-y-6">
              <Section title="Win / Loss">
                <StatRow label="Win Rate" value={`${fmtPct(report.win_rate, 1)} (${report.win_count}/${report.num_trades})`} color={report.win_rate >= 50 ? 'text-emerald-400' : 'text-red-400'} />
                <StatRow label="Total Wins" value={fmt$(report.total_win)} color="text-emerald-400" />
                <StatRow label="Total Losses" value={fmt$(report.total_loss)} color="text-red-400" />
                <StatRow label="Max Losing Streak" value={String(report.max_lose_streak)} />
              </Section>

              <Section title="Position Sizing">
                <StatRow label="Size" value={`${report.avg_size.toFixed(2)} (Min: ${report.min_size.toFixed(2)} / Max: ${report.max_size.toFixed(2)})`} />
              </Section>

              <Section title="Drawdown & Loss">
                <StatRow label="Max Drawdown" value={`${fmtPct(report.max_drawdown)} (${fmt$(report.max_drawdown_dollars)})`} color="text-red-400" />
                {report.max_drawdown_peak_date && <StatRow label="Drawdown Period" value={`${fmtDate(report.max_drawdown_peak_date)} → ${fmtDate(report.max_drawdown_trough_date)}`} />}
                <StatRow label="Avg Drawdown" value={`${fmtPct(report.avg_drawdown)} (${fmt$(report.avg_drawdown_dollars)})`} color="text-red-400" />
                <StatRow label="Max Intraday DD" value={`${fmtPct(report.max_intraday_drawdown)} (${fmt$(report.max_intraday_drawdown_dollars)})`} color="text-red-400" />
                <StatRow label="Avg Intraday DD" value={`${fmtPct(report.avg_intraday_drawdown)} (${fmt$(report.avg_intraday_drawdown_dollars)})`} color="text-red-400" />
                <StatRow label="Max Daily Loss" value={fmt$(report.max_daily_loss)} color="text-red-400" />
                <StatRow label="Avg Daily Loss" value={fmt$(report.avg_daily_loss)} color="text-red-400" />
              </Section>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'equity' && report && (
        <div className="relative flex-1 overflow-y-auto px-8 pb-8 pt-20 flex flex-col justify-center">
          {isLoadingViewTrades ? <EmptyState message="Loading trades…" /> : isViewTradesError ? <EmptyState message="Could not load trades." error /> : viewTrades?.length ? (
            <div className="w-full max-w-5xl mx-auto h-[400px]">
              <EquityChart trades={viewTrades} initialBalance={report.initial_bal} startDate={report.first_ts.slice(0, 10)} />
            </div>
          ) : <EmptyState message="No trades data available." />}
        </div>
      )}

      {activeTab === 'splicing' && report && (
        <div className="relative flex-1 overflow-y-auto px-8 pb-8 pt-20">
          {isLoadingViewTrades ? <EmptyState message="Loading trades…" /> : isViewTradesError ? <EmptyState message="Could not load trades." error /> : viewTrades?.length ? <Splicing trades={viewTrades} initialBalance={report.initial_bal} /> : <EmptyState message="No trades data available." />}
        </div>
      )}

      {activeTab === 'volatility' && report && (
        <div className="relative flex-1 overflow-y-auto px-8 pb-8 pt-20">
          {isLoadingViewTrades ? <EmptyState message="Loading trades…" /> : isViewTradesError ? <EmptyState message="Could not load trades." error /> : viewTrades?.length ? <Volatility trades={viewTrades} initialBalance={report.initial_bal} /> : <EmptyState message="No trades data available." />}
        </div>
      )}

      {activeTab === 'monte-carlo' && report && (
        showFx ? (
          fxData?.monteCarlo ? <MonteCarloResultView data={fxData.monteCarlo} /> : <EmptyState message="No March Monte Carlo simulation for this backtest." />
        ) : (
          isLoadingMonteCarlo ? <EmptyState message="Loading Monte Carlo…" /> : isMonteCarloError || !monteCarlo ? <EmptyState message="No Monte Carlo simulation for this backtest." /> : <MonteCarloResultView data={monteCarlo} />
        )
      )}
      </> : null}
    </aside>
  )
}

function EmptyState({ message, error = false }: { message: string; error?: boolean }) {
  return <div className={`absolute inset-0 flex items-center justify-center px-6 text-center text-sm ${error ? 'text-red-400' : 'text-gray-500'}`}>{message}</div>
}

function MonteCarloResultView({ data }: { data: MonteCarloData }) {
  const balances = [data.p5, data.p25, data.p50, data.p75, data.p95]
  const drawdowns = [data.ddP5, data.ddP25, data.ddP50, data.ddP75, data.ddP95]

  return (
    <div className="flex-1 overflow-y-auto no-scrollbar px-8 pb-8 pt-8 flex flex-col items-center justify-center gap-6">
      <div className="w-full max-w-5xl font-mono text-sm">
        <table className="w-full border-collapse">
          <thead>
            <tr className="text-gray-400 text-right">
              <th className="text-left pb-1 font-normal w-48"></th>
              <th className="pb-1 font-normal pr-6">p5</th>
              <th className="pb-1 font-normal pr-6">p25</th>
              <th className="pb-1 font-normal pr-6">median</th>
              <th className="pb-1 font-normal pr-6">p75</th>
              <th className="pb-1 font-normal">p95</th>
            </tr>
          </thead>
          <tbody>
            <tr className="text-right">
              <td className="text-left text-gray-300 pr-4">Final balance</td>
              {balances.map((value, index) => (
                <td key={index} className={index < balances.length - 1 ? 'pr-6' : ''}>{value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
              ))}
            </tr>
            <tr className="text-right">
              <td className="text-left text-gray-300 pr-4">Max drawdown %</td>
              {drawdowns.map((value, index) => (
                <td key={index} className={index < drawdowns.length - 1 ? 'pr-6' : ''}>{isNaN(value) ? '—' : fmtPct(value, 1)}</td>
              ))}
            </tr>
          </tbody>
        </table>
        <p className="text-gray-500 text-xs mt-1">(Worst case: the p5 column for balance, the p95 column for drawdown.)</p>
        <div className="mt-3 flex gap-8 text-sm">
          <span><span className="text-gray-400">P(profit)</span>&nbsp;&nbsp;&nbsp;{fmtPct(data.pProfit * 100, 1)}</span>
          <span><span className="text-gray-400">P(ruin ≤ 50% start)</span>&nbsp;&nbsp;&nbsp;{fmtPct(data.pRuin * 100, 1)}</span>
        </div>
      </div>
      <div className="w-full max-w-5xl h-[400px]">
        <MonteCarloChart data={data} />
      </div>
    </div>
  )
}
