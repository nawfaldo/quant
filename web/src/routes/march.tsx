import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useApp } from "../context/AppContext";
import { addAccountStrategy, addMt5Account, displayStrategyName, fetchBacktests, fetchEnvironments, fetchMarchLayouts, fetchMarchSettings, fetchMarchWorkspace, KNOWN_MARCH_STRATEGIES, saveMarchWorkspace } from "../api";
import { TIMEFRAMES, makeDefaultPanelConfig, type Backtest, type Indicators, type LayoutPanelConfig, type MarchWorkspace, type MarchWorkspaceTab, type TF } from "../types";
import ChartPanel from "../components/charts/ChartPanel";
import LiquidGlassSwitch from "../components/buttons/LiquidGlassSwitch";
import { SpinnerIcon } from "../components/ui/icons";
import ModalShell from "../components/ui/ModalShell";

export const Route = createFileRoute('/march')({
  // The root route owns this component's persistent mount.
  component: () => null,
})

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

type MarchTab = MarchWorkspaceTab;

function isMarchWorkspace(value: MarchWorkspace | null): value is MarchWorkspace {
  return value?.version === 1 &&
    typeof value.activeTabId === "string" &&
    Array.isArray(value.tabs) &&
    value.tabs.length > 0 &&
    value.tabs.every((tab) =>
      typeof tab.id === "string" &&
      typeof tab.layout === "string" &&
      typeof tab.bottomOpen === "boolean" &&
      typeof tab.bottomHeight === "number" &&
      (tab.environmentId === null || typeof tab.environmentId === "number") &&
      Array.isArray(tab.backtestIds),
    );
}

function createMarchTabId() {
  return crypto.randomUUID().slice(0, 8);
}

