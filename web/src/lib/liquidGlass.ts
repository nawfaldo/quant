// Liquid glass drawn with WebGL, so it works in every browser (Zen and
// Firefox included, which cannot refract with CSS).
//
//   <button use:liquidGlass={{ source: chartElement }} class="relative isolate rounded-full">
//
// When something behind it changes, the action copies the pixels of `source`
// (its <canvas> and <img> children, e.g. the chart) that sit behind the
// element, blurs them on the GPU, and a shader turns them into glass, tuned
// pixel by pixel against the macOS control centre:
//   - frosted fill: a smooth Gaussian blur, a little saturation, and a lift
//     that only shows on dark backdrops,
//   - lens rim: a band along the edge that pulls in the backdrop from just
//     outside the glass, squeezed, like the thick edge of a real lens,
//   - light: a 2px rim and a soft glow inside it that brighten whatever is
//     behind (light blue over blue, grey over black), lit from above and below.
// Capsules keep round ends; other shapes get squircle corners. The canvas
// sits under the element's content and draws its own silhouette.
//
// Performance: nothing runs while nothing changes. The glass redraws only
// when the element or its source resizes or moves, an image loads, the
// hover state animates, or the source signals a repaint. A chart signals by
// marking its container `data-glass-notify` and dispatching
// `liquidglass:invalidate` (see notifyGlass) whenever it repaints; a source
// with canvases that does not do this is polled at 12 fps instead. All glass
// on the page shares one WebGL context.

export type LiquidGlassOptions = {
	/** Element whose canvases and images are the backdrop. */
	source: HTMLElement | undefined;
	/** Width of the bent edge band, in CSS px. Defaults to 35% of the short side, max 30. */
	bezel?: number;
	/** Blur radius of the frosting, in CSS px. */
	blur?: number;
	/** How far the rim looks inward, as a multiple of the bezel. */
	refraction?: number;
	/** Shape of the bend across the bezel: 1 linear, 2 eases in, higher hugs the edge. */
	profile?: number;
	/** Lighter blur seen through the rim, where the lens magnifies, in CSS px. */
	edgeBlur?: number;
	/** Show the hover state (more blur, brighter). Defaults to true. */
	hoverable?: boolean;
	/** Extra blur while hovered, in CSS px. */
	hoverBlur?: number;
	/** Extra brightness while hovered (0.06 = 6% brighter). */
	hoverBrightness?: number;
};

const VERT = `
attribute vec2 a;
varying vec2 uv;
void main() { uv = a * 0.5 + 0.5; gl_Position = vec4(a, 0.0, 1.0); }`;

// Separable Gaussian blur, one direction per pass, on the GPU (a canvas
// filter blur runs on the CPU in Firefox and was the slowest step).
const BLUR = `
precision highp float;
uniform sampler2D src;
uniform vec2 srcSize; // allocated size of src, px
uniform vec2 limit;   // the region holding data, px
uniform vec2 dir;     // (1, 0) or (0, 1)
uniform float sigma;  // px
void main() {
	vec2 px = gl_FragCoord.xy;
	float spacing = max(1.0, sigma / 4.0); // 25 taps always reach 3 sigma
	vec3 sum = vec3(0.0);
	float total = 0.0;
	for (int i = -12; i <= 12; i++) {
		float x = float(i) * spacing;
		float w = exp(-x * x / (2.0 * sigma * sigma));
		vec2 at = clamp(px + dir * x, vec2(0.5), limit - 0.5);
		sum += texture2D(src, at / srcSize).rgb * w;
		total += w;
	}
	gl_FragColor = vec4(sum / total, 1.0);
}`;

