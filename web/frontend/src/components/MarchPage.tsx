import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useApp } from "../context/AppContext";
import { fetchMt5Accounts } from "../api";
import { TIMEFRAMES, makeDefaultPanelConfig, type TF } from "../types";
import AccountsTree from "./AccountsTree";
import ActivePositionsTable from "./ActivePositionsTable";
import ChartPanel from "./ChartPanel";

const DEFAULT_TF = TIMEFRAMES.find((t) => t.table === "1m") ?? TIMEFRAMES[0];

const LAYOUTS = [
  {
    id: "single",
    label: "Single Panel",
    icon: (w = 14, h = 14) => (
      <svg width={w} height={h} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
      </svg>
    ),
  },
  {
    id: "split-v",
    label: "Split Vertical",
    icon: (w = 14, h = 14) => (
      <svg width={w} height={h} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
        <line x1="12" y1="3" x2="12" y2="21" />
      </svg>
    ),
  },
  {
    id: "split-h",
    label: "Split Horizontal",
    icon: (w = 14, h = 14) => (
      <svg width={w} height={h} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
        <line x1="3" y1="12" x2="21" y2="12" />
      </svg>
    ),
  },
  {
    id: "grid-4",
    label: "2x2 Grid",
    icon: (w = 14, h = 14) => (
      <svg width={w} height={h} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
        <line x1="12" y1="3" x2="12" y2="21" />
        <line x1="3" y1="12" x2="21" y2="12" />
      </svg>
    ),
  },
  {
    id: "split-3-v",
    label: "3 Splits Vertical",
    icon: (w = 14, h = 14) => (
      <svg width={w} height={h} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
        <line x1="9" y1="3" x2="9" y2="21" />
        <line x1="15" y1="3" x2="15" y2="21" />
      </svg>
    ),
  },
  {
    id: "split-3-h",
    label: "3 Splits Horizontal",
    icon: (w = 14, h = 14) => (
      <svg width={w} height={h} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
        <line x1="3" y1="9" x2="21" y2="9" />
        <line x1="3" y1="15" x2="21" y2="15" />
      </svg>
    ),
  },
  {
    id: "left-col-right-row",
    label: "Left Column, Right Rows",
    icon: (w = 14, h = 14) => (
      <svg width={w} height={h} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
        <line x1="12" y1="3" x2="12" y2="21" />
        <line x1="12" y1="12" x2="21" y2="12" />
      </svg>
    ),
  },
  {
    id: "right-col-left-row",
    label: "Right Column, Left Rows",
    icon: (w = 14, h = 14) => (
      <svg width={w} height={h} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
        <line x1="12" y1="3" x2="12" y2="21" />
        <line x1="3" y1="12" x2="12" y2="12" />
      </svg>
    ),
  },
  {
    id: "top-row-bottom-col",
    label: "Top Row, Bottom Columns",
    icon: (w = 14, h = 14) => (
      <svg width={w} height={h} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
        <line x1="3" y1="12" x2="21" y2="12" />
        <line x1="12" y1="12" x2="12" y2="21" />
      </svg>
    ),
  },
  {
    id: "bottom-row-top-col",
    label: "Bottom Row, Top Columns",
    icon: (w = 14, h = 14) => (
      <svg width={w} height={h} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
        <line x1="3" y1="12" x2="21" y2="12" />
        <line x1="12" y1="3" x2="12" y2="12" />
      </svg>
    ),
  },
];

// How each layout maps to a CSS grid: panel count + the grid template. Panels
// are placed in order into the named areas a / b / c / d.
interface LayoutDef {
  count: number;
  cols: string;
  rows: string;
  areas: string;
}

