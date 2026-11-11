let ctx: AudioContext | undefined;

/**
 * Browsers only allow audio after the user has interacted with the page, so create the
 * AudioContext on the first click/key press. Call once on mount.
 */
export function unlockSound() {
	const unlock = () => {
		ctx ??= new AudioContext();
		void ctx.resume();
	};
	addEventListener('pointerdown', unlock, { once: true });
	addEventListener('keydown', unlock, { once: true });
}

/** A soft, rising two-note chime for an incoming message. */
export function playIncoming() {
	if (!ctx || ctx.state !== 'running') return;
	const now = ctx.currentTime;
	const out = ctx.createGain();
	out.gain.value = 0.18;
	out.connect(ctx.destination);
	for (const [freq, at] of [
		[1318.5, 0], // E6
		[1760, 0.11] // A6
	]) {
		const osc = ctx.createOscillator();
		const env = ctx.createGain();
		osc.type = 'sine';
		osc.frequency.value = freq;
		env.gain.setValueAtTime(0, now + at);
		env.gain.linearRampToValueAtTime(1, now + at + 0.01);
		env.gain.exponentialRampToValueAtTime(0.001, now + at + 0.45);
		osc.connect(env).connect(out);
		osc.start(now + at);
		osc.stop(now + at + 0.5);
	}
}
