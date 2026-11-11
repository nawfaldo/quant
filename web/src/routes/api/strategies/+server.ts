import { json } from '@sveltejs/kit';
import { strategyPool } from '#lib/server/backtest.ts';
import type { RequestHandler } from './$types';

// The strategies a book can add: GET /api/strategies.
export const GET: RequestHandler = () => json(strategyPool());