const LAYOUT_GRID: Record<string, LayoutDef> = {
  "single": { count: 1, cols: "1fr", rows: "1fr", areas: '"a"' },
  "split-v": { count: 2, cols: "1fr 1fr", rows: "1fr", areas: '"a b"' },
  "split-h": { count: 2, cols: "1fr", rows: "1fr 1fr", areas: '"a" "b"' },
  "grid-4": { count: 4, cols: "1fr 1fr", rows: "1fr 1fr", areas: '"a b" "c d"' },
  "split-3-v": { count: 3, cols: "1fr 1fr 1fr", rows: "1fr", areas: '"a b c"' },
  "split-3-h": { count: 3, cols: "1fr", rows: "1fr 1fr 1fr", areas: '"a" "b" "c"' },
  "left-col-right-row": { count: 3, cols: "1fr 1fr", rows: "1fr 1fr", areas: '"a b" "a c"' },
  "right-col-left-row": { count: 3, cols: "1fr 1fr", rows: "1fr 1fr", areas: '"b a" "c a"' },
  "top-row-bottom-col": { count: 3, cols: "1fr 1fr", rows: "1fr 1fr", areas: '"a a" "b c"' },
  "bottom-row-top-col": { count: 3, cols: "1fr 1fr", rows: "1fr 1fr", areas: '"b c" "a a"' },
};

const AREA_LETTERS = ["a", "b", "c", "d"];

