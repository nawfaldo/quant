import { db } from './db.ts';

export type Position = {
	ticket: number;
	account: string;
	strategy: string;
	symbol: string;
	side: 'long' | 'short';
	openTime: number; // unix seconds, UTC
	price: number; // the broker's fill
	// Where the chart draws it: the strategy's own entry on the Dukascopy /
	// Binance bars, on the chart's clock (New York wall clock as UTC). The
	// broker's fill and time when the position has no strategy entry.
	chartTime: number;
	chartPrice: number;
	lot: number;
	pnl: number;
};

// Unix seconds -> the chart's convention: New York wall-clock time written as
// if it were UTC (how every bar in the store is stamped).
const nyParts = new Intl.DateTimeFormat('en-US', {
	timeZone: 'America/New_York',
	hourCycle: 'h23',
	year: 'numeric',
	month: '2-digit',
	day: '2-digit',
	hour: '2-digit',
	minute: '2-digit',
	second: '2-digit'
});
function toChartTime(unix: number): number {
	const p = Object.fromEntries(nyParts.formatToParts(new Date(unix * 1000)).map((x) => [x.type, x.value]));
	return Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second) / 1000;
}

// MT5 stamps a position with the SERVER's clock. Exness runs on UTC; FundedNext
// runs on New York wall clock + 7 hours (since 2024-07-08), so its stamp minus
// 7 hours is already the chart's clock.
const FUNDEDNEXT_AHEAD_OF_NY = 7 * 3600;

// The inverse of toChartTime: New York wall clock written as UTC -> unix seconds.
function fromChartTime(chart: number): number {
	let unix = chart;
	for (let i = 0; i < 2; i++) unix += chart - toChartTime(unix);
	return unix;
}

// Positions the MT5 bridge reports as open, named by the strategy that opened
// them, with that strategy's entry: the live server records the bar time and
// price it entered on in account_trades (the "bookmap_" columns are a legacy
// name; the bars are the vendor tables the chart draws).
export function openPositions(): Position[] {
	return db
		.prepare(
			`SELECT p.ticket, a.login AS account, COALESCE(s.strategy, '') AS strategy, p.symbol,
			        p.position_type AS side, p.open_time, p.open_price, p.volume, p.profit, a.server,
			        t.bookmap_entry_time AS entry_time, t.bookmap_entry_price AS entry_price
			 FROM mt5_bridge_positions p
			 JOIN mt5_accounts a ON a.id = p.account_id
			 LEFT JOIN mt5_strategy_positions s ON s.rowid = (
			     SELECT rowid FROM mt5_strategy_positions
			     WHERE account_id = p.account_id AND ticket = p.ticket
			     ORDER BY status = 'open' DESC LIMIT 1)
			 LEFT JOIN account_trades t ON t.id = s.account_trade_id
			 ORDER BY p.open_time`
		)
		.all()
		.map((r: Record<string, unknown>) => {
			const stamp = Number(r.open_time);
			const fundedNext = String(r.server).startsWith('FundedNext');
			const brokerChartTime = fundedNext ? stamp - FUNDEDNEXT_AHEAD_OF_NY : toChartTime(stamp);
			// "2026-10-13 23:30", New York wall clock.
			const entryTime = Date.parse(String(r.entry_time ?? '').replace(' ', 'T') + ':00Z') / 1000;
			const entryPrice = Number(r.entry_price ?? 0);
			const hasEntry = Number.isFinite(entryTime) && entryPrice > 0;
			return {
				ticket: Number(r.ticket),
				account: String(r.account),
				strategy: String(r.strategy),
				symbol: String(r.symbol),
				side: r.side === 'short' ? 'short' : 'long',
				openTime: fundedNext ? fromChartTime(brokerChartTime) : stamp,
				price: Number(r.open_price),
				chartTime: hasEntry ? entryTime : brokerChartTime,
				chartPrice: hasEntry ? entryPrice : Number(r.open_price),
				lot: Number(r.volume),
				pnl: Number(r.profit)
			};
		});
}
