import { useState, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useApp } from "../context/AppContext";
import {
  fetchMt5Accounts,
  fetchAccountStrategies,
  fetchAccountStatuses,
  deleteMt5Account,
  deleteAccountStrategy,
  setAccountStrategyActive,
  type AccountStatus,
} from "../api";

export default function AccountsTree() {
  const queryClient = useQueryClient();
  const { 
    selectedAccountId, 
    setSelectedAccountId, 
    setMarchAccountModalOpen, 
    setMarchStrategyModalOpen,
    visibleTradeStrategies,
    toggleTradeStrategy
  } = useApp();
  const [isAccountsExpanded, setIsAccountsExpanded] = useState(true);
  const [expandedAccountIds, setExpandedAccountIds] = useState<
    Record<number, boolean>
  >({});
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    accountId: number;
    strat: any;
  } | null>(null);

  useEffect(() => {
    const handleClose = () => setContextMenu(null);
    window.addEventListener("click", handleClose);
    window.addEventListener("contextmenu", handleClose);
    window.addEventListener("scroll", handleClose, true);
    return () => {
      window.removeEventListener("click", handleClose);
      window.removeEventListener("contextmenu", handleClose);
      window.removeEventListener("scroll", handleClose, true);
    };
  }, []);

  const {
    data: accounts,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["mt5Accounts"],
    queryFn: fetchMt5Accounts,
    refetchInterval: 10000,
  });

  // Live MT5 connection health, polled. Failure (Python server down) leaves the
  // map empty → rows show a grey "unavailable" dot rather than erroring.
  const { data: statuses } = useQuery({
    queryKey: ["mt5AccountStatuses"],
    queryFn: fetchAccountStatuses,
    refetchInterval: 10000,
    retry: false,
  });
  const statusById = new Map((statuses ?? []).map((s) => [s.account_id, s]));

  const toggleAccount = (id: number) => {
    setExpandedAccountIds((prev) => ({
      ...prev,
      [id]: !prev[id],
    }));
  };

  return (
    <div className="w-80 h-full bg-gray-950/40 select-none overflow-y-auto font-sans text-sm flex-shrink-0">
      {/* Root Node: Accounts */}
      <div className="flex flex-col gap-1 p-3">
        <div
          onClick={() => setIsAccountsExpanded(!isAccountsExpanded)}
          className="flex items-center justify-between py-1 px-1.5 hover:bg-gray-900/60 rounded cursor-pointer text-gray-300 hover:text-white transition-colors group"
        >
          <div className="flex items-center gap-1.5">
            {/* Chevron */}
            <div className="w-4 h-4 flex items-center justify-center shrink-0">
              {isAccountsExpanded ? (
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="text-gray-400"
                >
                  <polyline points="6 9 12 15 18 9" />
                </svg>
              ) : (
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="text-gray-400"
                >
                  <polyline points="9 18 15 12 9 6" />
                </svg>
              )}
            </div>

            {/* Group Icon (White) */}
            <svg
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="text-white shrink-0"
            >
              <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
              <circle cx="9" cy="7" r="4" />
              <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
              <path d="M16 3.13a4 4 0 0 1 0 7.75" />
            </svg>

            {/* Label */}
            <span className="font-semibold tracking-wide text-gray-200">
              Accounts
            </span>
          </div>

          {/* Plus button to add Account */}
          <button
            onClick={(e) => {
              e.stopPropagation();
              setMarchAccountModalOpen(true);
            }}
            className="w-5 h-5 flex items-center justify-center rounded text-gray-400 hover:text-emerald-400 hover:bg-gray-800 transition-colors opacity-0 group-hover:opacity-100"
            title="Add Account"
          >
            <svg
              width="11"
              height="11"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </button>
        </div>

        {/* Child Accounts List */}
        {isAccountsExpanded && (
          <div className="pl-[7px] border-l border-dashed border-gray-800/80 ml-3.5 flex flex-col gap-1 mt-0.5">
            {isLoading && (
              <div className="pl-6 py-1 text-xs text-gray-500 italic">
                Loading accounts...
              </div>
            )}
            {error && (
              <div className="pl-6 py-1 text-xs text-red-500/80">
                Failed to load accounts
              </div>
            )}
            {!isLoading && !error && accounts && accounts.length === 0 && (
              <div className="pl-6 py-1 text-xs text-gray-600 italic">
                No accounts added
              </div>
            )}
            {!isLoading &&
              !error &&
              accounts &&
              accounts.map((account) => {
                const isExpanded = !!expandedAccountIds[account.id];
                const isSelected = selectedAccountId === account.id;

                return (
                  <div key={account.id} className="flex flex-col gap-1">
                    {/* Account Node */}
                    <div
                      onClick={() => {
                        setSelectedAccountId(account.id);
                        toggleAccount(account.id);
                      }}
                      className={`flex items-center justify-between py-1 px-1.5 rounded cursor-pointer transition-all duration-150 group hover:bg-gray-900/40 ${
                        isSelected
                          ? "text-white font-semibold"
                          : "text-gray-400 hover:text-gray-200"
                      }`}
                    >
                      <div className="flex items-center gap-1.5 min-w-0">
                        {/* Chevron */}
                        <div className="w-4 h-4 flex items-center justify-center shrink-0">
                          {isExpanded ? (
                            <svg
                              width="11"
                              height="11"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2.5"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              className="text-gray-500"
                            >
                              <polyline points="6 9 12 15 18 9" />
                            </svg>
                          ) : (
                            <svg
                              width="11"
                              height="11"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2.5"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              className="text-gray-500"
                            >
                              <polyline points="9 18 15 12 9 6" />
                            </svg>
                          )}
                        </div>

                        {/* Server/Monitor Icon (White) */}
                        <svg
                          width="14"
                          height="14"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2.5"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          className="text-white shrink-0"
                        >
                          <rect
                            x="2"
                            y="3"
                            width="20"
                            height="14"
                            rx="2"
                            ry="2"
                          />
                          <line x1="8" y1="21" x2="16" y2="21" />
                          <line x1="12" y1="17" x2="12" y2="21" />
                        </svg>

                        {/* Label */}
                        <span className="truncate flex items-baseline gap-1.5 min-w-0">
                          <span className="truncate">{account.name}</span>
                        </span>

                        {/* Connection status dot */}
                        <AccountStatusDot status={statusById.get(account.id)} />

                        {/* Balance and Equity */}
                        {(() => {
                          const st = statusById.get(account.id);
                          if (st && st.status === "ready" && st.balance !== undefined && st.equity !== undefined) {
                            const currencySymbol = st.currency === "USD" || !st.currency ? "$" : st.currency + " ";
                            const formattedBal = st.balance.toLocaleString(undefined, {
                              minimumFractionDigits: 2,
                              maximumFractionDigits: 2,
                            });
                            const formattedEq = st.equity.toLocaleString(undefined, {
                              minimumFractionDigits: 2,
                              maximumFractionDigits: 2,
                            });
                            return (
                              <span className={`text-[11px] font-normal tracking-normal shrink-0 ${isSelected ? 'text-gray-300' : 'text-gray-500'}`}>
                                {currencySymbol}{formattedBal} {currencySymbol}{formattedEq}
                              </span>
                            );
                          }
                          return null;
                        })()}
                      </div>

                      {/* Actions: Add Strategy / Delete Account */}
                      <div className="flex items-center gap-0.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                        {/* Plus button to add strategy */}
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelectedAccountId(account.id);
                            setMarchStrategyModalOpen(true);
                          }}
                          className="w-5 h-5 flex items-center justify-center rounded text-gray-400 hover:text-emerald-400 hover:bg-gray-800 transition-colors"
                          title="Add Strategy"
                        >
                          <svg
                            width="11"
                            height="11"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2.5"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          >
                            <line x1="12" y1="5" x2="12" y2="19" />
                            <line x1="5" y1="12" x2="19" y2="12" />
                          </svg>
                        </button>

                        {/* Trashcan button to delete account */}
                        <button
                          onClick={async (e) => {
                            e.stopPropagation();
                            if (confirm(`Delete account "${account.name}"?`)) {
                              try {
                                await deleteMt5Account(account.id);
                                queryClient.invalidateQueries({ queryKey: ["mt5Accounts"] });
                                if (selectedAccountId === account.id) {
                                  setSelectedAccountId(null);
                                }
                              } catch (err) {
                                console.error("Failed to delete account:", err);
                              }
                            }
                          }}
                          className="w-5 h-5 flex items-center justify-center rounded text-gray-400 hover:text-red-400 hover:bg-gray-800 transition-colors"
                          title="Delete Account"
                        >
                          <TrashIcon />
                        </button>
                      </div>
                    </div>

                    {/* Account Strategies Subtree */}
                    {isExpanded && (
                      <div className="pl-[7px] border-l border-dashed border-gray-800/80 ml-3.5 mt-0.5">
                        <AccountStrategiesTree
                          accountId={account.id}
                          onContextMenu={(e, strat) => {
                            e.preventDefault();
                            e.stopPropagation();
                            const menuWidth = 190;
                            const menuHeight = 36;
                            let x = e.clientX;
                            let y = e.clientY;

                            if (x + menuWidth > window.innerWidth) {
                              x = window.innerWidth - menuWidth - 8;
                            }
                            if (y + menuHeight > window.innerHeight) {
                              y = window.innerHeight - menuHeight - 8;
                            }
                            setContextMenu({ x, y, accountId: account.id, strat });
                          }}
                        />
                      </div>
                    )}
                  </div>
                );
              })}
          </div>
        )}
      </div>

      {/* Context Menu */}
      {contextMenu && (
        <div
          className="fixed z-50 bg-gray-900/95 backdrop-blur-md border border-gray-800/80 rounded-lg shadow-xl shadow-black/60 py-0.5 font-sans text-xs text-gray-300 select-none transition-all duration-100 ease-out"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            onClick={() => {
              toggleTradeStrategy(contextMenu.strat.strategy);
              setContextMenu(null);
            }}
            className="w-full px-4 py-2 text-left hover:bg-blue-600/20 hover:text-white transition-colors duration-150 cursor-pointer whitespace-nowrap"
          >
            {visibleTradeStrategies.has(contextMenu.strat.strategy)
              ? "Hide Historical Trades"
              : "Show Historical Trades"}
          </button>
        </div>
      )}
    </div>
  );
}