const FRAG = `
precision highp float;
varying vec2 uv;
uniform sampler2D tex;
uniform sampler2D texEdge; // the same backdrop, less blurred, seen through the rim
uniform vec2 size;     // element size, CSS px
uniform float margin;  // captured border around the element, CSS px
uniform float radius;
uniform float bezel;
uniform float refraction;
uniform float profile;
uniform float hover; // 0 at rest, 1 while hovered, eased in between
uniform float hoverBrightness;

uniform float squircle; // corner exponent: 2 = circular, higher = smoother
uniform vec2 texScale;  // the captured region's share of the (grow-only) blur textures

// Signed distance to a rounded rectangle centred on the origin (negative
// inside). Corners are superellipses, Apple's smooth "squircle" corners.
float box(vec2 p, vec2 b, float r) {
	vec2 q = abs(p) - b + r;
	vec2 k = max(q, 0.0);
	float corner = pow(pow(k.x, squircle) + pow(k.y, squircle), 1.0 / squircle);
	return corner + min(max(q.x, q.y), 0.0) - r;
}

vec3 sampleAt(vec2 p) {
	vec2 full = size + 2.0 * margin;
	return texture2D(tex, clamp((p + margin) / full, 0.0, 1.0) * texScale).rgb;
}

vec3 sampleEdge(vec2 p) {
	vec2 full = size + 2.0 * margin;
	return texture2D(texEdge, clamp((p + margin) / full, 0.0, 1.0) * texScale).rgb;
}


void main() {
	vec2 p = vec2(uv.x, 1.0 - uv.y) * size;          // CSS px, y down
	vec2 c = p - size * 0.5;
	vec2 half_ = size * 0.5;
	float r = min(radius, min(half_.x, half_.y));
	float d = box(c, half_, r);
	float depth = -d;

	// Outward normal from the distance field's gradient, sampled wide so it
	// turns smoothly round corners (a tight sample streaks the bent image).
	float e = 3.0;
	vec2 n = normalize(vec2(
		box(c + vec2(e, 0.0), half_, r) - box(c - vec2(e, 0.0), half_, r),
		box(c + vec2(0.0, e), half_, r) - box(c - vec2(0.0, e), half_, r)) + 1e-6);

	// Lens rim: inside the bezel, look outward past the edge, more the closer
	// to it, so a wide band shows what surrounds the glass, warped and
	// magnified, like the thick curved edge of a lens. The bend grows
	// smoothly across the whole band rather than piling up at the very edge
	// (which reads as a hard line). The capture's margin holds those pixels.
	float t = clamp(1.0 - depth / bezel, 0.0, 1.0);
	float bend = pow(t, profile);
	vec2 q = p - n * bend * bezel * refraction;

	// Blurred on capture; through the rim the lens magnifies, so less blurred.
	vec3 col = mix(sampleAt(q), sampleEdge(q), smoothstep(0.0, 0.6, t));

	// Frosted tint, fitted to the macOS control centre over a bright scene
	// (1,925 points) and over black: brightness is pulled toward the middle
	// (0.71x + 0.08, so pale yellow turns gold and white turns grey), black
	// lifts to rgb 32, and colour is boosted 15%.
	float l = dot(col, vec3(0.299, 0.587, 0.114));
	float l2 = 0.71 * l + 0.081 + 0.05 * pow(1.0 - l, 6.0);
	col = clamp(vec3(l2) + (col - vec3(l)) * 1.15, 0.0, 1.0);

	// Hover: brighter glass.
	col = col * (1.0 + hoverBrightness * hover) + vec3(0.01 * hover);

	// Light on the edge, measured on the macOS control centre. The rim is not
	// white: it multiplies what is behind it, so it is light blue over blue,
	// orange over orange, grey over black and nearly nothing over a dark patch.
	// (Mostly: it also takes a little white, measured below.)
	// Lit from above and below, almost nothing on the sides, a little extra on
	// the top-left and bottom-right. Over black it is 2px (x2 then x2.9), then
	// a soft glow fading over about 10px.
	vec2 tl = normalize(vec2(-1.0, -1.0));
	float lit = 0.28 * pow(abs(n.y), 4.0) + 0.1 * pow(abs(dot(n, tl)), 6.0) + 0.005;
	float fall = depth < 1.0 ? 0.55 : depth < 2.0 ? 1.0 : 0.32 * exp(-(depth - 2.0) / 4.5);
	// At full strength: 1.6x brighter plus 18% white, which reproduces the
	// reference rim on black (32 -> ~90) and on red (127,34,27 -> ~210,90,80).
	float w = lit * fall / 0.28;
	col = mix(col * (1.0 + 0.6 * w), vec3(1.0), 0.18 * w);

	// Anti-aliased silhouette, premultiplied.
	float alpha = clamp(0.5 - d, 0.0, 1.0);
	gl_FragColor = vec4(col * alpha, alpha);
}`;

function compile(gl: WebGLRenderingContext, type: number, src: string) {
	const s = gl.createShader(type)!;
	gl.shaderSource(s, src);
	gl.compileShader(s);
	if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) ?? 'shader');
	return s;
}

