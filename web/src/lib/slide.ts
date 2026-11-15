import { goto } from '$app/navigation';

// Login / logout transition. The shop ("/") always sits in front of /chart:
//  - login: the shop is frozen, cut into six horizontal bands, and the bands slide off to
//    the left (top band fastest), uncovering /chart underneath.
//  - logout: the reverse. /chart stays frozen while the shop's six bands slide back in from
//    the left over it; once they meet, the live shop is underneath them.

const BANDS = 6;
const EASE = 'cubic-bezier(0.65, 0, 0.35, 1)';
const duration = (i: number) => 450 + i * 80;

// A copy of the page's DOM; canvases (charts) are copied pixel for pixel, since cloning a
// canvas element leaves it blank.
function snapshot(src: HTMLElement): HTMLElement {
	const copy = src.cloneNode(true) as HTMLElement;
	const from = src.querySelectorAll('canvas');
	const to = copy.querySelectorAll('canvas');
	from.forEach((c, k) => {
		const d = to[k];
		d.width = c.width;
		d.height = c.height;
		try {
			d.getContext('2d')?.drawImage(c, 0, 0);
		} catch {
			// unreadable canvas: blank for the half second it is on screen
		}
	});
	return copy;
}

const app = () => document.querySelector('body > div') as HTMLElement;

// A fixed layer holding copies of the page as it looks now: one whole copy, or six copies
// each clipped to a sixth of the screen (so sticky and fixed parts stay where they were).
function freeze(bands: number, bg: string) {
	const src = app();
	const { innerWidth: w, innerHeight: h, scrollX: x, scrollY: y } = window;
	const layer = document.createElement('div');
	layer.style.cssText = 'position:fixed;inset:0;z-index:2147483000;pointer-events:none';
	const parts: HTMLElement[] = [];
	const views: HTMLElement[] = [];
	for (let i = 0; i < bands; i++) {
		const top = (i * h) / bands;
		const bottom = Math.max(0, h - ((i + 1) * h) / bands - 1);
		const part = document.createElement('div');
		part.style.cssText = `position:absolute;inset:0;clip-path:inset(${top}px 0 ${bottom}px 0)`;
		const view = document.createElement('div');
		view.style.cssText = `position:absolute;left:0;top:0;width:${w}px;height:${h}px;overflow:hidden;background:${bg}`;
		view.appendChild(snapshot(src));
		part.appendChild(view);
		layer.appendChild(part);
		parts.push(part);
		views.push(view);
	}
	document.body.appendChild(layer);
	for (const v of views) {
		v.scrollTop = y;
		v.scrollLeft = x;
	}
	return { layer, parts };
}

const slide = (parts: HTMLElement[], from: string, to: string) =>
	Promise.all(
		parts.map(
			(p, i) =>
				p.animate([{ transform: `translateX(${from})` }, { transform: `translateX(${to})` }], {
					duration: duration(i),
					easing: EASE,
					fill: 'forwards'
				}).finished
		)
	);

// Images and fonts of the page that just mounted, so the copies are not half-loaded.
async function settled(ms = 1500) {
	const imgs = [...app().querySelectorAll('img')].filter((i) => !i.complete);
	const loaded = Promise.all(imgs.map((i) => new Promise((r) => ((i.onload = r), (i.onerror = r)))));
	await Promise.race([Promise.all([loaded, document.fonts.ready]), new Promise((r) => setTimeout(r, ms))]);
}

export async function login() {
	const shop = freeze(BANDS, '#f5f4f0');
	await goto('/chart', { invalidateAll: true });
	// let the charts draw their candles before they are uncovered
	await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
	await new Promise((r) => setTimeout(r, 250));
	await slide(shop.parts, '0', '-100%');
	shop.layer.remove();
}

export async function logout() {
	const chart = freeze(1, '#181818');
	await goto('/', { invalidateAll: true });
	window.scrollTo(0, 0);
	await settled();
	const shop = freeze(BANDS, '#f5f4f0');
	shop.parts.forEach((p) => (p.style.transform = 'translateX(-100%)'));
	await slide(shop.parts, '-100%', '0');
	chart.layer.remove();
	shop.layer.remove();
}
