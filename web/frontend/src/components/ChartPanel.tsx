import { useRef, useEffect, useState } from "react";
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  ColorType,
  CrosshairMode,
  type UTCTimestamp,
} from "lightweight-charts";
import {
  ActivePositionsPrimitive,
  type ActivePosInfo,
  HistoricalTradesPrimitive,
  type HistoricalTradeInfo,
  TradeLinesPrimitive,
} from "../lib/primitives";
import { useQuery } from "@tanstack/react-query";
import { useApp } from "../context/AppContext";
import { fetchActivePositions, fetchLiveTradeHistory } from "../api";
import { BACKEND_URL, type Bar, type TF } from "../types";
import Header from "./Header";

// Per-panel chart configuration. Each ChartPanel runs its own data fetch and
// live stream against this config, independent of the other panels.
export interface PanelConfig {
  symbol: "nq" | "es";
  tf: TF;
  mode: "latest" | "range";
  fromDate: string;
  toDate: string;
  vwap: boolean;
}

interface ChartPanelProps {
  config: PanelConfig;
  setSymbol: (sym: "nq" | "es") => void;
  setTf: (tf: TF) => void;
  onApplyRange: (from: string, to: string) => void;
  onLatest: (from: string) => void;
  onOpenIndicators: () => void;
  onOpenBacktests: () => void;
}

function matchesMarchSymbol(
  posSymbol: string,
  marchSymbol: "nq" | "es",
): boolean {
  const ps = posSymbol.toLowerCase();
  if (marchSymbol === "nq") {
    return ps.includes("nq") || ps.includes("ustec") || ps.includes("nas");
  } else if (marchSymbol === "es") {
    return ps.includes("es") || ps.includes("us500") || ps.includes("spx");
  }
  return false;
}

interface Tick {
  ts: number; // nanoseconds
  price: number;
  size: number;
  side: "BUY" | "SELL";
  sym?: string; // present on WS pushes (addon broadcasts all instruments)
}

// The Bookmap addon's live-push WebSocket server (runs on the Windows host,
// same machine as the browser). Bypasses QuestDB + the Zig backend for sub-100ms ticks.
const MARCH_WS_PORT = 8765;

// RTH open, in minutes since ET midnight. The 24h VWAP re-anchors here (and at
// midnight) — see web/backend/src/cache.zig buildVwap for the matching server-side
// computation used by the main web chart.
const RTH_OPEN_MIN  = 9 * 60 + 30; // 09:30 ET
const RTH_CLOSE_MIN = 16 * 60;     // 16:00 ET

async function fetchMarchCandles(
  symbol: string,
  tf: TF,
  from?: string,
  to?: string,
): Promise<Bar[]> {
  const params = new URLSearchParams({ symbol, tf: tf.table });
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  const res = await fetch(
    `${BACKEND_URL}/api/march/candles/bin?${params.toString()}`,
  );
  if (!res.ok) throw new Error(`Backend error: ${res.status}`);
  const buf = await res.arrayBuffer();
  const view = new DataView(buf);
  if (view.getUint32(0, true) !== 0x45444c43)
    throw new Error("Bad response magic");
  const count = view.getUint32(4, true);
  const data: Bar[] = new Array(count);
  let off = 8;
  for (let i = 0; i < count; i++) {
    data[i] = {
      time: view.getUint32(off, true) as UTCTimestamp,
      open: view.getFloat32(off + 4, true),
      high: view.getFloat32(off + 8, true),
      low: view.getFloat32(off + 12, true),
      close: view.getFloat32(off + 16, true),
      volume: view.getFloat32(off + 20, true),
    };
    off += 24;
  }
  return data;
}