function program(gl: WebGLRenderingContext, frag: string) {
	const p = gl.createProgram()!;
	gl.attachShader(p, compile(gl, gl.VERTEX_SHADER, VERT));
	gl.attachShader(p, compile(gl, gl.FRAGMENT_SHADER, frag));
	gl.bindAttribLocation(p, 0, 'a');
	gl.linkProgram(p);
	const cache = new Map<string, WebGLUniformLocation | null>();
	return {
		p,
		u(name: string) {
			if (!cache.has(name)) cache.set(name, gl.getUniformLocation(p, name));
			return cache.get(name)!;
		}
	};
}

type Target = { tex: WebGLTexture; fbo: WebGLFramebuffer };

// One WebGL context renders every glass element in turn (browsers allow a
// page only about 16), and each element shows the result on its own 2D
// canvas. Textures and the output canvas only ever grow, so differently
// sized glass never reallocates them.
type Renderer = {
	canvas: HTMLCanvasElement;
	gl: WebGLRenderingContext;
	glass: ReturnType<typeof program>;
	blur: ReturnType<typeof program>;
	sharp: WebGLTexture; // the captured backdrop, uploaded each draw
	temp: Target; // horizontal pass
	frost: Target; // fully blurred backdrop
	edge: Target; // lightly blurred backdrop, seen through the rim
	size: { w: number; h: number }; // allocated size of the three targets
};
let shared: Renderer | null = null;

function texture(gl: WebGLRenderingContext) {
	const t = gl.createTexture()!;
	gl.bindTexture(gl.TEXTURE_2D, t);
	gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
	gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
	gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
	gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
	return t;
}

function target(gl: WebGLRenderingContext): Target {
	const tex = texture(gl);
	gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
	const fbo = gl.createFramebuffer()!;
	gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
	gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
	gl.bindFramebuffer(gl.FRAMEBUFFER, null);
	return { tex, fbo };
}

function renderer(): Renderer {
	if (shared && !shared.gl.isContextLost()) return shared;
	const canvas = document.createElement('canvas');
	const gl = canvas.getContext('webgl', { premultipliedAlpha: true, alpha: true, preserveDrawingBuffer: true })!;
	gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
	gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
	gl.enableVertexAttribArray(0);
	gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
	shared = {
		canvas,
		gl,
		glass: program(gl, FRAG),
		blur: program(gl, BLUR),
		sharp: texture(gl),
		temp: target(gl),
		frost: target(gl),
		edge: target(gl),
		size: { w: 1, h: 1 }
	};
	return shared;
}

function ensureTargets(r: Renderer, w: number, h: number) {
	if (r.size.w >= w && r.size.h >= h) return;
	const { gl } = r;
	r.size = { w: Math.max(r.size.w, w), h: Math.max(r.size.h, h) };
	for (const t of [r.temp, r.frost, r.edge]) {
		gl.bindTexture(gl.TEXTURE_2D, t.tex);
		gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, r.size.w, r.size.h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
	}
}

// Blur `src` into `out` with a Gaussian of `sigma` px: horizontal, then vertical.
function gaussian(r: Renderer, src: WebGLTexture, srcW: number, srcH: number, out: Target, w: number, h: number, sigma: number) {
	const { gl, blur } = r;
	gl.useProgram(blur.p);
	gl.uniform1i(blur.u('src'), 0);
	gl.uniform2f(blur.u('limit'), w, h);
	gl.uniform1f(blur.u('sigma'), Math.max(0.01, sigma));
	gl.viewport(0, 0, w, h);
	gl.activeTexture(gl.TEXTURE0);

	gl.bindFramebuffer(gl.FRAMEBUFFER, r.temp.fbo);
	gl.bindTexture(gl.TEXTURE_2D, src);
	gl.uniform2f(blur.u('srcSize'), srcW, srcH);
	gl.uniform2f(blur.u('dir'), 1, 0);
	gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);

	gl.bindFramebuffer(gl.FRAMEBUFFER, out.fbo);
	gl.bindTexture(gl.TEXTURE_2D, r.temp.tex);
	gl.uniform2f(blur.u('srcSize'), r.size.w, r.size.h);
	gl.uniform2f(blur.u('dir'), 0, 1);
	gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
	gl.bindFramebuffer(gl.FRAMEBUFFER, null);
}

// Tell glass over `el` (or over an ancestor of it used as a glass source) that
// `el` just repainted, so the glass redraws. Mark `el` with `data-glass-notify`
// so the glass knows it will be told and need not poll.
export function notifyGlass(el: HTMLElement | undefined) {
	el?.dispatchEvent(new Event('liquidglass:invalidate', { bubbles: true }));
}

