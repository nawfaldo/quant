import { activeAccounts } from '#lib/server/accounts.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = () => ({ accounts: activeAccounts() });
