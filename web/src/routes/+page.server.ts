import type { PageServerLoad } from './$types';

// Values for the fake Cloudflare 522 page: a fresh Ray ID, the time in UTC, the visitor's IP.
export const load: PageServerLoad = ({ request, getClientAddress }) => {
	const hex = Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, '0')).join('');
	return {
		ray: hex,
		time: new Date().toISOString().replace('T', ' ').slice(0, 19) + ' UTC',
		ip: request.headers.get('cf-connecting-ip') ?? getClientAddress()
	};
};
