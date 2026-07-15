import { useState, useEffect } from "react";
import { TIMEFRAMES, type TF } from "../../types";

interface Props {
  symbol: "nq" | "es";
  setSymbol: (sym: "nq" | "es") => void;
  tf: TF;
  setTf: (tf: TF) => void;
  streamStatus: "loading" | "live" | "idle" | "error";
  mode: "latest" | "range";
  fromDate: string;
  toDate: string;
  onApplyRange: (from: string, to: string) => void;
  onLatest: (from: string) => void;
  onOpenIndicators: () => void;
  onOpenBacktests: () => void;
  showFxNq: boolean;
  onToggleFxNq: () => void;
}

function dateToDisplay(iso: string): string {
  if (!iso) return "";
  const [year, month, day] = iso.split("-");
  return `${day}/${month}/${year.slice(2)}`;
}

function displayToIso(display: string): string {
  if (!display) return "";
  const parts = display.split("/");
  if (parts.length !== 3) return "";
  const [dd, mm, yy] = parts;
  if (!mm || !dd || !yy) return "";
  const year = yy.length === 4 ? yy : `20${yy}`;
  return `${year}-${mm.padStart(2, "0")}-${dd.padStart(2, "0")}`;
}

export default function Header({
  symbol,
  setSymbol,
  tf,
  setTf,
  streamStatus,
  mode,
  fromDate,
  toDate,
  onApplyRange,
  onLatest,
  onOpenIndicators,
  onOpenBacktests,
  showFxNq,
  onToggleFxNq,
}: Props) {
  const [marchDraftFrom, setMarchDraftFrom] = useState(dateToDisplay(fromDate));
  const [marchDraftTo, setMarchDraftTo] = useState(mode === "latest" ? "Now" : dateToDisplay(toDate));
  const [isActionsOpen, setIsActionsOpen] = useState(false);
  const marchDraftLatest = marchDraftTo.trim().toLowerCase() === "now";

  useEffect(() => {
    setMarchDraftFrom(dateToDisplay(fromDate));
  }, [fromDate]);
  useEffect(() => {
    setMarchDraftTo(mode === "latest" ? "Now" : dateToDisplay(toDate));
  }, [toDate, mode]);

  const marchDirty =
    marchDraftLatest !== (mode === "latest") ||
    marchDraftFrom !== dateToDisplay(fromDate) ||
    (!marchDraftLatest && marchDraftTo !== dateToDisplay(toDate));

  const applyMarchRange = () => {
    if (!marchDirty || !marchDraftFrom || (!marchDraftLatest && !marchDraftTo)) return;
    if (marchDraftLatest) onLatest(displayToIso(marchDraftFrom));
    else onApplyRange(displayToIso(marchDraftFrom), displayToIso(marchDraftTo));
  };

  const applyOnEnter = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    applyMarchRange();
  };

  return (
    <div className="absolute top-3 left-3 z-50 flex items-center gap-3">
      {/* Symbol selector */}
      <div className="march-liquid-select shrink-0">
        <select
          value={symbol}
          onChange={(e) => setSymbol(e.target.value as "nq" | "es")}
          className="march-liquid-select-input"
          aria-label="Symbol"
        >
          <option value="nq">NQ</option>
          <option value="es">ES</option>
        </select>
      </div>

      {/* Timeframe selector */}
      <div className="march-liquid-select shrink-0">
        <select
          value={tf.table}
          onChange={(e) => {
            const selectedTf = TIMEFRAMES.find((t) => t.table === e.target.value);
            if (selectedTf) setTf(selectedTf);
          }}
          className="march-liquid-select-input"
          aria-label="Timeframe"
        >
          {TIMEFRAMES.map((t) => (
            <option key={t.table} value={t.table}>
              {t.label}
            </option>
          ))}
        </select>
      </div>

      {/* Date range — static history when applied (range mode) */}
      <div className="march-liquid-date-range shrink-0">
        <div className="march-liquid-date-from-group">
          <div className="march-liquid-date march-liquid-date-from">
            <input
              type="text"
              value={marchDraftFrom}
              onChange={(e) => setMarchDraftFrom(e.target.value)}
              onKeyDown={applyOnEnter}
              placeholder="DD/MM/YY"
              style={{ width: `${Math.max(3, marchDraftFrom.length || 8)}ch` }}
              className={`bg-transparent text-xs font-mono outline-none py-1 transition-all duration-200 ${
                marchDraftFrom ? "text-gray-200" : "text-gray-500"
              }`}
            />
          </div>
          <span className="march-liquid-date-separator" role="separator" aria-label="Date range to" />
        </div>
        <div className="march-liquid-date march-liquid-date-to">
          <input
            type="text"
            value={marchDraftTo}
            onChange={(e) => setMarchDraftTo(e.target.value)}
            onKeyDown={applyOnEnter}
            placeholder="DD/MM/YY"
            style={{ width: `${Math.max(3, marchDraftTo.length || 8)}ch` }}
            className={`bg-transparent text-xs font-mono outline-none py-1 transition-all duration-200 ${
              marchDraftLatest
                ? "text-white"
                : marchDraftTo
                  ? "text-gray-200"
                  : "text-gray-500"
            }`}
          />
        </div>
      </div>

      <div className="relative shrink-0">
        <button
          type="button"
          className="w-8 h-8 liquid-glass-btn"
          title="Chart actions"
          aria-label="Chart actions"
          aria-expanded={isActionsOpen}
          onClick={(event) => {
            event.stopPropagation();
            setIsActionsOpen((open) => !open);
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <circle cx="5" cy="12" r="1" />
            <circle cx="12" cy="12" r="1" />
            <circle cx="19" cy="12" r="1" />
          </svg>
        </button>

        {isActionsOpen && (
          <>
            <div className="fixed inset-0 z-30" onClick={() => setIsActionsOpen(false)} />
            <div className="absolute left-full top-1/2 z-40 ml-3 w-28 -translate-y-[16px] liquid-glass-dropdown rounded-lg py-1 text-left flex flex-col">
              <div className="absolute -left-[6px] top-[16px] h-2.5 w-2.5 -translate-y-1/2 rotate-45 liquid-glass-dropdown-arrow" />
              <button
                type="button"
                onClick={() => {
                  setIsActionsOpen(false);
                  onOpenIndicators();
                }}
                className="relative z-10 w-full px-3 py-1.5 text-left text-xs text-gray-200 hover:bg-white/10 transition-colors cursor-pointer"
              >
                Indicators
              </button>
              <button
                type="button"
                onClick={() => {
                  setIsActionsOpen(false);
                  onOpenBacktests();
                }}
                className="relative z-10 w-full px-3 py-1.5 text-left text-xs text-gray-200 hover:bg-white/10 transition-colors cursor-pointer"
              >
                Backtests
              </button>
              {symbol === "nq" && (
                <button
                  type="button"
                  onClick={() => {
                    setIsActionsOpen(false);
                    onToggleFxNq();
                  }}
                  className="relative z-10 w-full px-3 py-1.5 text-left text-xs text-gray-200 hover:bg-white/10 transition-colors cursor-pointer"
                >
                  {showFxNq ? "Hide USTEC" : "Show USTEC"}
                </button>
              )}
            </div>
          </>
        )}
      </div>

      {/* Stream status indicator */}
      {marchDraftLatest && (
        <div className="flex items-center gap-1.5 px-2 shrink-0">
          {streamStatus === 'loading' && (
            <>
              <span className="w-2 h-2 rounded-full bg-gray-500 animate-pulse" />
              <span className="text-[11px] text-gray-500">Loading</span>
            </>
          )}
          {streamStatus === 'live' && (
            <>
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
              </span>
              <span className="text-[11px] text-green-400">Live</span>
            </>
          )}
          {streamStatus === 'idle' && (
            <>
              <span className="w-2 h-2 rounded-full bg-yellow-600" />
              <span className="text-[11px] text-yellow-600">Idle</span>
            </>
          )}
          {streamStatus === 'error' && (
            <>
              <span className="w-2 h-2 rounded-full bg-red-500" />
              <span className="text-[11px] text-red-400">Error</span>
            </>
          )}
        </div>
      )}

      {/* Spacer */}
      <div className="flex-1" />
    </div>
  );
}
