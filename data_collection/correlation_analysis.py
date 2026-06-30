#!/usr/bin/env python3
"""
Correlation Analysis Module
===========================
A modular and extensible script to analyze the correlation between 
Nasdaq Futures (nq) and Forex/CFD proxy (fx_nq) prices stored in QuestDB.

Features:
- Timeframe selection (1m, 5m, 15m, 30m, 1h, 4h, 1d)
- Price metric selection (mid, bid, ask)
- Returns correlation (both simple percentage returns and log returns)
- Rolling window correlation to inspect stability over time
- Lead-lag (cross-correlation) analysis to detect order-flow latency leading/lagging
- Rich, premium visualizations saved as PNGs
- Text and JSON report exporting
"""

import os
import sys
import argparse
import json
import logging
from typing import Dict, Any, Tuple, Optional
import numpy as np
import pandas as pd
import httpx

# Optional plotting libraries
try:
    import matplotlib.pyplot as plt
    import seaborn as sns
    _PLOTTING_AVAILABLE = True
except ImportError:
    _PLOTTING_AVAILABLE = False

# Import configuration
try:
    import config
    QUESTDB_HOST = config.QUESTDB_HOST
    QUESTDB_HTTP_PORT = config.QUESTDB_HTTP_PORT
except ImportError:
    # Fallback default values
    QUESTDB_HOST = "127.0.0.1"
    QUESTDB_HTTP_PORT = 9000

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("correlation_analysis")


class QuestDBClient:
    """Manages HTTP connections and executes queries against QuestDB."""

    def __init__(self, host: str = QUESTDB_HOST, port: int = QUESTDB_HTTP_PORT):
        self.host = host
        self.port = port
        self.url = f"http://{host}:{port}/exec"

    def query(self, sql: str, timeout: float = 120.0) -> pd.DataFrame:
        """Executes a SQL query and parses the response into a Pandas DataFrame."""
        logger.debug(f"Executing QuestDB SQL: {sql}")
        try:
            response = httpx.get(self.url, params={"query": sql}, timeout=timeout)
            response.raise_for_status()
            res_json = response.json()

            if "error" in res_json:
                raise ValueError(f"QuestDB compilation error: {res_json['error']}")

            columns = [c["name"] for c in res_json.get("columns", [])]
            dataset = res_json.get("dataset", [])

            df = pd.DataFrame(dataset, columns=columns)
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df.set_index("timestamp", inplace=True)
                # Ensure UTC awareness
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                else:
                    df.index = df.index.tz_convert("UTC")
            return df
        except Exception as e:
            logger.error(f"QuestDB query failed: {e}")
            raise


