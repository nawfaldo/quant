import { useEffect, useRef, useMemo } from 'react'
import type { MonteCarloData } from '../types'

const PAD = { top: 28, right: 110, bottom: 36, left: 90 }

function fmt$(v: number) {
  return '$' + Math.round(v).toLocaleString()
}

function pctLabel(v: number) {
  return (v * 100).toFixed(1) + '%'
}

export default function MonteCarloChart({ data }: { data: MonteCarloData }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)

  const perStep = useMemo(() => {
    const { paths, steps } = data
    if (paths.length === 0 || steps === 0) return null
    const n = paths.length
    const p5arr  = new Float32Array(steps)
    const p50arr = new Float32Array(steps)
    const p95arr = new Float32Array(steps)
    const tmp = new Float32Array(n)
    for (let s = 0; s < steps; s++) {
      for (let i = 0; i < n; i++) tmp[i] = paths[i][s]
      tmp.sort()
      p5arr[s]  = tmp[Math.max(0, Math.floor(n * 0.05) - 1)]
      p50arr[s] = tmp[Math.floor(n * 0.50)]
      p95arr[s] = tmp[Math.min(n - 1, Math.ceil(n * 0.95) - 1)]
    }
    return { p5: p5arr, p50: p50arr, p95: p95arr }
  }, [data])

  useEffect(() => {
    const container = containerRef.current
    const canvas = canvasRef.current
    if (!container || !canvas) return

    function draw() {
      const W = container!.clientWidth
      const H = container!.clientHeight
      if (W === 0 || H === 0) return
      const dpr = window.devicePixelRatio || 1
      canvas!.width  = W * dpr
      canvas!.height = H * dpr
      canvas!.style.width  = W + 'px'
      canvas!.style.height = H + 'px'

      const ctx = canvas!.getContext('2d')
      if (!ctx) return
      ctx.scale(dpr, dpr)

      const { paths, steps, stepValues, initialBalance, sims, pProfit, pRuin } = data

      // Y range across all paths
      let yMin = Infinity, yMax = -Infinity
      for (const path of paths) {
        for (let s = 0; s < steps; s++) {
          const v = path[s]
          if (v < yMin) yMin = v
          if (v > yMax) yMax = v
        }
      }
      if (!isFinite(yMin)) { yMin = 0; yMax = 1 }
      const yRange = (yMax - yMin) || 1
      yMin -= yRange * 0.04
      yMax += yRange * 0.04

      const plotW = W - PAD.left - PAD.right
      const plotH = H - PAD.top  - PAD.bottom

      const tradeMin = stepValues[0]
      const tradeMax = stepValues[steps - 1]
      const tradeRange = (tradeMax - tradeMin) || 1
      // Map by actual trade count, not step index
      const xS = (s: number) => PAD.left + ((stepValues[s] - tradeMin) / tradeRange) * plotW
      const yS = (v: number) => PAD.top  + (1 - (v - yMin) / (yMax - yMin)) * plotH

      ctx.clearRect(0, 0, W, H)

      // Horizontal grid + Y labels — nice round ticks
      const rawStep = (yMax - yMin) / 10
      const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)))
      const niceStep = Math.ceil(rawStep / magnitude) * magnitude
      const tickStart = Math.ceil(yMin / niceStep) * niceStep
      const yTicks: number[] = []
      for (let v = tickStart; v <= yMax + niceStep * 0.01; v += niceStep) yTicks.push(v)

      for (const val of yTicks) {
        const y = yS(val)
        if (y < PAD.top - 2 || y > H - PAD.bottom + 2) continue
        ctx.strokeStyle = '#1f2937'
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.moveTo(PAD.left, y)
        ctx.lineTo(W - PAD.right, y)
        ctx.stroke()
        ctx.fillStyle = '#6b7280'
        ctx.font = '10px monospace'
        ctx.textAlign = 'right'
        ctx.fillText(fmt$(val), PAD.left - 6, y + 3)
      }

      // X axis labels (actual trade counts)
      ctx.fillStyle = '#6b7280'
      ctx.font = '10px monospace'
      ctx.textAlign = 'center'
      const xTickCount = 6
      for (let i = 0; i <= xTickCount; i++) {
        const tradeVal = Math.round(tradeMin + (i / xTickCount) * tradeRange)
        const x = PAD.left + (i / xTickCount) * plotW
        ctx.fillText(tradeVal.toLocaleString(), x, H - PAD.bottom + 14)
      }
      ctx.fillStyle = '#4b5563'
      ctx.font = '10px monospace'
      ctx.fillText('trades', PAD.left + plotW / 2, H - 4)

      // p5–p95 shaded band
      if (perStep && steps > 1) {
        ctx.fillStyle = 'rgba(99, 102, 241, 0.06)'
        ctx.beginPath()
        ctx.moveTo(xS(0), yS(perStep.p95[0]))
        for (let s = 1; s < steps; s++) ctx.lineTo(xS(s), yS(perStep.p95[s]))
        for (let s = steps - 1; s >= 0; s--) ctx.lineTo(xS(s), yS(perStep.p5[s]))
        ctx.closePath()
        ctx.fill()
      }

      // Spaghetti paths. "Ruin" matches the backend stat (montecarlo.zig): equity
      // ever touching <= 50% of the INITIAL balance — not a 50% drawdown from peak.
      const ruinLevel = initialBalance * 0.5
      ctx.lineWidth = 0.5
      for (const path of paths) {
        let crossedRuin = false
        for (let s = 0; s < steps; s++) {
          if (path[s] <= ruinLevel) { crossedRuin = true; break }
        }
        ctx.strokeStyle = crossedRuin
          ? 'rgba(239, 68, 68, 0.25)'
          : 'rgba(16, 185, 129, 0.06)'
        ctx.beginPath()
        for (let s = 0; s < steps; s++) {
          const x = xS(s)
          const y = yS(path[s])
          if (s === 0) ctx.moveTo(x, y)
          else ctx.lineTo(x, y)
        }
        ctx.stroke()
      }

      // Initial balance — bright solid line + label on both sides
      const initY = yS(initialBalance)
      ctx.strokeStyle = 'rgba(250, 204, 21, 0.7)'
      ctx.lineWidth = 1.5
      ctx.setLineDash([6, 4])
      ctx.beginPath()
      ctx.moveTo(PAD.left, initY)
      ctx.lineTo(W - PAD.right, initY)
      ctx.stroke()
      ctx.setLineDash([])
      ctx.fillStyle = 'rgba(250, 204, 21, 0.9)'
      ctx.font = '10px monospace'
      ctx.textAlign = 'right'
      ctx.fillText(fmt$(initialBalance), PAD.left - 6, initY + 3)
      ctx.textAlign = 'left'
      ctx.fillText('start', W - PAD.right + 8, initY + 3)

      // p5 / p95 dashed lines
      if (perStep && steps > 1) {
        ctx.strokeStyle = 'rgba(99, 102, 241, 0.55)'
        ctx.lineWidth = 1
        ctx.setLineDash([4, 3])
        for (const line of [perStep.p5, perStep.p95]) {
          ctx.beginPath()
          for (let s = 0; s < steps; s++) {
            const x = xS(s); const y = yS(line[s])
            if (s === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
          }
          ctx.stroke()
        }
        ctx.setLineDash([])

        // p50 median line
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.65)'
        ctx.lineWidth = 1.5
        ctx.beginPath()
        for (let s = 0; s < steps; s++) {
          const x = xS(s); const y = yS(perStep.p50[s])
          if (s === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
        }
        ctx.stroke()
      }

      // Axes border
      ctx.strokeStyle = '#374151'
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(PAD.left, PAD.top)
      ctx.lineTo(PAD.left, H - PAD.bottom)
      ctx.lineTo(W - PAD.right, H - PAD.bottom)
      ctx.stroke()

      // Right-side percentile labels
      if (perStep) {
        const finalX = W - PAD.right + 8
        const labels: [string, string, number][] = [
          ['P95', 'rgba(99,102,241,0.85)', perStep.p95[steps - 1]],
          ['P50', 'rgba(220,220,220,0.8)', perStep.p50[steps - 1]],
          ['P5',  'rgba(99,102,241,0.85)', perStep.p5[steps - 1]],
        ]
        ctx.font = '10px monospace'
        ctx.textAlign = 'left'
        for (const [label, color, val] of labels) {
          const y = yS(val)
          ctx.fillStyle = color
          ctx.fillText(`${label} ${fmt$(val)}`, finalX, y + 3)
        }
      }

      // Stats text (top-left corner)
      const sx = PAD.left + 10
      let sy = PAD.top + 14
      ctx.textAlign = 'left'

      ctx.font = '10px monospace'
      ctx.fillStyle = 'rgba(107,114,128,0.9)'
      ctx.fillText(`${sims} sims · ${tradeMax.toLocaleString()} trades`, sx, sy)
      sy += 18

      ctx.font = '11px monospace'
      ctx.fillStyle = pProfit >= 0.5 ? 'rgba(16,185,129,0.9)' : 'rgba(239,68,68,0.9)'
      ctx.fillText(`P(profit) ${pctLabel(pProfit)}`, sx, sy)
      sy += 16

      ctx.fillStyle = pRuin > 0.05 ? 'rgba(239,68,68,0.9)' : 'rgba(107,114,128,0.85)'
      ctx.fillText(`P(ruin)   ${pctLabel(pRuin)}`, sx, sy)
    }

    draw()
    const ro = new ResizeObserver(draw)
    ro.observe(container)
    return () => ro.disconnect()
  }, [data, perStep])

  return (
    <div ref={containerRef} className="w-full h-full relative">
      <canvas ref={canvasRef} className="absolute inset-0" />
    </div>
  )
}
