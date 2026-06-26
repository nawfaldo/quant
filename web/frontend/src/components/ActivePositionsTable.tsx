import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchActivePositions,
  fetchLiveTradeHistory,
  type LiveTrade,
} from "../api";

type FilterRange = "1day" | "1week" | "1month" | "1year" | "all";

// Helper to calculate profit of a completed live trade in USD.
// Standard CFD contract size for USTEC/NQ is 10 index units on Exness.
const getTradeProfit = (t: LiveTrade) => {
  const entry = t.mt5_entry_price > 0 ? t.mt5_entry_price : t.zig_entry_price;
  const exit = t.mt5_close_price > 0 ? t.mt5_close_price : t.zig_close_price;
  if (!entry || !exit) return 0;
  const diff = t.side === "long" ? exit - entry : entry - exit;
  return diff * t.contract * 10;
};

export default function ActivePositionsTable() {
  const [tab, setTab] = useState<"active" | "history">("active");
  const [filterRange, setFilterRange] = useState<FilterRange>("1day");

  const {
    data: positions,
    isLoading: loadingPositions,
    error: errorPositions,
  } = useQuery({
    queryKey: ["activePositions"],
    queryFn: fetchActivePositions,
    refetchInterval: tab === "active" ? 2000 : false,
    enabled: tab === "active",
    retry: false,
  });

  const {
    data: history,
    isLoading: loadingHistory,
    error: errorHistory,
  } = useQuery({
    queryKey: ["liveTradeHistory"],
    queryFn: fetchLiveTradeHistory,
    refetchInterval: tab === "history" ? 5000 : false,
    enabled: tab === "history",
    retry: false,
  });

  const isLoading = tab === "active" ? loadingPositions : loadingHistory;
  const error = tab === "active" ? errorPositions : errorHistory;

  const getFilteredHistory = () => {
    if (!history) return [];

    // Filter out active trades (i.e. those with empty zig_close_time)
    const closedHistory = history.filter((t) => t.zig_close_time !== "");

    if (filterRange === "all") return closedHistory;

    const now = new Date();
    let cutoffTime = now.getTime();

    if (filterRange === "1day") {
      cutoffTime -= 24 * 60 * 60 * 1000;
    } else if (filterRange === "1week") {
      cutoffTime -= 7 * 24 * 60 * 60 * 1000;
    } else if (filterRange === "1month") {
      cutoffTime -= 30 * 24 * 60 * 60 * 1000;
    } else if (filterRange === "1year") {
      cutoffTime -= 365 * 24 * 60 * 60 * 1000;
    }

    return closedHistory.filter((t) => {
      const timeStr = t.zig_close_time;
      if (!timeStr) return false;

      let tradeTime = NaN;
      try {
        const cleaned = timeStr.trim();
        const parts = cleaned.split(/[- :.]/);
        if (parts.length >= 6) {
          const year = parseInt(parts[0], 10);
          const month = parseInt(parts[1], 10) - 1;
          const day = parseInt(parts[2], 10);
          const hour = parseInt(parts[3], 10);
          const minute = parseInt(parts[4], 10);
          const second = parseInt(parts[5], 10);
          const ms = parts[6]
            ? parseInt(parts[6].padEnd(3, "0").slice(0, 3), 10)
            : 0;
          tradeTime = Date.UTC(year, month, day, hour, minute, second, ms);
        } else {
          const utcString =
            cleaned.replace(" ", "T") + (cleaned.endsWith("Z") ? "" : "Z");
          tradeTime = new Date(utcString).getTime();
        }
      } catch (err) {
        console.error("Error parsing trade date:", timeStr, err);
      }

      if (isNaN(tradeTime)) {
        console.warn("Skipping trade parsing due to NaN timestamp:", timeStr);
        return true; // Keep it by default if parsing failed
      }

      return tradeTime >= cutoffTime;
    });
  };

  const filteredHistory = getFilteredHistory();
  const completedTrades = filteredHistory.filter(
    (t) => t.zig_close_time !== "",
  );
  const totalProfit = completedTrades.reduce(
    (sum, t) => sum + getTradeProfit(t),
    0,
  );
  const wins = completedTrades.filter((t) => getTradeProfit(t) > 0).length;
  const winRate =
    completedTrades.length > 0 ? (wins / completedTrades.length) * 100 : 0;

  return (
    <div className="flex-1 h-full flex flex-col min-w-0 bg-gray-950/20 font-sans select-none overflow-hidden">
      {/* Header Tabs */}
      <div className="flex items-center justify-start px-6 pt-3 pb-2 bg-transparent shrink-0 gap-4">
        <div className="flex items-center gap-0.5 bg-gray-900 rounded-lg p-0.5 border border-gray-800/80 shrink-0">
          <button
            onClick={() => setTab("active")}
            className={`px-3 py-1 text-xs font-medium rounded-md transition-all duration-150 ${
              tab === "active"
                ? "bg-gray-700 text-white shadow-sm"
                : "text-gray-500 hover:text-gray-200 hover:bg-gray-800/70"
            }`}
          >
            Active
          </button>
          <button
            onClick={() => setTab("history")}
            className={`px-3 py-1 text-xs font-medium rounded-md transition-all duration-150 ${
              tab === "history"
                ? "bg-gray-700 text-white shadow-sm"
                : "text-gray-500 hover:text-gray-200 hover:bg-gray-800/70"
            }`}
          >
            History
          </button>
        </div>

        {tab === "active" &&
          !loadingPositions &&
          (!positions || positions.length === 0) && (
            <span className="text-xs text-gray-600 font-medium">
              No active trades running
            </span>
          )}

        {/* History filter dropdown and stats */}
        {tab === "history" && (
          <div className="flex items-center gap-3 shrink-0">
            <select
              value={filterRange}
              onChange={(e) => setFilterRange(e.target.value as FilterRange)}
              className="bg-gray-900 border border-gray-800/80 text-xs font-medium text-gray-200 rounded-lg px-2.5 py-1.5 outline-none cursor-pointer hover:border-gray-700 transition-colors shrink-0"
            >
              <option value="1day">1 day</option>
              <option value="1week">1 week</option>
              <option value="1month">1 month</option>
              <option value="1year">1 year</option>
              <option value="all">all time</option>
            </select>

            <div className="flex items-center gap-3 text-xs">
              <span className="text-gray-500 font-normal">
                PnL:{" "}
                <span
                  className={`font-mono font-semibold ${
                    totalProfit >= 0 ? "text-emerald-400" : "text-red-400"
                  }`}
                >
                  {totalProfit >= 0 ? "+" : ""}
                  {totalProfit.toLocaleString(undefined, {
                    style: "currency",
                    currency: "USD",
                  })}
                </span>
              </span>
              <span className="text-gray-500 font-normal">
                Win Rate:{" "}
                <span className="font-semibold text-white">
                  {winRate.toFixed(1)}%
                </span>
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {isLoading && (
          <div className="h-full flex items-center justify-center text-xs text-gray-500 italic">
            {tab === "active"
              ? "Fetching active positions..."
              : "Loading trade history..."}
          </div>
        )}

        {error && (
          <div className="h-full flex flex-col items-center justify-center gap-1.5 text-xs text-red-500/80">
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              className="text-red-500/60"
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            <span>Execution server offline</span>
          </div>
        )}

        {!isLoading && !error && (
          <>
            {/* Tab: Active */}
            {tab === "active" && positions && positions.length > 0 && (
              <div className="px-6 pb-6 pt-1.5 overflow-x-auto min-w-full">
                <div className="border border-gray-900/60 rounded-xl bg-gray-950/40 overflow-hidden shadow-2xl shadow-black/40">
                  <table className="min-w-full table-fixed text-left border-collapse text-xs">
                    <thead>
                      <tr className="bg-gray-900/40 border-b border-gray-900/60 text-gray-400 font-medium tracking-wide text-[10px] uppercase select-none">
                        <th className="py-3 pl-6 w-[16%]">Account</th>
                        <th className="py-3 px-3 w-[14%]">Strategy</th>
                        <th className="py-3 px-3 w-[12%]">Symbol</th>
                        <th className="py-3 px-3 w-[12%]">Type</th>
                        <th className="py-3 px-3 w-[12%]">Volume</th>
                        <th className="py-3 px-3 w-[16%]">Open Price</th>
                        <th className="py-3 px-3 w-[18%] font-semibold">
                          Profit
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-900/40 bg-gray-950/10">
                      {positions.map((pos) => {
                        const isProfit = pos.profit >= 0;

                        return (
                          <tr
                            key={pos.ticket}
                            className="hover:bg-gray-900/25 border-b border-gray-900/40 last:border-0 transition-colors text-gray-300"
                          >
                            {/* Account */}
                            <td className="py-3.5 pl-6">
                              <div
                                className="font-semibold text-gray-200 truncate max-w-[150px]"
                                title={pos.account_name}
                              >
                                {pos.account_name}
                              </div>
                              <div className="text-[10px] text-gray-500 mt-0.5 font-mono">
                                #{pos.account}
                              </div>
                            </td>

                            {/* Strategy */}
                            <td className="py-3.5 px-3">
                              {pos.strategy ? (
                                <span className="font-mono text-[11px] text-gray-300 bg-gray-900/60 border border-gray-800/60 rounded px-1.5 py-0.5 inline-block">
                                  {pos.strategy}
                                </span>
                              ) : (
                                <span className="text-gray-600 italic">—</span>
                              )}
                            </td>

                            {/* Symbol */}
                            <td className="py-3.5 px-3">
                              <span className="bg-gray-900 border border-gray-850 px-2 py-0.5 rounded text-gray-300 font-semibold font-mono text-[10px] tracking-wide uppercase inline-block">
                                {pos.symbol}
                              </span>
                            </td>

                            {/* Type */}
                            <td className="py-3.5 px-3">
                              <span
                                className={`inline-block px-2.5 py-0.5 rounded-full text-[10px] font-semibold tracking-wide uppercase border ${
                                  pos.type.toLowerCase() === "buy" ||
                                  pos.type.toLowerCase() === "long"
                                    ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                                    : "bg-rose-500/10 text-rose-400 border-rose-500/20"
                                }`}
                              >
                                {pos.type}
                              </span>
                            </td>

                            {/* Volume */}
                            <td className="py-3.5 px-3 font-mono font-medium text-gray-200">
                              {pos.volume.toFixed(2)}
                            </td>

                            {/* Open Price */}
                            <td className="py-3.5 px-3 font-mono text-gray-300">
                              {pos.open_price.toLocaleString(undefined, {
                                minimumFractionDigits: 2,
                                maximumFractionDigits: 2,
                              })}
                            </td>

                            {/* Profit */}
                            <td
                              className={`py-3.5 px-3 font-mono font-bold text-sm ${
                                isProfit ? "text-emerald-400" : "text-rose-400"
                              }`}
                            >
                              {isProfit ? "+" : ""}
                              {pos.profit.toLocaleString(undefined, {
                                style: "currency",
                                currency: "USD",
                              })}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Tab: History */}
            {tab === "history" && filteredHistory && (
              <div
                className={`px-6 pb-6 pt-1.5 overflow-x-auto min-w-full ${filteredHistory.length === 0 ? "h-full flex flex-col" : ""}`}
              >
                {filteredHistory.length > 0 ? (
                  <div className="border border-gray-900/60 rounded-xl bg-gray-950/40 overflow-hidden shadow-2xl shadow-black/40">
                    <table className="min-w-full table-fixed text-left border-collapse text-xs">
                      <thead>
                        <tr className="bg-gray-900/40 border-b border-gray-900/60 text-gray-400 font-medium tracking-wide text-[10px] uppercase select-none">
                          <th className="py-3 pl-6 w-[8%]">ID</th>
                          <th className="py-3 px-3 w-[16%]">Strategy</th>
                          <th className="py-3 px-3 w-[8%]">Side</th>
                          <th className="py-3 px-3 w-[8%]">Volume</th>
                          <th className="py-3 px-3 w-[12%]">Entry Price</th>
                          <th className="py-3 px-3 w-[12%]">Exit Price</th>
                          <th className="py-3 px-3 w-[12%]">Profit</th>
                          <th className="py-3 px-3 w-[24%]">
                            Time (Open → Close)
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-900/40 bg-gray-950/10">
                        {filteredHistory.map((t) => {
                          const openTimeStr = t.zig_open_time
                            ? t.zig_open_time.slice(5, 19)
                            : "—";
                          const closeTimeStr = t.zig_close_time
                            ? t.zig_close_time.slice(5, 19)
                            : "Active";
                          const tradeProfit = getTradeProfit(t);

                          return (
                            <tr
                              key={t.id}
                              className="hover:bg-gray-900/25 border-b border-gray-900/40 last:border-0 transition-colors text-gray-300"
                            >
                              {/* ID */}
                              <td className="py-3.5 pl-6 font-mono text-gray-400">
                                #{t.id}
                              </td>

                              {/* Strategy */}
                              <td className="py-3.5 px-3 font-mono text-xs text-gray-300 truncate">
                                <span
                                  className="font-semibold text-gray-200 block truncate"
                                  title={t.strategy_name}
                                >
                                  {t.strategy_name}
                                </span>
                              </td>

                              {/* Side */}
                              <td className="py-3.5 px-3 font-medium uppercase text-white">
                                {t.side}
                              </td>

                              {/* Volume */}
                              <td className="py-3.5 px-3 font-mono font-medium text-gray-200">
                                {t.contract.toFixed(2)}
                              </td>

                              {/* Entry Price */}
                              <td className="py-3.5 px-3 font-mono text-gray-300">
                                {t.zig_entry_price > 0
                                  ? t.zig_entry_price.toLocaleString(
                                      undefined,
                                      {
                                        minimumFractionDigits: 2,
                                        maximumFractionDigits: 2,
                                      },
                                    )
                                  : "—"}
                              </td>

                              {/* Exit Price */}
                              <td className="py-3.5 px-3 font-mono text-gray-400">
                                {t.zig_close_price > 0
                                  ? t.zig_close_price.toLocaleString(
                                      undefined,
                                      {
                                        minimumFractionDigits: 2,
                                        maximumFractionDigits: 2,
                                      },
                                    )
                                  : "—"}
                              </td>

                              {/* Profit */}
                              <td
                                className={`py-3.5 px-3 font-mono font-bold text-sm ${
                                  tradeProfit >= 0
                                    ? "text-emerald-400"
                                    : "text-rose-400"
                                }`}
                              >
                                {t.zig_close_time ? (
                                  <>
                                    {tradeProfit >= 0 ? "+" : ""}
                                    {tradeProfit.toLocaleString(undefined, {
                                      style: "currency",
                                      currency: "USD",
                                    })}
                                  </>
                                ) : (
                                  <span className="text-gray-500 italic">
                                    Active
                                  </span>
                                )}
                              </td>

                              {/* Time (Open -> Close) */}
                              <td className="py-3.5 px-3 font-mono text-gray-500 text-xs truncate">
                                {openTimeStr}{" "}
                                <span className="text-gray-700 pl-0.5 pr-0.5">
                                  →
                                </span>{" "}
                                {closeTimeStr}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="flex-1 flex flex-col items-center justify-center gap-1.5 text-xs text-gray-600">
                    <svg
                      width="18"
                      height="18"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      className="text-gray-700"
                    >
                      <rect x="3" y="3" width="18" height="18" rx="2" />
                      <path d="M21 9H3" />
                      <path d="M21 15H3" />
                      <path d="M12 3v18" />
                    </svg>
                    <span>No historical trades found in selected range</span>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