class CorrelationAnalyzer:
    """Handles data processing, return calculations, and correlation metrics."""

    def __init__(self, client: QuestDBClient):
        self.client = client

    def fetch_and_align(self, timeframe: str, price_type: str = "mid") -> pd.DataFrame:
        """Builds and runs the query to get aligned NQ futures close price and FX_NQ price."""
        # Determine aggregate column for forex ticks
        if price_type == "mid":
            fx_expr = "last((bid + ask) / 2.0) as fx_close"
        elif price_type == "bid":
            fx_expr = "last(bid) as fx_close"
        elif price_type == "ask":
            fx_expr = "last(ask) as fx_close"
        else:
            raise ValueError(f"Unknown price type: {price_type}")

        sql = (
            f"SELECT a.timestamp, a.close as nq_close, b.fx_close "
            f"FROM nq_{timeframe} a "
            f"JOIN ("
            f"  SELECT timestamp, {fx_expr} "
            f"  FROM fx_nq_ticks "
            f"  SAMPLE BY {timeframe} FILL(NONE) "
            f") b ON a.timestamp = b.timestamp "
            f"ORDER BY timestamp ASC"
        )
        
        logger.info(f"Fetching aligned data for timeframe: {timeframe} using FX {price_type} price...")
        df = self.client.query(sql)
        logger.info(f"Successfully loaded {len(df)} aligned observations.")
        return df

    def add_returns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Appends simple percentage returns and log returns to the DataFrame."""
        df = df.copy()
        
        # Simple percentage returns
        df["nq_pct_return"] = df["nq_close"].pct_change()
        df["fx_pct_return"] = df["fx_close"].pct_change()
        
        # Log returns
        df["nq_log_return"] = np.log(df["nq_close"] / df["nq_close"].shift(1))
        df["fx_log_return"] = np.log(df["fx_close"] / df["fx_close"].shift(1))
        
        return df

    def analyze(self, df: pd.DataFrame, rolling_window: int = 100, max_lags: int = 10) -> Dict[str, Any]:
        """Runs the entire statistical correlation analysis suite."""
        if len(df) < 2:
            raise ValueError("Insufficient data to perform correlation analysis.")

        # Precompute returns
        df_rich = self.add_returns(df)
        
        # 1. Standard Correlation Coefficients (Pearson & Spearman)
        # Drop first row since return is NaN
        clean_returns = df_rich.dropna(subset=["nq_log_return", "fx_log_return"])
        
        pearsons = {
            "price": float(df_rich["nq_close"].corr(df_rich["fx_close"], method="pearson")),
            "pct_return": float(clean_returns["nq_pct_return"].corr(clean_returns["fx_pct_return"], method="pearson")),
            "log_return": float(clean_returns["nq_log_return"].corr(clean_returns["fx_log_return"], method="pearson"))
        }

        spearmans = {
            "price": float(df_rich["nq_close"].corr(df_rich["fx_close"], method="spearman")),
            "pct_return": float(clean_returns["nq_pct_return"].corr(clean_returns["fx_pct_return"], method="spearman")),
            "log_return": float(clean_returns["nq_log_return"].corr(clean_returns["fx_log_return"], method="spearman"))
        }

        # 2. Rolling correlation (Log Returns)
        rolling_corr = clean_returns["nq_log_return"].rolling(window=rolling_window).corr(clean_returns["fx_log_return"])
        rolling_mean = float(rolling_corr.mean())
        rolling_std = float(rolling_corr.std())
        rolling_min = float(rolling_corr.min())
        rolling_max = float(rolling_corr.max())

        # 3. Cross-correlation (Lead-lag analysis on log returns)
        cross_corrs = {}
        for lag in range(-max_lags, max_lags + 1):
            if lag < 0:
                # NQ lags FX (FX leads NQ) - shift NQ back in time relative to FX
                shifted_nq = clean_returns["nq_log_return"].shift(-lag)
                cross_corrs[lag] = float(shifted_nq.corr(clean_returns["fx_log_return"]))
            elif lag > 0:
                # NQ leads FX (FX lags NQ) - shift NQ forward in time relative to FX
                shifted_nq = clean_returns["nq_log_return"].shift(lag)
                cross_corrs[lag] = float(shifted_nq.corr(clean_returns["fx_log_return"]))
            else:
                cross_corrs[lag] = pearsons["log_return"]

        # Identify peak lag
        abs_corrs = {k: abs(v) for k, v in cross_corrs.items()}
        peak_lag = max(abs_corrs, key=abs_corrs.get)
        peak_corr = cross_corrs[peak_lag]

        return {
            "summary_stats": {
                "observations": len(df),
                "start_time": df.index.min().isoformat(),
                "end_time": df.index.max().isoformat(),
            },
            "correlation_coefficients": {
                "pearson": pearsons,
                "spearman": spearmans
            },
            "rolling_correlation": {
                "window_size": rolling_window,
                "mean": rolling_mean,
                "std": rolling_std,
                "min": rolling_min,
                "max": rolling_max,
                "series": rolling_corr # Pandas Series
            },
            "lead_lag_analysis": {
                "max_lags": max_lags,
                "cross_correlations": cross_corrs,
                "peak_lag": int(peak_lag),
                "peak_correlation": float(peak_corr)
            },
            "processed_df": df_rich
        }


class CorrelationPlotter:
    """Generates professional financial charts using Seaborn and Matplotlib."""

    def __init__(self, theme: str = "dark"):
        self.theme = theme
        if _PLOTTING_AVAILABLE:
            if theme == "dark":
                plt.style.use("dark_background")
                self.primary_color = "#3a86c8"   # Sleek Blue
                self.secondary_color = "#f77f00" # Warm Orange
                self.accent_color = "#d62728"    # Warning Red
                self.grid_color = "#333333"
                self.text_color = "#FFFFFF"
            else:
                sns.set_theme(style="whitegrid")
                self.primary_color = "#1f77b4"
                self.secondary_color = "#ff7f0e"
                self.accent_color = "#d62728"
                self.grid_color = "#e0e0e0"
                self.text_color = "#333333"

    def check_libraries(self) -> bool:
        """Returns True if matplotlib and seaborn are available, else logs warning."""
        if not _PLOTTING_AVAILABLE:
            logger.warning("Plotting requested but 'matplotlib' and 'seaborn' are not installed.")
            return False
        return True

    def plot_price_comparison(self, df: pd.DataFrame, title: str, save_path: str) -> None:
        """Plots the normalized price series of NQ and FX_NQ overlaid on each other."""
        if not self.check_libraries():
            return

        fig, ax = plt.subplots(figsize=(14, 6))
        
        # Normalize to 0-1 range to align scale
        nq_norm = (df["nq_close"] - df["nq_close"].min()) / (df["nq_close"].max() - df["nq_close"].min())
        fx_norm = (df["fx_close"] - df["fx_close"].min()) / (df["fx_close"].max() - df["fx_close"].min())

        ax.plot(df.index, nq_norm, label="NQ Futures (Normalized)", color=self.primary_color, alpha=0.85, linewidth=1.5)
        ax.plot(df.index, fx_norm, label="FX_NQ Forex Proxy (Normalized)", color=self.secondary_color, alpha=0.85, linewidth=1.5, linestyle="--")

        ax.set_title(title, fontsize=14, fontweight="bold", color=self.text_color, pad=15)
        ax.set_xlabel("Date", fontsize=11, color=self.text_color)
        ax.set_ylabel("Normalized Index Level (0-1)", fontsize=11, color=self.text_color)
        ax.legend(frameon=True, facecolor="#1c1c1c" if self.theme == "dark" else "#f5f5f5")
        ax.grid(True, color=self.grid_color, linestyle=":", alpha=0.6)
        
        plt.tight_layout()
        plt.savefig(save_path, dpi=300)
        plt.close()
        logger.info(f"Price comparison chart saved to: {save_path}")

    def plot_rolling_correlation(self, series: pd.Series, window: int, title: str, save_path: str) -> None:
        """Plots the rolling correlation line chart with its mean."""
        if not self.check_libraries():
            return

        fig, ax = plt.subplots(figsize=(14, 5))
        
        mean_val = series.mean()
        ax.plot(series.index, series, color="#2ca02c", label=f"Rolling Corr ({window} periods)", alpha=0.8, linewidth=1.5)
        ax.axhline(mean_val, color=self.accent_color, linestyle="--", linewidth=1.2, label=f"Mean: {mean_val:.4f}")
        ax.axhline(0, color="gray", linestyle="-", linewidth=0.5)

        ax.set_title(title, fontsize=14, fontweight="bold", color=self.text_color, pad=15)
        ax.set_xlabel("Date", fontsize=11, color=self.text_color)
        ax.set_ylabel("Correlation Coefficient", fontsize=11, color=self.text_color)
        ax.set_ylim(-1.05, 1.05)
        ax.legend(frameon=True, facecolor="#1c1c1c" if self.theme == "dark" else "#f5f5f5", loc="lower left")
        ax.grid(True, color=self.grid_color, linestyle=":", alpha=0.6)

        plt.tight_layout()
        plt.savefig(save_path, dpi=300)
        plt.close()
        logger.info(f"Rolling correlation chart saved to: {save_path}")

    def plot_lead_lag(self, cross_corrs: Dict[int, float], peak_lag: int, label1: str, label2: str, title: str, save_path: str) -> None:
        """Plots cross-correlation values at different lags as a bar chart."""
        if not self.check_libraries():
            return

        fig, ax = plt.subplots(figsize=(12, 5))
        
        lags = list(cross_corrs.keys())
        corrs = list(cross_corrs.values())
        
        # Color the bar with the highest absolute correlation differently
        colors = [self.accent_color if l == peak_lag else self.primary_color for l in lags]
        
        bars = ax.bar(lags, corrs, color=colors, alpha=0.8, width=0.6)
        ax.axhline(0, color="gray", linestyle="-", linewidth=0.8)
        
        # Highlight peak correlation
        for bar, lag, corr in zip(bars, lags, corrs):
            if lag == peak_lag:
                ax.text(
                    bar.get_x() + bar.get_width()/2,
                    bar.get_height() + (0.02 if corr >= 0 else -0.05),
                    f"{corr:.4f}",
                    ha="center", va="bottom" if corr >= 0 else "top",
                    fontweight="bold", color=self.accent_color, fontsize=10
                )

        ax.set_title(title, fontsize=14, fontweight="bold", color=self.text_color, pad=15)
        ax.set_xlabel(f"Lag (Intervals) — Negative: {label2} leads | Positive: {label1} leads", fontsize=11, color=self.text_color)
        ax.set_ylabel("Correlation Coefficient", fontsize=11, color=self.text_color)
        ax.set_xticks(lags)
        ax.grid(True, color=self.grid_color, linestyle=":", alpha=0.6)

        plt.tight_layout()
        plt.savefig(save_path, dpi=300)
        plt.close()
        logger.info(f"Lead-lag chart saved to: {save_path}")


class ReportFormatter:
    """Formats the statistical analysis output for console and markdown reports."""

    @staticmethod
    def print_console_report(timeframe: str, price_type: str, stats: Dict[str, Any]) -> None:
        """Prints a human-readable summary of the correlation analysis to the console."""
        info = stats["summary_stats"]
        coeffs = stats["correlation_coefficients"]
        rolling = stats["rolling_correlation"]
        lead_lag = stats["lead_lag_analysis"]

        print("=" * 60)
        print(f"CORRELATION ANALYSIS REPORT: NQ FUTURES VS FX_NQ FOREX")
        print("=" * 60)
        print(f"Timeframe:      nq_{timeframe}")
        print(f"FX Price Type:  {price_type}")
        print(f"Observations:   {info['observations']:,} rows")
        print(f"Start Time:     {info['start_time']}")
        print(f"End Time:       {info['end_time']}")
        print("-" * 60)
        
        print("CORRELATION COEFFICIENTS:")
        print(f"  - Price Levels (Pearson):      {coeffs['pearson']['price']:.6f}")
        print(f"  - Price Levels (Spearman):     {coeffs['spearman']['price']:.6f}")
        print(f"  - Pct Returns  (Pearson):      {coeffs['pearson']['pct_return']:.6f}")
        print(f"  - Pct Returns  (Spearman):     {coeffs['spearman']['pct_return']:.6f}")
        print(f"  - Log Returns  (Pearson):      {coeffs['pearson']['log_return']:.6f}")
        print(f"  - Log Returns  (Spearman):     {coeffs['spearman']['log_return']:.6f}")
        print("-" * 60)

        print(f"ROLLING CORRELATION STATISTICS (Window: {rolling['window_size']} intervals):")
        print(f"  - Mean Rolling Correlation:    {rolling['mean']:.6f}")
        print(f"  - Std Dev:                     {rolling['std']:.6f}")
        print(f"  - Range [Min / Max]:           [{rolling['min']:.6f} / {rolling['max']:.6f}]")
        print("-" * 60)

        print("LEAD-LAG (CROSS-CORRELATION) ANALYSIS:")
        print(f"  - Max Lag Window:             {lead_lag['max_lags']} intervals")
        print(f"  - Peak Correlation:            {lead_lag['peak_correlation']:.6f}")
        
        peak = lead_lag["peak_lag"]
        if peak < 0:
            lead_str = f"FX_NQ leads NQ Futures by {-peak} interval(s)"
        elif peak > 0:
            lead_str = f"NQ Futures leads FX_NQ by {peak} interval(s)"
        else:
            lead_str = "Co-incident movement (no lag detected)"
        print(f"  - Peak Lag / Direction:        Lag {peak} ({lead_str})")
        print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Check how correlated NQ futures and FX_NQ forex proxy are in QuestDB."
    )
    parser.add_argument(
        "--timeframe",
        type=str,
        default="1m",
        choices=["1m", "5m", "15m", "30m", "1h", "4h", "1d"],
        help="Timeframe of the target NQ table (default: 1m)."
    )
    parser.add_argument(
        "--price-type",
        type=str,
        default="mid",
        choices=["mid", "bid", "ask"],
        help="Price to aggregate from FX ticks (default: mid)."
    )
    parser.add_argument(
        "--window",
        type=int,
        default=100,
        help="Rolling window size for correlation stability analysis (default: 100)."
    )
    parser.add_argument(
        "--lags",
        type=int,
        default=10,
        help="Max number of intervals to test for lead-lag cross correlation (default: 10)."
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Generate and save PNG charts."
    )
    parser.add_argument(
        "--plot-dir",
        type=str,
        default=".",
        help="Directory to save generated charts (default: current directory)."
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Path to save the stats JSON output (optional)."
    )
    parser.add_argument(
        "--theme",
        type=str,
        default="dark",
        choices=["dark", "light"],
        help="Plotting theme (default: dark)."
    )

    args = parser.parse_args()

    client = QuestDBClient()
    analyzer = CorrelationAnalyzer(client)

    try:
        # 1. Fetch aligned data
        df = analyzer.fetch_and_align(args.timeframe, args.price_type)

        # 2. Analyze correlation
        stats = analyzer.analyze(df, rolling_window=args.window, max_lags=args.lags)

        # 3. Print report
        ReportFormatter.print_console_report(args.timeframe, args.price_type, stats)

        # 4. Save JSON results if requested
        if args.output:
            # We copy stats and format for serialization (drop the raw series and df)
            export_stats = {
                "summary_stats": stats["summary_stats"],
                "correlation_coefficients": stats["correlation_coefficients"],
                "rolling_correlation": {
                    "window_size": stats["rolling_correlation"]["window_size"],
                    "mean": stats["rolling_correlation"]["mean"],
                    "std": stats["rolling_correlation"]["std"],
                    "min": stats["rolling_correlation"]["min"],
                    "max": stats["rolling_correlation"]["max"],
                },
                "lead_lag_analysis": stats["lead_lag_analysis"]
            }
            with open(args.output, "w") as f:
                json.dump(export_stats, f, indent=4)
            logger.info(f"JSON metrics written to: {args.output}")

        # 5. Generate plots if requested
        if args.plot:
            os.makedirs(args.plot_dir, exist_ok=True)
            plotter = CorrelationPlotter(theme=args.theme)

            # Price overlay plot
            price_plot_path = os.path.join(args.plot_dir, f"price_overlay_{args.timeframe}.png")
            plotter.plot_price_comparison(
                df=stats["processed_df"],
                title=f"NQ Futures Close vs FX_NQ Forex (Normalized) - {args.timeframe}",
                save_path=price_plot_path
            )

            # Rolling correlation plot
            roll_plot_path = os.path.join(args.plot_dir, f"rolling_corr_{args.timeframe}.png")
            plotter.plot_rolling_correlation(
                series=stats["rolling_correlation"]["series"],
                window=args.window,
                title=f"Rolling Correlation of Log Returns - NQ vs FX_NQ ({args.timeframe})",
                save_path=roll_plot_path
            )

            # Lead-lag plot
            lead_lag_path = os.path.join(args.plot_dir, f"lead_lag_{args.timeframe}.png")
            plotter.plot_lead_lag(
                cross_corrs=stats["lead_lag_analysis"]["cross_correlations"],
                peak_lag=stats["lead_lag_analysis"]["peak_lag"],
                label1="NQ Futures",
                label2="FX_NQ Forex",
                title=f"Cross-Correlation at Lags - NQ vs FX_NQ ({args.timeframe})",
                save_path=lead_lag_path
            )

    except Exception as e:
        logger.error(f"Execution failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
