import { json } from '@sveltejs/kit';
import { listSymbols } from '#lib/server/bars.ts';
import type { RequestHandler } from './$types';

// Every symbol with Dukascopy or Binance 1-minute data, for the symbol picker.
export const GET: RequestHandler = () => json(listSymbols());
