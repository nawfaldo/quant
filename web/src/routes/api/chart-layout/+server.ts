import { error, json } from '@sveltejs/kit';
import { symbolTable, TIMEFRAMES } from '#lib/server/bars.ts';
import { CHART_SLOTS, setChartLayout, setChartSlot } from '#lib/server/webdb.ts';
import type { RequestHandler } from './$types';

const LAYOUT_COUNT = 6;

// Save the chart layout: { layout } and/or one slot's { slot, symbol, minutes }.
export const POST: RequestHandler = async ({ request }) => {
	const body = await request.json();
	if (body.layout !== undefined) {
		const layout = Number(body.layout);
		if (!Number.isInteger(layout) || layout < 0 || layout >= LAYOUT_COUNT) error(400, 'bad layout');
		setChartLayout(layout);
	}
	if (body.slot !== undefined) {
		const slot = Number(body.slot);
		const minutes = Number(body.minutes);
		const symbol = String(body.symbol ?? '').toUpperCase();
		if (!Number.isInteger(slot) || slot < 0 || slot >= CHART_SLOTS) error(400, 'bad slot');
		if (!(TIMEFRAMES as readonly number[]).includes(minutes)) error(400, 'bad timeframe');
		if (!symbolTable(symbol)) error(400, 'bad symbol');
		setChartSlot(slot, symbol, minutes);
	}
	return json({ ok: true });
};