export function liquidGlass(node: HTMLElement, options: LiquidGlassOptions) {
	let opts = options;

	const canvas = document.createElement('canvas');
	canvas.dataset.liquidGlass = '';
	Object.assign(canvas.style, {
		position: 'absolute',
		inset: '0',
		width: '100%',
		height: '100%',
		zIndex: '-1',
		pointerEvents: 'none'
	});
	node.prepend(canvas);
	const out = canvas.getContext('2d')!;

	// The backdrop behind the element (plus a margin the blur and the rim's
	// bend reach into), copied at half resolution: it is blurred anyway.
	const CAPTURE = 0.5;
	const sharp = document.createElement('canvas');
	const cctx = sharp.getContext('2d')!;

	// --- Scheduling: draw only when something changed ---------------------
	let frame = 0;
	let dirty = 2; // frames left to redraw; 2 also catches a source that paints a frame late
	let hover = 0;
	let hoverTarget = 0;
	let lastFrame = 0;
	const schedule = () => {
		if (!frame) frame = requestAnimationFrame(tick);
	};
	const invalidate = () => {
		dirty = 2;
		schedule();
	};
	const enter = () => ((hoverTarget = 1), schedule());
	const leave = () => ((hoverTarget = 0), schedule());
	node.addEventListener('pointerenter', enter);
	node.addEventListener('pointerleave', leave);

	const resize = new ResizeObserver(invalidate);
	resize.observe(node);
	if (node.parentElement) resize.observe(node.parentElement);
	addEventListener('resize', invalidate);
	addEventListener('scroll', invalidate, true);

	let watched: HTMLElement | undefined;
	let poll: ReturnType<typeof setInterval> | undefined;
	const mutations = new MutationObserver(() => {
		invalidate();
		updatePolling();
	});
	// A source with canvases that never signals a repaint is polled at 12 fps.
	function updatePolling() {
		const s = opts.source;
		const needsPoll =
			!!s && !!s.querySelector('canvas:not([data-liquid-glass])') && !s.querySelector('[data-glass-notify]');
		if (needsPoll && !poll) poll = setInterval(invalidate, 83);
		if (!needsPoll && poll) {
			clearInterval(poll);
			poll = undefined;
		}
	}
	function watch(source: HTMLElement | undefined) {
		if (source === watched) return;
		if (watched) {
			resize.unobserve(watched);
			watched.removeEventListener('liquidglass:invalidate', invalidate);
			watched.removeEventListener('load', invalidate, true);
		}
		mutations.disconnect();
		watched = source;
		if (source) {
			resize.observe(source);
			source.addEventListener('liquidglass:invalidate', invalidate);
			source.addEventListener('load', invalidate, true); // images finishing loading
			mutations.observe(source, { childList: true, subtree: true });
		}
		updatePolling();
		invalidate();
	}
	watch(opts.source);

	function tick(now: number) {
		frame = 0;
		const dt = lastFrame ? Math.min(now - lastFrame, 50) : 16;
		lastFrame = now;
		const target = opts.hoverable === false ? 0 : hoverTarget;
		if (hover !== target) {
			// Hover eases toward its target over about 150ms.
			hover += (target - hover) * Math.min(1, dt / 50);
			if (Math.abs(target - hover) < 0.01) hover = target;
			dirty = Math.max(dirty, 1);
		}
		if (dirty > 0 && !document.hidden) {
			dirty--;
			draw();
		}
		if (dirty > 0 || hover !== target) schedule();
		else lastFrame = 0;
	}

	// --- Drawing -----------------------------------------------------------
	function draw() {
		const source = opts.source;
		if (!source) return;
		const rect = node.getBoundingClientRect();
		if (rect.width === 0 || rect.height === 0) return;
		const css = parseFloat(getComputedStyle(node).borderTopLeftRadius) || 0;
		const dpr = window.devicePixelRatio || 1;
		const blur = (opts.blur ?? 7) + hover * (opts.hoverBlur ?? 2);
		const margin = Math.ceil(blur * 2.5 + 20);
		const capsule = css >= Math.min(rect.width, rect.height) / 2 - 0.5;
		const radius = capsule ? css : Math.min(css * 1.45, Math.min(rect.width, rect.height) / 2);
		const bezel = opts.bezel ?? Math.min(30, Math.min(rect.width, rect.height) * 0.35);

		// Copy what is behind the element: the source's background, then each
		// of its canvases and images at their on-screen positions.
		const cw = Math.max(1, Math.round((rect.width + 2 * margin) * CAPTURE));
		const ch = Math.max(1, Math.round((rect.height + 2 * margin) * CAPTURE));
		if (sharp.width !== cw || sharp.height !== ch) {
			sharp.width = cw;
			sharp.height = ch;
		}
		const ox = rect.left - margin;
		const oy = rect.top - margin;
		cctx.fillStyle = getComputedStyle(source).backgroundColor || '#000';
		cctx.fillRect(0, 0, cw, ch);
		for (const el of source.querySelectorAll<HTMLCanvasElement | HTMLImageElement>('canvas, img')) {
			if (el instanceof HTMLCanvasElement && 'liquidGlass' in el.dataset) continue;
			if (el instanceof HTMLImageElement && !el.complete) continue;
			const b = el.getBoundingClientRect();
			if (b.right < ox || b.bottom < oy || b.left > ox + cw / CAPTURE || b.top > oy + ch / CAPTURE) continue;
			cctx.drawImage(el, (b.left - ox) * CAPTURE, (b.top - oy) * CAPTURE, b.width * CAPTURE, b.height * CAPTURE);
		}

		// Blur on the GPU: the full frosting, and the lighter blur seen through the rim.
		const r = renderer();
		const { gl, glass } = r;
		gl.bindTexture(gl.TEXTURE_2D, r.sharp);
		gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, sharp);
		ensureTargets(r, cw, ch);
		gaussian(r, r.sharp, cw, ch, r.frost, cw, ch, blur * CAPTURE);
		gaussian(r, r.sharp, cw, ch, r.edge, cw, ch, (opts.edgeBlur ?? 9) * CAPTURE);

		const w = Math.round(rect.width * dpr);
		const h = Math.round(rect.height * dpr);
		if (canvas.width !== w || canvas.height !== h) {
			canvas.width = w;
			canvas.height = h;
		}
		// Render into the bottom-left corner of the shared output canvas.
		if (r.canvas.width < w || r.canvas.height < h) {
			r.canvas.width = Math.max(r.canvas.width, w);
			r.canvas.height = Math.max(r.canvas.height, h);
		}
		gl.useProgram(glass.p);
		gl.viewport(0, 0, w, h);
		gl.activeTexture(gl.TEXTURE1);
		gl.bindTexture(gl.TEXTURE_2D, r.edge.tex);
		gl.activeTexture(gl.TEXTURE0);
		gl.bindTexture(gl.TEXTURE_2D, r.frost.tex);
		gl.uniform1i(glass.u('tex'), 0);
		gl.uniform1i(glass.u('texEdge'), 1);
		gl.uniform2f(glass.u('texScale'), cw / r.size.w, ch / r.size.h);
		gl.uniform2f(glass.u('size'), rect.width, rect.height);
		gl.uniform1f(glass.u('margin'), margin);
		gl.uniform1f(glass.u('radius'), radius);
		gl.uniform1f(glass.u('squircle'), capsule ? 2 : 3);
		gl.uniform1f(glass.u('bezel'), bezel);
		gl.uniform1f(glass.u('refraction'), opts.refraction ?? 4);
		gl.uniform1f(glass.u('profile'), opts.profile ?? 3);
		gl.uniform1f(glass.u('hover'), hover);
		gl.uniform1f(glass.u('hoverBrightness'), opts.hoverBrightness ?? 0.06);
		gl.clearColor(0, 0, 0, 0);
		gl.clear(gl.COLOR_BUFFER_BIT);
		gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
		out.clearRect(0, 0, w, h);
		// WebGL's bottom-left corner is the bottom of the canvas image.
		out.drawImage(r.canvas, 0, r.canvas.height - h, w, h, 0, 0, w, h);
	}

	return {
		update(next: LiquidGlassOptions) {
			opts = next;
			watch(next.source);
			invalidate();
		},
		destroy() {
			cancelAnimationFrame(frame);
			if (poll) clearInterval(poll);
			resize.disconnect();
			mutations.disconnect();
			watch(undefined);
			removeEventListener('resize', invalidate);
			removeEventListener('scroll', invalidate, true);
			node.removeEventListener('pointerenter', enter);
			node.removeEventListener('pointerleave', leave);
			canvas.remove();
		}
	};
}
