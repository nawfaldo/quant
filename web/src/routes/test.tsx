import { useState, useMemo, useEffect, useRef } from "react";
import { createFileRoute } from "@tanstack/react-router";
import EquityChart from "../components/charts/EquityChart";
import MonteCarloChart from "../components/charts/MonteCarloChart";
import Splicing from "../components/backtests/Splicing";
import LiquidGlassTabs from "../components/navigation/LiquidGlassTabs";
import Volatility from "../components/backtests/Volatility";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { runBacktest, saveRun, combineBacktests, saveCombine, fetchBacktests, fetchEnvironments, fetchEnvironmentStrategies, runTune, fetchTuneStatus, type RunParams, type TuneResult } from "../api";
import { useApp } from "../context/AppContext";
import { isFuturesInstrument } from "../types";
import PageShell from "../components/layout/PageShell";

export const Route = createFileRoute('/test')({
  component: TestRouteComponent,
})

function TestRouteComponent() {
  return (
    <PageShell minHeight>
      <TestPage />
    </PageShell>
  )
}

interface TestTab {
  id: string;
  title: string;
  selectedCommand: string;
  selectedEnvironmentId: string;
  selectedStrategy: string;
  selectedSymbol: string;
  selectedInstrument: string;
  initialBalance: string;
  baseLot: string;
  leverage: string;
  sizing: string;
  fromDate: string;
  toDate: string;
  volTarget: string;
  volHalflife: string;
  volMaxMult: string;
  volMinDays: string;
  hasResult: boolean;
  isSaved: boolean;
  activeTab: "analysis" | "equity" | "splicing" | "volatility" | "monte-carlo";
  combineBacktestIds: string[];
}

function fmt$(val: string | number) {
  const num = typeof val === "string" ? parseFloat(val) : val;
  if (isNaN(num)) return "$0.00";
  return "$" + num.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function fmtPct(v: number, decimals = 2) {
  return v.toFixed(decimals) + "%";
}

function fmtDate(ts: string) {
  return ts ? ts.split(" ")[0] : "";
}

function questionLabel(label: string, filled: boolean) {
  return filled ? label : `${label}?`;
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
      <h3 className="text-[10px] font-semibold tracking-widest uppercase text-gray-500 mb-2 select-none">
        {title}
      </h3>
      <div className="bg-[#1A1A1E] border border-[#212124] px-4 py-1">
        {children}
      </div>
    </div>
  );
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
    <div className="flex items-start justify-between py-2 border-b border-gray-800/40 last:border-b-0 gap-4">
      <span className="text-xs text-gray-500 shrink-0 whitespace-nowrap pt-0.5">{label}</span>
      <span className={`text-xs font-mono font-medium text-right ${color ?? "text-gray-200"}`}>
        {value}
      </span>
    </div>
  );
}

