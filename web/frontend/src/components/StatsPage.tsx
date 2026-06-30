import { useState, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchBacktests, fetchTrades, fetchMonteCarloData, deleteBacktest, fetchBacktestFx } from "../api";
import EquityChart from "./EquityChart";
import MonteCarloChart from "./MonteCarloChart";
import Splicing from "./Splicing";
import { useApp } from "../context/AppContext";

function fmt$(v: number) {
  return (
    "$" +
    v.toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })
  );
}
function fmtPct(v: number, decimals = 2) {
  return v.toFixed(decimals) + "%";
}
function fmtDate(ts: string) {
  return ts.split(" ")[0];
}

function StatRow({
  label,
  value,
  color,
}: {
  label: string;
  value: React.ReactNode;
  color?: string;
}) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-gray-800/40 last:border-b-0">
      <span className="text-xs text-gray-500">{label}</span>
      <span
        className={`text-xs font-mono font-medium ${color ?? "text-gray-200"}`}
      >
        {value}
      </span>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h3 className="text-[11px] font-semibold tracking-widest uppercase text-gray-600 mb-2">
        {title}
      </h3>
      <div className="bg-gray-900/40 rounded-lg border border-gray-800/50 px-4 py-1">
        {children}
      </div>
    </div>
  );
}

function MonteCarloTab({ backtestId }: { backtestId: number }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["montecarlo", backtestId],
    queryFn: () => fetchMonteCarloData(backtestId),
    staleTime: Infinity,
    retry: false,
  });

  const isNotFound = (error as { code?: string })?.code === "not_found";

  if (isLoading)
    return (
      <div className="flex-1 flex items-center justify-center">
        <span className="text-sm text-gray-400">Loading Monte Carlo...</span>
      </div>
    );
  if (isError)
    return (
      <div className="flex-1 flex items-center justify-center">
        {isNotFound ? (
          <span className="text-sm text-gray-600">
            No Monte Carlo simulation for this backtest.
          </span>
        ) : (
          <span className="text-sm text-red-400">
            Error loading Monte Carlo data.
          </span>
        )}
      </div>
    );
  if (!data) return null;

  return <MonteCarloView data={data} />;
}

