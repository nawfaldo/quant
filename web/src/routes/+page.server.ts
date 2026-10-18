import { activeAccounts } from '#lib/server/accounts.ts';
import { chartBars, symbolTable } from '#lib/server/bars.ts';
import { openPositions } from '#lib/server/positions.ts';
import { chartLayout } from '#lib/server/webdb.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async () => {
	const saved = chartLayout();
	// The first chart's candles come with the page; the others fetch their own.
	const first = saved.slots[0];
	const table = symbolTable(first.symbol) ?? 'ustec_1m';
	return {
		accounts: activeAccounts(),
		positions: openPositions(),
		layout: saved.layout,
		slots: saved.slots,
		bars: await chartBars(table, 3000, first.minutes)
	};
};