export default function TestPage() {
  const {
    testResults: results,
    setTestResults: setResults,
    testErrors: errors,
    setTestErrors: setErrors,
    tuneResults,
    setTuneResults,
    testLoading,
    setTestLoading,
    testTuneProgress,
    setTestTuneProgress,
  } = useApp()

  const [tabs, setTabs] = useState<TestTab[]>(() => {
    const saved = sessionStorage.getItem("test_page_tabs");
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed) && parsed.length > 0) {
          // Migrate stale tabs
          return parsed.map((t: any) => {
            let migrated = { ...t };
            if (migrated.volMaxMult === undefined && migrated.volMaxDays !== undefined) {
              const { volMaxDays, ...rest } = migrated;
              migrated = { ...rest, volMaxMult: volMaxDays };
            }
            if (migrated.volMaxMult === undefined) {
              migrated.volMaxMult = "";
            }
            if (migrated.leverage === undefined) {
              migrated.leverage = "";
            }
            if (migrated.selectedEnvironmentId === undefined) {
              migrated.selectedEnvironmentId = "";
            }
            if (migrated.selectedInstrument === undefined) {
              migrated.selectedInstrument = typeof migrated.instrument === "string" ? migrated.instrument : "";
            }
            delete migrated.instrument;
            delete migrated.mimMode;
            delete migrated.spread;
            delete migrated.slippage;
            if (migrated.combineBacktestIds === undefined) {
              migrated.combineBacktestIds = [
                migrated.combineFirstBacktestId || "",
                migrated.combineSecondBacktestId || ""
              ];
              delete migrated.combineFirstBacktestId;
              delete migrated.combineSecondBacktestId;
            }
            return migrated;
          });
        }
      } catch (e) {
        console.error(e);
      }
    }
    return [
      {
        id: "tab-1",
        title: "Backtest 1",
        selectedCommand: "",
        selectedEnvironmentId: "",
        selectedStrategy: "",
        selectedSymbol: "",
        selectedInstrument: "",
        initialBalance: "",
        baseLot: "",
        leverage: "",
        sizing: "",
        fromDate: "",
        toDate: "",
        volTarget: "",
        volHalflife: "",
        volMaxMult: "",
        volMinDays: "",
        hasResult: false,
        isSaved: false,
        activeTab: "analysis",
        combineBacktestIds: ["", ""],
      },
    ];
  });

  const [activeTabId, setActiveTabId] = useState<string>(() => {
    const saved = sessionStorage.getItem("test_page_active_tab_id");
    return saved || "tab-1";
  });

  // Which execution view the result panel shows: native (nq fills) or the
  // fx-re-priced book (signals from nq bars, fills from fx_nq_ticks).
  const [execView, setExecView] = useState<"native" | "fx">("native");

  const [savingTabs, setSavingTabs] = useState<Record<string, boolean>>({});

  const queryClient = useQueryClient();

  const { data: backtests } = useQuery({
    queryKey: ["backtests"],
    queryFn: fetchBacktests,
    staleTime: Infinity,
  });

  const { data: environments = [], isLoading: isEnvironmentsLoading } = useQuery({
    queryKey: ["environments"],
    queryFn: fetchEnvironments,
    staleTime: Infinity,
  });

  // Sync to sessionStorage
  useEffect(() => {
    sessionStorage.setItem("test_page_tabs", JSON.stringify(tabs));
  }, [tabs]);

  useEffect(() => {
    sessionStorage.setItem("test_page_active_tab_id", activeTabId);
  }, [activeTabId]);

  const activeTabObj = useMemo(() => {
    return tabs.find((t) => t.id === activeTabId) || tabs[0];
  }, [tabs, activeTabId]);

  const {
    selectedCommand,
    selectedEnvironmentId,
    selectedStrategy,
    selectedSymbol,
    selectedInstrument,
    initialBalance,
    baseLot,
    leverage,
    sizing,
    fromDate,
    toDate,
    volTarget,
    volHalflife,
    volMaxMult,
    volMinDays,
    hasResult: savedHasResult,
    isSaved,
    activeTab,
    combineBacktestIds,
  } = activeTabObj;

  const commands = ["Run", "Tune", "Combine"];
  // Internally sized strategies can't use the generic position-sizing tuner.
  const symbols = ["NQ", "ES"];
  const instruments = ["Forex", "Futures Mini", "Futures Micro"];
  const selectedEnvironment = environments.find(
    (environment) => String(environment.id) === selectedEnvironmentId,
  );
  // Internally sized strategies don't expose Base Lot / Leverage steps.
  // generic Sizing step (None / Vol Target) has been removed from the
  // wizard entirely — sizing always defaults to "" (backend treats that as
  // .none, see server/src/bt/run.rs).

  const isCommandFilled = selectedCommand !== "";
  const isRunOrTune = selectedCommand === "Run" || selectedCommand === "Tune";
  const isEnvironmentFilled = selectedEnvironment !== undefined;
  const { data: environmentStrategies = [], isLoading: isStrategiesLoading } = useQuery({
    queryKey: ["environment-strategies", selectedEnvironmentId],
    queryFn: () => fetchEnvironmentStrategies(Number(selectedEnvironmentId)),
    enabled: isEnvironmentFilled,
    staleTime: Infinity,
  });
  const strategies = selectedCommand === "Tune"
    ? environmentStrategies.filter((strategy) => strategy.id === "night_drift")
    : environmentStrategies;
  // Internally sized strategies own their lot/leverage calculation.
  const isNightDrift = selectedStrategy === "Night Drift";
  const isInternallySized =
    isNightDrift ||
    selectedStrategy.startsWith("Noise Momentum");
  const quantityFilled = isInternallySized || baseLot.trim() !== "";
  const showEnvironment = isCommandFilled;
  const showStrategy = isRunOrTune && isEnvironmentFilled;
  const showSymbol = showStrategy && selectedStrategy !== "";
  const showInstrument = showSymbol && selectedSymbol !== "";
  const showBalanceAndLot = showInstrument && selectedInstrument !== "";

  const showDate =
    showBalanceAndLot &&
    initialBalance.trim() !== "" &&
    quantityFilled;

  const showActions = showDate && fromDate.trim() !== "" && toDate.trim() !== "";

  // Circles dynamic fill state
  const isStrategyFilled = strategies.some((strategy) => strategy.name === selectedStrategy);
  const isSymbolFilled = selectedSymbol !== "";
  const isInstrumentFilled = selectedInstrument !== "";
  const isBalanceAndLotFilled =
    initialBalance.trim() !== "" && quantityFilled;
  const isDateFilled = fromDate.trim() !== "" && toDate.trim() !== "";

  const getTabTitle = (strategy: string, symbol: string, fallback: string) => {
    if (strategy && symbol) return `${strategy} (${symbol})`;
    if (strategy) return strategy;
    return fallback;
  };

  const updateActiveTab = (updates: Partial<TestTab>) => {
    setTabs((prev) =>
      prev.map((t) => (t.id === activeTabId ? { ...t, ...updates } : t))
    );
  };

  const addTab = () => {
    const nextId = "tab-" + Date.now();
    const newTab: TestTab = {
      id: nextId,
      title: `Backtest ${tabs.length + 1}`,
      selectedCommand: "",
      selectedEnvironmentId: "",
      selectedStrategy: "",
      selectedSymbol: "",
      selectedInstrument: "",
      initialBalance: "",
      baseLot: "",
      leverage: "",
      sizing: "",
      fromDate: "",
      toDate: "",
      volTarget: "",
      volHalflife: "",
      volMaxMult: "",
      volMinDays: "",
      hasResult: false,
      isSaved: false,
      activeTab: "analysis",
      combineBacktestIds: ["", ""],
    };
    setTabs((prev) => [...prev, newTab]);
    setActiveTabId(nextId);
  };

  const closeTab = (id: string) => {
    if (tabs.length <= 1) return;
    const closingIndex = tabs.findIndex((t) => t.id === id);
    const newTabs = tabs.filter((t) => t.id !== id);
    setTabs(newTabs);

    if (id === activeTabId) {
      const nextActiveIndex = closingIndex === 0 ? 0 : closingIndex - 1;
      setActiveTabId(newTabs[nextActiveIndex].id);
    }
  };

  const handleAddCombineStrategy = () => {
    updateActiveTab({
      combineBacktestIds: [...combineBacktestIds, ""],
    });
  };

  const handleRemoveCombineStrategy = (indexToRemove: number) => {
    if (combineBacktestIds.length <= 2) return;
    updateActiveTab({
      combineBacktestIds: combineBacktestIds.filter((_, idx) => idx !== indexToRemove),
    });
  };

  const draggedIdxRef = useRef<number | null>(null);
  const [activeDragIdx, setActiveDragIdx] = useState<number | null>(null);

  const handleDragStart = (e: React.DragEvent, index: number) => {
    draggedIdxRef.current = index;
    setActiveDragIdx(index);
    e.dataTransfer.effectAllowed = "move";
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
  };

  const handleDragEnter = (targetIndex: number) => {
    const sourceIndex = draggedIdxRef.current;
    if (sourceIndex === null || sourceIndex === targetIndex) return;

    setTabs((prev) => {
      const copy = [...prev];
      const temp = copy[sourceIndex];
      copy[sourceIndex] = copy[targetIndex];
      copy[targetIndex] = temp;
      return copy;
    });

    draggedIdxRef.current = targetIndex;
    setActiveDragIdx(targetIndex);
  };

  const handleDragEnd = () => {
    draggedIdxRef.current = null;
    setActiveDragIdx(null);
  };

  // The live run result for the active tab (null until "See Result" succeeds).
  const activeResult = results[activeTabId] ?? null;
  const activeError = errors[activeTabId] ?? null;
  const activeTuneResult: TuneResult | null = tuneResults[activeTabId] ?? null;
  // A result may finish after the Test route was unmounted. Its tab metadata is
  // persisted locally, but the result itself lives in AppContext, so derive the
  // visible state from both sources.
  const hasStoredResult = activeResult !== null || activeTuneResult !== null;
  const hasResult = savedHasResult || hasStoredResult;
  const isLoading = testLoading[activeTabId] ?? false;
  const tuneProgress = testTuneProgress[activeTabId] ?? null;

  const setTabLoading = (tabId: string, loading: boolean) => {
    setTestLoading((prev) => {
      if (loading) return { ...prev, [tabId]: true };
      const next = { ...prev };
      delete next[tabId];
      return next;
    });
  };

  const setTabTuneProgress = (
    tabId: string,
    progress: { progress: number; total: number } | null,
  ) => {
    setTestTuneProgress((prev) => {
      if (progress) return { ...prev, [tabId]: progress };
      const next = { ...prev };
      delete next[tabId];
      return next;
    });
  };

  // The current tab's wizard inputs as a backtest run request.
  const buildRunParams = (): RunParams => ({
    environmentId: selectedEnvironmentId,
    strategy: selectedCommand === "Run" ? selectedStrategy : selectedCommand,
    symbol: selectedSymbol,
    instrument: selectedInstrument,
    initialBalance,
    baseLot,
    leverage,
    sizing,
    volTarget,
    volHalflife,
    volMaxMult,
    volMinDays,
    fromDate,
    toDate,
  });

  const handleSeeResult = async () => {
    const tabId = activeTabId;
    updateActiveTab({ hasResult: false, isSaved: false, activeTab: "analysis" });
    setExecView("native");
    setResults((prev) => {
      const next = { ...prev };
      delete next[tabId];
      return next;
    });
    setErrors((prev) => {
      const next = { ...prev };
      delete next[tabId];
      return next;
    });
    setTuneResults((prev) => {
      const next = { ...prev };
      delete next[tabId];
      return next;
    });
    setTabLoading(tabId, true);

    if (selectedCommand === "Combine") {
      try {
        const result = await combineBacktests({
          environmentId: selectedEnvironmentId,
          ids: combineBacktestIds.map(Number),
          initialBalance,
          fromDate,
          toDate,
        });
        setResults((prev) => ({ ...prev, [tabId]: result }));
        setTabs((prev) =>
          prev.map((t) => (t.id === tabId ? { ...t, hasResult: true } : t))
        );
      } catch (e) {
        setErrors((prev) => ({
          ...prev,
          [tabId]: e instanceof Error ? e.message : "Combine failed",
        }));
      } finally {
        setTabLoading(tabId, false);
      }
      return;
    }

    if (selectedCommand === "Tune") {
      const parseListLen = (s: string, def = 1) => {
        const trimmed = s.trim();
        if (!trimmed) return def;
        return trimmed.split(/[\s,]+/).filter(Boolean).length;
      };
      const baseLotsN = parseListLen(baseLot);
      const leveragesN = parseListLen(leverage);
      const hasVol = sizing === "Vol Target";
      const volTargetsN = hasVol ? parseListLen(volTarget, 1) : 1;
      const volHalflifesN = hasVol ? parseListLen(volHalflife, 1) : 1;
      const volMaxMultsN = hasVol ? parseListLen(volMaxMult, 1) : 1;
      const volMinDaysN = hasVol ? parseListLen(volMinDays, 1) : 1;
      const estimatedTotal = baseLotsN * leveragesN * volTargetsN * volHalflifesN * volMaxMultsN * volMinDaysN;

      setTabTuneProgress(tabId, { progress: 0, total: estimatedTotal });

      let progressInterval: any;

      try {
        await runTune({
          environmentId: selectedEnvironmentId,
          strategy: selectedStrategy,
          symbol: selectedSymbol,
          instrument: selectedInstrument,
          initialBalance,
          baseLot,
          leverage,
          sizing,
          volTarget,
          volHalflife,
          volMaxMult,
          volMinDays,
          fromDate,
          toDate,
        });

        progressInterval = setInterval(async () => {
          try {
            const status = await fetchTuneStatus();
            if (status.status === "completed" && status.result) {
              clearInterval(progressInterval);
              setTuneResults((prev) => ({ ...prev, [tabId]: status.result! }));
              setTabs((prev) =>
                prev.map((t) => (t.id === tabId ? { ...t, hasResult: true } : t))
              );
              setTabLoading(tabId, false);
              setTabTuneProgress(tabId, null);
            } else if (status.status === "failed") {
              clearInterval(progressInterval);
              setErrors((prev) => ({
                ...prev,
                [tabId]: status.error || "Tune failed",
              }));
              setTabLoading(tabId, false);
              setTabTuneProgress(tabId, null);
            } else {
              setTabTuneProgress(tabId, {
                progress: status.progress ?? 0,
                total: status.total ?? estimatedTotal,
              });
            }
          } catch (e) {
            console.error("Failed to fetch tune status:", e);
          }
        }, 300);
      } catch (e) {
        setErrors((prev) => ({
          ...prev,
          [tabId]: e instanceof Error ? e.message : "Tune failed",
        }));
        setTabLoading(tabId, false);
        setTabTuneProgress(tabId, null);
      }
      return;
    }

    const params = buildRunParams();
    try {
      const result = await runBacktest(params);
      setResults((prev) => ({ ...prev, [tabId]: result }));
      setTabs((prev) =>
        prev.map((t) => (t.id === tabId ? { ...t, hasResult: true } : t))
      );
    } catch (e) {
      setErrors((prev) => ({
        ...prev,
        [tabId]: e instanceof Error ? e.message : "Run failed",
      }));
    } finally {
      setTabLoading(tabId, false);
    }
  };

  const handleSave = async () => {
    const tabId = activeTabId;
    setSavingTabs((prev) => ({ ...prev, [tabId]: true }));

    if (selectedCommand === "Combine") {
      try {
        await saveCombine({
          environmentId: selectedEnvironmentId,
          ids: combineBacktestIds.map(Number),
          initialBalance,
          fromDate,
          toDate,
        });
        setTabs((prev) =>
          prev.map((t) => (t.id === tabId ? { ...t, isSaved: true } : t))
        );
        queryClient.invalidateQueries({ queryKey: ["backtests"] });
      } catch (e) {
        setErrors((prev) => ({
          ...prev,
          [tabId]: e instanceof Error ? e.message : "Save failed",
        }));
      } finally {
        setSavingTabs((prev) => {
          const next = { ...prev };
          delete next[tabId];
          return next;
        });
      }
      return;
    }

    try {
      await saveRun(buildRunParams());
      setTabs((prev) =>
        prev.map((t) => (t.id === tabId ? { ...t, isSaved: true } : t))
      );
      // Refresh the DatabasePage backtest list so the new run shows up.
      queryClient.invalidateQueries({ queryKey: ["backtests"] });
    } catch (e) {
      setErrors((prev) => ({
        ...prev,
        [tabId]: e instanceof Error ? e.message : "Save failed",
      }));
    } finally {
      setSavingTabs((prev) => {
        const next = { ...prev };
        delete next[tabId];
        return next;
      });
    }
  };

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-gray-950 text-white">
      
      {/* Chrome Browser-like Tab Selection (Dark Theme & Left Aligned) */}
      <div className="flex items-end bg-[#121214] pl-0 pr-4 pt-2 border-b border-[#212124] flex-wrap gap-0.5 shrink-0 select-none">
        {tabs.map((tab, idx) => {
          const isActive = tab.id === activeTabId;
          const tabNumber = idx + 1;
          return (
            <div
              key={tab.id}
              onClick={() => setActiveTabId(tab.id)}
              draggable={true}
               onDragStart={(e) => handleDragStart(e, idx)}
              onDragOver={handleDragOver}
              onDragEnter={() => handleDragEnter(idx)}
              onDragEnd={handleDragEnd}
              className={`group relative flex items-center h-9 cursor-grab active:cursor-grabbing transition-all duration-150 ${
                isActive ? "z-30" : "z-10"
              } ${activeDragIdx === idx ? "opacity-40" : "opacity-100"} -ml-2.5 first:ml-0`}
              style={{ width: "110px", minWidth: "110px" }}
            >
              {/* Outer Trapezoid (Border) */}
              <div
                className="absolute inset-0 transition-colors duration-150"
                style={{
                  backgroundColor: isActive ? "#212124" : "#27272a",
                  clipPath: "polygon(12px 0%, calc(100% - 12px) 0%, 100% 100%, 0% 100%)",
                }}
              />
              {/* Inner Trapezoid (Fill) */}
              <div
                className="absolute inset-[1px] bottom-0 transition-colors duration-150"
                style={{
                  backgroundColor: isActive ? "#1A1A1E" : "#0f0f12",
                  clipPath: "polygon(11px 0%, calc(100% - 11px) 0%, 100% 100%, 0% 100%)",
                }}
              />
              
              {/* Tab Title & Close Button */}
              <div className="relative z-10 flex items-center justify-center w-full select-none h-full">
                {/* Title (Only Number) */}
                <span className={`font-mono text-xs select-none ${
                  isActive ? "text-white font-bold" : "text-gray-500 font-medium"
                }`}>
                  {tabNumber}
                </span>

                {/* Close Button */}
                {tabs.length > 1 && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      closeTab(tab.id);
                    }}
                    className={`absolute right-3.5 w-3.5 h-3.5 rounded-full flex items-center justify-center transition-all text-[9px] font-bold opacity-0 group-hover:opacity-100 duration-150 ${
                      isActive 
                        ? "text-gray-400 hover:text-white hover:bg-gray-700/50" 
                        : "text-gray-650 hover:text-white hover:bg-gray-800/50"
                    }`}
                  >
                    ✕
                  </button>
                )}
              </div>
            </div>
          );
        })}
        
        {/* Plus Button */}
        <button
          onClick={addTab}
          className="h-7 w-7 rounded-md flex items-center justify-center text-gray-500 hover:text-white hover:bg-gray-900 transition-colors cursor-pointer ml-3 mb-1 select-none text-lg font-light"
        >
          +
        </button>
      </div>

      {/* Main Split Layout Content */}
      <div className="flex-1 flex flex-row min-h-0 bg-gray-900/10">
        
        {/* Left Column: Wizard Config */}
        <div className="w-[550px] shrink-0 flex flex-col min-h-0 bg-[#1A1A1E]">
          <div className="flex-1 overflow-y-auto no-scrollbar pt-8 pr-8 pb-8 pl-[18px] flex flex-col gap-6">
            
            {/* Row 0: Command */}
            <div className="flex gap-4 items-start relative">
              {/* Left Timeline */}
              <div className="relative flex flex-col items-center w-4 shrink-0 self-stretch">
                <div className={`w-3.5 h-3.5 rounded-full border z-10 absolute top-[31px] transition-colors duration-200 ${
                  isCommandFilled ? "bg-gray-600 border-[#3A3A3E]" : "bg-[#28282D] border-[#3A3A3E]"
                }`} />
                {showEnvironment && (
                  <div className="absolute top-[45px] bottom-[-55px] w-[2px] bg-[#3A3A3E] z-0" />
                )}
              </div>
              {/* Right Content */}
              <div className="flex flex-col gap-1.5 h-[54px] justify-end">
                <span className="text-xs font-semibold tracking-wider text-gray-400 uppercase select-none">
                  {questionLabel("Command", isCommandFilled)}
                </span>
                <div className="relative w-48 shrink-0">
                  <select
                    value={selectedCommand}
                    onChange={(e) => {
                      updateActiveTab({
                        selectedCommand: e.target.value,
                        selectedEnvironmentId: "",
                        selectedStrategy: "",
                        selectedSymbol: "",
                        selectedInstrument: "",
                        initialBalance: "",
                        baseLot: "",
                        leverage: "",
                        sizing: "",
                        volTarget: "",
                        volHalflife: "",
                        volMaxMult: "",
                        volMinDays: "",
                        fromDate: "",
                        toDate: "",
                        hasResult: false,
                        isSaved: false,
                        title: "Backtest",
                        combineBacktestIds: ["", ""],
                      });
                    }}
                    className="bg-[#121214] border border-[#212124] text-xs font-medium text-gray-200 px-2.5 py-1.5 outline-none cursor-pointer hover:border-gray-700 transition-colors w-full h-8 appearance-none pr-8"
                  >
                    <option value="" disabled>Select command...</option>
                    {commands.map((cmd) => (
                      <option key={cmd} value={cmd}>
                        {cmd}
                      </option>
                    ))}
                  </select>
                  <div className="absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none flex items-center">
                    <svg className="w-3 h-3 text-gray-500 fill-none stroke-current" strokeWidth="2.5" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                    </svg>
                  </div>
                </div>
              </div>
            </div>

            {/* Row 1: Environment */}
            {showEnvironment && (
              <div className="flex gap-4 items-start relative">
                <div className="relative flex flex-col items-center w-4 shrink-0 self-stretch">
                  <div className={`w-3.5 h-3.5 rounded-full border z-10 absolute top-[31px] transition-colors duration-200 ${
                    isEnvironmentFilled ? "bg-gray-600 border-[#3A3A3E]" : "bg-[#28282D] border-[#3A3A3E]"
                  }`} />
                  {isEnvironmentFilled && (
                    <div className="absolute top-[45px] bottom-[-55px] w-[2px] bg-[#3A3A3E] z-0" />
                  )}
                </div>
                <div className="flex flex-col gap-1.5 h-[54px] justify-end">
                  <span className="text-xs font-semibold tracking-wider text-gray-400 uppercase select-none">
                    {questionLabel("Environment", isEnvironmentFilled)}
                  </span>
                  <div className="relative w-48 shrink-0">
                    <select
                      value={selectedEnvironmentId}
                      onChange={(e) => {
                        updateActiveTab({
                          selectedEnvironmentId: e.target.value,
                          selectedStrategy: "",
                          selectedSymbol: "",
                          selectedInstrument: "",
                          initialBalance: "",
                          baseLot: "",
                          leverage: "",
                          sizing: "",
                          volTarget: "",
                          volHalflife: "",
                          volMaxMult: "",
                          volMinDays: "",
                          fromDate: "",
                          toDate: "",
                          hasResult: false,
                          isSaved: false,
                          title: "Backtest",
                        });
                      }}
                      className="bg-[#121214] border border-[#212124] text-xs font-medium text-gray-200 px-2.5 py-1.5 outline-none cursor-pointer hover:border-gray-700 transition-colors w-full h-8 appearance-none pr-8"
                    >
                      <option value="" disabled>{isEnvironmentsLoading ? "Loading environments..." : "Select environment..."}</option>
                      {environments.map((environment) => (
                        <option key={environment.id} value={environment.id}>
                          {environment.name}
                        </option>
                      ))}
                    </select>
                    <div className="absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none flex items-center">
                      <svg className="w-3 h-3 text-gray-500 fill-none stroke-current" strokeWidth="2.5" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                      </svg>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Row 1 (Alternative for Combine): Initial Balance */}
            {selectedCommand === "Combine" && isEnvironmentFilled && (
              <div className="flex gap-4 items-start relative">
                {/* Left Timeline */}
                <div className="relative flex flex-col items-center w-4 shrink-0 self-stretch">
                  <div className={`w-3.5 h-3.5 rounded-full border z-10 absolute top-[31px] transition-colors duration-200 ${
                    initialBalance.trim() !== "" ? "bg-gray-600 border-[#3A3A3E]" : "bg-[#28282D] border-[#3A3A3E]"
                  }`} />
                  {initialBalance.trim() !== "" && (
                    <div className="absolute top-[45px] bottom-[-55px] w-[2px] bg-[#3A3A3E] z-0" />
                  )}
                </div>
                {/* Right Content */}
                <div className="flex flex-col gap-1.5 h-[54px] justify-end">
                  <span className="text-xs font-semibold tracking-wider text-gray-400 uppercase select-none">
                    {questionLabel("Initial Balance", initialBalance.trim() !== "")}
                  </span>
                  <input
                    type="text"
                    value={initialBalance}
                    onChange={(e) => {
                      updateActiveTab({
                        initialBalance: e.target.value,
                      });
                    }}
                    placeholder="e.g. 100000"
                    className="bg-[#121214] border border-[#212124] text-xs font-medium text-gray-200 px-2.5 py-1.5 outline-none hover:border-gray-700 transition-colors shrink-0 w-48 h-8 font-mono"
                  />
                </div>
              </div>
            )}

            {/* Row 1.5 (Alternative for Combine): Date Selection */}
            {selectedCommand === "Combine" && isEnvironmentFilled && initialBalance.trim() !== "" && (
              <div className="flex gap-4 items-start relative">
                {/* Left Timeline */}
                <div className="relative flex flex-col items-center w-4 shrink-0 self-stretch">
                  <div className={`w-3.5 h-3.5 rounded-full border z-10 absolute top-[31px] transition-colors duration-200 ${
                    isDateFilled ? "bg-gray-600 border-[#3A3A3E]" : "bg-[#28282D] border-[#3A3A3E]"
                  }`} />
                  {isDateFilled && (
                    <div className="absolute top-[45px] bottom-[-55px] w-[2px] bg-[#3A3A3E] z-0" />
                  )}
                </div>
                {/* Right Content */}
                <div className="flex flex-col gap-1.5 h-[54px] justify-end">
                  <span className="text-xs font-semibold tracking-wider text-gray-400 uppercase select-none">
                    {questionLabel("Date", isDateFilled)}
                  </span>
                  <div className="flex items-center" style={{ gap: 0 }}>
                    <input
                      type="text"
                      value={fromDate}
                      onChange={(e) => {
                        updateActiveTab({
                          fromDate: e.target.value,
                          hasResult: false,
                          isSaved: false,
                        });
                      }}
                      placeholder="From (YYYY-MM-DD)"
                      className="bg-[#121214] border border-[#212124] text-xs font-medium text-gray-200 px-2.5 py-1.5 outline-none hover:border-gray-700 transition-colors w-48 h-8 font-mono"
                    />
                    <svg
                      width="10"
                      height="10"
                      viewBox="0 0 24 24"
                      className="text-gray-500 fill-current shrink-0 select-none"
                      style={{ display: "block", margin: 0, padding: 0 }}
                    >
                      <path d="M6 4v16l14-8z" />
                    </svg>
                    <input
                      type="text"
                      value={toDate}
                      onChange={(e) => {
                        updateActiveTab({
                          toDate: e.target.value,
                          hasResult: false,
                          isSaved: false,
                        });
                      }}
                      placeholder="To (YYYY-MM-DD)"
                      className="bg-[#121214] border border-[#212124] text-xs font-medium text-gray-200 px-2.5 py-1.5 outline-none hover:border-gray-700 transition-colors w-48 h-8 font-mono"
                    />
                  </div>
                </div>
              </div>
            )}

            {selectedCommand === "Combine" && isEnvironmentFilled && initialBalance.trim() !== "" && isDateFilled && (
              <>
                {combineBacktestIds.map((btId, index) => {
                  const isLast = index === combineBacktestIds.length - 1;
                  const isFirst = index === 0;
                  const isFilled = btId !== "";
                  
                  // Show the row if it's the first strategy, or if the previous strategy is filled
                  const isRowShown = isFirst || (combineBacktestIds[index - 1] !== "");
                  if (!isRowShown) return null;

                  return (
                    <div key={index} className="flex gap-4 items-start relative">
                      {/* Left Timeline */}
                      <div className="relative flex flex-col items-center w-4 shrink-0 self-stretch">
                        <div className={`w-3.5 h-3.5 rounded-full border z-10 absolute top-[31px] transition-colors duration-200 flex items-center justify-center text-[9px] font-bold ${
                          isFilled ? "bg-gray-600 border-[#3A3A3E] text-white" : "bg-[#28282D] border-[#3A3A3E] text-gray-500"
                        }`}>
                          {!isFirst && isLast && isFilled && "✓"}
                        </div>
                        {/* Draw connector line down if the item is filled and NOT the last strategy */}
                        {isFilled && !isLast && (
                          <div className="absolute top-[45px] bottom-[-55px] w-[2px] bg-[#3A3A3E] z-0" />
                        )}
                      </div>
                      {/* Right Content */}
                      <div className="flex flex-col gap-1.5 h-[54px] justify-end">
                        <span className="text-xs font-semibold tracking-wider text-gray-400 uppercase select-none">
                          {questionLabel(index === 0 ? "First Strategy" : index === 1 ? "Second Strategy" : `Strategy ${index + 1}`, isFilled)}
                        </span>
                        <div className="flex items-center gap-2">
                          <div className="relative w-48 shrink-0">
                            <select
                              value={btId}
                              onChange={(e) => {
                                const newIds = [...combineBacktestIds];
                                newIds[index] = e.target.value;
                                updateActiveTab({
                                  combineBacktestIds: newIds,
                                });
                              }}
                              className="bg-[#121214] border border-[#212124] text-xs font-medium text-gray-200 px-2.5 py-1.5 outline-none cursor-pointer hover:border-gray-700 transition-colors w-full h-8 appearance-none pr-8 font-mono"
                            >
                              <option value="" disabled>Select backtest...</option>
                              {backtests?.map((bt) => (
                                <option key={bt.id} value={bt.id}>
                                  {bt.strategy} (#{bt.id})
                                </option>
                              ))}
                            </select>
                            <div className="absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none flex items-center">
                              <svg className="w-3 h-3 text-gray-500 fill-none stroke-current" strokeWidth="2.5" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                              </svg>
                            </div>
                          </div>
                          {index >= 2 && (
                            <button
                              onClick={() => handleRemoveCombineStrategy(index)}
                              className="p-1.5 rounded text-gray-500 hover:text-red-400 hover:bg-gray-800/60 transition-colors cursor-pointer shrink-0"
                              title="Remove strategy"
                            >
                              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
                              </svg>
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}

                {/* Plus Button Row for Combine */}
                {combineBacktestIds.length >= 2 &&
                  combineBacktestIds[combineBacktestIds.length - 1] !== "" && (
                    <div className="flex gap-4 items-start relative h-8 mt-2">
                      {/* Left Timeline */}
                      <div className="relative flex flex-col items-center w-4 shrink-0 self-stretch">
                        <button
                          onClick={handleAddCombineStrategy}
                          className="text-gray-400 hover:text-white bg-transparent border-none outline-none font-light text-2xl cursor-pointer select-none transition-colors duration-150 absolute top-[2px] leading-none"
                          title="Add another strategy"
                        >
                          +
                        </button>
                      </div>
                      {/* Right Content is Empty */}
                      <div className="flex-1 h-8" />
                    </div>
                )}

                {/* See Result & Save for Combine */}
                {combineBacktestIds.length >= 2 &&
                  combineBacktestIds.every((id) => id !== "") && (
                    <div className="flex gap-4 items-start relative mt-4">
                      {/* Left Timeline */}
                      <div className="relative flex flex-col items-center w-4 shrink-0 self-stretch">
                        {/* Terminal circle or empty space */}
                      </div>
                      {/* Right Content */}
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          disabled={isLoading}
                          onClick={handleSeeResult}
                          className="liquid-glass-btn liquid-glass-btn-no-grow disabled:cursor-wait text-xs font-semibold tracking-wide px-4 py-2.5 h-9 gap-1.5 select-none shrink-0 w-auto"
                        >
                          {isLoading ? (
                            <>
                              <svg className="animate-spin h-3.5 w-3.5 text-white" fill="none" viewBox="0 0 24 24">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                              </svg>
                              Running...
                            </>
                          ) : (
                            <>
                              See Result
                              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2.2" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
                              </svg>
                            </>
                          )}
                        </button>

                        {!isLoading && hasResult && (
                          <button
                            disabled={isSaved || !!savingTabs[activeTabId]}
                            onClick={handleSave}
                            className="flex items-center gap-1.5 text-xs font-semibold tracking-wide text-gray-400 hover:text-white disabled:text-gray-500 disabled:cursor-not-allowed transition-colors select-none bg-transparent hover:bg-gray-900/50 disabled:hover:bg-transparent px-3 py-2 rounded-lg cursor-pointer shrink-0"
                          >
                            {savingTabs[activeTabId] ? (
                              <>
                                <svg className="animate-spin h-3.5 w-3.5 text-gray-300" fill="none" viewBox="0 0 24 24">
                                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                                </svg>
                                Saving...
                              </>
                            ) : isSaved ? (
                              <>
                                <svg className="w-3.5 h-3.5 text-emerald-400" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                                </svg>
                                Saved
                              </>
                            ) : (
                              <>
                                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-4.72-4.72a.75.75 0 00-.53-.22H5.25A2.25 2.25 0 003 5.5v13.5A2.25 2.25 0 005.25 21.25h13.5A2.25 2.25 0 0021 19V8.78a.75.75 0 00-.22-.53z" />
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 3v6h8V3M9 21v-8h6v8" />
                                </svg>
                                Save
                              </>
                            )}
                          </button>
                        )}
                      </div>
                    </div>
                )}
              </>
            )}

            {/* Row 1: Strategy */}
            {showStrategy && (
              <div className="flex gap-4 items-start relative">
                {/* Left Timeline */}
                <div className="relative flex flex-col items-center w-4 shrink-0 self-stretch">
                  <div className={`w-3.5 h-3.5 rounded-full border z-10 absolute top-[31px] transition-colors duration-200 ${
                    isStrategyFilled ? "bg-gray-600 border-[#3A3A3E]" : "bg-[#28282D] border-[#3A3A3E]"
                  }`} />
                  {isStrategyFilled && (
                    <div className="absolute top-[45px] bottom-[-55px] w-[2px] bg-[#3A3A3E] z-0" />
                  )}
                </div>
                {/* Right Content */}
                <div className="flex flex-col gap-1.5 h-[54px] justify-end">
                  <span className="text-xs font-semibold tracking-wider text-gray-400 uppercase select-none">
                    {questionLabel("Strategy", isStrategyFilled)}
                  </span>
                  <div className="relative w-48 shrink-0">
                    <select
                      value={selectedStrategy}
                      onChange={(e) => {
                        const stratVal = e.target.value;
                        updateActiveTab({
                          selectedStrategy: stratVal,
                          selectedSymbol: "",
                          selectedInstrument: "",
                          initialBalance: "",
                          baseLot: "",
                          leverage: "",
                          sizing: "",
                          volTarget: "",
                          volHalflife: "",
                          volMaxMult: "",
                          volMinDays: "",
                          fromDate: "",
                          toDate: "",
                          hasResult: false,
                          isSaved: false,
                          title: getTabTitle(stratVal, "", "Backtest")
                        });
                      }}
                      className="bg-[#121214] border border-[#212124] text-xs font-medium text-gray-200 px-2.5 py-1.5 outline-none cursor-pointer hover:border-gray-700 transition-colors w-full h-8 appearance-none pr-8"
                    >
                      <option value="" disabled>
                        {isStrategiesLoading ? "Loading strategies..." : strategies.length === 0 ? "No strategies in this environment" : "Select strategy..."}
                      </option>
                      {strategies.map((strategy) => (
                        <option key={strategy.id} value={strategy.name}>
                          {strategy.name}
                        </option>
                      ))}
                    </select>
                    <div className="absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none flex items-center">
                      <svg className="w-3 h-3 text-gray-500 fill-none stroke-current" strokeWidth="2.5" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                      </svg>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Row 2: Symbol */}
            {showSymbol && (
              <div className="flex gap-4 items-start relative">
                {/* Left Timeline */}
                <div className="relative flex flex-col items-center w-4 shrink-0 self-stretch">
                  <div className={`w-3.5 h-3.5 rounded-full border z-10 absolute top-[31px] transition-colors duration-200 ${
                    isSymbolFilled ? "bg-gray-600 border-[#3A3A3E]" : "bg-[#28282D] border-[#3A3A3E]"
                  }`} />
                  {showInstrument && (
                    <div className="absolute top-[45px] bottom-[-55px] w-[2px] bg-[#3A3A3E] z-0" />
                  )}
                </div>
                {/* Right Content */}
                <div className="flex flex-row items-end gap-4 h-[54px]">
                  <div className="flex flex-col gap-1.5">
                    <span className="text-xs font-semibold tracking-wider text-gray-400 uppercase select-none">
                      {questionLabel("Symbol", isSymbolFilled)}
                    </span>
                    <div className="relative w-48 shrink-0">
                      <select
                        value={selectedSymbol}
                        onChange={(e) => {
                          const symVal = e.target.value;
                          updateActiveTab({
                            selectedSymbol: symVal,
                            selectedInstrument: "",
                            initialBalance: "",
                            baseLot: "",
                            leverage: "",
                            sizing: "",
                            volTarget: "",
                            volHalflife: "",
                            volMaxMult: "",
                            volMinDays: "",
                            fromDate: "",
                            toDate: "",
                            hasResult: false,
                            isSaved: false,
                            title: getTabTitle(selectedStrategy, symVal, "Backtest")
                          });
                        }}
                        className="bg-[#121214] border border-[#212124] text-xs font-medium text-gray-200 px-2.5 py-1.5 outline-none cursor-pointer hover:border-gray-700 transition-colors w-full h-8 appearance-none pr-8"
                      >
                        <option value="" disabled>
                          Select symbol...
                        </option>
                        {symbols.map((symbol) => (
                          <option key={symbol} value={symbol}>
                            {symbol}
                          </option>
                        ))}
                      </select>
                      <div className="absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none flex items-center">
                        <svg className="w-3 h-3 text-gray-500 fill-none stroke-current" strokeWidth="2.5" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                        </svg>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Row 3: Instrument */}
            {showInstrument && (
              <div className="flex gap-4 items-start relative">
                <div className="relative flex flex-col items-center w-4 shrink-0 self-stretch">
                  <div className={`w-3.5 h-3.5 rounded-full border z-10 absolute top-[31px] transition-colors duration-200 ${
                    isInstrumentFilled ? "bg-gray-600 border-[#3A3A3E]" : "bg-[#28282D] border-[#3A3A3E]"
                  }`} />
                  {showBalanceAndLot && (
                    <div className="absolute top-[45px] bottom-[-55px] w-[2px] bg-[#3A3A3E] z-0" />
                  )}
                </div>
                <div className="flex flex-col gap-1.5 h-[54px] justify-end">
                  <span className="text-xs font-semibold tracking-wider text-gray-400 uppercase select-none">
                    {questionLabel("Instrument", isInstrumentFilled)}
                  </span>
                  <div className="relative w-48 shrink-0">
                    <select
                      value={selectedInstrument}
                      onChange={(e) => {
                        updateActiveTab({
                          selectedInstrument: e.target.value,
                          initialBalance: "",
                          baseLot: "",
                          leverage: "",
                          sizing: "",
                          volTarget: "",
                          volHalflife: "",
                          volMaxMult: "",
                          volMinDays: "",
                          fromDate: "",
                          toDate: "",
                          hasResult: false,
                          isSaved: false,
                        });
                      }}
                      className="bg-[#121214] border border-[#212124] text-xs font-medium text-gray-200 px-2.5 py-1.5 outline-none cursor-pointer hover:border-gray-700 transition-colors w-full h-8 appearance-none pr-8"
                    >
                      <option value="" disabled>Select instrument...</option>
                      {instruments.map((instrument) => (
                        <option key={instrument} value={instrument}>
                          {instrument}
                        </option>
                      ))}
                    </select>
                    <div className="absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none flex items-center">
                      <svg className="w-3 h-3 text-gray-500 fill-none stroke-current" strokeWidth="2.5" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                      </svg>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Row 4: Initial Balance & Base Lot (Combined) */}
            {showBalanceAndLot && (
              <div className="flex gap-4 items-start relative">
                {/* Left Timeline */}
                <div className="relative flex flex-col items-center w-4 shrink-0 self-stretch">
                  <div className={`w-3.5 h-3.5 rounded-full border z-10 absolute top-[31px] transition-colors duration-200 ${
                    isBalanceAndLotFilled ? "bg-gray-600 border-[#3A3A3E]" : "bg-[#28282D] border-[#3A3A3E]"
                  }`} />
                  {showDate && (
                    <div className="absolute top-[45px] bottom-[-55px] w-[2px] bg-[#3A3A3E] z-0" />
                  )}
                </div>
                {/* Right Content */}
                <div className="flex flex-row items-start gap-4">
                  {/* Left Column: Initial Balance & Leverage */}
                  <div className="flex flex-col gap-4">
                    <div className="flex flex-col gap-1.5">
                      <span className="text-xs font-semibold tracking-wider text-gray-400 uppercase select-none">
                        {questionLabel("Initial Balance", initialBalance.trim() !== "")}
                      </span>
                      <input
                        type="text"
                        value={initialBalance}
                        onChange={(e) => {
                          updateActiveTab({
                            initialBalance: e.target.value,
                            hasResult: false,
                            isSaved: false,
                          });
                        }}
                        placeholder="e.g. 100000"
                        className="bg-[#121214] border border-[#212124] text-xs font-medium text-gray-200 px-2.5 py-1.5 outline-none hover:border-gray-700 transition-colors shrink-0 w-48 h-8 font-mono"
                      />
                    </div>

                    {!isInternallySized && (
                      <div className="flex flex-col gap-1.5">
                        <span className="text-xs font-semibold tracking-wider text-gray-400 uppercase select-none">
                          {questionLabel("Leverage", leverage.trim() !== "")}
                        </span>
                        <input
                          type="text"
                          value={leverage}
                          onChange={(e) => {
                            updateActiveTab({
                              leverage: e.target.value,
                              hasResult: false,
                              isSaved: false,
                            });
                          }}
                          placeholder="e.g. 1 (default)"
                          className="bg-[#121214] border border-[#212124] text-xs font-medium text-gray-200 px-2.5 py-1.5 outline-none hover:border-gray-700 transition-colors shrink-0 w-48 h-8 font-mono"
                        />
                      </div>
                    )}

                  </div>

                  {!isInternallySized && (
                    <div className="flex flex-col gap-1.5">
                      <span className="text-xs font-semibold tracking-wider text-gray-400 uppercase select-none">
                        {questionLabel("Base Lot", baseLot.trim() !== "")}
                      </span>
                      <input
                        type="text"
                        value={baseLot}
                        onChange={(e) => {
                          updateActiveTab({
                            baseLot: e.target.value,
                            hasResult: false,
                            isSaved: false,
                          });
                        }}
                        placeholder="e.g. 1"
                        className="bg-[#121214] border border-[#212124] text-xs font-medium text-gray-200 px-2.5 py-1.5 outline-none hover:border-gray-700 transition-colors shrink-0 w-48 h-8 font-mono"
                      />
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Row 4: Date Selection */}
            {showDate && (
              <div className="flex gap-4 items-start relative">
                {/* Left Timeline */}
                <div className="relative flex flex-col items-center w-4 shrink-0 self-stretch">
                  <div className={`w-3.5 h-3.5 rounded-full border z-10 absolute top-[31px] transition-all duration-200 ${
                    isDateFilled ? "bg-gray-200 border-[#3A3A3E]" : "bg-[#28282D] border-[#3A3A3E]"
                  }`} />
                </div>
                {/* Right Content */}
                <div className="flex flex-col gap-1.5 h-[54px] justify-end">
                  <span className="text-xs font-semibold tracking-wider text-gray-400 uppercase select-none">
                    {questionLabel("Date", isDateFilled)}
                  </span>
                  <div className="flex items-center gap-0">
                    <input
                      type="text"
                      value={fromDate}
                      onChange={(e) => {
                        updateActiveTab({
                          fromDate: e.target.value,
                          hasResult: false,
                          isSaved: false,
                        });
                      }}
                      placeholder="From (YYYY-MM-DD)"
                      className="bg-[#121214] border border-[#212124] text-xs font-medium text-gray-200 px-2.5 py-1.5 outline-none hover:border-gray-700 transition-colors w-48 h-8 font-mono"
                    />
                    <svg width="10" height="10" viewBox="0 0 24 24" className="text-gray-500 fill-current shrink-0 select-none">
                      <path d="M6 4v16l14-8z" />
                    </svg>
                    <input
                      type="text"
                      value={toDate}
                      onChange={(e) => {
                        updateActiveTab({
                          toDate: e.target.value,
                          hasResult: false,
                          isSaved: false,
                        });
                      }}
                      placeholder="To (YYYY-MM-DD)"
                      className="bg-[#121214] border border-[#212124] text-xs font-medium text-gray-200 px-2.5 py-1.5 outline-none hover:border-gray-700 transition-colors w-48 h-8 font-mono"
                    />
                  </div>
                </div>
              </div>
            )}

            {/* Row 5: Run actions */}
            {showActions && (
              <div className="flex gap-4 items-start relative">
                {/* Left Timeline */}
                <div className="relative flex flex-col items-center w-4 shrink-0 self-stretch" />
                {/* Right Content */}
                <div className="flex items-center gap-2">
                      <button
                        type="button"
                        disabled={isLoading || hasResult}
                        onClick={handleSeeResult}
                        className="liquid-glass-btn liquid-glass-btn-no-grow disabled:cursor-not-allowed text-xs font-semibold tracking-wide px-4 py-2.5 h-9 gap-1.5 select-none shrink-0 w-auto"
                      >
                        {isLoading ? (
                          <>
                            <svg className="animate-spin h-3.5 w-3.5 text-white" fill="none" viewBox="0 0 24 24">
                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                            </svg>
                            Running...
                          </>
                        ) : (
                          <>
                            See Result
                            <svg
                              className="w-3.5 h-3.5"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2.2"
                              viewBox="0 0 24 24"
                            >
                              <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3"
                              />
                            </svg>
                          </>
                        )}
                      </button>

                      {!isLoading && hasResult && selectedCommand !== "Tune" && (
                        <button
                          disabled={isSaved || !!savingTabs[activeTabId]}
                          onClick={handleSave}
                          className="flex items-center gap-1.5 text-xs font-semibold tracking-wide text-gray-400 hover:text-white disabled:text-gray-500 disabled:cursor-not-allowed transition-colors select-none bg-transparent hover:bg-gray-900/50 disabled:hover:bg-transparent px-3 py-2 rounded-lg cursor-pointer shrink-0"
                        >
                          {savingTabs[activeTabId] ? (
                            <>
                              <svg className="animate-spin h-3.5 w-3.5 text-gray-300" fill="none" viewBox="0 0 24 24">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                              </svg>
                              Saving...
                            </>
                          ) : isSaved ? (
                            <>
                              <svg className="w-3.5 h-3.5 text-emerald-400" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                              </svg>
                              Saved
                            </>
                          ) : (
                            <>
                              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-4.72-4.72a.75.75 0 00-.53-.22H5.25A2.25 2.25 0 003 5.5v13.5A2.25 2.25 0 005.25 21.25h13.5A2.25 2.25 0 0021 19V8.78a.75.75 0 00-.22-.53z" />
                                <path strokeLinecap="round" strokeLinejoin="round" d="M6 3v6h8V3M9 21v-8h6v8" />
                              </svg>
                              Save
                            </>
                          )}
                        </button>
                      )}
                </div>
              </div>
            )}

          </div>
        </div>

        {/* Right Column: Results Dashboard */}
        <div className="flex-1 flex flex-col min-h-0 bg-[#121214] relative">
          {isLoading && (
            <div className="flex-1 flex items-center justify-center">
              <span className="text-sm text-gray-400 flex items-center gap-2">
                <svg className="animate-spin h-4 w-4 text-gray-400" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                {selectedCommand === "Tune" && tuneProgress ? (
                  <>running {tuneProgress.progress}/{tuneProgress.total} combinations</>
                ) : (
                  <>Running backtest...</>
                )}
              </span>
            </div>
          )}

          {!isLoading && activeError && (
            <div className="flex-1 flex items-center justify-center px-8">
              <span className="text-sm text-red-400 text-center">{activeError}</span>
            </div>
          )}

          {!isLoading && hasResult && selectedCommand === "Tune" && activeTuneResult && (() => {
            const hasVol = sizing === "Vol Target";
            const renderTable = (title: string, list: any[]) => (
              <div className="flex flex-col gap-3">
                <h3 className="text-sm font-semibold tracking-wider text-gray-400 uppercase select-none">
                  {title}
                </h3>
                <div className="bg-gray-900/40 rounded-lg border border-gray-800/50 p-4 overflow-x-auto">
                  <table className="w-full border-collapse font-mono text-xs text-right">
                    <thead>
                      <tr className="text-gray-500 border-b border-gray-800/40 pb-2">
                        <th className="text-left pb-2 font-semibold w-12">Rank</th>
                        <th className="pb-2 font-semibold pr-4">Growth</th>
                        <th className="pb-2 font-semibold pr-4">Max DD</th>
                        <th className="pb-2 font-semibold pr-4">Score</th>
                        <th className="pb-2 font-semibold pr-4">Base Lot</th>
                        <th className="pb-2 font-semibold pr-4">Leverage</th>
                        {hasVol && (
                          <>
                            <th className="pb-2 font-semibold pr-4">Vol Target</th>
                            <th className="pb-2 font-semibold pr-4">Halflife</th>
                            <th className="pb-2 font-semibold pr-4">Max Mult</th>
                            <th className="pb-2 font-semibold pr-4">Min Days</th>
                          </>
                        )}
                      </tr>
                    </thead>
                    <tbody>
                      {list.map((c, idx) => (
                        <tr key={idx} className="hover:bg-gray-800/20 border-b border-gray-800/20 last:border-b-0">
                          <td className="text-left py-2 text-gray-500 font-bold">{idx + 1}</td>
                          <td className={`py-2 pr-4 ${c.growth >= 0 ? "text-emerald-400" : "text-red-400"}`}>{fmtPct(c.growth)}</td>
                          <td className="py-2 pr-4 text-red-400">{fmtPct(c.drawdown)}</td>
                          <td className="py-2 pr-4 text-blue-400 font-medium">{c.score.toFixed(3)}</td>
                          <td className="py-2 pr-4 text-gray-350">{c.baseLot.toFixed(2)}</td>
                          <td className="py-2 pr-4 text-gray-350">{(c.leverage ?? 1).toFixed(2)}</td>
                          {hasVol && (
                            <>
                              <td className="py-2 pr-4 text-gray-350">{(c.volTarget ?? 0).toFixed(2)}</td>
                              <td className="py-2 pr-4 text-gray-350">{(c.volHalflife ?? 0).toFixed(1)}</td>
                              <td className="py-2 pr-4 text-gray-350">{(c.volMaxMult ?? 0).toFixed(2)}</td>
                              <td className="py-2 pr-4 text-gray-350">{c.volMinDays ?? 0}</td>
                            </>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            );

            return (
              <div className="flex-1 overflow-y-auto no-scrollbar px-8 py-8 flex flex-col gap-8 max-w-5xl mx-auto w-full">
                {renderTable("Top 10 — Best Growth", activeTuneResult.bestGrowth)}
                {renderTable("Top 10 — Smallest Drawdown", activeTuneResult.minDrawdown)}
                {renderTable("Top 10 — Best of Two (Balanced)", activeTuneResult.bestOfTwo)}
              </div>
            );
          })()}

          {!isLoading && hasResult && selectedCommand !== "Tune" && activeResult && (() => {
            // Native vs Forex-execution view. `fxData` is the same trade book re-priced
            // from fx_nq_ticks; when selected, every sub-tab below reads from it.
            const fxData = activeResult.fx;
            const isFutures = isFuturesInstrument(activeResult.report.instrument);
            const showFx = !isFutures && execView === "fx";
            const view = showFx && fxData ? fxData : activeResult;
            const b = view.report;
            const mc = view.monteCarlo;
            // Result sub-tabs. Splicing re-aggregates the trade log and applies to
            // Run and Combine. `effectiveTab` guards a persisted tab not valid here.
            const resultTabs: { id: TestTab["activeTab"]; label: string }[] = [
              { id: "analysis", label: "Analysis" },
              { id: "equity", label: "Equity" },
              { id: "splicing", label: "Splicing" },
              { id: "volatility", label: "Volatility" },
              { id: "monte-carlo", label: "Monte Carlo" },
            ];
            const effectiveTab = resultTabs.some((t) => t.id === activeTab) ? activeTab : "analysis";
            return (
            <>
              {/* Floating result tabs */}
              <div className="absolute top-6 left-8 z-10 shrink-0">
                <LiquidGlassTabs
                  items={resultTabs}
                  activeId={effectiveTab}
                  onChange={(id) => updateActiveTab({ activeTab: id as TestTab["activeTab"] })}
                  label="Result views"
                />
              </div>

              {/* Native / Forex-execution switch */}
              {!isFutures && <div className="absolute top-6 right-8 z-10 shrink-0">
                <LiquidGlassTabs
                  items={[
                    { id: "native", label: "Native" },
                    { id: "fx", label: "March" },
                  ]}
                  activeId={execView}
                  onChange={(id) => setExecView(id as "native" | "fx")}
                  label="Execution view"
                />
              </div>}

              {/* Forex selected but no trades fell inside the tick window. */}
              {showFx && !fxData && (
                <div className="flex-1 flex items-center justify-center pt-20 px-8 text-center">
                  <span className="text-sm text-gray-500 max-w-md">
                    No forex execution for this run. Forex re-pricing covers NQ trades within
                    the tick window 2026-01-01 → 2026-06-26 — run a backtest inside that range to see it.
                  </span>
                </div>
              )}

              {/* Tab Contents */}
              {!(showFx && !fxData) && (<>
              {effectiveTab === "analysis" && (
                <div className="flex-1 overflow-y-auto no-scrollbar px-8 pb-8 pt-20">
                  <div className="grid grid-cols-2 gap-6 max-w-5xl mx-auto pb-16">

                    {/* Left Sub-column */}
                    <div className="space-y-6">
                      <Section title="Overview">
                        {selectedCommand === "Combine" && b.strategy && (
                          <StatRow label="Strategies" value={b.strategy} />
                        )}
                        <StatRow label="Symbol" value={b.symbol.toUpperCase()} />
                        <StatRow label="Instrument" value={b.instrument} />
                        <StatRow label="Period" value={`${fmtDate(b.first_ts)} → ${fmtDate(b.last_ts)}`} />
                        <StatRow label="Total Days" value={String(b.total_days)} />
                        <StatRow label="Number of Trades" value={String(b.num_trades)} />
                      </Section>

                      <Section title="Balance">
                        <StatRow label="Initial Balance" value={fmt$(b.initial_bal)} />
                        <StatRow
                          label="Final Balance"
                          value={`${fmt$(b.final_bal)} (${b.net_growth >= 0 ? "+" : ""}${fmtPct(b.net_growth)})`}
                          color={b.final_bal >= b.initial_bal ? "text-emerald-400" : "text-red-400"}
                        />
                      </Section>

                      <Section title="Average Returns">
                        <StatRow label="Weekly" value={`${fmt$(b.avg_weekly)} (${fmtPct(b.avg_weekly_pct)})`} color={b.avg_weekly >= 0 ? "text-emerald-400" : "text-red-400"} />
                        <StatRow label="Monthly" value={`${fmt$(b.avg_monthly)} (${fmtPct(b.avg_monthly_pct)})`} color={b.avg_monthly >= 0 ? "text-emerald-400" : "text-red-400"} />
                      </Section>

                      <Section title="Performance Ratios">
                        <StatRow label="Sharpe Ratio" value={b.sharpe.toFixed(2)} color={b.sharpe >= 1 ? "text-emerald-400" : b.sharpe >= 0 ? "text-gray-200" : "text-red-400"} />
                        <StatRow label="Profit Factor" value={b.profit_factor.toFixed(2)} color={b.profit_factor >= 1 ? "text-emerald-400" : "text-red-400"} />
                        <StatRow label="Expectancy" value={fmt$(b.expectancy)} color={b.expectancy >= 0 ? "text-emerald-400" : "text-red-400"} />
                      </Section>
                    </div>

                    {/* Right Sub-column */}
                    <div className="space-y-6">
                      <Section title="Win / Loss">
                        <StatRow label="Win Rate" value={`${fmtPct(b.win_rate, 1)} (${b.win_count}/${b.num_trades})`} color={b.win_rate >= 50 ? "text-emerald-400" : "text-red-400"} />
                        <StatRow label="Total Wins" value={fmt$(b.total_win)} color="text-emerald-400" />
                        <StatRow label="Total Losses" value={fmt$(b.total_loss)} color="text-rose-400" />
                        <StatRow label="Max Losing Streak" value={String(b.max_lose_streak)} />
                      </Section>

                      <Section title="Position Sizing">
                        <StatRow
                          label="Size"
                          value={`${b.avg_size.toFixed(2)} (Min: ${b.min_size.toFixed(2)} / Max: ${b.max_size.toFixed(2)})`}
                        />
                      </Section>

                      <Section title="Drawdown & Loss">
                        <StatRow
                          label="Max Drawdown"
                          value={
                            <div className="flex flex-col items-end">
                              <span className="text-red-400">{fmtPct(b.max_drawdown)} ({fmt$(b.max_drawdown_dollars)})</span>
                              {b.max_drawdown_peak_date.trim() && (
                                <span className="text-gray-500 text-[10px] mt-0.5">[{fmtDate(b.max_drawdown_peak_date)} → {fmtDate(b.max_drawdown_trough_date)}]</span>
                              )}
                            </div>
                          }
                        />
                        <StatRow label="Avg Drawdown" value={`${fmtPct(b.avg_drawdown)} (${fmt$(b.avg_drawdown_dollars)})`} color="text-red-400" />
                        <StatRow
                          label="Max Intraday DD"
                          value={
                            <div className="flex flex-col items-end">
                              <span className="text-red-400">{fmtPct(b.max_intraday_drawdown)} ({fmt$(b.max_intraday_drawdown_dollars)})</span>
                              {b.max_intraday_drawdown_date.trim() && (
                                <span className="text-gray-500 text-[10px] mt-0.5">[{fmtDate(b.max_intraday_drawdown_date)}]</span>
                              )}
                            </div>
                          }
                        />
                        <StatRow label="Avg Intraday DD" value={`${fmtPct(b.avg_intraday_drawdown)} (${fmt$(b.avg_intraday_drawdown_dollars)})`} color="text-red-400" />
                        <StatRow
                          label="Max Daily Loss"
                          value={
                            <div className="flex flex-col items-end">
                              <span className="text-red-400">{fmt$(b.max_daily_loss)}</span>
                              {b.max_daily_loss_date.trim() && (
                                <span className="text-gray-500 text-[10px] mt-0.5">[{fmtDate(b.max_daily_loss_date)}]</span>
                              )}
                            </div>
                          }
                        />
                        <StatRow label="Avg Daily Loss" value={fmt$(b.avg_daily_loss)} color="text-red-400" />
                      </Section>
                    </div>

                  </div>
                </div>
              )}

              {effectiveTab === "equity" && (
                <div className="flex-1 overflow-y-auto no-scrollbar px-8 pb-8 pt-20 flex flex-col justify-center">
                  <div className="w-full max-w-5xl mx-auto h-[400px]">
                    {view.trades.length > 0 ? (
                      <EquityChart trades={view.trades} initialBalance={b.initial_bal} startDate={fromDate || undefined} />
                    ) : (
                      <div className="h-full flex items-center justify-center text-sm text-gray-500">No trades in this period.</div>
                    )}
                  </div>
                </div>
              )}

              {effectiveTab === "splicing" && (
                <div className="flex-1 overflow-y-auto no-scrollbar px-8 pb-8 pt-20">
                  <Splicing trades={view.trades} initialBalance={b.initial_bal} />
                </div>
              )}

              {effectiveTab === "volatility" && (
                <div className="flex-1 overflow-y-auto no-scrollbar px-8 pb-8 pt-20">
                  <Volatility trades={view.trades} initialBalance={b.initial_bal} />
                </div>
              )}

              {effectiveTab === "monte-carlo" && (
                <div className="flex-1 overflow-y-auto no-scrollbar px-8 pb-8 pt-8 flex flex-col items-center justify-center gap-6">
                  {mc ? (
                    <>
                      {/* Summary table */}
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
                              <td className="pr-6">{mc.p5.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                              <td className="pr-6">{mc.p25.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                              <td className="pr-6">{mc.p50.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                              <td className="pr-6">{mc.p75.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                              <td>{mc.p95.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                            </tr>
                            <tr className="text-right">
                              <td className="text-left text-gray-300 pr-4">Max drawdown %</td>
                              {([mc.ddP5, mc.ddP25, mc.ddP50, mc.ddP75, mc.ddP95] as number[]).map((v, i, arr) => (
                                <td key={i} className={i < arr.length - 1 ? "pr-6" : ""}>{isNaN(v) ? "—" : fmtPct(v, 1)}</td>
                              ))}
                            </tr>
                          </tbody>
                        </table>
                        <p className="text-gray-500 text-xs mt-1">(Worst case: the p5 column for balance, the p95 column for drawdown.)</p>
                        <div className="mt-3 flex gap-8 text-sm">
                          <span><span className="text-gray-400">P(profit)</span>&nbsp;&nbsp;&nbsp;{fmtPct(mc.pProfit * 100, 1)}</span>
                          <span><span className="text-gray-400">P(ruin ≤ 50% start)</span>&nbsp;&nbsp;&nbsp;{fmtPct(mc.pRuin * 100, 1)}</span>
                        </div>
                      </div>
                      {/* Chart */}
                      <div className="w-full max-w-5xl h-[400px]">
                        <MonteCarloChart data={mc} />
                      </div>
                    </>
                  ) : (
                    <div className="text-sm text-gray-500">Not enough trades for a Monte Carlo simulation.</div>
                  )}
                </div>
              )}
              </>)}
            </>
            );
          })()}
        </div>
        
      </div>

    </div>
  );
}
