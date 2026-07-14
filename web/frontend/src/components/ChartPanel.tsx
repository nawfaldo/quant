import { useRef, useEffect, useState } from "react";
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
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
  NoiseAreaPrimitive,
  SessionVolumeProfilePrimitive,
  VolumeDeltaBubblesPrimitive,
  type VolumeDeltaBubble,
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
  noiseArea: boolean;
  volume: boolean;
  volumeDeltaBubbles: boolean;
  sessionVolumeProfile: boolean;
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
const SESSION_PROFILE_ROW_SIZE = 15.0;

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

interface VolumeDeltaPoint {
  time: number;
  delta: number;
}

async function fetchMarchVolumeDelta(
  symbol: string,
  tf: TF,
  from?: string,
  to?: string,
): Promise<VolumeDeltaPoint[]> {
  const params = new URLSearchParams({ symbol, tf: tf.table });
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  const res = await fetch(
    `${BACKEND_URL}/api/march/indicators/volume-delta?${params.toString()}`,
  );
  if (!res.ok) throw new Error(`Backend error: ${res.status}`);
  return res.json();
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
  const {
    symbol, tf, mode, fromDate, toDate,
    vwap, noiseArea, volume, volumeDeltaBubbles, sessionVolumeProfile,
  } = config;

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
  const [candlesRevision, setCandlesRevision] = useState(0);
  const chartRef = useRef<any>(null);
  const vwapSeriesRef = useRef<any>(null);
  const volumeSeriesRef = useRef<any>(null);
  const candleHistoryRef = useRef<Bar[]>([]);
  const historicalDeltaRef = useRef(new Map<number, number>());
  const liveDeltaRef = useRef(new Map<number, number>());
  const volumeDeltaBubblesEnabledRef = useRef(volumeDeltaBubbles);
  volumeDeltaBubblesEnabledRef.current = volumeDeltaBubbles;
  const sessionVolumeProfileEnabledRef = useRef(sessionVolumeProfile);
  sessionVolumeProfileEnabledRef.current = sessionVolumeProfile;
  const fxSeriesRef = useRef<any>(null);
  const [showFxNq, setShowFxNq] = useState(false);
  const activePositionsPlugin = useRef(new ActivePositionsPrimitive());
  const historicalTradesPlugin = useRef(new HistoricalTradesPrimitive());
  const tradeLinesPrimitive = useRef(new TradeLinesPrimitive());
  const fxTradeLinesPrimitive = useRef(new TradeLinesPrimitive());
  const noiseAreaPlugin = useRef(new NoiseAreaPrimitive());
  const sessionVolumeProfilePlugin = useRef(new SessionVolumeProfilePrimitive());
  const volumeDeltaBubblesPlugin = useRef(new VolumeDeltaBubblesPrimitive());

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
        background: { type: ColorType.Solid, color: "#0F0F0F" },
        textColor: "#d1d5db",
        panes: {
          separatorColor: "#0F0F0F",
          separatorHoverColor: "#0F0F0F",
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
        borderColor: "#212124",
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: number, tickMarkType: number) => {
          const date = new Date(time * 1000);
          return tickMarkType >= 3
            ? tickTimeFormatter.format(date)
            : tickDateFormatter.format(date);
        },
      },
      rightPriceScale: { borderColor: "#212124" },
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#FFFFFF",
      downColor: "#000000",
      borderUpColor: "#FFFFFF",
      borderDownColor: "#FFFFFF",
      wickUpColor: "#FFFFFF",
      wickDownColor: "#FFFFFF",
      // The right-edge latest-price label is meaningful only while the chart
      // ends at Now. Historical date ranges keep the price scale unlabelled.
      lastValueVisible: mode === "latest",
      priceLineVisible: mode === "latest",
    });
    setChartSeries(series);
    chartRef.current = chart;
    series.attachPrimitive(activePositionsPlugin.current);
    series.attachPrimitive(historicalTradesPlugin.current);
    series.attachPrimitive(tradeLinesPrimitive.current);
    series.attachPrimitive(noiseAreaPlugin.current);
    series.attachPrimitive(sessionVolumeProfilePlugin.current);
    series.attachPrimitive(volumeDeltaBubblesPlugin.current);
    sessionVolumeProfilePlugin.current.setData([]);
    volumeDeltaBubblesPlugin.current.setData([]);
    historicalDeltaRef.current.clear();
    liveDeltaRef.current.clear();

    const vwapSeries = chart.addSeries(LineSeries, {
      color: "#60a5fa",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      visible: vwap,
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
    let profileFrameId: number | null = null;

    function scheduleSessionVolumeProfileUpdate() {
      if (!sessionVolumeProfileEnabledRef.current || profileFrameId !== null) return;
      profileFrameId = window.requestAnimationFrame(() => {
        profileFrameId = null;
        sessionVolumeProfilePlugin.current.setCandles(
          candleHistoryRef.current,
          SESSION_PROFILE_ROW_SIZE,
          tf.seconds,
        );
      });
    }

    // Apply one batch of ticks to the chart. Never throws — bad ticks are
    // skipped individually so a single rejected bar can't stop the stream.
    function applyTicks(ticks: Tick[]) {
      const tfSecs = tf.seconds;

      for (const tick of ticks) {
        if (!Number.isFinite(tick.ts) || !Number.isFinite(tick.price)) continue;
        if (lastTickNanos === null || tick.ts > lastTickNanos)
          lastTickNanos = tick.ts;

        const tsSecs = Math.floor(tick.ts / 1_000_000_000);
        const size = Number.isFinite(tick.size) ? tick.size : 0;

        const candleStartSecs = Math.floor(tsSecs / tfSecs) * tfSecs;
        const lastTime = lastCandle ? (lastCandle.time as number) : -1;

        try {
          const side = tick.side?.toUpperCase();
          const signedSize = side === "BUY" ? size : side === "SELL" ? -size : 0;
          if (signedSize !== 0) {
            liveDeltaRef.current.set(
              candleStartSecs,
              (liveDeltaRef.current.get(candleStartSecs) ?? 0) + signedSize,
            );
          }

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

          if (lastCandle && volumeSeriesRef.current) {
            volumeSeriesRef.current.update({
              time: lastCandle.time,
              value: formingVol,
              color: lastCandle.close >= lastCandle.open
                ? "#089981"
                : "#F23645",
            });
          }
          if (lastCandle) {
            const volumeCandle = { ...lastCandle, volume: formingVol };
            const historyIndex = candleHistoryRef.current.length - 1;
            const historyLast = candleHistoryRef.current[historyIndex];
            if (historyLast?.time === lastCandle.time) {
              candleHistoryRef.current[historyIndex] = volumeCandle;
            } else if (!historyLast || historyLast.time < lastCandle.time) {
              candleHistoryRef.current.push(volumeCandle);
            }
            scheduleSessionVolumeProfileUpdate();

            if (volumeDeltaBubblesEnabledRef.current) {
              const delta = (historicalDeltaRef.current.get(candleStartSecs) ?? 0) +
                (liveDeltaRef.current.get(candleStartSecs) ?? 0);
              volumeDeltaBubblesPlugin.current.setCandle(
                candleStartSecs,
                lastCandle.open,
                lastCandle.high,
                lastCandle.low,
                lastCandle.close,
                delta,
              );
            }
          }

          // VWAP for the forming bar (24h) = completed Σ(typical×vol) in this
          // session plus the forming bar folded in, over the matching volume.
          if (lastCandle) {
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
      candleHistoryRef.current = candles;
      setCandlesRevision((revision) => revision + 1);

      if (volumeSeriesRef.current) {
        volumeSeriesRef.current.setData(candles.map((c) => ({
          time: c.time,
          value: c.volume ?? 0,
          color: c.close >= c.open
            ? "#089981"
            : "#F23645",
        })));
      }

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
        vwapSeries.setData(vwapPoints);
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
      if (profileFrameId !== null) cancelAnimationFrame(profileFrameId);
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
      volumeSeriesRef.current = null;
      candleHistoryRef.current = [];
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
    const filteredTrades = allTrades.filter(t => (t as any).symbol?.toLowerCase() === symbol.toLowerCase());
    tradeLinesPrimitive.current.setTrades(filteredTrades, tf.seconds);
  }, [allTrades, symbol, tf, chartSeries]);

  // fx-priced trades for the same toggled-on backtests, drawn on the fx_nq pane.
  // Only populated while the fx_nq overlay is shown (NQ only); cleared otherwise.
  useEffect(() => {
    if (!chartSeries) return;
    const show = showFxNq && symbol === "nq";
    const filteredFxTrades = allFxTrades.filter(t => (t as any).symbol?.toLowerCase() === symbol.toLowerCase());
    fxTradeLinesPrimitive.current.setTrades(show ? filteredFxTrades : [], tf.seconds);
  }, [allFxTrades, showFxNq, symbol, tf, chartSeries]);

  useEffect(() => {
    vwapSeriesRef.current?.applyOptions({ visible: vwap });
  }, [vwap, chartSeries]);

  useEffect(() => {
    if (!sessionVolumeProfile || !chartSeries) {
      sessionVolumeProfilePlugin.current.setData([]);
      return;
    }
    sessionVolumeProfilePlugin.current.setCandles(
      candleHistoryRef.current,
      SESSION_PROFILE_ROW_SIZE,
      tf.seconds,
    );
  }, [sessionVolumeProfile, tf, chartSeries, candlesRevision]);

  // Volume lives in its own compact pane. It is created and removed on demand
  // so disabling the indicator gives all of that height back to the price chart.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !chartSeries || !volume) return;

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceLineVisible: false,
      lastValueVisible: false,
    }, chart.panes().length);
    volumeSeriesRef.current = volumeSeries;
    volumeSeries.setData(candleHistoryRef.current.map((c) => ({
      time: c.time,
      value: c.volume ?? 0,
      color: c.close >= c.open
        ? "#089981"
        : "#F23645",
    })));

    const panes = chart.panes();
    panes[0]?.setStretchFactor(4);
    panes[panes.length - 1]?.setStretchFactor(1);

    return () => {
      if (volumeSeriesRef.current === volumeSeries) volumeSeriesRef.current = null;
      try { chart.removeSeries(volumeSeries); } catch { /* chart already disposed */ }
    };
  }, [volume, chartSeries]);

  useEffect(() => {
    let active = true;
    async function updateNoiseArea() {
      if (noiseArea) {
        try {
          const params = new URLSearchParams({ symbol });
          if (fromDate) params.set("from", fromDate);
          if (toDate) params.set("to", toDate);

          const res = await fetch(`${BACKEND_URL}/api/march/indicators/noise-area?${params.toString()}`);
          if (!res.ok) throw new Error("Backend error");
          const data = await res.json();

          if (!active) return;
          noiseAreaPlugin.current.setData(data);
        } catch (err) {
          console.error("Failed to load noise area:", err);
        }
      } else {
        noiseAreaPlugin.current.setData([]);
      }
    }
    updateNoiseArea();
    return () => {
      active = false;
    };
  }, [noiseArea, symbol, fromDate, toDate, chartSeries]);

  useEffect(() => {
    if (!volumeDeltaBubbles || !chartSeries) {
      historicalDeltaRef.current.clear();
      liveDeltaRef.current.clear();
      volumeDeltaBubblesPlugin.current.setData([]);
      return;
    }

    let active = true;
    historicalDeltaRef.current.clear();
    liveDeltaRef.current.clear();
    volumeDeltaBubblesPlugin.current.setData([]);

    (async () => {
      try {
        const points = mode === "latest"
          ? await fetchMarchVolumeDelta(symbol, tf, fromDate)
          : await fetchMarchVolumeDelta(symbol, tf, fromDate, toDate);
        if (!active) return;

        historicalDeltaRef.current = new Map(
          points.map((point) => [point.time, point.delta]),
        );
        const bubbles: VolumeDeltaBubble[] = [];
        for (const candle of candleHistoryRef.current) {
          const time = candle.time as number;
          const delta = (historicalDeltaRef.current.get(time) ?? 0) +
            (liveDeltaRef.current.get(time) ?? 0);
          const positiveDivergence = candle.close < candle.open && delta > 0;
          const negativeDivergence = candle.close > candle.open && delta < 0;
          if (!positiveDivergence && !negativeDivergence) continue;
          bubbles.push({
            time,
            price: positiveDivergence ? candle.low : candle.high,
            delta,
            below: positiveDivergence,
          });
        }
        volumeDeltaBubblesPlugin.current.setData(bubbles);
      } catch (err) {
        console.warn("Estimated volume delta history unavailable:", err);
      }
    })();

    return () => { active = false; };
  }, [volumeDeltaBubbles, symbol, tf, mode, fromDate, toDate, chartSeries, candlesRevision]);

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
        upColor: "#FFFFFF",
        downColor: "#000000",
        borderUpColor: "#FFFFFF",
        borderDownColor: "#FFFFFF",
        wickUpColor: "#FFFFFF",
        wickDownColor: "#FFFFFF",
        lastValueVisible: true,
        priceLineVisible: false,
      },
      chart.panes().length,
    );
    fxSeries.attachPrimitive(fxTradeLinesPrimitive.current);
    fxSeriesRef.current = fxSeries;
    fxTradeLinesPrimitive.current.setTrades(allFxTradesRef.current, tf.seconds);

    // Keep volume as the bottom study when both optional panes are visible.
    const volumePane = chart.panes().find((pane: any) =>
      volumeSeriesRef.current && pane.getSeries().includes(volumeSeriesRef.current),
    );
    if (volumePane) volumePane.moveTo(chart.panes().length - 1);

    const panes = chart.panes();
    if (panes.length > 1) {
      panes[0].setStretchFactor(4);
      const fxPane = panes.find((pane: any) => pane.getSeries().includes(fxSeries));
      fxPane?.setStretchFactor(2);
      volumePane?.setStretchFactor(1);
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
  }, [showFxNq, symbol, tf, mode, fromDate, toDate, volume, chartSeries]);

  return (
    <div className="relative flex flex-col bg-[#0F0F0F] min-h-0 min-w-0 h-full w-full">
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
        onOpenIndicators={onOpenIndicators}
        onOpenBacktests={onOpenBacktests}
        showFxNq={showFxNq}
        onToggleFxNq={() => setShowFxNq((visible) => !visible)}
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
        />
      </div>
    </div>
  );
}
