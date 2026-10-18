// Where the last forward circle started (the Settings link). Going back shrinks into it.
let from: { x: number; y: number } | undefined;

// One dark cover, the chart background, grows or shrinks as a circle around (x, y).
function cover(x: number, y: number) {
	const el = document.createElement('div');
	Object.assign(el.style, {
		position: 'fixed',
		inset: '0',
		zIndex: '9999',
		background: '#121212',
		pointerEvents: 'none'
	});
	document.body.append(el);
	return el;
}

// The circle's radius that just covers the page's farthest corner.
const radius = (x: number, y: number) => Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
const shape = (r: number, x: number, y: number) => `circle(${r}px at ${x}px ${y}px)`;

// Forward: a circle grows out of (x, y) to cover the page, `show` switches the
// view under it, and the cover fades away.
export async function circleOpen(show: () => unknown, x: number, y: number) {
	from = { x, y };
	const el = cover(x, y);
	const r = radius(x, y);
	try {
		await el.animate([{ clipPath: shape(0, x, y) }, { clipPath: shape(r, x, y) }], {
			duration: 200,
			easing: 'ease-in',
			fill: 'forwards'
		}).finished;
		await show();
		await el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 100, fill: 'forwards' }).finished;
	} finally {
		el.remove();
	}
}

// Back: the reverse of circleOpen. The cover fades in, `hide` switches the view
// back under it, and the circle shrinks into the spot the forward circle started from.
export async function circleClose(hide: () => unknown, x: number, y: number) {
	const t = from ?? { x, y };
	from = undefined;
	const el = cover(t.x, t.y);
	const r = radius(t.x, t.y);
	el.style.clipPath = shape(r, t.x, t.y);
	try {
		await el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 100, fill: 'forwards' }).finished;
		await hide();
		await el.animate([{ clipPath: shape(r, t.x, t.y) }, { clipPath: shape(0, t.x, t.y) }], {
			duration: 200,
			easing: 'ease-out',
			fill: 'forwards'
		}).finished;
	} finally {
		el.remove();
	}
}
