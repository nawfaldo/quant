import { chartSymbol } from '#lib/symbols.ts';

// Same naming as the Open positions table (OpenPositions.svelte), shared for the History tab.
const ACRONYMS = new Set([
	'orb', 'rsi', 'rsi2', 'ema', 'sma', 'ma', 'macd', 'atr', 'adx', 'bb', 'vwap', 'obv', 'cci', 'xma',
	'htf', 'rvol', 'vol', 'fib', 'cusum', 'fvg', 'ifvg', 'bos', 'mss', 'cisd', 'po3', 'amd', 'rth', 'ny', 'stoch'
]);
const TIKTOK_TAGS = new Set([
	'atp', 'av', 'ma', 'bnq', 'jg', 'cf', 'ct', 'dh', 'dt', 'fx', 'jjs', 'jt', 'jj', 'kk', 'lux', 'luxalgo',
	'mac', 'mql', 'mab', 'mul', 'nt', 'nmk', 'novo', 'o80', 'ptl', 'plab', 'pql', 'qc', 'qs', 'qt', 'qp',
	'qtt', 'rp', 'safu', 's4a', 'ctel', 'tqb', 'tom', 'twp', 'td', 'tmb', 'tr', 'mus', 'tst', 'yf'
]);

export const titleCase = (text: string) =>
	text
		.split(' ')
		.map((w) => (ACRONYMS.has(w.toLowerCase()) ? w.toUpperCase() : w.charAt(0).toUpperCase() + w.slice(1)))
		.join(' ');

// fn_ustec_ptl_orb_candle_30m-off on NDX100 -> "orb candle".
export function strategyName(strategy: string, symbol: string) {
	const [name] = strategy.replace(/^fn_/i, '').split('-');
	const parts = name.split('_');
	const market = chartSymbol(symbol).toLowerCase();
	if (parts.length > 1 && parts[0] && market.startsWith(parts[0].toLowerCase())) parts.shift();
	if (parts.length > 1 && TIKTOK_TAGS.has(parts[0].toLowerCase())) parts.shift();
	return parts.filter((w) => !/^\d+[mh]$/.test(w)).join(' ');
}

// Everything after the first "-": london_open, offhour, ...
export const strategyWindow = (strategy: string) =>
	strategy
		.replace(/^fn_/i, '')
		.split('-')
		.slice(1)
		// barlb = bar-counted lookbacks, an engine setting, not a time window.
		.filter((tag) => tag !== 'barlb')
		.map((tag) => (tag === 'off' ? 'offhour' : tag.replaceAll('_', ' ')))
		.join(' ');

// "RVOL-London Open@30m": strategy, window (if any), timeframe (if any).
export function strategyLabel(strategy: string, symbol: string) {
	const name = titleCase(strategyName(strategy, symbol)) || '—';
	// No window tag (e.g. -barlb alone) = the default RTH window.
	const window = titleCase(strategyWindow(strategy) || 'rth');
	const tf = strategy.replace(/^fn_/i, '').split('-')[0].split('_').find((w) => /^\d+[mh]$/.test(w));
	return `${name}${window ? '-' + window : ''}${tf ? '@' + tf : ''}`;
}