function MonteCarloView({ data }: { data: import("../types").MonteCarloData }) {
  return (
    <div className="flex-1 min-h-0 w-full flex flex-col gap-4 pt-20 px-8 pb-8 overflow-y-auto">
      {/* Summary table */}
      <div className="font-mono text-sm">
        <table className="border-collapse">
          <thead>
            <tr className="text-gray-400 text-right">
              <th className="text-left pb-1 font-normal w-40"></th>
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
              {([data.p5, data.p25, data.p50, data.p75, data.p95] as number[]).map((v, i, arr) => (
                <td key={i} className={i < arr.length - 1 ? "pr-6" : ""}>{v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
              ))}
            </tr>
            <tr className="text-right">
              <td className="text-left text-gray-300 pr-4">Max drawdown %</td>
              {([data.ddP5, data.ddP25, data.ddP50, data.ddP75, data.ddP95] as number[]).map((v, i, arr) => (
                <td key={i} className={i < arr.length - 1 ? "pr-6" : ""}>{isNaN(v) ? "—" : fmtPct(v, 1)}</td>
              ))}
            </tr>
          </tbody>
        </table>
        <p className="text-gray-500 text-xs mt-1">(Worst case: the p5 column for balance, the p95 column for drawdown.)</p>
        <div className="mt-3 flex gap-8 text-sm">
          <span><span className="text-gray-400">P(profit)</span>&nbsp;&nbsp;&nbsp;<strong>{fmtPct(data.pProfit * 100, 1)}</strong></span>
          <span><span className="text-gray-400">P(ruin ≤ 50% start)</span>&nbsp;&nbsp;&nbsp;<strong>{fmtPct(data.pRuin * 100, 1)}</strong></span>
        </div>
      </div>
      {/* Chart */}
      <div className="flex-1 min-h-0 w-full relative">
        <MonteCarloChart data={data} />
      </div>
    </div>
  );
}

export default function StatsPage() {
  const { selectedBacktestId, setSelectedBacktestId, activeTab, setActiveTab } =
    useApp();

  const queryClient = useQueryClient();

  const handleDelete = async (id: number) => {
    if (!confirm("Are you sure you want to delete this backtest?")) return;
    try {
      await deleteBacktest(id);
      queryClient.invalidateQueries({ queryKey: ["backtests"] });
      if (selectedBacktestId === id) {
        setSelectedBacktestId(null);
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const { data: backtests } = useQuery({
    queryKey: ["backtests"],
    queryFn: fetchBacktests,
    staleTime: Infinity,
  });

  const b =
    selectedBacktestId !== null
      ? backtests?.find((bt) => bt.id === selectedBacktestId)
      : null;

  const {
    data: trades,
    isLoading: loadingTrades,
    isError: tradesError,
    error: errorObj,
  } = useQuery({
    queryKey: ["trades", selectedBacktestId],
    queryFn: () => fetchTrades(selectedBacktestId!),
    enabled:
      selectedBacktestId !== null &&
      (activeTab === "equity" || activeTab === "splicing"),
    staleTime: Infinity,
  });

  // FX-execution view: the same saved book re-priced from fx_nq_ticks. Fetched
  // whenever a backtest is selected so the toggle knows if an fx view exists.
  const [execView, setExecView] = useState<"native" | "fx">("native");
  useEffect(() => {
    setExecView("native");
  }, [selectedBacktestId]);

  const fxQuery = useQuery({
    queryKey: ["backtest-fx", selectedBacktestId],
    queryFn: () => fetchBacktestFx(selectedBacktestId!),
    enabled: selectedBacktestId !== null,
    staleTime: Infinity,
    retry: false,
  });
  const fxData = fxQuery.data ?? null;
  const showFx = execView === "fx";
  // Report/trades the tabs render: native (list row) or the fx-re-priced book.
  // When forex is selected but unavailable, rep is null so the tabs yield to the
  // explanatory empty state below instead of rendering native numbers.
  const rep = showFx ? (fxData ? fxData.report : null) : b;
  const viewTrades = showFx ? (fxData ? fxData.trades : undefined) : trades;
  const tradesLoadingNow = showFx ? fxQuery.isLoading : loadingTrades;
  const tradesErrorNow = showFx ? fxQuery.isError : tradesError;

  return (
    <div className="flex-1 bg-gray-950 flex flex-row min-h-0">
      {/* Backtest List Left Sidebar */}
      <div className="w-64 shrink-0 bg-gray-900/10 border-r border-gray-900/60 flex flex-col min-h-0">
        <div className="px-5 pt-4 text-[10px] uppercase tracking-wider font-bold text-gray-500 select-none">
          Backtests
        </div>
        <div className="flex-1 overflow-y-auto no-scrollbar py-2">
          {backtests?.map((bt) => (
            <div key={bt.id} className="group relative w-full flex items-center">
              <button
                onClick={() => setSelectedBacktestId(bt.id)}
                className={`w-full text-left px-5 pr-12 py-3.5 flex flex-col justify-center transition-all duration-150 select-none ${
                  selectedBacktestId === bt.id
                    ? "text-white bg-gray-900/20"
                    : "text-gray-400 hover:text-gray-200 hover:bg-gray-900/10"
                }`}
              >
                <div className="text-[11px] font-semibold truncate leading-tight">
                  {bt.strategy}
                </div>
                <div className="text-[9px] text-gray-500 leading-tight mt-1">
                  #{bt.id} · {bt.symbol.toUpperCase()}
                </div>
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  handleDelete(bt.id);
                }}
                className="absolute right-4 p-1.5 rounded text-gray-500 hover:text-red-400 hover:bg-gray-800/60 opacity-0 group-hover:opacity-100 transition-opacity duration-150 z-20 cursor-pointer"
                title="Delete backtest"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
                </svg>
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Main Stats Content */}
      <div className="flex-1 flex flex-col min-h-0">
        {selectedBacktestId === null ? (
          <div className="flex-1 flex items-center justify-center">
            <span className="text-sm text-gray-600">Select a backtest</span>
          </div>
        ) : (
          <div className="flex-1 min-h-0 bg-gray-950 flex flex-col relative">
            {/* Floating Tab Selection */}
            <div className="absolute top-6 left-8 z-10 flex items-center gap-0.5 bg-gray-900 rounded-lg p-0.5 border border-gray-800/80 shrink-0 shadow-lg shadow-black/40">
              {([
                ["analysis", "Analysis"],
                ["equity", "Equity"],
                ["splicing", "Splicing"],
                ["monte-carlo", "Monte Carlo"],
              ] as const).map(([id, label]) => (
                <button
                  key={id}
                  onClick={() => setActiveTab(id)}
                  className={`px-2.5 py-1 transition-all duration-150 text-xs font-medium rounded-md select-none ${
                    activeTab === id
                      ? "bg-gray-700 text-white shadow-sm"
                      : "text-gray-500 hover:text-gray-200 hover:bg-gray-800/70"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            {/* Native / Forex-execution switch */}
            <div className="absolute top-6 right-8 z-10 flex items-center gap-0.5 bg-gray-900 rounded-lg p-0.5 border border-gray-800/80 shrink-0 shadow-lg shadow-black/40">
              {([
                ["native", "Native"],
                ["fx", "March"],
              ] as const).map(([id, label]) => {
                const active = execView === id;
                return (
                  <button
                    key={id}
                    onClick={() => setExecView(id)}
                    className={`px-2.5 py-1 transition-all duration-150 text-xs font-medium rounded-md select-none cursor-pointer ${
                      active
                        ? "bg-indigo-600 text-white shadow-sm"
                        : "text-gray-500 hover:text-gray-200 hover:bg-gray-800/70"
                    }`}
                  >
                    {label}
                  </button>
                );
              })}
            </div>

            {/* Forex selected but unavailable for this backtest. */}
            {showFx && !fxData && (
              <div className="flex-1 flex items-center justify-center pt-20 px-8 text-center">
                <span className="text-sm text-gray-500 max-w-md">
                  {fxQuery.isLoading
                    ? "Loading forex execution…"
                    : "No forex execution for this backtest. Forex re-pricing covers NQ trades within the tick window 2026-01-01 → 2026-06-26."}
                </span>
              </div>
            )}

            {activeTab === "analysis" && rep && (
              <div className="px-8 pb-8 pt-20 overflow-y-auto flex-1">
                <div className="grid grid-cols-2 gap-6 max-w-5xl mx-auto">
                  {/* Left Column */}
                  <div className="space-y-6">
                    <Section title="Overview">
                      {(rep.symbol.toLowerCase() === 'combined' || rep.strategy.includes(' + ')) && (
                        <StatRow label="Strategies" value={rep.strategy} />
                      )}
                      <StatRow label="Symbol" value={rep.symbol.toUpperCase()} />
                      <StatRow
                        label="Period"
                        value={`${fmtDate(rep.first_ts)} → ${fmtDate(rep.last_ts)}`}
                      />
                      <StatRow
                        label="Total Days"
                        value={String(rep.total_days)}
                      />
                      <StatRow
                        label="Number of Trades"
                        value={String(rep.num_trades)}
                      />
                    </Section>

                    <Section title="Balance">
                      <StatRow
                        label="Initial Balance"
                        value={fmt$(rep.initial_bal)}
                      />
                      <StatRow
                        label="Final Balance"
                        value={`${fmt$(rep.final_bal)} (${rep.net_growth >= 0 ? "+" : ""}${fmtPct(rep.net_growth)})`}
                        color={
                          rep.final_bal >= rep.initial_bal
                            ? "text-emerald-400"
                            : "text-red-400"
                        }
                      />
                    </Section>

                    <Section title="Average Returns">
                      <StatRow
                        label="Weekly"
                        value={`${fmt$(rep.avg_weekly)} (${fmtPct(rep.avg_weekly_pct)})`}
                        color={
                          rep.avg_weekly >= 0
                            ? "text-emerald-400"
                            : "text-red-400"
                        }
                      />
                      <StatRow
                        label="Monthly"
                        value={`${fmt$(rep.avg_monthly)} (${fmtPct(rep.avg_monthly_pct)})`}
                        color={
                          rep.avg_monthly >= 0
                            ? "text-emerald-400"
                            : "text-red-400"
                        }
                      />
                    </Section>

                    <Section title="Performance Ratios">
                      <StatRow
                        label="Sharpe Ratio"
                        value={rep.sharpe.toFixed(2)}
                        color={
                          rep.sharpe >= 1
                            ? "text-emerald-400"
                            : rep.sharpe >= 0
                              ? "text-gray-200"
                              : "text-red-400"
                        }
                      />
                      <StatRow
                        label="Profit Factor"
                        value={rep.profit_factor.toFixed(2)}
                        color={
                          rep.profit_factor >= 1
                            ? "text-emerald-400"
                            : "text-red-400"
                        }
                      />
                      <StatRow
                        label="Expectancy"
                        value={fmt$(rep.expectancy)}
                        color={
                          rep.expectancy >= 0
                            ? "text-emerald-400"
                            : "text-red-400"
                        }
                      />
                    </Section>
                  </div>

                  {/* Right Column */}
                  <div className="space-y-6">
                    <Section title="Win / Loss">
                      <StatRow
                        label="Win Rate"
                        value={`${fmtPct(rep.win_rate, 1)} (${rep.win_count}/${rep.num_trades})`}
                        color={
                          rep.win_rate >= 50 ? "text-emerald-400" : "text-red-400"
                        }
                      />
                      <StatRow
                        label="Total Wins"
                        value={fmt$(rep.total_win)}
                        color="text-emerald-400"
                      />
                      <StatRow
                        label="Total Losses"
                        value={fmt$(rep.total_loss)}
                        color="text-red-400"
                      />
                      <StatRow
                        label="Max Losing Streak"
                        value={String(rep.max_lose_streak)}
                      />
                    </Section>

                    <Section title="Position Sizing">
                      <StatRow
                        label="Size"
                        value={`${rep.avg_size.toFixed(1)} (Min: ${rep.min_size.toFixed(1)} / Max: ${rep.max_size.toFixed(1)})`}
                      />
                    </Section>

                    <Section title="Drawdown & Loss">
                      <StatRow
                        label="Max Drawdown"
                        value={
                          <>
                            <span className="text-red-400">
                              {fmtPct(rep.max_drawdown)} (
                              {fmt$(rep.max_drawdown_dollars)})
                            </span>
                            {rep.max_drawdown_peak_date && (
                              <span className="text-white">
                                {" "}
                                [{fmtDate(rep.max_drawdown_peak_date)} →{" "}
                                {fmtDate(rep.max_drawdown_trough_date)}]
                              </span>
                            )}
                          </>
                        }
                      />
                      <StatRow
                        label="Avg Drawdown"
                        value={`${fmtPct(rep.avg_drawdown)} (${fmt$(rep.avg_drawdown_dollars)})`}
                        color="text-red-400"
                      />
                      <StatRow
                        label="Max Intraday DD"
                        value={
                          <>
                            <span className="text-red-400">
                              {fmtPct(rep.max_intraday_drawdown)} (
                              {fmt$(rep.max_intraday_drawdown_dollars)})
                            </span>
                            {rep.max_intraday_drawdown_date && (
                              <span className="text-white">
                                {" "}
                                [{fmtDate(rep.max_intraday_drawdown_date)}]
                              </span>
                            )}
                          </>
                        }
                      />
                      <StatRow
                        label="Avg Intraday DD"
                        value={`${fmtPct(rep.avg_intraday_drawdown)} (${fmt$(rep.avg_intraday_drawdown_dollars)})`}
                        color="text-red-400"
                      />
                      <StatRow
                        label="Max Daily Loss"
                        value={
                          <>
                            <span className="text-red-400">
                              {fmt$(rep.max_daily_loss)}
                            </span>
                            {rep.max_daily_loss_date && (
                              <span className="text-white">
                                {" "}
                                [{fmtDate(rep.max_daily_loss_date)}]
                              </span>
                            )}
                          </>
                        }
                      />
                      <StatRow
                        label="Avg Daily Loss"
                        value={fmt$(rep.avg_daily_loss)}
                        color="text-red-400"
                      />
                    </Section>
                  </div>
                </div>
              </div>
            )}

            {activeTab === "analysis" && selectedBacktestId !== null && !b && (
              <div className="flex-1 flex items-center justify-center py-20">
                <span className="text-sm text-gray-600">Loading...</span>
              </div>
            )}

            {activeTab === "equity" && rep && (
              <div className="flex-1 min-h-0 w-full relative">
                {tradesLoadingNow ? (
                  <div className="absolute inset-0 flex items-center justify-center">
                    <span className="text-sm text-gray-400">
                      Loading trades...
                    </span>
                  </div>
                ) : tradesErrorNow ? (
                  <div className="absolute inset-0 flex items-center justify-center text-red-400 text-xs">
                    Error loading trades: {errorObj?.message}
                  </div>
                ) : viewTrades ? (
                  <EquityChart trades={viewTrades} initialBalance={rep.initial_bal} startDate={rep.first_ts?.slice(0, 10)} />
                ) : (
                  <div className="absolute inset-0 flex items-center justify-center">
                    <span className="text-sm text-gray-500">
                      No trades data available.
                    </span>
                  </div>
                )}
              </div>
            )}

            {activeTab === "splicing" && rep && (
              <div className="flex-1 min-h-0 w-full relative overflow-y-auto px-8 pb-8 pt-20">
                {tradesLoadingNow ? (
                  <div className="text-sm text-gray-400">Loading trades...</div>
                ) : tradesErrorNow ? (
                  <div className="text-red-400 text-xs">Error loading trades: {errorObj?.message}</div>
                ) : viewTrades && viewTrades.length > 0 ? (
                  <Splicing trades={viewTrades} initialBalance={rep.initial_bal} />
                ) : (
                  <div className="text-sm text-gray-500">No trades data available.</div>
                )}
              </div>
            )}

            {activeTab === "monte-carlo" && rep && (
              showFx ? (
                fxData!.monteCarlo ? (
                  <MonteCarloView data={fxData!.monteCarlo} />
                ) : (
                  <div className="flex-1 flex items-center justify-center">
                    <span className="text-sm text-gray-600">Not enough fx trades for a Monte Carlo simulation.</span>
                  </div>
                )
              ) : (
                <MonteCarloTab backtestId={selectedBacktestId!} />
              )
            )}
          </div>
        )}
      </div>
    </div>
  );
}
