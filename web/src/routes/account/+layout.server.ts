import { activeAccounts, allBooks } from '#lib/server/accounts.ts';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = () => ({ accounts: activeAccounts(), books: allBooks() });