// fx_nq overlay candles: same binary format as the march candles, aggregated
// server-side from the fx_nq_ticks tick table. No symbol param — the table is
// NQ-specific.
async function fetchFxNqCandles(
  tf: TF,
  from?: string,
  to?: string,
): Promise<Bar[]> {
  const params = new URLSearchParams({ tf: tf.table });
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  const res = await fetch(
    `${BACKEND_URL}/api/march/fx-candles/bin?${params.toString()}`,
  );
  if (!res.ok) throw new Error(`Backend error: ${res.status}`);
  const buf = await res.arrayBuffer();
  const view = new DataView(buf);
  if (view.getUint32(0, true) !== 0x45444c43)
    throw new Error("Bad response magic");
  const count = view.getUint32(4, true);
  const data: Bar[] = new Array(count);
  let off = 8;
  for (let i = 0; i < count; i++) {
    data[i] = {
      time: view.getUint32(off, true) as UTCTimestamp,
      open: view.getFloat32(off + 4, true),
      high: view.getFloat32(off + 8, true),
      low: view.getFloat32(off + 12, true),
      close: view.getFloat32(off + 16, true),
      volume: view.getFloat32(off + 20, true),
    };
    off += 24;
  }
  return data;
}

const TZ = "UTC";

const timeFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: TZ,
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const tickTimeFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: TZ,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const tickDateFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: TZ,
  year: "numeric",
  month: "short",
  day: "numeric",
});

// Inserts whitespace entries for every missing TF slot so lightweight-charts
// renders real time gaps instead of compressing bars together.
function fillGaps(
  candles: Bar[],
  tfSecs: number,
): Array<Bar | { time: UTCTimestamp }> {
  if (candles.length === 0) return [];
  const out: Array<Bar | { time: UTCTimestamp }> = [];
  // Only fill gaps that are smaller than 2 hours to avoid filling weekends, holidays, and night sessions
  const maxGapToFill = Math.max(7200, 100 * tfSecs);

  out.push(candles[0]);
  for (let i = 1; i < candles.length; i++) {
    const prev = candles[i - 1];
    const curr = candles[i];
    const gap = (curr.time as number) - (prev.time as number);
    if (gap > tfSecs && gap <= maxGapToFill) {
      for (let t = (prev.time as number) + tfSecs; t < (curr.time as number); t += tfSecs) {
        out.push({ time: t as UTCTimestamp });
      }
    }
    out.push(curr);
  }
  return out;
}

