import tailwindcss from '@tailwindcss/vite';
import adapter from '@sveltejs/adapter-node';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	server: {
		// tools/start_web.bat serves the dev server through a Cloudflare quick
		// tunnel, whose requests arrive as <random>.trycloudflare.com.
		allowedHosts: ['.trycloudflare.com', 'ariyanto.cloud', 'www.ariyanto.cloud'],
		// Never serve the databases (users, sessions) as raw files.
		fs: { deny: ['.env', '.env.*', '*.{crt,pem}', '**/.git/**', '**/*.db', '**/*.db-*', '**/data/**'] }
	},
	plugins: [
		{
			// Cloudflare stamps .js files with a 4-hour browser cache, so browsers kept running stale
			// dev modules (the page flashed, then went blank). Every response is no-store instead.
			name: 'no-store',
			configureServer(server) {
				server.middlewares.use((req, res, next) => {
					// Pre-bundled deps carry a content hash (?v=...): safe to cache, and they are most of
					// the bytes (three.js, lightweight-charts), so logins don't re-download them each time.
					if (/^\/node_modules\/\.vite\/deps\/[^?]+\?v=[0-9a-f]+/.test(req.url ?? '')) return next();
					const set = res.setHeader.bind(res);
					// Shop photos rarely change: a day in the browser and at Cloudflare, so "/" doesn't
					// re-download them on every visit.
					if (/^\/(figs\/|sky\.jpg)/.test(req.url ?? '')) {
						res.setHeader = (k, v) =>
							String(k).toLowerCase() === 'cache-control' ? res : set(k, v);
						set('Cache-Control', 'public, max-age=86400');
						return next();
					}
					res.setHeader = (k, v) =>
						set(k, String(k).toLowerCase() === 'cache-control' ? 'no-store' : v);
					set('Cache-Control', 'no-store');
					set('CDN-Cache-Control', 'no-store');
					next();
				});
			}
		},
		tailwindcss(),
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},

			// adapter-node: `bun run build` makes web/build, which tools/start_web.bat serves with node.
			adapter: adapter()
		})
	]
});