export default function MarchPage() {
  const {
    marchLayout,
    setMarchLayout,
    marchLayouts,
    updateMarchPanel,
    setActiveMarchPanel,
    setIndicatorsOpen,
    setModalOpen,
    isBottomOpen,
    setIsBottomOpen,
    marchBottomHeight,
    setMarchBottomHeight,
    selectedAccountId,
    setSelectedAccountId,
  } = useApp();

  const [isPanelPopupOpen, setIsPanelPopupOpen] = useState(false);

  // Keep a valid selected account: default to the first one, and recover the
  // selection if the chosen account was deleted. (Lives here so it runs once
  // for the page regardless of how many chart panels are mounted.)
  const { data: marchAccounts } = useQuery({
    queryKey: ["mt5Accounts"],
    queryFn: fetchMt5Accounts,
    refetchInterval: 10000,
  });

  useEffect(() => {
    if (!marchAccounts) return;
    if (marchAccounts.length === 0) {
      if (selectedAccountId !== null) setSelectedAccountId(null);
      return;
    }
    if (!marchAccounts.some((a) => a.id === selectedAccountId)) {
      setSelectedAccountId(marchAccounts[0].id);
    }
  }, [marchAccounts, selectedAccountId, setSelectedAccountId]);

  useEffect(() => {
    const handleClose = () => setIsPanelPopupOpen(false);
    window.addEventListener("click", handleClose);
    window.addEventListener("contextmenu", handleClose);
    return () => {
      window.removeEventListener("click", handleClose);
      window.removeEventListener("contextmenu", handleClose);
    };
  }, []);

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    const startY = e.clientY;
    const startHeight = marchBottomHeight;

    const handleMouseMove = (moveEvent: MouseEvent) => {
      const deltaY = moveEvent.clientY - startY;
      const newHeight = startHeight - deltaY;

      if (newHeight <= 100) {
        setIsBottomOpen(false);
        setMarchBottomHeight(400);
        window.removeEventListener("mousemove", handleMouseMove);
        window.removeEventListener("mouseup", handleMouseUp);
      } else {
        const maxHeight = window.innerHeight - 100;
        setMarchBottomHeight(Math.max(100, Math.min(maxHeight, newHeight)));
      }
    };

    const handleMouseUp = () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
  };

  const def = LAYOUT_GRID[marchLayout] ?? LAYOUT_GRID["single"];

  // Each panel's config comes from the active layout's stored slot (persisted
  // per layout). Missing slots fall back to a default until the user edits them.
  const panelProps = (i: number) => {
    const stored = marchLayouts[marchLayout]?.[i] ?? makeDefaultPanelConfig();
    const tf = TIMEFRAMES.find((t) => t.table === stored.tf) ?? DEFAULT_TF;
    return {
      config: {
        symbol: stored.symbol,
        tf,
        mode: stored.mode,
        fromDate: stored.from,
        toDate: stored.to,
        vwap: stored.indicators.vwap,
      },
      setSymbol: (s: "nq" | "es") => updateMarchPanel(marchLayout, i, { symbol: s }),
      setTf: (t: TF) => updateMarchPanel(marchLayout, i, { tf: t.table }),
      onApplyRange: (from: string, to: string) =>
        updateMarchPanel(marchLayout, i, { mode: "range", from, to }),
      onLatest: (from: string) => updateMarchPanel(marchLayout, i, { mode: "latest", from }),
      onOpenIndicators: () => {
        setActiveMarchPanel({ layout: marchLayout, index: i });
        setIndicatorsOpen(true);
      },
      onOpenBacktests: () => setModalOpen(true),
    };
  };

  return (
    <div className="flex-1 flex flex-col bg-gray-950 min-h-0">
      {/* Chart panel grid — driven by the selected bottom-bar layout */}
      <div
        className="flex-1 min-h-0 min-w-0 bg-gray-800"
        style={{
          display: "grid",
          gridTemplateColumns: def.cols,
          gridTemplateRows: def.rows,
          gridTemplateAreas: def.areas,
          gap: "1px",
        }}
      >
        {Array.from({ length: def.count }).map((_, i) => (
          <div
            key={i}
            className="min-h-0 min-w-0 overflow-hidden"
            style={{ gridArea: AREA_LETTERS[i] }}
          >
            <ChartPanel {...panelProps(i)} />
          </div>
        ))}
      </div>

      {/* Bottom section — shared across all panels */}
      <div
        className="w-full bg-gray-950 shrink-0 border-t border-gray-900 flex flex-row min-h-0 relative"
        style={{ height: isBottomOpen ? `${marchBottomHeight}px` : "50px" }}
      >
        {isBottomOpen && (
          <div
            className="absolute top-0 left-0 right-0 h-1.5 -translate-y-1/2 cursor-ns-resize z-50 select-none hover:bg-blue-500/20 active:bg-blue-500/40 transition-colors"
            onMouseDown={handleMouseDown}
          />
        )}
        {/* Content — only rendered when open, takes all space except controls */}
        {isBottomOpen && (
          <div className="flex flex-row flex-1 min-h-0 min-w-0">
            <AccountsTree />
            <ActivePositionsTable />
          </div>
        )}

        {/* Controls pinned to top-right, always 50px tall */}
        <div className="absolute top-0 right-0 h-[50px] flex items-center gap-1.5 pr-6 pl-4 z-30">
          <div className="relative">
            <button
              onClick={(e) => {
                e.stopPropagation();
                setIsPanelPopupOpen(!isPanelPopupOpen);
              }}
              className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-900 rounded transition-colors flex items-center justify-center cursor-pointer border border-transparent"
              title="Panel Layout Options"
            >
              {LAYOUTS.find((l) => l.id === marchLayout)?.icon(14, 14) || (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
                </svg>
              )}
            </button>
            {isPanelPopupOpen && (
              <div
                className="absolute bottom-full right-0 mb-2.5 bg-gray-900/95 backdrop-blur border border-gray-800 rounded-lg shadow-xl p-2 z-50 w-[170px]"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="grid grid-cols-5 gap-1.5">
                  {LAYOUTS.map((layout) => {
                    const isActive = marchLayout === layout.id;
                    return (
                      <button
                        key={layout.id}
                        onClick={() => {
                          setMarchLayout(layout.id);
                          setIsPanelPopupOpen(false);
                        }}
                        title={layout.label}
                        className={`w-7 h-7 flex items-center justify-center rounded transition-all duration-150 cursor-pointer ${
                          isActive
                            ? "bg-white text-black font-semibold shadow-sm"
                            : "text-gray-400 hover:text-white hover:bg-gray-800"
                        }`}
                      >
                        {layout.icon(14, 14)}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          <button
            onClick={() => {
              const nextOpen = !isBottomOpen;
              setIsBottomOpen(nextOpen);
              if (nextOpen && marchBottomHeight <= 100) {
                setMarchBottomHeight(400);
              }
            }}
            className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-900 rounded transition-colors"
            title={isBottomOpen ? "Collapse Section" : "Expand Section"}
          >
            {isBottomOpen ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="6 9 12 15 18 9" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="18 15 12 9 6 15" />
              </svg>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
