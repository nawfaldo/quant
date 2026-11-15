<script lang="ts">
	// ASUS Prime AP201, black mesh, as a CSS 3D model you drag to rotate (it coasts a little on release).
	// Real size 205 W x 350 H x 460 D mm (feet included), drawn at K px per mm, laid out from ASUS's product
	// photos: a solid band wraps the top of the front and both sides, a solid strip runs along the bottom,
	// the front-to-side corners are rounded, the top panel is mesh inside a solid border, and the rear is
	// coarse square-hole steel with the power inlet, I/O cutout, 120 mm fan grille and four slot covers.
	import { onMount } from 'svelte';

	let { class: klass = '' }: { class?: string } = $props();

	const K = 0.3;
	const mm = (v: number) => v * K;
	const W = mm(205), D = mm(460);
	const FOOT_H = mm(16);
	const H = mm(350) - FOOT_H; // body without feet
	const R = mm(10); // front corner radius
	const Y = -FOOT_H / 2; // body centre: the case with its feet is centred on the origin

	let rx = $state(0); // tilt, degrees: level
	let ry = $state(90); // turn: square-on to the side panel with the lit GPU logo
	let vx = 0, vy = 0, raf = 0;
	let stageW = $state(166);
	// Keep the case's right-most point EDGE px from the stage's right side at every angle (it lines up with the
	// toolbar icons above): project the eight corners (rotateY, rotateX, 900 px perspective) and shift the stage.
	const EDGE = 14; // projected corner sits ~2 px inside the drawn edge; 14 here = 16 px on screen
	const reach = $derived.by(() => {
		const r = (a: number) => (a * Math.PI) / 180;
		const cy = Math.cos(r(ry)), sy = Math.sin(r(ry)), cx = Math.cos(r(rx)), sx = Math.sin(r(rx));
		let m = -Infinity;
		for (const x of [-W / 2, W / 2])
			for (const y of [Y - H / 2, H / 2 + FOOT_H / 2])
				for (const z of [-D / 2, D / 2]) {
					const x1 = x * cy + z * sy, z1 = -x * sy + z * cy, z2 = y * sx + z1 * cx;
					m = Math.max(m, (x1 * 900) / (900 - z2));
				}
		return m;
	});

	type Face = { key: string; kind: string; w: number; h: number; t: string; n: [number, number, number]; r?: string };
	const t3 = (x: number, y: number, z: number) => `translate3d(${x}px, ${y}px, ${z}px)`;

	const faces: Face[] = [
		{ key: 'front', kind: 'front', w: W - 2 * R, h: H, t: t3(0, Y, D / 2), n: [0, 0, 1] },
		{ key: 'back', kind: 'back', w: W, h: H, t: `${t3(0, Y, -D / 2)} rotateY(180deg)`, n: [0, 0, -1] },
		{ key: 'right', kind: 'side', w: D - R, h: H, t: `${t3(W / 2, Y, -R / 2)} rotateY(90deg)`, n: [1, 0, 0] },
		{ key: 'left', kind: 'side', w: D - R, h: H, t: `${t3(-W / 2, Y, -R / 2)} rotateY(-90deg)`, n: [-1, 0, 0] },
		// top: its local bottom edge is the front, so the two lower corners follow the rounded front
		{ key: 'top', kind: 'top', w: W, h: D, t: `${t3(0, Y - H / 2, 0)} rotateX(90deg)`, n: [0, -1, 0], r: `0 0 ${R}px ${R}px` },
		// bottom: its local top edge is the front
		{ key: 'bottom', kind: 'bottom', w: W, h: D, t: `${t3(0, Y + H / 2, 0)} rotateX(-90deg)`, n: [0, 1, 0], r: `${R}px ${R}px 0 0` }
	];

	// Rounded front corners: a quarter circle in N flat strips.
	const N = 4, half = Math.PI / 4 / N;
	for (const sx of [-1, 1])
		for (let i = 0; i < N; i++) {
			const th = (i + 0.5) * 2 * half;
			const x = sx * (W / 2 - R + R * Math.cos(half) * Math.sin(th));
			const z = D / 2 - R + R * Math.cos(half) * Math.cos(th);
			faces.push({
				key: `corner${sx}${i}`,
				kind: 'corner',
				w: 2 * R * Math.sin(half) + 0.25,
				h: H,
				t: `${t3(x, Y, z)} rotateY(${(sx * th * 180) / Math.PI}deg)`,
				n: [sx * Math.sin(th), 0, Math.cos(th)]
			});
		}

	// Feet: wide tapered blocks under the long edges, flush with the sides, near the front and back.
	for (const [i, [sx, sz]] of [[-1, -1], [1, -1], [-1, 1], [1, 1]].entries()) {
		const fw = mm(36), fd = mm(40), cx = sx * (W / 2 - mm(18)), cy = H / 2, cz = sz * (D / 2 - mm(34));
		faces.push(
			{ key: `f${i}a`, kind: 'foot foot-end', w: fw, h: FOOT_H, t: t3(cx, cy, cz + fd / 2), n: [0, 0, 1] },
			{ key: `f${i}b`, kind: 'foot foot-end', w: fw, h: FOOT_H, t: `${t3(cx, cy, cz - fd / 2)} rotateY(180deg)`, n: [0, 0, -1] },
			{ key: `f${i}c`, kind: 'foot foot-side', w: fd, h: FOOT_H, t: `${t3(cx + fw / 2, cy, cz)} rotateY(90deg)`, n: [1, 0, 0] },
			{ key: `f${i}d`, kind: 'foot foot-side', w: fd, h: FOOT_H, t: `${t3(cx - fw / 2, cy, cz)} rotateY(-90deg)`, n: [-1, 0, 0] },
			{ key: `f${i}e`, kind: 'foot', w: fw * 0.8, h: fd * 0.84, t: `${t3(cx, cy + FOOT_H / 2, cz)} rotateX(-90deg)`, n: [0, 1, 0] }
		);
	}

	// ---- Inside, seen through the mesh. y is measured in mm down from the top of the body.
	const yAt = (v: number) => Y - H / 2 + mm(v);
	const IN = 0.4; // inner walls sit just inside the shell
	// Dark inner walls, facing inward, so the far side of the case is never see-through.
	faces.push(
		{ key: 'in-l', kind: 'inner', w: D, h: H, t: `${t3(-W / 2 + IN, Y, 0)} rotateY(90deg)`, n: [1, 0, 0] },
		{ key: 'in-r', kind: 'inner', w: D, h: H, t: `${t3(W / 2 - IN, Y, 0)} rotateY(-90deg)`, n: [-1, 0, 0] },
		{ key: 'in-f', kind: 'inner', w: W, h: H, t: `${t3(0, Y, D / 2 - IN)} rotateY(180deg)`, n: [0, 0, -1] },
		{ key: 'in-b', kind: 'inner', w: W, h: H, t: t3(0, Y, -D / 2 + IN), n: [0, 0, 1] },
		{ key: 'in-t', kind: 'inner', w: W, h: D, t: `${t3(0, Y - H / 2 + IN, 0)} rotateX(-90deg)`, n: [0, 1, 0] },
		{ key: 'in-d', kind: 'inner', w: W, h: D, t: `${t3(0, Y + H / 2 - IN, 0)} rotateX(90deg)`, n: [0, -1, 0] }
	);

	// Solid structure behind the mesh, as in the open-case photo: on the board side the front section is the
	// PSU compartment's solid wall (with a mesh window) standing out from the tray, and chassis pillars
	// run down the rear and the front edge.
	const CH_Z0 = 40, CH_X = 102.5 - 95; // compartment: starts 40 mm in front of centre, 95 mm deep from the tray wall
	faces.push(
		{ key: 'ch-s', kind: 'inner chamber', w: mm(230 - CH_Z0), h: H - mm(40), t: `${t3(mm(CH_X), Y + mm(20), mm((230 + CH_Z0) / 2))} rotateY(-90deg)`, n: [-1, 0, 0] },
		{ key: 'ch-e', kind: 'inner', w: mm(95), h: H - mm(40), t: `${t3(mm(CH_X + 47.5), Y + mm(20), mm(CH_Z0))} rotateY(180deg)`, n: [0, 0, -1] },
		{ key: 'pil-r', kind: 'inner pillar', w: mm(16), h: H, t: `${t3(-W / 2 + mm(5), Y, -D / 2 + mm(8))} rotateY(-90deg)`, n: [-1, 0, 0] },
		{ key: 'pil-f', kind: 'inner pillar', w: mm(14), h: H, t: `${t3(-W / 2 + mm(5), Y, D / 2 - mm(30))} rotateY(-90deg)`, n: [-1, 0, 0] }
	);

	// Motherboard: ROG Crosshair X670E Gene (244 x 244 mm micro-ATX) on the tray on the right-hand wall, parts
	// facing the mesh side. Board plane and height are set so its I/O fills the rear I/O cutout (13-64 mm in
	// from the right wall, 58-224 mm down) and its x16 slot meets the first rear slot cover.
	const MB = 244, MB_TOP = 62, MB_REAR = 12, MB_X = 102.5 - 18; // board plane, mm from the centre
	const mbZ = -460 / 2 + MB_REAR + MB / 2;
	faces.push({ key: 'mb', kind: 'mb', w: mm(MB), h: mm(MB), t: `${t3(mm(MB_X), yAt(MB_TOP + MB / 2), mm(mbZ))} rotateY(-90deg)`, n: [-1, 0, 0] });

	// GPU: GALAX RTX 4060 Ti 1-Click OC, about 250 long x 115 tall x 40 thick (two 92 mm fans), in the x16 slot
	// with the fans facing down; its bracket takes rear slots 1-2. Its top edge faces the mesh side panel and
	// carries the lit GEFORCE RTX logo.
	const G_LEN = 250, G_TALL = 115, G_THK = 40, G_SLOT = 227;
	const gz = -460 / 2 + 12 + G_LEN / 2, gx = MB_X - G_TALL / 2, gy = G_SLOT + G_THK / 2;
	faces.push(
		{ key: 'gpu-edge', kind: 'gpu-edge', w: mm(G_LEN), h: mm(G_THK), t: `${t3(mm(gx - G_TALL / 2), yAt(gy), mm(gz))} rotateY(-90deg)`, n: [-1, 0, 0] },
		{ key: 'gpu-top', kind: 'gpu-top', w: mm(G_TALL), h: mm(G_LEN), t: `${t3(mm(gx), yAt(G_SLOT), mm(gz))} rotateX(90deg)`, n: [0, -1, 0] },
		{ key: 'gpu-fans', kind: 'gpu-fans', w: mm(G_TALL), h: mm(G_LEN), t: `${t3(mm(gx), yAt(G_SLOT + G_THK), mm(gz))} rotateX(-90deg)`, n: [0, 1, 0] },
		{ key: 'gpu-end', kind: 'gpu-end', w: mm(G_TALL), h: mm(G_THK), t: t3(mm(gx), yAt(gy), mm(gz + G_LEN / 2)), n: [0, 0, 1] }
	);
	// Where the logo's light lands on the left side panel (that face's local coords: x from the rear edge).
	const spill = { x: mm(12 + 25 + 48), y: mm(gy) };

	// A fan as a box: two blade faces (kind 'nfan') and four frame edges. axis 'z' = blows along z
	// (faces front/back), 'y' = lies flat (faces up/down). Sizes in px.
	type V3 = [number, number, number];
	const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
	const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
	const unit = (a: V3): V3 => { const l = Math.hypot(...a); return [a[0] / l, a[1] / l, a[2] / l]; };
	// A tube between two points (px) of diameter d mm: two crossed strips placed with matrix3d, so it reads as
	// a round pipe from any angle (both sides drawn).
	function tube(key: string, p0: V3, p1: V3, d = 6, kind = 'pipe3'): Face[] {
		const u = unit(sub(p1, p0)), L = Math.hypot(...sub(p1, p0));
		const v1 = unit(cross(u, Math.abs(u[1]) < 0.9 ? [0, 1, 0] : [1, 0, 0]));
		const v2 = cross(u, v1);
		const m: V3 = [(p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2, (p0[2] + p1[2]) / 2];
		return [v1, v2].map((v, j) => {
			const n = cross(v, u);
			const t = `matrix3d(${v.join(',')},0,${u.join(',')},0,${n.join(',')},0,${m.join(',')},1)`;
			return { key: `${key}-${j}`, kind, w: mm(d), h: L + mm(d) * 0.6, t, n };
		});
	}
	// NF-A14x25r G2 (the D15 G2's fans): round frame, 140 mm, 25 mm deep, blowing along z. Two blade faces and
	// the outside of the ring as 16 strips.
	function roundFan(key: string, cx: number, cy: number, cz: number): Face[] {
		const s = mm(150), th = mm(25), R = mm(73), N = 16;
		const out: Face[] = [
			{ key: `${key}-f`, kind: 'nfanR', w: s, h: s, t: t3(cx, cy, cz + th / 2), n: [0, 0, 1] },
			{ key: `${key}-b`, kind: 'nfanR', w: s, h: s, t: `${t3(cx, cy, cz - th / 2)} rotateY(180deg)`, n: [0, 0, -1] }
		];
		for (let i = 0; i < N; i++) {
			const a = (i * 360) / N, r = (a * Math.PI) / 180;
			out.push({ key: `${key}-r${i}`, kind: 'nfan-side', w: 2 * R * Math.tan(Math.PI / N) + 0.2, h: th, t: `${t3(cx, cy, cz)} rotateZ(${a}deg) translateY(${-R}px) rotateX(90deg)`, n: [Math.sin(r), -Math.cos(r), 0] });
		}
		return out;
	}
	function fanBox(key: string, cx: number, cy: number, cz: number, s: number, th: number, axis: 'y' | 'z'): Face[] {
		if (axis === 'z')
			return [
				{ key: `${key}-f`, kind: 'nfan', w: s, h: s, t: t3(cx, cy, cz + th / 2), n: [0, 0, 1] },
				{ key: `${key}-b`, kind: 'nfan', w: s, h: s, t: `${t3(cx, cy, cz - th / 2)} rotateY(180deg)`, n: [0, 0, -1] },
				{ key: `${key}-r`, kind: 'nfan-side', w: th, h: s, t: `${t3(cx + s / 2, cy, cz)} rotateY(90deg)`, n: [1, 0, 0] },
				{ key: `${key}-l`, kind: 'nfan-side', w: th, h: s, t: `${t3(cx - s / 2, cy, cz)} rotateY(-90deg)`, n: [-1, 0, 0] },
				{ key: `${key}-t`, kind: 'nfan-side', w: s, h: th, t: `${t3(cx, cy - s / 2, cz)} rotateX(90deg)`, n: [0, -1, 0] },
				{ key: `${key}-d`, kind: 'nfan-side', w: s, h: th, t: `${t3(cx, cy + s / 2, cz)} rotateX(-90deg)`, n: [0, 1, 0] }
			];
		return [
			{ key: `${key}-u`, kind: 'nfan', w: s, h: s, t: `${t3(cx, cy - th / 2, cz)} rotateX(90deg)`, n: [0, -1, 0] },
			{ key: `${key}-d`, kind: 'nfan', w: s, h: s, t: `${t3(cx, cy + th / 2, cz)} rotateX(-90deg)`, n: [0, 1, 0] },
			{ key: `${key}-f`, kind: 'nfan-side', w: s, h: th, t: t3(cx, cy, cz + s / 2), n: [0, 0, 1] },
			{ key: `${key}-b`, kind: 'nfan-side', w: s, h: th, t: `${t3(cx, cy, cz - s / 2)} rotateY(180deg)`, n: [0, 0, -1] },
			{ key: `${key}-r`, kind: 'nfan-side', w: s, h: th, t: `${t3(cx + s / 2, cy, cz)} rotateY(90deg)`, n: [1, 0, 0] },
			{ key: `${key}-l`, kind: 'nfan-side', w: s, h: th, t: `${t3(cx - s / 2, cy, cz)} rotateY(-90deg)`, n: [-1, 0, 0] }
		];
	}
	// Plain box with a kind per side (null = leave that side out). Sizes and centre in px.
	function block(key: string, cx: number, cy: number, cz: number, w: number, h: number, d: number, k: Record<string, string | null>): Face[] {
		const all: Face[] = [
			{ key: `${key}-f`, kind: k.f ?? '', w, h, t: t3(cx, cy, cz + d / 2), n: [0, 0, 1] },
			{ key: `${key}-b`, kind: k.b ?? '', w, h, t: `${t3(cx, cy, cz - d / 2)} rotateY(180deg)`, n: [0, 0, -1] },
			{ key: `${key}-r`, kind: k.r ?? '', w: d, h, t: `${t3(cx + w / 2, cy, cz)} rotateY(90deg)`, n: [1, 0, 0] },
			{ key: `${key}-l`, kind: k.l ?? '', w: d, h, t: `${t3(cx - w / 2, cy, cz)} rotateY(-90deg)`, n: [-1, 0, 0] },
			{ key: `${key}-u`, kind: k.u ?? '', w, h: d, t: `${t3(cx, cy - h / 2, cz)} rotateX(90deg)`, n: [0, -1, 0] },
			{ key: `${key}-d`, kind: k.d ?? '', w, h: d, t: `${t3(cx, cy + h / 2, cz)} rotateX(-90deg)`, n: [0, 1, 0] }
		];
		return all.filter((f) => k[f.key.slice(-1)] !== null);
	}

	// One swept Noctua-style blade (fan centred at 0,0, radius 60): narrow root at the hub, wide tip at the rim,
	// leading edge curved forward. Seven of these nearly fill the disc.
	const BLADE = (() => {
		const p = (r: number, a: number) => { const t = (a * Math.PI) / 180; return `${(r * Math.sin(t)).toFixed(2)} ${(-r * Math.cos(t)).toFixed(2)}`; };
		return `M ${p(19, -22)} C ${p(32, -2)} ${p(44, 18)} ${p(56, 30)} A 56 56 0 0 0 ${p(56, -12)} C ${p(44, -24)} ${p(32, -38)} ${p(19, -50)} A 19 19 0 0 1 ${p(19, -22)} Z`;
	})();
	// NF-A14 G2 blade (fan radius 75): seven large sickle blades, wide at the rim, swept forward.
	const RBLADE = (() => {
		const p = (r: number, a: number) => { const t = (a * Math.PI) / 180; return `${(r * Math.sin(t)).toFixed(2)} ${(-r * Math.cos(t)).toFixed(2)}`; };
		return `M ${p(24, -10)} C ${p(40, 10)} ${p(56, 28)} ${p(66, 46)} A 66 66 0 0 0 ${p(66, -6)} C ${p(54, -22)} ${p(40, -38)} ${p(24, -48)} A 24 24 0 0 1 ${p(24, -10)} Z`;
	})();
	// GALAX 92 mm fan blade (radius 48): eleven narrow swept blades.
	const GBLADE = (() => {
		const p = (r: number, a: number) => { const t = (a * Math.PI) / 180; return `${(r * Math.sin(t)).toFixed(2)} ${(-r * Math.cos(t)).toFixed(2)}`; };
		return `M ${p(13, -10)} C ${p(24, 6)} ${p(36, 18)} ${p(47, 22)} A 47 47 0 0 0 ${p(47, -6)} C ${p(36, -14)} ${p(24, -24)} ${p(13, -32)} A 13 13 0 0 1 ${p(13, -10)} Z`;
	})();

	// Fans: Noctua NF-A12x25 G2 chromax.black (120 x 25 mm). Rear exhaust behind the grille
	// (x -90..32 mm, 56..217 mm down), and one on the roof, 20 mm behind the front-most position.
	faces.push(...fanBox('rfan', mm(-29), yAt(136), -D / 2 + mm(4 + 12.5), mm(120), mm(25), 'z'));
	faces.push(...fanBox('tfan-f', mm(-8), yAt(3 + 12.5), mm(230 - 18 - 60 - 120), mm(120), mm(25), 'y'));

	// CPU cooler: Noctua NH-D15 G2 chromax.black on the AM5 socket (socket centre: 129 mm from the board's rear
	// edge, 88 mm below its top). Height 168 mm off the board. Along z, rear to front: fin stack, middle 140 mm
	// fan, fin stack, front 140 mm fan. Heatpipes rise from the base into both stacks.
	{
		const sz = -230 + 12 + 129, sy = 62 + 88; // socket centre (mm)
		const top = MB_X - 168; // cooler top (x, mm)
		const finX0 = MB_X - 42; // fins start 42 mm off the board
		const fy = sy, fH = 120; // fin stacks: 120 mm wide (y), narrower than the 150 mm fan frame
		// Each tower is a stack of separate thin fin plates (2.2 mm pitch, gaps between them), as in Noctua's photo.
		// Every other plate is 3 mm shorter at both depth ends, which makes the toothed comb edge.
		const tower = (key: string, z0: number, z1: number) => {
			const out: Face[] = [];
			for (let k = 0, x = finX0; x > top + 8; k++, x -= 2.2) {
				const inset = k % 2 ? 3 : 0;
				out.push({ key: `${key}-${k}`, kind: 'fin', w: mm(z1 - z0 - 2 * inset), h: mm(fH), t: `${t3(mm(x), yAt(fy), mm((z0 + z1) / 2))} rotateY(90deg)`, n: [1, 0, 0] });
			}
			return out;
		};
		faces.push(
			// base: small flat black plate on the CPU
			...block('hs-base', mm(MB_X - 4), yAt(sy), mm(sz), mm(7), mm(46), mm(40), { r: null, f: 'hs-base', b: 'hs-base', l: 'hs-base', u: 'hs-base', d: 'hs-base' }),
			...tower('hs-r', sz - 74, sz - 24),
			...tower('hs-f', sz + 1, sz + 51),
			...roundFan('hs-fm', mm(top + 75), yAt(fy), mm(sz - 11.5)),
			...roundFan('hs-ff', mm(top + 75), yAt(fy), mm(sz + 63.5))
		);
		// Eight 8 mm U-shaped heatpipes, as in Noctua's photo: packed side by side on the base, then each end bends
		// smoothly up (a curve, drawn in short straight pieces) and fans out across the tower width into the rear
		// and the front fin stack.
		const zR = sz - 49, zF = sz + 26, x0 = MB_X - 12;
		const P = (x: number, y: number, z: number): V3 => [mm(x), yAt(y), mm(z)];
		const bend = (key: string, a: V3, c: V3, b: V3, n = 5): Face[] => {
			const q = (t: number): V3 => a.map((v, i) => (1 - t) ** 2 * v + 2 * (1 - t) * t * c[i] + t * t * b[i]) as V3;
			return Array.from({ length: n }, (_, j) => tube(`${key}${j}`, q(j / n), q((j + 1) / n), 8)).flat();
		};
		for (let i = 0; i < 8; i++) {
			const yb = sy + (i - 3.5) * 8, yt = sy + (i - 3.5) * 14;
			faces.push(
				...tube(`hp${i}base`, P(x0, yb, sz - 16), P(x0, yb, sz + 16), 8),
				...bend(`hp${i}r`, P(x0, yb, sz - 16), P(x0, (yb + yt) / 2, zR), P(finX0 - 6, yt, zR)),
				...bend(`hp${i}f`, P(x0, yb, sz + 16), P(x0, (yb + yt) / 2, zF), P(finX0 - 6, yt, zF))
			);
		}
	}

	// Solid steel behind the mesh where the real case is closed (open-case photo): the motherboard tray seen
	// through the right-hand panel, the standing PSU's side in front of it, and the PSU compartment behind
	// the front panel. These face outward, so the case no longer looks hollow from the front or the right.
	faces.push(
		{ key: 'tray', kind: 'tray', w: mm(270), h: H - 2 * IN, t: `${t3(mm(MB_X + 4), Y, mm(-95))} rotateY(90deg)`, n: [1, 0, 0] },
		{ key: 'psu-side', kind: 'psu-side', w: mm(168), h: H - 2 * IN, t: `${t3(W / 2 - mm(18), Y, mm(124))} rotateY(90deg)`, n: [1, 0, 0] },
		{ key: 'psu-front', kind: 'psu-front', w: W - 2 * IN, h: H - 2 * IN, t: t3(0, Y, D / 2 - mm(22)), n: [0, 0, 1] }
	);

	// The expansion-slot section of the rear is recessed 10 mm into the case. Hole in the rear plate (as % of
	// the rear face, viewer's left = +x): left 5.9%, top 67.2%, 57.2% x 25.1%.
	const REC = { l: 5.9, t: 67.2, w: 57.2, h: 25.1, d: mm(10) };
	{
		const x0 = W / 2 - (REC.l / 100) * W, x1 = W / 2 - ((REC.l + REC.w) / 100) * W; // +x edge, -x edge
		const y0 = Y - H / 2 + (REC.t / 100) * H, y1 = y0 + (REC.h / 100) * H;
		const rw = x0 - x1, rh = y1 - y0, cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, zb = -D / 2 + REC.d;
		faces.push(
			{ key: 'rec-back', kind: 'rec-back', w: rw, h: rh, t: `${t3(cx, cy, zb)} rotateY(180deg)`, n: [0, 0, -1] },
			{ key: 'rec-top', kind: 'rec-wall', w: rw, h: REC.d, t: `${t3(cx, y0, -D / 2 + REC.d / 2)} rotateX(-90deg)`, n: [0, 1, 0] },
			{ key: 'rec-bot', kind: 'rec-wall', w: rw, h: REC.d, t: `${t3(cx, y1, -D / 2 + REC.d / 2)} rotateX(90deg)`, n: [0, -1, 0] },
			{ key: 'rec-px', kind: 'rec-wall', w: REC.d, h: rh, t: `${t3(x0, cy, -D / 2 + REC.d / 2)} rotateY(-90deg)`, n: [-1, 0, 0] },
			{ key: 'rec-nx', kind: 'rec-wall', w: REC.d, h: rh, t: `${t3(x1, cy, -D / 2 + REC.d / 2)} rotateY(90deg)`, n: [1, 0, 0] }
		);
	}

	// Front I/O, centre offsets in mm from the middle of the front, symmetric with the power button
	// in the middle: USB-C, headphone, mic, power, 2x USB-A.
	const PORTS: [string, number][] = [
		['usbc', -47],
		['jack', -34],
		['jack', -19],
		['pwr', 0],
		['usba', 23],
		['usba', 47]
	];

	// Room light, fixed to the case (not the camera), so each face keeps one steady shade while you rotate:
	// from above, a little toward the front and the window side.
	const L = (() => {
		const v = [-0.3, -0.85, 0.42], m = Math.hypot(...v);
		return v.map((c) => c / m);
	})();
	function shade(n: [number, number, number]): number {
		const d = n[0] * L[0] + n[1] * L[1] + n[2] * L[2];
		return 0.8 + 0.32 * Math.max(0, d);
	}

	function drag(e: PointerEvent) {
		if (e.button !== 0) return;
		e.preventDefault();
		cancelAnimationFrame(raf);
		const el = e.currentTarget as HTMLElement;
		el.setPointerCapture(e.pointerId);
		let px = e.clientX, py = e.clientY;
		vx = vy = 0;
		const move = (m: PointerEvent) => {
			const dx = m.clientX - px, dy = m.clientY - py;
			px = m.clientX;
			py = m.clientY;
			ry += dx * 0.6;
			rx = Math.max(-85, Math.min(85, rx - dy * 0.6));
			vy = dx * 0.6;
			vx = -dy * 0.6;
		};
		const up = () => {
			el.removeEventListener('pointermove', move);
			el.removeEventListener('pointerup', up);
			el.removeEventListener('pointercancel', up);
			coast();
		};
		el.addEventListener('pointermove', move);
		el.addEventListener('pointerup', up);
		el.addEventListener('pointercancel', up);
	}

	// Momentum after release, decaying to a stop.
	function coast() {
		vx *= 0.92;
		vy *= 0.92;
		if (Math.abs(vx) < 0.02 && Math.abs(vy) < 0.02) return;
		ry += vy;
		rx = Math.max(-85, Math.min(85, rx + vx));
		raf = requestAnimationFrame(coast);
	}

	onMount(() => () => cancelAnimationFrame(raf));
</script>

{#snippet noctuaR()}
	<!-- NF-A14x25r G2 chromax.black (on the NH-D15 G2): round frame with four small corner tabs, seven large
	     sickle blades, big hub with curved grooves. Near-black. -->
	<svg class="nf" viewBox="-75 -75 150 150" aria-hidden="true">
		{#each [[-1, -1], [1, -1], [-1, 1], [1, 1]] as [sx, sy], i (i)}
			<path d="M {sx * 47} {sy * 70} L {sx * 70} {sy * 70} L {sx * 70} {sy * 47} Z" fill="#141414" stroke="#141414" stroke-width="8" stroke-linejoin="round" />
			<circle cx={sx * 64} cy={sy * 64} r="2.4" fill="#000" />
		{/each}
		<circle r="74" fill="#141414" />
		<circle r="69.5" fill="#060606" stroke="#2a2a2a" stroke-width="1.2" />
		<g class="spin">
		{#each Array(7) as _, i (i)}
			<path transform="rotate({(i * 360) / 7})" d={RBLADE} fill="url(#rfb)" stroke="#121314" stroke-width="0.6" />
		{/each}
		<circle r="20" fill="#47494d" stroke="#1d1e20" stroke-width="0.8" />
		{#each Array(20) as _, i (i)}
			<path transform="rotate({i * 18})" d="M 3 -1 Q 10 -8 19.5 -5" fill="none" stroke="#2f3134" stroke-width="0.7" />
		{/each}
		<circle r="1.2" fill="#18191a" />
		</g>
		<defs>
			<linearGradient id="rfb" x1="0" y1="0" x2="1" y2="1">
				<stop offset="0" stop-color="#5c5e62" />
				<stop offset="1" stop-color="#3c3e42" />
			</linearGradient>
		</defs>
	</svg>
{/snippet}

{#snippet noctua()}
	<!-- NF-A12x25 G2 chromax.black: black frame with corner pads, seven swept grey blades, ridged hub -->
	<svg class="nf" viewBox="-60 -60 120 120" aria-hidden="true">
		<rect x="-60" y="-60" width="120" height="120" rx="7" fill="#161616" />
		{#each [[-1, -1], [1, -1], [-1, 1], [1, 1]] as [sx, sy], i (i)}
			<rect x={sx * 52.5 - 7.5} y={sy * 52.5 - 7.5} width="15" height="15" rx="3" fill="#0d0d0d" />
			<circle cx={sx * 52.5} cy={sy * 52.5} r="2.4" fill="#000" />
		{/each}
		<circle r="57.5" fill="#080808" stroke="#2a2a2a" stroke-width="1.2" />
		<g class="spin">
		{#each Array(7) as _, i (i)}
			<path
				transform="rotate({(i * 360) / 7})"
				d={BLADE}
				fill="url(#nfb)"
				stroke="#161718"
				stroke-width="0.6"
			/>
		{/each}
		<circle r="20" fill="#242527" stroke="#161718" stroke-width="0.8" />
		{#each Array(18) as _, i (i)}
			<line transform="rotate({i * 20})" x1="5" y1="0" x2="19" y2="-4" stroke="#18191b" stroke-width="0.5" />
		{/each}
		<circle r="1" fill="#2c2e31" />
		</g>
		<defs>
			<linearGradient id="nfb" x1="0" y1="0" x2="1" y2="1">
				<stop offset="0" stop-color="#303235" />
				<stop offset="1" stop-color="#1f2022" />
			</linearGradient>
		</defs>
	</svg>
{/snippet}

{#snippet rogIO()}
	<!-- ROG Crosshair X670E Gene integrated I/O shield, drawn from ASUS's rear-panel photo (210 x 735 units,
	     top = top of the board). Ports stand upright because the board is vertical. -->
	<svg class="fill" viewBox="0 0 210 735" preserveAspectRatio="none" aria-hidden="true">
		<defs>
			<linearGradient id="ioS" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#4b4b4b" /><stop offset="1" stop-color="#3a3a3a" /></linearGradient>
			<radialGradient id="gold"><stop offset="0" stop-color="#e6c76e" /><stop offset="1" stop-color="#8f6d24" /></radialGradient>
		</defs>
		<rect x="1" y="1" width="208" height="733" rx="4" fill="url(#ioS)" stroke="#9a9a9a" stroke-width="2" />
		<!-- BIOS FlashBack + Clear CMOS -->
		<text x="62" y="44" class="lbl">BIOS</text><text x="110" y="40" class="lbl">CLEAR CMOS</text>
		<rect x="50" y="53" width="50" height="47" rx="3" fill="#111" stroke="#cfcfcf" stroke-width="2" />
		<circle cx="75" cy="72" r="11" fill="none" stroke="#e0e0e0" stroke-width="2.5" /><text x="62" y="96" class="lbl">BIOS</text>
		<rect x="107" y="53" width="50" height="47" rx="3" fill="#111" stroke="#9a9a9a" stroke-width="2" />
		<circle cx="132" cy="76" r="11" fill="none" stroke="#e0e0e0" stroke-width="2.5" />
		<!-- 2x USB 2.0 (BIOS FlashBack port boxed) + PS/2 combo -->
		<rect x="23" y="110" width="74" height="80" fill="none" stroke="#eeeeee" stroke-width="2" /><text x="30" y="124" class="lbl">BIOS</text>
		{#each [28, 67] as x (x)}
			<rect {x} y="127" width="27" height="58" fill="#0b0b0b" stroke="#c9c9c9" stroke-width="2" /><rect x={x + 5} y="133" width="8" height="46" fill="#1f1f1f" />
		{/each}
		<circle cx="132" cy="155" r="25" fill="#6f58ad" stroke="#bfbfbf" stroke-width="2" /><path d="M 132 130 A 25 25 0 0 1 132 180 Z" fill="#3e9a5a" />
		<circle cx="132" cy="155" r="17" fill="#262626" />
		{#each [[-8, -5], [8, -5], [-10, 4], [10, 4], [-4, 10], [4, 10]] as [dx, dy], i (i)}<circle cx={132 + dx} cy={155 + dy} r="2" fill="#0a0a0a" />{/each}
		<!-- USB4 Type-C x2 (40 Gbps + DP marks), each beside two red USB 3.2 Gen 2 -->
		{#each [227, 340] as y (y)}
			<text x="24" y={y - 12} class="lbl">40⇆</text><text x="88" y={y - 12} class="lbl">SS10</text>
			<rect x="23" {y} width="30" height="60" rx="14" fill="#0b0b0b" stroke="#d0d0d0" stroke-width="2.5" /><rect x="34" y={y + 10} width="8" height="40" rx="2" fill="#2a2a2a" />
			<text x="30" y={y + 78} class="lbl">D</text>
			{#each [87, 127] as x (x)}
				<rect {x} y={y + 1} width="28" height="57" fill="#5c1014" stroke="#cfcfcf" stroke-width="2" /><rect x={x + 5} y={y + 7} width="9" height="45" fill="#c7272d" />
			{/each}
		{/each}
		<!-- USB-C 3.2, red USB-A, 2.5G LAN -->
		<text x="40" y="440" class="lbl">SS10</text>
		<rect x="23" y="450" width="30" height="56" rx="14" fill="#0b0b0b" stroke="#d0d0d0" stroke-width="2.5" /><rect x="34" y="459" width="8" height="38" rx="2" fill="#2a2a2a" />
		<rect x="65" y="446" width="28" height="57" fill="#5c1014" stroke="#cfcfcf" stroke-width="2" /><rect x="70" y="452" width="9" height="45" fill="#c7272d" />
		<path d="M 109 446 H 157 V 494 H 143 V 501 H 123 V 494 H 109 Z" fill="#0b0b0b" stroke="#cfcfcf" stroke-width="2" />
		<rect x="149" y="440" width="7" height="6" fill="#3fbf55" /><text x="162" y="478" class="lbl">2.5G</text>
		<!-- Wi-Fi 6E antenna sockets -->
		{#each [57, 162] as x (x)}
			<circle cx={x} cy="553" r="18" fill="url(#gold)" stroke="#5e4716" stroke-width="1.5" /><circle cx={x} cy="553" r="8" fill="#2a2a2a" /><circle cx={x} cy="553" r="2.5" fill="#e3c97c" />
		{/each}
		<text x="80" y="558" class="lbl big">WI-FI 6E</text>
		<!-- optical S/PDIF + five audio jacks -->
		<text x="22" y="603" class="lbl">SPDIF OUT</text><text x="88" y="603" class="lbl">REAR</text><text x="135" y="603" class="lbl">C/SUB</text>
		<rect x="31" y="610" width="40" height="38" rx="3" fill="#d9d9d9" stroke="#8a8a8a" stroke-width="1.5" /><rect x="44" y="620" width="14" height="16" fill="#2a2a2a" />
		{#each [[100, 629, '#1b1b1b'], [150, 629, '#e08b4e'], [50, 685, '#ec96b8'], [100, 685, '#97dba2'], [150, 685, '#93bde9']] as [x, y, c], i (i)}
			<circle cx={x} cy={y} r="16" fill={String(c)} stroke="#a0a0a0" stroke-width="1.2" /><circle cx={x} cy={y} r="7" fill="#0c0c0c" />
		{/each}
		<text x="30" y="664" class="lbl">MIC IN</text><text x="82" y="664" class="lbl">LINE OUT</text><text x="134" y="664" class="lbl">LINE IN</text>
		<text x="105" y="722" class="lbl big" text-anchor="middle">REPUBLIC OF GAMERS</text>
	</svg>
{/snippet}

{#snippet galaxBracket()}
	<!-- GALAX RTX 4060 Ti 1-Click OC bracket, seen from the rear: grey steel, HDMI + 3x DisplayPort on the PCB
	     half (board side on the left), organic honeycomb vent on the cooler half. -->
	<svg class="fill" viewBox="0 0 120 42" preserveAspectRatio="none" aria-hidden="true">
		<defs>
			<linearGradient id="brk" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#c4c4c4" /><stop offset="1" stop-color="#8e8e8e" /></linearGradient>
			<pattern id="hex" width="7" height="6.06" patternUnits="userSpaceOnUse">
				<path d="M 1.75 0.3 L 5.25 0.3 L 6.7 3.03 L 5.25 5.76 L 1.75 5.76 L 0.3 3.03 Z" fill="#141414" />
			</pattern>
		</defs>
		<rect x="0.5" y="0.5" width="119" height="41" rx="1" fill="url(#brk)" stroke="#5e5e5e" stroke-width="0.6" />
		<path d="M 7 5 H 27 V 13 L 24 16 H 10 L 7 13 Z" fill="#e2e2e2" stroke="#555" stroke-width="0.5" />
		<path d="M 9 7 H 25 V 12.5 L 23 14.5 H 11 L 9 12.5 Z" fill="#111" />
		{#each [36, 62, 88] as x (x)}
			<path d="M {x} 5 H {x + 18} V 16 H {x + 3} L {x} 13 Z" fill="#e2e2e2" stroke="#555" stroke-width="0.5" />
			<path d="M {x + 2} 7 H {x + 16} V 14 H {x + 4} L {x + 2} 12 Z" fill="#111" />
		{/each}
		<circle cx="114" cy="10.5" r="1.6" fill="#6a6a6a" />
		<rect x="4" y="21" width="112" height="18" fill="url(#hex)" />
	</svg>
{/snippet}

{#snippet gpuBackplate()}
	<!-- GALAX backplate (product photo): black plate, outlined GEFORCE RTX badge with circuit lines, open
	     flow-through end over the fins with X bars. Drawn along the card (250 x 115), turned onto the face. -->
	<svg class="fill" viewBox="0 0 115 250" preserveAspectRatio="none" aria-hidden="true">
		<g transform="translate(115 0) rotate(90)">
			<rect width="250" height="115" fill="#1b1b1b" stroke="#2f2f2f" stroke-width="1" />
			<rect x="160" y="8" width="84" height="99" fill="#0a0a0a" />
			{#each Array(29) as _, i (i)}<line x1="162" x2="242" y1={11 + i * 3.25} y2={11 + i * 3.25} stroke="#4c4c4c" stroke-width="1.2" />{/each}
			<path d="M 160 8 L 172 8 L 244 95 L 244 107 L 232 107 L 160 20 Z M 244 8 L 244 20 L 172 107 L 160 107 L 160 95 L 232 8 Z" fill="#1b1b1b" />
			<path d="M 18 38 H 128 L 140 50 V 70 L 128 82 H 18 L 10 74 V 46 Z" fill="none" stroke="#d6d6d6" stroke-width="2.4" />
			<text x="24" y="67" font-family="Inter, system-ui, sans-serif" font-weight="800" font-size="17" fill="#e6e6e6">GEFORCE RTX</text>
			<path d="M 6 28 H 58 L 66 20 H 150 M 6 92 H 88 L 96 100 H 150 M 140 50 H 155 V 40" fill="none" stroke="#cfcfcf" stroke-width="1.6" />
			{#each [96, 106, 116] as u (u)}<path d="M {u} 10 h 7 l -4 9 h -7 Z" fill="#d6d6d6" />{/each}
			<rect x="14" y="99" width="30" height="8" fill="#e8e8e8" /><rect x="16" y="101" width="26" height="4" fill="#9a9a9a" />
			{#each [[40, 26], [120, 26], [40, 90], [120, 90]] as [u, v], i (i)}<circle cx={u} cy={v} r="2.5" fill="#0d0d0d" />{/each}
		</g>
	</svg>
{/snippet}

{#snippet galaxFan()}
	<!-- GALAX 92 mm fan: black swept blades, black hub with the GALAX mark -->
	<svg class="gfan" viewBox="-50 -50 100 100" aria-hidden="true">
		<circle r="48.5" fill="#0d0d0d" stroke="#2a2a2a" stroke-width="1.5" />
		<g class="spin">
		{#each Array(11) as _, i (i)}
			<path transform="rotate({(i * 360) / 11})" d={GBLADE} fill="#151515" stroke="#2b2b2b" stroke-width="0.5" />
		{/each}
		<circle r="13" fill="#121212" stroke="#333" stroke-width="0.8" />
		<text y="1.9" text-anchor="middle" font-family="Inter, system-ui, sans-serif" font-weight="700" font-size="5.2" fill="#d8d8d8">GALAX</text>
		</g>
	</svg>
{/snippet}


<div
	class="pc-stage {klass}"
	role="img"
	aria-label="ASUS Prime AP201 case, drag to rotate"
	onpointerdown={drag}
	ondragstart={(e) => e.preventDefault()}
	bind:clientWidth={stageW}
	style="--band:{mm(54)}px;--base:{mm(20)}px;perspective-origin:calc(50% + {stageW / 2 - EDGE - reach}px) 50%"
>
	<div class="pc-scene" style="translate: {stageW / 2 - EDGE - reach}px 0; transform: rotateX({rx}deg) rotateY({ry}deg)">
		<!-- soft contact shadow on the floor -->
		<div class="pc-shadow" style="width:{W * 1.25}px;height:{D * 1.08}px;transform:translate3d(0,{H / 2 + FOOT_H / 2 + 0.3}px,0) rotateX(90deg)"></div>
		{#each faces as f (f.key)}
			<div class="pc-face {f.kind}" style="width:{f.w}px;height:{f.h}px;transform:{f.t};--s:{shade(f.n)};{f.r ? `border-radius:${f.r}` : ''}">
				{#if f.kind === 'front' || f.kind === 'side' || f.kind === 'corner' || f.kind === 'top' || f.kind === 'back'}<i class="veil"></i>{/if}
				{#if f.kind === 'front' || f.kind === 'side' || f.kind === 'corner'}
					<!-- solid band on top (ports on the front), mesh, solid strip at the bottom -->
					<div class="band">
						{#if f.kind === 'front'}
							{#each PORTS as [p, x], i (i)}
								<span class="port {p}" style="left:calc(50% + {mm(x)}px)"></span>
							{/each}
						{/if}
					</div>
					<div class="mesh"></div>
					<div class="base"></div>
				{:else if f.kind === 'top'}
					<div class="mesh" style="margin:{mm(22)}px {mm(18)}px"></div>
				{:else if f.kind === 'bottom'}
					<div class="filter" style="margin:{mm(30)}px {mm(24)}px"></div>
				{:else if f.kind === 'back'}
					<!-- One perforated steel plate, laid out as % of the rear from ASUS's rear photo (viewer's left =
					     left). Solid zones are the same steel; holes are see-through. -->
					<div class="rear">
						<div class="rear-steel"></div>
						<!-- solid zones: inlet plate (top left), I/O flange, slot frame -->
						<div class="solid" style="left:3.3%;top:0;width:25.5%;height:17.6%"></div>
						<div class="solid" style="left:3.3%;top:17.6%;width:27.1%;height:49.6%"></div>
						<!-- IEC C14 power inlet with its two screws -->
						<div class="iec" style="left:6.9%;top:4.8%;width:10.4%;height:8%"><b></b><b></b><b></b></div>
						<!-- motherboard I/O through the cutout: integrated shield with the ports in two stacks -->
						<div class="io" style="left:5.2%;top:18.4%;width:20.9%;height:47.2%">{@render rogIO()}</div>
						<!-- 120 mm fan mount: four adjustment slots -->
						<i class="slot" style="left:35.3%;top:24.8%"></i><i class="slot" style="left:89.2%;top:24.8%"></i>
						<i class="slot" style="left:35.3%;top:56%"></i><i class="slot" style="left:89.2%;top:56%"></i>
						<span class="stamp" style="left:38.6%;top:62.3%">ASUS CASE</span>
						<!-- screws -->
						{#each [[11.4, 3.2], [11.4, 15.2], [28.8, 3.2], [66.3, 4.3], [37, 27.5], [87.5, 27.5], [37, 58.7], [87.5, 58.7], [33, 61.3], [65.7, 65.6], [65.7, 90.4]] as [x, y], i (i)}
							<i class="screw" style="left:{x}%;top:{y}%"></i>
						{/each}
						<div class="base rear-base"></div>
					</div>
				{:else if f.kind === 'mb'}
					<!-- ROG Crosshair X670E Gene, laid out from ASUS's board photo (left = rear I/O) -->
					<div class="mb-io"><span>CROSSHAIR</span><i></i></div>
					<div class="mb-vrm-l"></div>
					<div class="mb-vrm-t"></div>
					<div class="mb-socket"><b></b></div>
					<div class="mb-name">CROSSHAIR X670E GENE</div>
					<div class="mb-dimm"><span></span><span></span></div>
					<div class="mb-atx"></div>
					<div class="mb-pcie"></div>
					<div class="mb-shroud"></div>
					<div class="mb-m2"><span>GENE</span><em></em><small>FOR THOSE WHO DARE</small></div>
					<div class="mb-low"></div>
					<div class="mb-sfx">SupremeFX</div>
					<div class="mb-sata"><span></span><span></span></div>
					<div class="mb-hdr"></div>
				{:else if f.kind === 'nfan'}
					{@render noctua()}
				{:else if f.kind === 'nfanR'}
					{@render noctuaR()}
				{:else if f.kind === 'rec-back'}
					<!-- recessed slot plate (positions as % of the recess): GPU bracket over 1-2, covers 3-4 -->
					<div class="gbr" style="left:2.8%;top:4.8%;width:89.7%;height:43.8%">{@render galaxBracket()}</div>
					<div class="cover" style="left:2.8%;top:53%;width:89.7%;height:18.3%"></div>
					<div class="cover" style="left:2.8%;top:75.3%;width:89.7%;height:18.3%"></div>
				{:else if f.kind === 'gpu-edge'}
					<!-- the logo is drawn where its light lands on the side panel (.rtx-out) -->
				{:else if f.kind === 'gpu-top'}
					{@render gpuBackplate()}
				{:else if f.kind === 'tray'}
					<!-- tray from the open-case photo: CPU cutout, cable pass-throughs -->
					<i class="cut" style="left:46%;top:20%;width:30%;height:34%"></i>
					{#each [16, 36, 62] as y (y)}<i class="cut grommet" style="left:4%;top:{y}%;width:3.5%;height:14%"></i>{/each}
					<i class="cut" style="left:40%;top:72%;width:22%;height:12%"></i>
					<span class="tray-mark">PRIME</span>
				{:else if f.kind === 'psu-side'}
					<i class="psu-label"></i>
				{:else if f.kind === 'gpu-fans'}
					{@render galaxFan()}{@render galaxFan()}
				{/if}
				{#if f.key === 'left'}
					<!-- the GPU logo's light on the steel of the side panel -->
					<div class="spill" style="left:{spill.x}px;top:{spill.y}px"></div>
					<!-- the lit logo shining through the perforation, as in the real case: drawn where its light lands on the panel -->
					<span class="rtx-out" style="left:{mm(12 + 25)}px;top:{spill.y}px">GEFORCE RTX<sup>™</sup></span>
				{/if}
				<div class="light"></div>
			</div>
		{/each}
	</div>
</div>

<style>
	.pc-stage {
		perspective: 900px;
		cursor: grab;
		touch-action: none;
		user-select: none;
		display: flex;
		align-items: center;
		justify-content: center;
		/* black steel: solid parts, fine mesh, its holes */
		--steel: #282828;
		--mesh: #3a3a3a;
		--hole: #121212;
	}
	.pc-stage:active {
		cursor: grabbing;
	}
	.pc-scene {
		position: relative;
		width: 0;
		height: 0;
		transform-style: preserve-3d;
	}
	.pc-face,
	.pc-shadow {
		position: absolute;
		left: 0;
		top: 0;
		translate: -50% -50%;
		backface-visibility: hidden;
	}
	/* children that get their own layer (glow, mesh, bands) must hide with their face, or a panel turned away
	   from you still shows through */
	.pc-face * {
		backface-visibility: hidden;
	}
	.pc-face {
		box-sizing: border-box;
		display: flex;
		flex-direction: column;
		overflow: hidden;
		background: var(--steel);
	}
	.pc-shadow {
		border-radius: 50%;
		background: radial-gradient(closest-side, rgba(0, 0, 0, 0.75), rgba(0, 0, 0, 0.35) 60%, transparent);
	}

	/* fine perforated steel */
	.mesh {
		flex: 1;
		min-height: 0;
		position: relative; /* paints above the veil */
		/* At this size the holes are finer than a pixel, so a dot pattern shimmers (moiré) as the case turns.
		   A real mesh panel at this distance reads as an even, partly see-through steel tone: draw exactly that,
		   with only a faint coarse texture that stays steady while rotating. */
		background-color: color-mix(in srgb, var(--mesh) 74%, transparent);
		background-image: linear-gradient(90deg, rgba(0, 0, 0, 0.07) 50%, transparent 50%);
		background-size: 2.4px 100%;
	}
	.band {
		flex: none;
		height: var(--band);
		position: relative;
		background: linear-gradient(#2c2c2c, #232323);
	}
	.base {
		flex: none;
		position: relative;
		height: var(--base);
		background: linear-gradient(#232323, #1c1c1c);
	}
	/* walls that carry mesh are open behind it; their solid parts paint themselves */
	.front,
	.side,
	.corner,
	.top,
	.back {
		background: transparent;
	}
	/* side panels: thin solid frame at the rear and front edges */
	.side .mesh {
		border-inline: 0.5px solid var(--steel);
	}
	/* top: mesh inside a wide solid border (the shadow fills the margin) */
	.top .mesh {
		--mesh: #575757;
		box-shadow: 0 0 0 60px var(--steel);
	}
	.bottom {
		background: #171717;
	}
	.filter {
		flex: 1;
		background-color: #222;
		background-image: radial-gradient(circle at center, #0a0a0a 0 0.3px, transparent 0.4px);
		background-size: 0.9px 0.9px;
		border-radius: 1px;
	}

	/* front I/O */
	.port {
		position: absolute;
		top: 52%;
		translate: -50% -50%;
		display: block;
		background: #050505;
		/* the front I/O gives off a little light: a faint cool glow around each port */
		box-shadow:
			0 0 0 0.35px #6a6a6a,
			0 0 0.8px rgba(200, 220, 255, 0.45),
			0 0 2px rgba(150, 185, 255, 0.25);
	}
	.port.pwr {
		/* power button LED */
		box-shadow:
			0 0 0 0.35px #8a8a8a,
			0 0 1px rgba(225, 235, 255, 0.7),
			0 0 3px rgba(160, 195, 255, 0.35);
	}
	.usbc {
		width: 2.7px;
		height: 0.95px;
		border-radius: 0.7px;
	}
	.jack {
		width: 1.65px;
		height: 1.65px;
		border-radius: 50%;
	}
	.pwr {
		width: 3.6px;
		height: 3.6px;
		border-radius: 0.6px;
		background: #161616;
	}
	.usba {
		width: 3.9px;
		height: 1.7px;
	}

	/* rear */
	.rear {
		flex: 1;
		position: relative;
	}
	.rear > * {
		position: absolute;
	}
	.rear-steel {
		/* square-hole perforation, see-through; the steel around it is the plate's rim */
		inset: 4% 6% 6.4% 3.3%;
		background-image: linear-gradient(90deg, var(--steel) 0.45px, transparent 0.45px), linear-gradient(var(--steel) 0.45px, transparent 0.45px);
		background-size: 1.85px 1.85px;
		box-shadow: 0 0 0 60px var(--steel);
	}
	.solid {
		background: var(--steel);
	}
	.solid.recess {
		/* the slot frame is pressed in a little */
		box-shadow: inset 0.3px 0.3px 0.4px rgba(0, 0, 0, 0.6), inset -0.2px -0.2px 0.3px rgba(255, 255, 255, 0.06);
	}
	.iec {
		/* C14 inlet: black socket, chamfered lower corners, three pins */
		background: #0b0b0b;
		clip-path: polygon(0 0, 100% 0, 100% 70%, 78% 100%, 22% 100%, 0 70%);
		display: flex;
		align-items: center;
		justify-content: space-evenly;
		padding: 0 1.4px;
	}
	.iec b {
		width: 0.45px;
		height: 1.6px;
		background: #8c8c8c;
	}
	.iec b:nth-child(2) {
		translate: 0 -0.8px;
	}
	.screw {
		width: 1.2px;
		height: 1.2px;
		translate: -50% -50%;
		border-radius: 50%;
		background: radial-gradient(circle at 35% 35%, #4a4a4a, #141414 70%);
		box-shadow: 0 0 0 0.15px #0a0a0a;
	}
	.slot {
		/* fan-height adjustment slot */
		width: 1.1px;
		height: 4.6%;
		translate: -50% -50%;
		border-radius: 0.6px;
		background: #050505;
	}
	.stamp {
		color: #3c3c3c;
		font: 600 1.5px/1 Inter, system-ui, sans-serif;
		letter-spacing: 0.15px;
		white-space: nowrap;
	}
	/* drawn parts (I/O shield, GPU bracket, backplate) fill their box */
	.io,
	.gbr {
		display: block;
	}
	.fill {
		display: block;
		width: 100%;
		height: 100%;
	}
	.lbl {
		font: 600 11px/1 Inter, system-ui, sans-serif;
		fill: #e6e6e6;
	}
	.lbl.big {
		font-size: 14px;
		letter-spacing: 1px;
	}

	/* solid steel behind the right-hand mesh and the front */
	.tray {
		background: linear-gradient(180deg, #232323, #1b1b1b);
	}
	.tray > .cut {
		position: absolute;
		background: #0a0a0a;
		border-radius: 0.6px;
		box-shadow: inset 0 0 0 0.3px #2e2e2e;
	}
	.tray > .grommet {
		border-radius: 1px;
	}
	.tray-mark {
		position: absolute;
		left: 50%;
		top: 88%;
		color: #3a3a3a;
		font: 600 1.6px/1 Inter, system-ui, sans-serif;
		letter-spacing: 0.4px;
	}
	.psu-side {
		background: linear-gradient(180deg, #1e1e1e, #161616);
		box-shadow: inset 0 0 0 0.3px #2b2b2b;
	}
	.psu-label {
		position: absolute;
		left: 20%;
		top: 22%;
		width: 60%;
		height: 30%;
		background: #262626;
		box-shadow: inset 0 0 0 0.3px #333;
	}
	.psu-front {
		background: linear-gradient(180deg, #202020, #181818);
	}
	.tray .light,
	.psu-side .light,
	.psu-front .light {
		background: rgba(0, 0, 0, 0.3);
	}
	.cover {
		/* blank slot cover: two rows of small vent holes */
		background-color: #222;
		background-image: radial-gradient(circle, #050505 0 0.22px, transparent 0.3px);
		background-size: 0.9px 0.85px;
		background-position: 0 0.5px;
		background-repeat: repeat-x;
		box-shadow: inset 0 0 0 0.2px #111;
	}
	.cover::after {
		content: '';
		position: absolute;
		left: 0;
		right: 0;
		top: 55%;
		height: 0.9px;
		background-image: radial-gradient(circle, #050505 0 0.22px, transparent 0.3px);
		background-size: 0.9px 0.9px;
	}
	.rear-base {
		left: 0;
		right: 0;
		bottom: 0;
	}

	.foot {
		background: #0d0d0d;
	}
	/* tapered: wider where they meet the case */
	.foot-end {
		clip-path: polygon(0 0, 100% 0, 90% 100%, 10% 100%);
	}
	.foot-side {
		clip-path: polygon(0 0, 100% 0, 88% 100%, 12% 100%);
	}

	/* ---- inside */
	/* inner steel: chassis, tray, divider, floor (solid, a touch lighter than the shadows) */
	.inner {
		background: linear-gradient(180deg, #262626, #1d1d1d);
	}
	.pillar {
		background: #202020;
		box-shadow: inset 0 0 0 0.3px #2c2c2c;
	}
	/* PSU compartment wall: solid steel with a mesh window, as in the photo */
	.chamber {
		padding: 22% 12% 30% 14%;
	}
	.chamber::before {
		content: "";
		flex: 1;
		background-color: #0c0c0c;
		background-image: linear-gradient(90deg, #262626 0.5px, transparent 0.5px), linear-gradient(#262626 0.5px, transparent 0.5px);
		background-size: 1.9px 1.9px;
	}
	.pillar .light {
		background: rgba(0, 0, 0, 0.35);
	}
	.inner .light,
	.mb .light,
	.gpu-top .light,
	.gpu-fans .light,
	.gpu-end .light {
		/* no daylight in here: a fixed dim, the GPU logo is the light source */
		background: rgba(0, 0, 0, 0.35);
	}

	/* motherboard: % positions measured off ASUS's Crosshair X670E Gene photo */
	.mb {
		overflow: visible;
		background-color: #0f0f0f;
		/* small surface parts */
		background-image: radial-gradient(circle, #2b2b2b 0 0.25px, transparent 0.35px);
		background-size: 2.3px 1.9px;
		box-shadow: inset 0 0 0 0.3px #262626;
	}
	.mb > div {
		position: absolute;
	}
	.mb-io {
		/* rear I/O cover: big CROSSHAIR down the left, dot pattern beside it */
		left: -3.7%;
		top: -3.2%;
		width: 21%;
		height: 68%;
		background: linear-gradient(90deg, #1b1b1b, #141414);
		box-shadow: inset 0 0 0 0.3px #3a3a3a;
	}
	.mb-io span {
		position: absolute;
		left: 5%;
		top: 6%;
		height: 88%;
		color: #e8e8e8;
		font: 700 4.4px/1 Inter, system-ui, sans-serif;
		writing-mode: vertical-rl;
		letter-spacing: 1.45px;
	}
	.mb-io i {
		position: absolute;
		left: 50%;
		top: 10%;
		width: 44%;
		height: 84%;
		background-image: radial-gradient(circle, #d6d6d6 0 0.3px, transparent 0.42px);
		background-size: 1.25px 1.25px;
		/* the pattern thins out toward the top and the inner edge, as on the board */
		-webkit-mask-image: linear-gradient(200deg, transparent 15%, #000 70%);
		mask-image: linear-gradient(200deg, transparent 15%, #000 70%);
	}
	.mb-vrm-l {
		left: 17.4%;
		top: 5%;
		width: 12.5%;
		height: 57%;
		background: repeating-linear-gradient(150deg, #0c0c0c 0 1.5px, #242424 1.5px 2.1px, #0c0c0c 2.1px 3.4px);
		box-shadow: inset 0 0 0 0.3px #2e2e2e;
	}
	.mb-vrm-t {
		left: 26%;
		top: 0;
		width: 45%;
		height: 15%;
		background: repeating-linear-gradient(118deg, #101010 0 1.6px, #5a5a5a 1.6px 1.95px, #1c1c1c 1.95px 3.6px);
		box-shadow: inset 0 0 0 0.3px #2e2e2e;
	}
	.mb-socket {
		left: 40%;
		top: 17%;
		width: 26%;
		height: 38%;
		background: #1a1a1a;
		box-shadow: inset 0 0 0 0.7px #8c8c8c, inset 0 0 0 1.3px #222;
		display: grid;
		place-items: center;
	}
	.mb-socket b {
		/* AM5 heat spreader: matte grey, finely textured */
		width: 64%;
		height: 50%;
		background-color: #8a8a8a;
		background-image: radial-gradient(circle, #6a6a6a 0 0.2px, transparent 0.28px);
		background-size: 0.6px 0.6px;
		border-radius: 0.4px;
	}
	.mb-name {
		left: 68.5%;
		top: 22%;
		color: #bdbdbd;
		font: 700 1.25px/1 Inter, system-ui, sans-serif;
		writing-mode: vertical-rl;
		rotate: 180deg;
		letter-spacing: 0.1px;
		white-space: nowrap;
	}
	.mb-dimm {
		left: 73.4%;
		top: 3.6%;
		width: 9.2%;
		height: 61%;
		display: flex;
		gap: 1px;
	}
	.mb-dimm span {
		flex: 1;
		background: #060606;
		box-shadow: 0 0 0 0.3px #3a3a3a;
	}
	.mb-atx {
		left: 95.5%;
		top: 29%;
		width: 3.5%;
		height: 22%;
		background: #060606;
		box-shadow: 0 0 0 0.3px #3a3a3a;
	}
	.mb-pcie {
		left: 16.5%;
		top: 65%;
		width: 37.6%;
		height: 2.6%;
		background: #050505;
		box-shadow: 0 0 0 0.35px #7a7a7a;
	}
	/* bottom of the board, from the photo: M.2 heatsink (left), black shroud with black shroud (right),
	   lower striped heatsink, SupremeFX, SATA, header row */
	.mb-shroud {
		left: 46%;
		top: 65.7%;
		width: 47.6%;
		height: 23.6%;
		background: #0e0e0e;
		box-shadow: inset 0 0 0 0.3px #2a2a2a;
		clip-path: polygon(30% 0, 100% 0, 100% 100%, 0 100%);
	}
	.mb-m2 {
		left: 5.1%;
		top: 70.5%;
		width: 57.3%;
		height: 18.8%;
		background: linear-gradient(115deg, #353535 0 62%, #2a2a2a 62% 66%, #353535 66% 72%, #262626 72% 76%, #303030 76%);
		box-shadow: inset 0 0 0 0.3px #444;
		clip-path: polygon(0 0, 100% 0, 74% 100%, 0 100%);
	}
	.mb-m2 span {
		position: absolute;
		left: 3%;
		top: 64%;
		color: #f0f0f0;
		font: 700 1.9px/1 Inter, system-ui, sans-serif;
		letter-spacing: 1.15px;
	}
	.mb-m2 em {
		position: absolute;
		left: 33%;
		top: 61%;
		width: 0.25px;
		height: 2.6px;
		background: #d0d0d0;
	}
	.mb-m2 small {
		position: absolute;
		left: 3%;
		top: 84%;
		color: #9a9a9a;
		font: 600 0.75px/1 Inter, system-ui, sans-serif;
		letter-spacing: 0.15px;
		white-space: nowrap;
	}
	.mb-low {
		left: 35.8%;
		top: 89.3%;
		width: 33%;
		height: 8%;
		background: repeating-linear-gradient(115deg, #242424 0 2px, #161616 2px 2.6px);
		box-shadow: inset 0 0 0 0.3px #333;
	}
	.mb-sfx {
		left: 6.5%;
		top: 92.5%;
		padding: 0.2px 0.4px;
		background: #1e1e1e;
		color: #cfcfcf;
		font: 700 0.95px/1 Inter, system-ui, sans-serif;
		white-space: nowrap;
	}
	.mb-sata {
		left: 76%;
		top: 93.6%;
		width: 6.6%;
		height: 5.5%;
		display: flex;
		gap: 0.4px;
	}
	.mb-sata span {
		flex: 1;
		background: #080808;
		box-shadow: 0 0 0 0.25px #4a4a4a;
	}
	.mb-hdr {
		left: 3%;
		top: 97.5%;
		width: 94%;
		height: 1.6%;
		background: repeating-linear-gradient(90deg, #2c2c2c 0 2.4px, transparent 2.4px 4.2px);
	}

	/* Noctua fans */
	.nfan {
		background: transparent;
	}
	.nf {
		display: block;
		width: 100%;
		height: 100%;
	}
	.nfan-side {
		background: linear-gradient(90deg, #121212, #1a1a1a 50%, #121212);
	}
	.nfan .light,
	.nfan-side .light {
		background: rgba(0, 0, 0, 0.3);
	}

	/* NH-D15 G2 chromax.black */
	.fin {
		/* one thin black fin plate; its edge catches a little light; both sides drawn */
		backface-visibility: visible;
		background: #121212;
		box-shadow: inset 0 0 0 0.25px #3c3c3c;
	}
	.fin .light {
		background: rgba(0, 0, 0, 0.15);
	}
	.hs-base {
		background: linear-gradient(#2a2a2a, #161616);
	}
	.pipe3 {
		/* black-coated copper heatpipe: shaded across its width so it reads round; both sides drawn */
		backface-visibility: visible;
		border-radius: 1px;
		background: linear-gradient(90deg, #080808, #3a3a3a 42%, #1a1a1a 70%, #080808);
	}
	.nfanR {
		background: transparent;
	}
	.nfanR .light {
		/* round fan: shade only the disc, the corners are empty */
		inset: 2%;
		border-radius: 50%;
		background: rgba(0, 0, 0, 0.25);
	}
	.pipe3 .light {
		background: rgba(0, 0, 0, 0.25);
	}
	.hs-base .light {
		background: rgba(0, 0, 0, 0.3);
	}
	.gpu-top .light {
		/* the backplate faces up into the dark under the cooler: hardly any light reaches it */
		background: rgba(0, 0, 0, 0.88);
	}
	/* fans turn slowly (blades + hub): easy to notice, not distracting */
	.spin {
		animation: fan-spin 3.2s linear infinite;
	}
	@keyframes fan-spin {
		to {
			rotate: 360deg;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.spin {
			animation: none;
		}
	}
	/* angle-dependent view through perforated steel (see veil()) */
	.veil {
		position: absolute;
		inset: 0;
		background: rgba(10, 10, 10, 0.2); /* fixed: real mesh only lets part of the inside through, the same from every angle */
		pointer-events: none;
	}

	/* recessed expansion-slot section of the rear */
	.back {
		clip-path: polygon(evenodd, 0 0, 100% 0, 100% 100%, 0 100%, 0 0, 5.9% 67.2%, 63.1% 67.2%, 63.1% 92.3%, 5.9% 92.3%, 5.9% 67.2%);
	}
	.rec-back {
		background: var(--steel);
	}
	.rec-back > div {
		position: absolute;
	}
	.rec-wall {
		background: linear-gradient(90deg, #1c1c1c, #242424);
	}

	/* GPU */
	.gpu-edge {
		background: linear-gradient(#1c1c1c, #111);
		box-shadow: inset 0 0 0 0.3px #2c2c2c;
		justify-content: center;
	}
	/* the lit logo: white letters with a cold blue-white halo */
	.rtx {
		margin-left: 10%;
		color: #f4f8ff;
		font: 800 3.9px/1 Inter, system-ui, sans-serif;
		letter-spacing: 0.05px;
		white-space: nowrap;
		text-shadow:
			0 0 0.4px #fff,
			0 0 1.5px #b9d2ff,
			0 0 3.5px #6f9bff,
			0 0 7px rgba(80, 125, 255, 0.7);
	}
	.rtx sup {
		font-size: 40%;
		vertical-align: 1.2px;
	}
	.gpu-top {
		/* backplate */
		background: linear-gradient(90deg, #2a2a2a, #1d1d1d);
		box-shadow: inset 0 0 0 0.3px #3a3a3a;
	}
	.gpu-fans {
		background: #151515;
		justify-content: space-evenly;
		align-items: center;
	}
	.gfan {
		width: 80%;
		aspect-ratio: 1;
	}
	.gpu-end {
		background: #181818;
		box-shadow: inset 0 0 0 0.3px #2c2c2c;
	}
	.rtx-out {
		position: absolute;
		z-index: 3;
		translate: 0 -50%;
		pointer-events: none;
		white-space: nowrap;
		color: #f2f6ff;
		font: 800 3.9px/1 Inter, system-ui, sans-serif;
		opacity: 0.9;
		filter: blur(0.12px); /* light diffused by the mesh */
		text-shadow:
			0 0 0.5px #fff,
			0 0 1.6px #b9d2ff,
			0 0 3.5px rgba(111, 155, 255, 0.75);
	}
	.rtx-out sup {
		font-size: 40%;
		vertical-align: 1.2px;
	}
	/* the logo's light on the left side panel's steel */
	.spill {
		position: absolute;
		z-index: 2;
		width: 60px;
		height: 26px;
		translate: -50% -50%;
		pointer-events: none;
		background: radial-gradient(closest-side, rgba(175, 200, 255, 0.24), rgba(110, 150, 255, 0.09) 55%, transparent);
	}

	/* lighting: darken by the face's angle to the light, plus a soft top-down sheen on the walls */
	.light {
		position: absolute;
		inset: 0;
		pointer-events: none;
		background: linear-gradient(180deg, rgba(255, 255, 255, 0.05), transparent 45%, rgba(0, 0, 0, 0.12)),
			rgba(0, 0, 0, calc(1.12 - var(--s)));
	}
	.foot .light,
	.top .light,
	.bottom .light {
		background: rgba(0, 0, 0, calc(1.12 - var(--s)));
	}
</style>