export default function ChartPanel({
  config,
  setSymbol,
  setTf,
  onApplyRange,
  onLatest,
  onOpenIndicators,
  onOpenBacktests,
}: ChartPanelProps) {
  const { symbol, tf, mode, fromDate, toDate, vwap } = config;

  const containerRef = useRef<HTMLDivElement>(null);
  const { visibleTradeStrategies, allTrades, allFxTrades } = useApp();
  // Latest fx trades, read inside the pane-creation effect without making it a
  // dependency (that effect re-fetches candles; trade updates flow via a separate
  // effect instead).
  const allFxTradesRef = useRef(allFxTrades);
  allFxTradesRef.current = allFxTrades;
  const [streamStatus, setStreamStatus] = useState<
    "loading" | "live" | "idle" | "error"
  >("idle");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [chartSeries, setChartSeries] = useState<any>(null);
  const chartRef = useRef<any>(null);
  const vwapSeriesRef = useRef<any>(null);
  const fxSeriesRef = useRef<any>(null);
  const [showFxNq, setShowFxNq] = useState(false);
  const activePositionsPlugin = useRef(new ActivePositionsPrimitive());
  const historicalTradesPlugin = useRef(new HistoricalTradesPrimitive());
  const tradeLinesPrimitive = useRef(new TradeLinesPrimitive());
  const fxTradeLinesPrimitive = useRef(new TradeLinesPrimitive());
  const [chartContextMenu, setChartContextMenu] = useState<{ x: number; y: number } | null>(null);

  useEffect(() => {
    const handleClose = () => setChartContextMenu(null);
    window.addEventListener("click", handleClose);
    window.addEventListener("contextmenu", handleClose);
    return () => {
      window.removeEventListener("click", handleClose);
      window.removeEventListener("contextmenu", handleClose);
    };
  }, []);

  const { data: positions } = useQuery({
    queryKey: ["activePositions"],
    queryFn: fetchActivePositions,
    refetchInterval: 2000,
    retry: false,
  });

  const { data: liveTrades } = useQuery({
    queryKey: ["liveTradeHistory"],
    queryFn: fetchLiveTradeHistory,
    refetchInterval: 5000,
    retry: false,
  });

  useEffect(() => {
    if (!containerRef.current) return;

    let active = true;
    let ws: WebSocket | null = null;
    let reconnectId: any = null;
    let idleTimerId: any = null;

    // Create chart
    const chart = createChart(containerRef.current, {
      autoSize: true,
      ...({ attributionLogo: false } as any),
      layout: {
        background: { type: ColorType.Solid, color: "#030712" },
        textColor: "#d1d5db",
        panes: {
          separatorColor: "#030712",
          separatorHoverColor: "#030712",
        },
      },
      grid: {
        vertLines: { color: "#111827" },
        horzLines: { color: "#111827" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "#374151" },
        horzLine: { color: "#374151" },
      },
      localization: {
        timeFormatter: (time: number) =>
          timeFormatter.format(new Date(time * 1000)),
      },
      timeScale: {
        borderColor: "#1f2937",
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: number, tickMarkType: number) => {
          const date = new Date(time * 1000);
          return tickMarkType >= 3
            ? tickTimeFormatter.format(date)
            : tickDateFormatter.format(date);
        },
      },
      rightPriceScale: { borderColor: "#1f2937" },
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderUpColor: "#22c55e",
      borderDownColor: "#ef4444",
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
      lastValueVisible: true,
      priceLineVisible: true,
    });
    setChartSeries(series);
    chartRef.current = chart;
    series.attachPrimitive(activePositionsPlugin.current);
    series.attachPrimitive(historicalTradesPlugin.current);
    series.attachPrimitive(tradeLinesPrimitive.current);

    const vwapSeries = chart.addSeries(LineSeries, {
      color: "#60a5fa",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      visible: vwap && symbol === "nq",
    });
    vwapSeriesRef.current = vwapSeries;

    // The fx_nq overlay candles live in a SECOND pane (created on demand by the
    // toggle effect below), so NQ keeps its own full pane + price axis on top and
    // fx_nq stacks underneath with its own axis.

    // Streaming state — shared between the catch-up fetch and the live WS feed.
    let lastCandle: Bar | null = null;
    // Highest tick timestamp (ns) seen so far; null means "give me the latest".
    let lastTickNanos: number | null = null;

    // 24h VWAP state — typical price (h+l+c)/3 weighted by bar volume, with the
    // running sums RE-ANCHORED (reset) at THREE points each ET day: midnight,
    // RTH open (09:30), and RTH close (16:00). This gives three sessions per day:
    //   overnight (00:00–09:30), RTH (09:30–16:00), after-hours (16:00–00:00).
    // Without the 16:00 reset, RTH volume dwarfs evening volume and the line
    // looks flat after close. sessionPv/sessionVol cover COMPLETED bars in the
    // current session; formingVol is the volume of the bar being built from ticks.
    let curDay: number = -1;
    let rthAnchored: boolean = false;
    let rthClosed: boolean = false;
    let sessionPv: number = 0;
    let sessionVol: number = 0;
    let formingVol: number = 0;

    // Apply one batch of ticks to the chart. Never throws — bad ticks are
    // skipped individually so a single rejected bar can't stop the stream.
    function applyTicks(ticks: Tick[]) {
      const tfSecs = tf.seconds;
      const isNq = symbol === "nq";

      for (const tick of ticks) {
        if (!Number.isFinite(tick.ts) || !Number.isFinite(tick.price)) continue;
        if (lastTickNanos === null || tick.ts > lastTickNanos)
          lastTickNanos = tick.ts;

        const tsSecs = Math.floor(tick.ts / 1_000_000_000);
        const size = Number.isFinite(tick.size) ? tick.size : 0;

        const candleStartSecs = Math.floor(tsSecs / tfSecs) * tfSecs;
        const lastTime = lastCandle ? (lastCandle.time as number) : -1;

        try {
          if (lastCandle && candleStartSecs === lastTime) {
            lastCandle.close = tick.price;
            if (tick.price > lastCandle.high) lastCandle.high = tick.price;
            if (tick.price < lastCandle.low) lastCandle.low = tick.price;
            series.update(lastCandle);
            formingVol += size;
          } else if (candleStartSecs > lastTime) {
            // A new bar starts. Re-anchor (reset the running sums) at midnight and
            // at RTH open (09:30), keyed on the NEW bar's day/minute so the live
            // breaks line up exactly with the historical block. If this bar
            // re-anchored, the just-completed bar belonged to the previous session
            // and is NOT folded in; otherwise fold it into the session sums.
            const barDay = Math.floor(candleStartSecs / 86400);
            const barMin = Math.floor((candleStartSecs % 86400) / 60);
            let reset = false;
            if (barDay !== curDay) {
              curDay = barDay;
              rthAnchored = false;
              rthClosed = false;
              sessionPv = 0;
              sessionVol = 0;
              reset = true;
            }
            if (!rthAnchored && barMin >= RTH_OPEN_MIN) {
              rthAnchored = true;
              rthClosed = false;
              sessionPv = 0;
              sessionVol = 0;
              reset = true;
            }
            if (rthAnchored && !rthClosed && barMin >= RTH_CLOSE_MIN) {
              rthClosed = true;
              sessionPv = 0;
              sessionVol = 0;
              reset = true;
            }
            if (lastCandle && !reset) {
              const t =
                (lastCandle.high + lastCandle.low + lastCandle.close) / 3;
              sessionPv += t * formingVol;
              sessionVol += formingVol;
            }
            formingVol = size;
            const newCandle: Bar = {
              time: candleStartSecs as UTCTimestamp,
              open: tick.price,
              high: tick.price,
              low: tick.price,
              close: tick.price,
            };
            series.update(newCandle);
            lastCandle = newCandle;
          } else {
            continue; // stale/out-of-order tick
          }

          // VWAP for the forming bar (24h) = completed Σ(typical×vol) in this
          // session plus the forming bar folded in, over the matching volume.
          if (isNq && lastCandle) {
            const curTypical =
              (lastCandle.high + lastCandle.low + lastCandle.close) / 3;
            const denom = sessionVol + formingVol;
            if (denom > 0) {
              vwapSeries.update({
                time: candleStartSecs as UTCTimestamp,
                value: (sessionPv + curTypical * formingVol) / denom,
              });
            }
          }
        } catch {
          // lightweight-charts rejected this bar; skip it and keep streaming.
        }
      }
    }

    const WS_URL = `ws://${window.location.hostname}:${MARCH_WS_PORT}`;

    // Live stream over WebSocket. Fail-safe: a dropped connection (Bookmap
    // closed, addon restart) never errors — it flips to idle and auto-reconnects.
    function connectWs() {
      if (!active) return;
      let socket: WebSocket;
      try {
        socket = new WebSocket(WS_URL);
      } catch {
        reconnectId = setTimeout(connectWs, 1000);
        return;
      }
      ws = socket;

      socket.onmessage = (ev) => {
        if (!active) return;
        let ticks: Tick[];
        try {
          ticks = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (!Array.isArray(ticks) || ticks.length === 0) return;
        // The addon broadcasts all instruments — keep only the selected symbol.
        const mine = ticks.filter(
          (t) => !t.sym || t.sym.toLowerCase() === symbol,
        );
        if (mine.length === 0) return;
        applyTicks(mine);
        setStreamStatus("live");
        clearTimeout(idleTimerId);
        idleTimerId = setTimeout(() => {
          if (active) setStreamStatus("idle");
        }, 5000);
      };

      socket.onclose = () => {
        if (!active) return;
        setStreamStatus("idle");
        reconnectId = setTimeout(connectWs, 1000);
      };
      socket.onerror = () => {
        try {
          socket.close();
        } catch {}
      };
    }

    // 'range' → static historical window from nq_; 'latest' → recent nq_ history
    // (bounded below by fromDate) followed by live bm_nq_ticks streaming.
    async function initAndPoll() {
      setLoading(true);
      setError(null);
      setStreamStatus("loading");

      const isLatest = mode === "latest";

      // 1. Load historical candles from nq_. Failure is non-fatal: start empty
      //    and (in latest mode) let the live stream fill it in.
      let candles: Bar[] = [];
      try {
        candles = isLatest
          ? await fetchMarchCandles(symbol, tf, fromDate)
          : await fetchMarchCandles(symbol, tf, fromDate, toDate);
      } catch (err) {
        console.warn("March historical load failed (starting empty):", err);
      }
      if (!active) return;

      try {
        const filled = fillGaps(candles, tf.seconds);
        series.setData(filled as any);
        if (filled.length < 5000) {
          chart.timeScale().fitContent();
        }
      } catch (err) {
        console.error("Failed to render historical candles:", err);
      }

      // Historical 24h VWAP. Accumulate EVERY bar, but re-anchor (reset the
      // running sums) at TWO points each ET day: midnight and RTH open (09:30).
      // This yields two continuous VWAP sessions per day — overnight (00:00–09:30)
      // and RTH+evening (09:30–24:00). The line is broken with a whitespace at
      // each re-anchor so it never connects across a reset. typical = (h+l+c)/3
      // weighted by bar volume.
      let hCurDay = -1;
      let hRthAnchored = false;
      let hRthClosed = false;
      let hCumPv = 0;
      let hCumVol = 0;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const vwapPoints: any[] = [];

      for (const c of candles) {
        const tsSecs = c.time as number;
        const etDay = Math.floor(tsSecs / 86400);
        const minOfDay = Math.floor((tsSecs % 86400) / 60);

        let reset = false;
        if (etDay !== hCurDay) {
          hCurDay = etDay;
          hRthAnchored = false;
          hRthClosed = false;
          hCumPv = 0;
          hCumVol = 0;
          reset = true;
        }
        if (!hRthAnchored && minOfDay >= RTH_OPEN_MIN) {
          hRthAnchored = true;
          hRthClosed = false;
          hCumPv = 0;
          hCumVol = 0;
          reset = true;
        }
        if (hRthAnchored && !hRthClosed && minOfDay >= RTH_CLOSE_MIN) {
          hRthClosed = true;
          hCumPv = 0;
          hCumVol = 0;
          reset = true;
        }

        const vol = c.volume ?? 0;
        const typical = ((c.high ?? 0) + (c.low ?? 0) + (c.close ?? 0)) / 3;
        hCumPv += typical * vol;
        hCumVol += vol;

        // At a re-anchor, push a whitespace so the line breaks; the new session's
        // visible line then starts at its second bar. Otherwise push the value.
        if (reset) {
          vwapPoints.push({ time: c.time });
        } else if (hCumVol > 0) {
          vwapPoints.push({ time: c.time, value: hCumPv / hCumVol });
        }
      }

      // Seed the streaming state. Treat the LAST historical bar as still
      // forming (live ticks may continue it) by un-folding it from the session
      // sums and moving its volume into formingVol — otherwise it's counted
      // twice once live ticks land in that same bar.
      curDay = hCurDay;
      rthAnchored = hRthAnchored;
      rthClosed = hRthClosed;
      sessionPv = hCumPv;
      sessionVol = hCumVol;
      formingVol = 0;
      if (candles.length > 0) {
        const lastC = candles[candles.length - 1];
        const lastVol = lastC.volume ?? 0;
        const lastTypical =
          ((lastC.high ?? 0) + (lastC.low ?? 0) + (lastC.close ?? 0)) / 3;
        sessionPv -= lastTypical * lastVol;
        sessionVol -= lastVol;
        formingVol = lastVol;
      }

      try {
        if (symbol === "nq") {
          vwapSeries.setData(vwapPoints);
        } else {
          vwapSeries.setData([]);
        }
      } catch (err) {
        console.error("Failed to render historical VWAP:", err);
      }

      // Static range mode: render history and stop — no catch-up, no WS.
      if (!isLatest) {
        if (candles.length === 0) {
          setError(`No data for ${fromDate} – ${toDate}`);
        }
        setLoading(false);
        setStreamStatus("idle");
        return;
      }

      // Seed streaming state from the last historical candle (if any).
      lastCandle =
        candles.length > 0 ? { ...candles[candles.length - 1] } : null;
      lastTickNanos = lastCandle
        ? (lastCandle.time as number) * 1_000_000_000
        : null;

      setLoading(false);
      setStreamStatus("idle");

      // 4. Switch to the realtime WebSocket stream (sub-100ms path).
      connectWs();
    }

    initAndPoll();

    return () => {
      active = false;
      if (reconnectId) clearTimeout(reconnectId);
      if (idleTimerId) clearTimeout(idleTimerId);
      if (ws) {
        try {
          ws.close();
        } catch {}
      }
      setStreamStatus("idle");
      chart.remove();
      setChartSeries(null);
      chartRef.current = null;
      fxSeriesRef.current = null;
    };
  }, [symbol, tf, mode, fromDate, toDate]);

  useEffect(() => {
    const series = chartSeries;
    if (!series) return;

    if (!positions || positions.length === 0) {
      activePositionsPlugin.current.setPositions([]);
      return;
    }

    const activeForChart = positions.filter((pos) =>
      matchesMarchSymbol(pos.symbol, symbol),
    );
    const activePosList: ActivePosInfo[] = [];
    const tfSecs = tf.seconds;

    // Get visible time range so we can clamp positions that are newer than the
    // last chart bar (happens when streaming is idle and candles haven't arrived yet).
    const visRange = chartRef.current?.timeScale().getVisibleRange();

    for (const pos of activeForChart) {
      // Need at least a price to show the marker. zig_entry_price falls back to
      // MT5 open_price in Python, so it's always set. zig_entry_time falls back
      // to p.time (MT5 open time) after the Python fix.
      if (!pos.zig_entry_price) continue;

      let markerTime: number;

      if (pos.zig_entry_time) {
        // Convert real-UTC entry time to fake-UTC (ET wall clock) to align with chart candles.
        const date = new Date(pos.zig_entry_time * 1000);
        const utcDate = new Date(date.toLocaleString("en-US", { timeZone: "UTC" }));
        const nyDate  = new Date(date.toLocaleString("en-US", { timeZone: "America/New_York" }));
        const offsetSeconds = (utcDate.getTime() - nyDate.getTime()) / 1000;
        const adjustedTime = pos.zig_entry_time - offsetSeconds;
        markerTime = Math.floor(adjustedTime / tfSecs) * tfSecs;
      } else {
        // No time info at all — place at the chart's right edge so it's always visible.
        markerTime = visRange ? (visRange.to as number) : Math.floor(Date.now() / 1000);
      }

      // If the position is newer than the last visible bar (streaming lag), clamp to
      // the right edge so the arrow is still visible on screen.
      if (visRange && markerTime > (visRange.to as number)) {
        markerTime = visRange.to as number;
      }

      activePosList.push({
        time: markerTime,
        price: pos.zig_entry_price,
        volume: pos.volume,
        profit: pos.profit,
        isLong: pos.type === "long",
        strategy: pos.strategy || "",
      });
    }

    activePositionsPlugin.current.setPositions(activePosList);
  }, [positions, symbol, tf, chartSeries]);

  useEffect(() => {
    const series = chartSeries;
    if (!series) return;

    if (!liveTrades || liveTrades.length === 0 || visibleTradeStrategies.size === 0) {
      historicalTradesPlugin.current.setTrades([]);
      return;
    }

    const tfSecs = tf.seconds;

    const parseZigTime = (timeStr: string): number => {
      if (!timeStr) return 0;
      const cleaned = timeStr.trim();
      const parts = cleaned.split(/[- :.]/);
      if (parts.length >= 6) {
        const year = parseInt(parts[0], 10);
        const month = parseInt(parts[1], 10) - 1;
        const day = parseInt(parts[2], 10);
        const hour = parseInt(parts[3], 10);
        const minute = parseInt(parts[4], 10);
        const second = parseInt(parts[5], 10);
        const ms = parts[6] ? parseInt(parts[6].padEnd(3, "0").slice(0, 3), 10) : 0;
        return Math.floor(Date.UTC(year, month, day, hour, minute, second, ms) / 1000);
      } else {
        const utcString = cleaned.replace(" ", "T") + (cleaned.endsWith("Z") ? "" : "Z");
        return Math.floor(new Date(utcString).getTime() / 1000);
      }
    };

    const tradesForChart: HistoricalTradeInfo[] = [];

    for (const t of liveTrades) {
      if (!visibleTradeStrategies.has(t.strategy_name)) continue;

      const openSecs = parseZigTime(t.zig_open_time);
      const closeSecs = parseZigTime(t.zig_close_time);

      if (openSecs === 0 || closeSecs === 0) continue;

      // zig_open/close_time are ET wall-clock stored as fake-UTC — parseZigTime
      // already returns the correct chart-space epoch. Just snap to timeframe.
      const et = Math.floor(openSecs / tfSecs) * tfSecs;
      const xt = Math.floor(closeSecs / tfSecs) * tfSecs;

      tradesForChart.push({
        et,
        xt,
        ep: t.zig_entry_price,
        xp: t.zig_close_price,
        isLong: t.side === "long",
        strategy: t.strategy_name,
      });
    }

    historicalTradesPlugin.current.setTrades(tradesForChart);
  }, [liveTrades, visibleTradeStrategies, tf, chartSeries]);

  useEffect(() => {
    if (!chartSeries) return;
    tradeLinesPrimitive.current.setTrades(allTrades, tf.seconds);
  }, [allTrades, tf, chartSeries]);

  // fx-priced trades for the same toggled-on backtests, drawn on the fx_nq pane.
  // Only populated while the fx_nq overlay is shown (NQ only); cleared otherwise.
  useEffect(() => {
    if (!chartSeries) return;
    const show = showFxNq && symbol === "nq";
    fxTradeLinesPrimitive.current.setTrades(show ? allFxTrades : [], tf.seconds);
  }, [allFxTrades, showFxNq, symbol, tf, chartSeries]);

  useEffect(() => {
    vwapSeriesRef.current?.applyOptions({ visible: vwap && symbol === "nq" });
  }, [vwap, symbol, chartSeries]);

  // fx_nq overlay: when toggled on (NQ only), create a SECOND pane below the NQ
  // pane holding the aggregated fx candles (own price axis), and fetch its data.
  // On toggle off / unmount / range change, the fx series is removed, which drops
  // the empty pane so NQ reclaims the full height.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !chartSeries) return;
    if (!(showFxNq && symbol === "nq")) return;

    const fxSeries = chart.addSeries(
      CandlestickSeries,
      {
        upColor: "#22c55e",
        downColor: "#ef4444",
        borderUpColor: "#22c55e",
        borderDownColor: "#ef4444",
        wickUpColor: "#22c55e",
        wickDownColor: "#ef4444",
        lastValueVisible: true,
        priceLineVisible: false,
      },
      1, // pane index 1 → new pane stacked below NQ
    );
    fxSeries.attachPrimitive(fxTradeLinesPrimitive.current);
    fxSeriesRef.current = fxSeries;
    fxTradeLinesPrimitive.current.setTrades(allFxTradesRef.current, tf.seconds);

    // Give the NQ pane ~2/3 of the height, fx_nq the rest.
    const panes = chart.panes();
    if (panes.length > 1) {
      panes[0].setStretchFactor(2);
      panes[1].setStretchFactor(1);
    }

    let cancelled = false;
    (async () => {
      try {
        const candles =
          mode === "latest"
            ? await fetchFxNqCandles(tf, fromDate)
            : await fetchFxNqCandles(tf, fromDate, toDate);
        if (cancelled) return;
        // fx_nq_ticks may extend past where nq_1m ends; setData would otherwise
        // scroll the (right-anchored) view to the fx end, leaving the NQ pane
        // blank. Preserve the current time window across the update.
        const prevRange = chart.timeScale().getVisibleRange();
        fxSeries.setData(fillGaps(candles, tf.seconds) as any);
        if (prevRange) {
          try { chart.timeScale().setVisibleRange(prevRange); } catch { /* range out of bounds */ }
        }
      } catch (err) {
        console.warn("fx_nq candles load failed:", err);
      }
    })();

    return () => {
      cancelled = true;
      try {
        chart.removeSeries(fxSeries);
      } catch { /* chart already disposed */ }
      fxSeriesRef.current = null;
    };
  }, [showFxNq, symbol, tf, mode, fromDate, toDate, chartSeries]);

  return (
    <div className="flex flex-col bg-gray-950 min-h-0 min-w-0 h-full w-full">
      <Header
        symbol={symbol}
        setSymbol={setSymbol}
        tf={tf}
        setTf={setTf}
        streamStatus={streamStatus}
        mode={mode}
        fromDate={fromDate}
        toDate={toDate}
        onApplyRange={onApplyRange}
        onLatest={onLatest}
      />
      <div className="flex-1 flex flex-col relative min-h-0 min-w-0">
        {loading && (
          <div className="absolute inset-0 bg-gray-950/80 backdrop-blur-sm flex items-center justify-center z-10">
            <div className="text-gray-400 text-sm flex items-center gap-2">
              <svg
                className="animate-spin h-4 w-4 text-blue-500"
                viewBox="0 0 24 24"
                fill="none"
              >
                <circle
                  className="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="4"
                />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                />
              </svg>
              Loading Chart...
            </div>
          </div>
        )}
        {error && (
          <div className="absolute inset-0 bg-gray-950/90 flex flex-col items-center justify-center gap-3 z-10 p-4">
            <div className="text-red-400 text-sm text-center">{error}</div>
            <button
              onClick={() => {
                setError(null);
                window.location.reload();
              }}
              className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-xs font-medium rounded-md transition-colors"
            >
              Retry
            </button>
          </div>
        )}
        <div
          ref={containerRef}
          className="flex-1 min-h-0"
          onContextMenu={(e) => {
            e.preventDefault();
            e.stopPropagation();
            const menuWidth = 140;
            const menuHeight = symbol === "nq" ? 144 : 104;
            let x = e.clientX;
            let y = e.clientY;

            if (x + menuWidth > window.innerWidth) {
              x = window.innerWidth - menuWidth - 8;
            }
            if (y + menuHeight > window.innerHeight) {
              y = window.innerHeight - menuHeight - 8;
            }
            setChartContextMenu({ x, y });
          }}
        />
      </div>

      {/* Chart Context Menu */}
      {chartContextMenu && (
        <div
          className="fixed z-50 bg-gray-900/95 backdrop-blur-md border border-gray-800/80 rounded-lg shadow-xl shadow-black/60 py-0.5 font-sans text-xs text-gray-300 select-none transition-all duration-100 ease-out"
          style={{ left: chartContextMenu.x, top: chartContextMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            onClick={() => {
              onOpenIndicators();
              setChartContextMenu(null);
            }}
            className="w-full px-4 py-2 text-left hover:bg-blue-600/20 hover:text-white transition-colors duration-150 cursor-pointer whitespace-nowrap flex items-center gap-2"
          >
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
              <polyline
                points="1,7.5 5,3 9,5 14.5,0.5"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <rect x="0.8" y="12" width="3" height="3.5" rx="0.4" stroke="currentColor" strokeWidth="1.1" />
              <rect x="5.5" y="10" width="3" height="5.5" rx="0.4" stroke="currentColor" strokeWidth="1.1" />
              <rect x="10.5" y="7.5" width="3" height="8" rx="0.4" stroke="currentColor" strokeWidth="1.1" />
            </svg>
            Indicators
          </button>
          <button
            onClick={() => {
              onOpenBacktests();
              setChartContextMenu(null);
            }}
            className="w-full px-4 py-2 text-left hover:bg-blue-600/20 hover:text-white transition-colors duration-150 cursor-pointer whitespace-nowrap flex items-center gap-2"
          >
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
              <rect x="1" y="1" width="14" height="14" rx="2" stroke="currentColor" strokeWidth="1.4" />
              <line x1="4" y1="5.5" x2="12" y2="5.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
              <line x1="4" y1="8" x2="12" y2="8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
              <line x1="4" y1="10.5" x2="9" y2="10.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
            </svg>
            Backtests
          </button>
          {symbol === "nq" && (
            <button
              onClick={() => {
                setShowFxNq((v) => !v);
                setChartContextMenu(null);
              }}
              className="w-full px-4 py-2 text-left hover:bg-blue-600/20 hover:text-white transition-colors duration-150 cursor-pointer whitespace-nowrap flex items-center gap-2"
            >
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
                <rect x="1" y="8.5" width="2.5" height="5" rx="0.4" stroke="currentColor" strokeWidth="1.1" />
                <line x1="2.25" y1="6.5" x2="2.25" y2="15" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
                <rect x="6.75" y="5" width="2.5" height="6" rx="0.4" stroke="currentColor" strokeWidth="1.1" />
                <line x1="8" y1="2.5" x2="8" y2="13" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
                <rect x="12.5" y="9" width="2.5" height="4" rx="0.4" stroke="currentColor" strokeWidth="1.1" />
                <line x1="13.75" y1="7" x2="13.75" y2="15" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
              </svg>
              {showFxNq ? "Hide fx_nq" : "Show fx_nq"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
