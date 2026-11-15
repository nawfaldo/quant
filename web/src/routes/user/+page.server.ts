import { avatarUrl } from '#lib/server/auth.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = ({ locals }) => ({ user: locals.user.name, avatar: avatarUrl(locals.user.id) });