export function MarchWorkspace() {
  const {
    marchLayout,
    setMarchLayout,
    marchLayouts,
    setMarchLayouts,
    updateMarchPanel,
    setActiveMarchPanel,
    activeMarchPanel,
    setIndicatorsOpen,
    indicatorsOpen,
    setModalOpen,
    modalOpen,
    selectedEnvironmentId,
    setSelectedEnvironmentId,
    visibleIds,
    setVisibleIds,
    loadingIds,
    toggleId,
    marchAccountModalOpen,
    setMarchAccountModalOpen,
    selectedAccountId,
    setSelectedAccountId,
    marchStrategyModalOpen,
    setMarchStrategyModalOpen,
    isBottomOpen,
    setIsBottomOpen,
    marchBottomHeight,
    setMarchBottomHeight,
  } = useApp();

  const activeCfg: LayoutPanelConfig =
    (activeMarchPanel && marchLayouts[activeMarchPanel.layout]?.[activeMarchPanel.index]) ||
    makeDefaultPanelConfig();

  const [isPanelPopupOpen, setIsPanelPopupOpen] = useState(false);
  const [isEnvironmentPopupOpen, setIsEnvironmentPopupOpen] = useState(false);
  const { data: environments = [], isLoading: isEnvironmentsLoading } = useQuery({
    queryKey: ["environments"],
    queryFn: fetchEnvironments,
  });
  const initialTabId = useRef(createMarchTabId()).current;
  const [tabs, setTabs] = useState<MarchTab[]>([
    {
      id: initialTabId,
      layout: marchLayout,
      layouts: marchLayouts,
      bottomOpen: isBottomOpen,
      bottomHeight: marchBottomHeight,
      environmentId: selectedEnvironmentId,
      backtestIds: [...visibleIds],
    },
  ]);
  const [activeTabId, setActiveTabId] = useState(initialTabId);
  const draggedIdxRef = useRef<number | null>(null);
  const [activeDragIdx, setActiveDragIdx] = useState<number | null>(null);
  const [editingTabId, setEditingTabId] = useState<string | null>(null);
  const [tabIdDraft, setTabIdDraft] = useState("");
  const tabIdEditorRef = useRef<HTMLSpanElement>(null);
  const [workspaceHydrated, setWorkspaceHydrated] = useState(false);
  // Keep whole-workspace writes ordered. The database stores the workspace as
  // one JSON document, so concurrent POSTs could otherwise let an older tab
  // snapshot (often with a null environment) overwrite a newer selection.
  const workspaceSaveQueueRef = useRef<Promise<void>>(Promise.resolve());
  const saveWorkspaceInOrder = (workspace: MarchWorkspace) => {
    const save = workspaceSaveQueueRef.current
      .catch(() => undefined)
      .then(() => saveMarchWorkspace(workspace));
    workspaceSaveQueueRef.current = save;
    void save.catch((error) => {
      console.error("Could not save March workspace", error);
    });
  };
  const workspaceQuery = useQuery({
    queryKey: ["marchWorkspace"],
    queryFn: fetchMarchWorkspace,
    staleTime: Infinity,
  });
  const shouldMigrateLegacyWorkspace = workspaceQuery.isSuccess && workspaceQuery.data === null;
  const legacySettingsQuery = useQuery({
    queryKey: ["marchSettings"],
    queryFn: fetchMarchSettings,
    staleTime: Infinity,
    enabled: shouldMigrateLegacyWorkspace,
  });
  const legacyLayoutsQuery = useQuery({
    queryKey: ["marchLayouts"],
    queryFn: fetchMarchLayouts,
    staleTime: Infinity,
    enabled: shouldMigrateLegacyWorkspace,
  });

  const snapshotActiveTab = (tab: MarchTab): MarchTab => ({
    ...tab,
    layout: marchLayout,
    layouts: marchLayouts,
    bottomOpen: isBottomOpen,
    bottomHeight: marchBottomHeight,
    environmentId: selectedEnvironmentId,
    backtestIds: [...visibleIds],
  });

  const loadTab = (tab: MarchTab) => {
    setMarchLayout(tab.layout);
    setMarchLayouts(tab.layouts);
    setIsBottomOpen(tab.bottomOpen);
    setMarchBottomHeight(tab.bottomHeight);
    setSelectedEnvironmentId(tab.environmentId);
    setVisibleIds(new Set(tab.backtestIds));
    setActiveMarchPanel(null);
  };

  // Restore the versioned workspace once. Existing single-tab settings are
  // migrated into the first workspace document so previous March setups stay.
  useEffect(() => {
    if (workspaceHydrated || !workspaceQuery.isSuccess) return;

    const storedWorkspace = workspaceQuery.data;
    if (isMarchWorkspace(storedWorkspace)) {
      const activeTab = storedWorkspace.tabs.find((tab) => tab.id === storedWorkspace.activeTabId)
        ?? storedWorkspace.tabs[0];
      setTabs(storedWorkspace.tabs);
      setActiveTabId(activeTab.id);
      loadTab(activeTab);
      setWorkspaceHydrated(true);
      return;
    }

    if (!legacySettingsQuery.data || legacyLayoutsQuery.data === undefined) return;
    const legacySettings = legacySettingsQuery.data;
    const layout = legacySettings.layout ?? "single";
    const layouts = Object.keys(legacyLayoutsQuery.data).length > 0
      ? legacyLayoutsQuery.data
      : { [layout]: [makeDefaultPanelConfig()] };
    const bottomHeight = Number(legacySettings.bottomHeight ?? 400);
    const migratedTab: MarchTab = {
      id: createMarchTabId(),
      layout,
      layouts,
      bottomOpen: legacySettings.bottomOpen === true || legacySettings.bottomOpen === "true",
      bottomHeight: Number.isFinite(bottomHeight) && bottomHeight > 0 ? bottomHeight : 400,
      environmentId: selectedEnvironmentId,
      backtestIds: [...visibleIds],
    };
    setTabs([migratedTab]);
    setActiveTabId(migratedTab.id);
    loadTab(migratedTab);
    setWorkspaceHydrated(true);
  }, [legacyLayoutsQuery.data, legacySettingsQuery.data, selectedEnvironmentId, visibleIds, workspaceHydrated, workspaceQuery.data, workspaceQuery.isSuccess]);

  // Keep the active tab's snapshot current as chart controls are changed.
  useEffect(() => {
    setTabs((prev) =>
      prev.map((tab) =>
        tab.id === activeTabId
          ? snapshotActiveTab(tab)
          : tab,
      ),
    );
  }, [activeTabId, marchLayout, marchLayouts, isBottomOpen, marchBottomHeight, selectedEnvironmentId, visibleIds]);

  useEffect(() => {
    if (!workspaceHydrated) return;
    const workspace: MarchWorkspace = {
      version: 1,
      activeTabId,
      tabs: tabs.map((tab) => tab.id === activeTabId ? snapshotActiveTab(tab) : tab),
    };
    saveWorkspaceInOrder(workspace);
  }, [activeTabId, tabs, marchLayout, marchLayouts, isBottomOpen, marchBottomHeight, selectedEnvironmentId, visibleIds, workspaceHydrated]);

  const activateTab = (tab: MarchTab) => {
    if (tab.id === activeTabId) return;
    setTabs((prev) =>
      prev.map((item) =>
        item.id === activeTabId ? snapshotActiveTab(item) : item,
      ),
    );
    setActiveTabId(tab.id);
    loadTab(tab);
  };

  const addTab = () => {
    const id = createMarchTabId();
    const newTab: MarchTab = {
      id,
      layout: "single",
      layouts: { single: [makeDefaultPanelConfig()] },
      bottomOpen: true,
      bottomHeight: 400,
      environmentId: selectedEnvironmentId,
      backtestIds: [],
    };
    setTabs((prev) => [
      ...prev.map((tab) =>
        tab.id === activeTabId ? snapshotActiveTab(tab) : tab,
      ),
      newTab,
    ]);
    setActiveTabId(id);
    loadTab(newTab);
  };

  const closeTab = (id: string) => {
    if (tabs.length <= 1) return;
    const closingIndex = tabs.findIndex((tab) => tab.id === id);
    const remaining = tabs.filter((tab) => tab.id !== id);
    setTabs(remaining);
    if (id !== activeTabId) return;

    const nextTab = remaining[Math.max(0, closingIndex - 1)];
    setActiveTabId(nextTab.id);
    loadTab(nextTab);
  };

  const beginTabIdEdit = (id: string) => {
    setEditingTabId(id);
    setTabIdDraft(id);
  };

  const commitTabIdEdit = () => {
    if (!editingTabId) return;
    const nextId = (tabIdEditorRef.current?.textContent ?? tabIdDraft).trim();
    if (!nextId || tabs.some((tab) => tab.id !== editingTabId && tab.id === nextId)) return;

    setTabs((prev) => prev.map((tab) => tab.id === editingTabId ? { ...tab, id: nextId } : tab));
    if (activeTabId === editingTabId) setActiveTabId(nextId);
    setEditingTabId(null);
  };

  useEffect(() => {
    if (!editingTabId || !tabIdEditorRef.current) return;
    const editor = tabIdEditorRef.current;
    editor.focus();
    const range = document.createRange();
    range.selectNodeContents(editor);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
  }, [editingTabId]);

  const handleDragStart = (e: React.DragEvent, index: number) => {
    draggedIdxRef.current = index;
    setActiveDragIdx(index);
    e.dataTransfer.effectAllowed = "move";
  };

  const handleDragEnter = (targetIndex: number) => {
    const sourceIndex = draggedIdxRef.current;
    if (sourceIndex === null || sourceIndex === targetIndex) return;
    setTabs((prev) => {
      const next = [...prev];
      [next[sourceIndex], next[targetIndex]] = [next[targetIndex], next[sourceIndex]];
      return next;
    });
    draggedIdxRef.current = targetIndex;
    setActiveDragIdx(targetIndex);
  };

  const handleDragEnd = () => {
    draggedIdxRef.current = null;
    setActiveDragIdx(null);
  };

  useEffect(() => {
    const handleClose = () => {
      setIsPanelPopupOpen(false);
      setIsEnvironmentPopupOpen(false);
    };
    window.addEventListener("click", handleClose);
    window.addEventListener("contextmenu", handleClose);
    return () => {
      window.removeEventListener("click", handleClose);
      window.removeEventListener("contextmenu", handleClose);
    };
  }, []);

  const selectedEnvironment = environments.find((environment) => environment.id === selectedEnvironmentId);

  const selectEnvironment = (environmentId: number) => {
    sessionStorage.setItem("selected_environment_id", String(environmentId));
    setSelectedEnvironmentId(environmentId);
    // Persist this tab immediately. Session storage remains a convenience for
    // the rest of the app, but the source of truth for March tabs is app.db.
    const nextTabs = tabs.map((tab) =>
      tab.id === activeTabId
        ? { ...snapshotActiveTab(tab), environmentId }
        : tab,
    );
    setTabs(nextTabs);
    saveWorkspaceInOrder({
      version: 1,
      activeTabId,
      tabs: nextTabs,
    });
    setIsEnvironmentPopupOpen(false);
  };

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
        noiseArea: stored.indicators.noise_area,
        volume: stored.indicators.volume === true,
        volumeDeltaBubbles: stored.indicators.volume_delta_bubbles === true,
        sessionVolumeProfile: stored.indicators.session_volume_profile === true,
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
      onOpenBacktests: () => {
        setActiveMarchPanel({ layout: marchLayout, index: i });
        setModalOpen(true);
      },
    };
  };

  return (
    <>
      <div className="flex-1 flex flex-col bg-gray-950 min-h-0">
      {/* Browser-style workspace tabs, matching the Test page. */}
      <div className="flex items-end bg-[#121214] pl-0 pr-4 pt-2 border-b border-[#212124] flex-wrap gap-0.5 shrink-0 select-none">
        {tabs.map((tab, idx) => {
          const isActive = tab.id === activeTabId;
          return (
            <div
              key={tab.id}
              onClick={() => activateTab(tab)}
              draggable={editingTabId !== tab.id}
              onDragStart={(e) => handleDragStart(e, idx)}
              onDragOver={(e) => e.preventDefault()}
              onDragEnter={() => handleDragEnter(idx)}
              onDragEnd={handleDragEnd}
              className={`group relative flex items-center h-9 cursor-grab active:cursor-grabbing transition-all duration-150 ${
                isActive ? "z-30" : "z-10"
              } ${activeDragIdx === idx ? "opacity-40" : "opacity-100"} -ml-2.5 first:ml-0`}
              style={{ width: "110px", minWidth: "110px" }}
            >
              <div
                className="absolute inset-0 transition-colors duration-150"
                style={{
                  backgroundColor: isActive ? "#212124" : "#27272a",
                  clipPath: "polygon(12px 0%, calc(100% - 12px) 0%, 100% 100%, 0% 100%)",
                }}
              />
              <div
                className="absolute inset-[1px] bottom-0 transition-colors duration-150"
                style={{
                  backgroundColor: isActive ? "#1A1A1E" : "#0f0f12",
                  clipPath: "polygon(11px 0%, calc(100% - 11px) 0%, 100% 100%, 0% 100%)",
                }}
              />
              <div className="relative z-10 flex items-center justify-center w-full h-full">
                {editingTabId === tab.id ? (
                  <span
                    ref={tabIdEditorRef}
                    contentEditable
                    suppressContentEditableWarning
                    onClick={(e) => e.stopPropagation()}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        commitTabIdEdit();
                      }
                      if (e.key === "Escape") setEditingTabId(null);
                    }}
                    onBlur={() => setEditingTabId(null)}
                    className="max-w-[78px] truncate font-mono text-xs text-white outline-none"
                    aria-label="Edit March tab ID"
                  >
                    {tabIdDraft}
                  </span>
                ) : (
                  <span
                    onDoubleClick={(e) => {
                      e.stopPropagation();
                      beginTabIdEdit(tab.id);
                    }}
                    title="Double-click to edit tab ID"
                    className={`font-mono text-xs cursor-text ${isActive ? "text-white font-bold" : "text-gray-500 font-medium"}`}
                  >
                    {tab.id}
                  </span>
                )}
                {tabs.length > 1 && editingTabId !== tab.id && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      closeTab(tab.id);
                    }}
                    className={`absolute right-3 w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-bold opacity-0 group-hover:opacity-100 bg-gray-800/90 hover:bg-gray-700 transition-all duration-150 ${
                      isActive
                        ? "text-gray-300 hover:text-white"
                        : "text-gray-400 hover:text-white"
                    }`}
                    aria-label={`Close March tab ${tab.id}`}
                  >
                    ✕
                  </button>
                )}
              </div>
            </div>
          );
        })}
        <button
          onClick={addTab}
          className="h-7 w-7 rounded-md flex items-center justify-center text-gray-500 hover:text-white hover:bg-gray-900 transition-colors cursor-pointer ml-3 mb-1 select-none text-lg font-light"
          aria-label="Add March tab"
        >
          +
        </button>
      </div>

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
        className="w-full bg-[#121214] shrink-0 border-t border-[#212124] flex flex-row min-h-0 relative"
        style={{ height: isBottomOpen ? `${marchBottomHeight}px` : "50px" }}
      >
        {isBottomOpen && (
          <div
            className="absolute top-0 left-0 right-0 h-1.5 -translate-y-1/2 cursor-ns-resize z-50 select-none hover:bg-blue-500/20 active:bg-blue-500/40 transition-colors"
            onMouseDown={handleMouseDown}
          />
        )}
        {/* Controls pinned to top-right, always 50px tall */}
        <div className="absolute top-0 right-0 h-[50px] flex items-center gap-1.5 pr-6 pl-4 z-30">
          <div className="relative">
            <button
              onClick={(e) => {
                e.stopPropagation();
                setIsEnvironmentPopupOpen(!isEnvironmentPopupOpen);
              }}
              className="px-2 py-1.5 text-xs text-gray-400 hover:text-white hover:bg-gray-900 rounded transition-colors flex items-center cursor-pointer border border-transparent"
              title="Select an environment"
              aria-haspopup="menu"
              aria-expanded={isEnvironmentPopupOpen}
            >
              {selectedEnvironment ? (
                <span className="max-w-[140px] truncate">{selectedEnvironment.name}</span>
              ) : (
                <span className="whitespace-nowrap">
                  Select an Env<sup className="relative top-0.5 ml-0.5 text-[18px] font-medium leading-none">*</sup>
                </span>
              )}
            </button>
            {isEnvironmentPopupOpen && (
              <div
                className="absolute bottom-full right-0 mb-2.5 liquid-glass-dropdown march-panel-layout-popup rounded-lg p-2 z-50 min-w-[180px] max-w-[260px]"
                onClick={(e) => e.stopPropagation()}
                role="menu"
              >
                <div className="absolute -bottom-[6px] right-2 h-2.5 w-2.5 rotate-45 march-panel-layout-popup-arrow" />
                {isEnvironmentsLoading ? (
                  <p className="px-2 py-1.5 text-xs text-gray-500">Loading environments…</p>
                ) : environments.length === 0 ? (
                  <p className="px-2 py-1.5 text-xs text-gray-500">No environments yet</p>
                ) : (
                  <div className="flex flex-col gap-1">
                    {environments.map((environment) => {
                      const isActive = environment.id === selectedEnvironmentId;
                      return (
                        <button
                          key={environment.id}
                          onClick={() => selectEnvironment(environment.id)}
                          className={`w-full px-2 py-1.5 rounded text-left text-xs truncate transition-colors cursor-pointer ${
                            isActive
                              ? "bg-white text-black font-semibold"
                              : "text-gray-300 hover:text-white hover:bg-gray-800"
                          }`}
                          title={environment.name}
                          role="menuitem"
                        >
                          {environment.name}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
          </div>

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
                className="absolute bottom-full right-0 mb-2.5 liquid-glass-dropdown march-panel-layout-popup rounded-lg p-2 z-50 w-[170px]"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="absolute -bottom-[6px] right-2 h-2.5 w-2.5 rotate-45 march-panel-layout-popup-arrow" />
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

      <IndicatorsModal
        open={indicatorsOpen}
        onClose={() => setIndicatorsOpen(false)}
        indicators={activeCfg.indicators}
        onToggle={(key) => {
          if (!activeMarchPanel) return;
          updateMarchPanel(activeMarchPanel.layout, activeMarchPanel.index, {
            indicators: { ...activeCfg.indicators, [key]: !activeCfg.indicators[key] },
          });
        }}
      />
      <AccountModal
        open={marchAccountModalOpen}
        onClose={() => setMarchAccountModalOpen(false)}
        onAdded={setSelectedAccountId}
      />
      <StrategyModal
        open={marchStrategyModalOpen}
        onClose={() => setMarchStrategyModalOpen(false)}
        accountId={selectedAccountId}
      />
      <BacktestsModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        visibleIds={visibleIds}
        loadingIds={loadingIds}
        onToggle={toggleId}
        activeSymbol={activeCfg.symbol}
        environmentId={selectedEnvironmentId}
      />
    </>
  );
}

function AccountModal({ open, onClose, onAdded }: {
  open: boolean;
  onClose: () => void;
  onAdded: (id: number) => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [login, setLogin] = useState("");
  const [server, setServer] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) {
      setName("");
      setLogin("");
      setServer("");
      setBusy(false);
    }
  }, [open]);

  async function handleSave() {
    if (!login.trim() || busy) return;
    setBusy(true);
    try {
      const id = await addMt5Account({ name: name.trim(), login: login.trim(), server: server.trim() });
      await queryClient.invalidateQueries({ queryKey: ["mt5Accounts"] });
      onAdded(id);
      onClose();
    } catch (error) {
      console.error("Failed to add MT5 account:", error);
      setBusy(false);
    }
  }

  return (
    <ModalShell open={open} onClose={onClose} title="Add MT5 Account" className="bg-[#222831]">
      <div className="px-4 py-3 flex flex-col gap-3">
        <ModalField label="Name" value={name} onChange={setName} placeholder="My account" />
        <ModalField label="Login" value={login} onChange={setLogin} placeholder="12345678" />
        <ModalField label="Server" value={server} onChange={setServer} placeholder="Broker-Server" />
      </div>
      <ModalActions
        busy={busy}
        disabled={!login.trim()}
        submitLabel="Add account"
        onClose={onClose}
        onSubmit={() => void handleSave()}
      />
    </ModalShell>
  );
}

function StrategyModal({ open, onClose, accountId }: {
  open: boolean;
  onClose: () => void;
  accountId: number | null;
}) {
  const queryClient = useQueryClient();
  const [strategy, setStrategy] = useState<string>(KNOWN_MARCH_STRATEGIES[0]);
  const [symbol, setSymbol] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) {
      setStrategy(KNOWN_MARCH_STRATEGIES[0]);
      setSymbol("");
      setBusy(false);
    }
  }, [open]);

  async function handleSave() {
    if (!symbol.trim() || accountId === null || busy) return;
    setBusy(true);
    try {
      await addAccountStrategy(accountId, { strategy, symbol: symbol.trim() });
      await queryClient.invalidateQueries({ queryKey: ["accountStrategies", accountId] });
      onClose();
    } catch (error) {
      console.error("Failed to add strategy:", error);
      setBusy(false);
    }
  }

  return (
    <ModalShell open={open} onClose={onClose} title="Add Strategy" className="bg-[#222831]">
      <div className="px-4 py-3 flex flex-col gap-3">
        <div className="flex items-center gap-3">
          <label className="w-20 text-xs text-gray-500 shrink-0">Strategy</label>
          <select
            value={strategy}
            onChange={(event) => setStrategy(event.target.value)}
            className="flex-1 bg-black/20 border border-white/10 text-sm text-gray-200 rounded-md px-3 py-1.5 outline-none focus:border-white/25 transition-colors cursor-pointer"
          >
            {KNOWN_MARCH_STRATEGIES.map((value) => (
              <option key={value} value={value}>{displayStrategyName(value)}</option>
            ))}
          </select>
        </div>
        <ModalField label="Symbol" value={symbol} onChange={setSymbol} placeholder="NAS100" />
      </div>
      <ModalActions
        busy={busy}
        disabled={!symbol.trim() || accountId === null}
        submitLabel="Add strategy"
        onClose={onClose}
        onSubmit={() => void handleSave()}
      />
    </ModalShell>
  );
}

const INDICATOR_ROWS: { key: keyof Indicators; label: string }[] = [
  { key: "session_volume_profile", label: "Session Volume Profile" },
  { key: "volume", label: "Volume Bars" },
  { key: "volume_delta_bubbles", label: "Volume Delta Bubbles" },
  { key: "vwap", label: "VWAP" },
  { key: "noise_area", label: "Noise Area" },
];

function IndicatorsModal({ open, onClose, indicators, onToggle }: {
  open: boolean;
  onClose: () => void;
  indicators: Indicators;
  onToggle: (key: keyof Indicators) => void;
}) {
  return (
    <ModalShell open={open} onClose={onClose} title="Indicators" className="h-[300px]">
      <div className="px-2 py-2">
        {INDICATOR_ROWS.map(({ key, label }) => (
          <button
            key={key}
            onClick={() => onToggle(key)}
            aria-pressed={indicators[key] === true}
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-md hover:bg-white/5 transition-colors text-left"
          >
            <span className="flex-1 text-sm text-gray-300">{label}</span>
            <LiquidGlassSwitch checked={indicators[key] === true} />
          </button>
        ))}
      </div>
    </ModalShell>
  );
}

function BacktestsModal({ open, onClose, visibleIds, loadingIds, onToggle, activeSymbol, environmentId }: {
  open: boolean;
  onClose: () => void;
  visibleIds: Set<number>;
  loadingIds: Set<number>;
  onToggle: (id: number) => void;
  activeSymbol: string;
  environmentId: number | null;
}) {
  const { data: backtests, isLoading, isError } = useQuery({
    queryKey: ["backtests"],
    queryFn: fetchBacktests,
    enabled: open,
    staleTime: Infinity,
  });
  const filteredBacktests = backtests?.filter(
    (backtest) => backtest.environment_id === environmentId && backtest.symbol.toLowerCase() === activeSymbol.toLowerCase(),
  );

  return (
    <ModalShell open={open} onClose={onClose} title="Backtests" className="h-[300px]">
      <div className="overflow-y-auto px-3 pb-3 space-y-1 mt-3">
        {isLoading && <div className="text-xs text-gray-600 text-center py-6">Loading…</div>}
        {isError && <div className="text-xs text-red-400 text-center py-6">Failed to load backtests</div>}
        {!isLoading && !isError && environmentId === null && (
          <div className="text-xs text-gray-600 text-center py-6">Select an environment first</div>
        )}
        {!isLoading && !isError && environmentId !== null && filteredBacktests?.length === 0 && (
          <div className="text-xs text-gray-600 text-center py-6">No backtests for this environment and symbol</div>
        )}
        {filteredBacktests?.map((backtest) => (
          <BacktestRow
            key={backtest.id}
            backtest={backtest}
            visible={visibleIds.has(backtest.id)}
            loading={loadingIds.has(backtest.id)}
            onToggle={() => onToggle(backtest.id)}
          />
        ))}
      </div>
    </ModalShell>
  );
}

function BacktestRow({ backtest, visible, loading, onToggle }: {
  backtest: Backtest;
  visible: boolean;
  loading: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3 px-1 py-2 rounded-lg">
      <span className="text-xs text-gray-300 truncate">
        {backtest.strategy} <span className="text-gray-600">#{backtest.id}</span>
      </span>
      {loading ? <SpinnerIcon /> : (
        <button
          onClick={onToggle}
          type="button"
          role="switch"
          aria-checked={visible}
          aria-label={`${visible ? "Hide" : "Show"} ${backtest.strategy} backtest`}
          className="flex-shrink-0 rounded-full focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 focus-visible:ring-offset-2 focus-visible:ring-offset-[#1A1A1E]"
        >
          <LiquidGlassSwitch checked={visible} />
        </button>
      )}
    </div>
  );
}

function ModalField({ label, value, onChange, placeholder }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="flex items-center gap-3">
      <label className="w-20 text-xs text-gray-500 shrink-0">{label}</label>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="flex-1 bg-black/20 border border-white/10 text-sm text-gray-200 rounded-md px-3 py-1.5 outline-none focus:border-white/25 transition-colors placeholder:text-gray-600"
      />
    </div>
  );
}

function ModalActions({ busy, disabled, submitLabel, onClose, onSubmit }: {
  busy: boolean;
  disabled: boolean;
  submitLabel: string;
  onClose: () => void;
  onSubmit: () => void;
}) {
  return (
    <div className="px-4 py-3 flex items-center justify-end gap-2">
      <button onClick={onClose} className="px-3 py-1.5 text-xs font-medium rounded-md text-gray-400 hover:text-gray-200 hover:bg-white/5 transition-colors">
        Cancel
      </button>
      <button
        onClick={onSubmit}
        disabled={disabled || busy}
        className={`px-4 py-1.5 text-xs font-medium rounded-md transition-colors ${
          !disabled && !busy ? "bg-emerald-600 text-white hover:bg-emerald-500" : "bg-gray-700/60 text-gray-500 cursor-default"
        }`}
      >
        {busy ? "Saving…" : submitLabel}
      </button>
    </div>
  );
}
