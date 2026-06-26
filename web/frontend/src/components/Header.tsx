import { useState, useEffect } from "react";
import { TIMEFRAMES, type TF } from "../types";

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
}

function dateToDisplay(iso: string): string {
  if (!iso) return "";
  const [year, month, day] = iso.split("-");
  return `${month}/${day}/${year.slice(2)}`;
}

function displayToIso(display: string): string {
  if (!display) return "";
  const parts = display.split("/");
  if (parts.length !== 3) return "";
  const [mm, dd, yy] = parts;
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
}: Props) {
  const [marchDraftFrom, setMarchDraftFrom] = useState(dateToDisplay(fromDate));
  const [marchDraftTo, setMarchDraftTo] = useState(mode === "latest" ? "Now" : dateToDisplay(toDate));
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

  return (
    <div className="h-[52px] px-5 border-b border-gray-800/60 flex items-center gap-3 bg-gray-950/95 backdrop-blur-sm shrink-0">
      {/* Symbol selector */}
      <select
        value={symbol}
        onChange={(e) => setSymbol(e.target.value as "nq" | "es")}
        className="bg-gray-900 border border-gray-800/80 text-xs font-medium text-gray-200 rounded-lg px-2.5 py-1.5 outline-none cursor-pointer hover:border-gray-700 transition-colors shrink-0"
      >
        <option value="nq">NQ</option>
        <option value="es">ES</option>
      </select>

      {/* Timeframe selector */}
      <select
        value={tf.table}
        onChange={(e) => {
          const selectedTf = TIMEFRAMES.find((t) => t.table === e.target.value);
          if (selectedTf) setTf(selectedTf);
        }}
        className="bg-gray-900 border border-gray-800/80 text-xs font-medium text-gray-200 rounded-lg px-2.5 py-1.5 outline-none cursor-pointer hover:border-gray-700 transition-colors shrink-0"
      >
        {TIMEFRAMES.map((t) => (
          <option key={t.table} value={t.table}>
            {t.label}
          </option>
        ))}
      </select>

      {/* Date range — static history when applied (range mode) */}
      <div className="flex items-center gap-1 bg-gray-900 rounded-lg p-0.5 px-2 border border-gray-800/80 shrink-0">
        <input
          type="text"
          value={marchDraftFrom}
          onChange={(e) => setMarchDraftFrom(e.target.value)}
          placeholder="MM/DD/YY"
          style={{ width: `${Math.max(3, marchDraftFrom.length || 8)}ch` }}
          className={`bg-transparent text-xs font-mono outline-none py-1 transition-all duration-200 ${
            marchDraftFrom ? "text-gray-200" : "text-gray-500"
          }`}
        />
        <span className="text-[10px] font-light select-none text-gray-600">—</span>
        <input
          type="text"
          value={marchDraftTo}
          onChange={(e) => setMarchDraftTo(e.target.value)}
          placeholder="MM/DD/YY"
          style={{ width: `${Math.max(3, marchDraftTo.length || 8)}ch` }}
          className={`bg-transparent text-xs font-mono outline-none py-1 transition-all duration-200 ${
            marchDraftLatest
              ? "text-white"
              : marchDraftTo
                ? "text-gray-200"
                : "text-gray-500"
          }`}
        />
        <button
          onClick={() =>
            marchDraftLatest
              ? onLatest(displayToIso(marchDraftFrom))
              : onApplyRange(displayToIso(marchDraftFrom), displayToIso(marchDraftTo))
          }
          disabled={!marchDirty || !marchDraftFrom || (!marchDraftLatest && !marchDraftTo)}
          title={marchDraftLatest ? "Apply new start date (keep streaming)" : "Show static history for this range"}
          className={`ml-1 px-2 py-1 text-[11px] font-medium rounded-md transition-all duration-150 shrink-0 ${
            marchDirty && marchDraftFrom && (marchDraftLatest || marchDraftTo)
              ? "bg-blue-600 text-white hover:bg-blue-500"
              : "bg-gray-800/50 text-gray-600 cursor-default"
          }`}
        >
          Apply
        </button>
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
