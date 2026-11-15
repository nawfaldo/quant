// The agent iPhone's state, shared by every page that shows it (/ and /account).
// Position: memory only (a reload resets it). Open/closed: saved in web.db via /api/agent-phone.
export const phonePos = $state({ x: 0, y: 0 });
export const phoneOpen = $state({ v: true, loaded: false, animate: false });

export function loadPhoneState() {
	if (phoneOpen.loaded) return;
	fetch('/api/agent-phone')
		.then((r) => r.json())
		.then((d) => ((phoneOpen.v = d.open), (phoneOpen.loaded = true)))
		.catch(() => (phoneOpen.loaded = true));
}

export function setPhoneOpen(v: boolean) {
	phoneOpen.animate = true; // only a tap or swipe animates; the first appearance after load is instant
	phoneOpen.v = v;
	fetch('/api/agent-phone', { method: 'POST', body: JSON.stringify({ open: v }) }).catch(() => {});
}