function AccountStrategiesTree({
  accountId,
  onContextMenu,
}: {
  accountId: number;
  onContextMenu: (e: React.MouseEvent, strat: any) => void;
}) {
  const queryClient = useQueryClient();
  const { visibleTradeStrategies } = useApp();
  const { data: strategies, isLoading, error } = useQuery({
    queryKey: ["accountStrategies", accountId],
    queryFn: () => fetchAccountStrategies(accountId),
  });

  if (isLoading) {
    return (
      <div className="pl-6 py-1 text-xs text-gray-500 italic">
        Loading strategies...
      </div>
    );
  }

  if (error) {
    return (
      <div className="pl-6 py-1 text-xs text-red-500/80">
        Failed to load strategies
      </div>
    );
  }

  if (!strategies || strategies.length === 0) {
    return (
      <div className="pl-6 py-1 text-xs text-gray-600 italic">
        No strategies
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1 py-0.5">
      {strategies.map((strat) => {
        const isTradeVisible = visibleTradeStrategies.has(strat.strategy);
        return (
          <div
            key={strat.id}
            onContextMenu={(e) => {
              onContextMenu(e, strat);
            }}
            className={`flex items-center justify-between py-0.5 px-2 hover:bg-gray-900/30 rounded text-xs transition-colors group cursor-pointer border ${
              isTradeVisible
                ? "bg-blue-500/10 text-blue-300 border-blue-500/30 hover:bg-blue-500/20"
                : "text-gray-400 hover:text-gray-300 border-transparent"
            }`}
            title="Right-click for options"
          >
            <div className="flex items-center gap-1.5 min-w-0">
              {/* Strategy name */}
              <span className="font-mono truncate">{strat.strategy}</span>

              {/* Symbol badge if exists */}
              {strat.symbol && (
                <span className="text-[9px] uppercase font-semibold px-1 py-0.2 bg-gray-900 border border-gray-800 text-gray-500 rounded shrink-0">
                  {strat.symbol}
                </span>
              )}

              {/* Active status indicator dot */}
              <span
                className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                  strat.active
                    ? "bg-emerald-400 shadow-sm shadow-emerald-400/50"
                    : "bg-gray-600"
                }`}
              />
            </div>

            <div className="flex items-center gap-1 shrink-0">
              {/* On and Off toggle button */}
              <button
                onClick={async (e) => {
                  e.stopPropagation();
                  try {
                    await setAccountStrategyActive(accountId, strat.id, !strat.active);
                    queryClient.invalidateQueries({ queryKey: ["accountStrategies", accountId] });
                  } catch (err) {
                    console.error("Failed to toggle strategy:", err);
                  }
                }}
                className={`flex items-center gap-1 px-1.5 py-0.5 text-[9px] font-medium rounded opacity-0 group-hover:opacity-100 transition-all duration-150 ${
                  strat.active
                    ? "bg-emerald-500/15 text-emerald-400 hover:bg-emerald-500/25 border border-emerald-500/30"
                    : "text-gray-500 hover:text-gray-300 hover:bg-gray-800/70 border border-gray-800"
                }`}
                title={strat.active ? "Turn off strategy" : "Turn on strategy"}
              >
                {strat.active ? "Turn off" : "Turn on"}
              </button>

              {/* Delete strategy button */}
              <button
                onClick={async (e) => {
                  e.stopPropagation();
                  if (confirm(`Delete strategy "${strat.strategy}"?`)) {
                    try {
                      await deleteAccountStrategy(accountId, strat.id);
                      queryClient.invalidateQueries({ queryKey: ["accountStrategies", accountId] });
                    } catch (err) {
                      console.error("Failed to delete strategy:", err);
                    }
                  }
                }}
                className="w-4 h-4 flex items-center justify-center rounded text-gray-500 hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100"
                title="Delete Strategy"
              >
                <TrashIcon />
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// Small colored dot reflecting an account's live MT5 connection. `undefined`
// (no entry in the status map) means the check hasn't returned yet or the
// Python server is unreachable → grey "unavailable".
function AccountStatusDot({ status }: { status?: AccountStatus }) {
  const kind = status?.status ?? "unknown";
  const config: Record<string, { dot: string; label: string }> = {
    ready: {
      dot: "bg-emerald-400 shadow-sm shadow-emerald-400/50",
      label: "Connected & ready",
    },
    incomplete: {
      dot: "bg-amber-400 shadow-sm shadow-amber-400/50",
      label: "Incomplete credentials",
    },
    error: {
      dot: "bg-red-500 shadow-sm shadow-red-500/50",
      label: "Connection error",
    },
    offline: { dot: "bg-gray-600", label: "MT5 terminal offline" },
    unknown: { dot: "bg-gray-600", label: "Status unavailable" },
  };
  const c = config[kind] ?? config.unknown;
  const title = status?.detail ? `${c.label} — ${status.detail}` : c.label;

  return (
    <span
      title={title}
      className={`w-1.5 h-1.5 rounded-full shrink-0 ${c.dot}`}
    />
  );
}

function TrashIcon() {
  return (
    <svg 
      width="11" 
      height="11" 
      viewBox="0 0 24 24" 
      fill="none" 
      stroke="currentColor" 
      strokeWidth="2"
      strokeLinecap="round" 
      strokeLinejoin="round"
    >
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  );
}
