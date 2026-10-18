<script lang="ts">
	// A round flag for a symbol. Currency pairs (AUDJPY) split the circle
	// diagonally: base currency top-left, quote currency bottom-right. Indices
	// and oil show their country. Crypto shows the official coin logo, alone
	// against the dollar (BTCUSD is just Bitcoin). Metals have no flag, so
	// their half is a solid colour.
	import au from 'flag-icons/flags/1x1/au.svg';
	import ca from 'flag-icons/flags/1x1/ca.svg';
	import de from 'flag-icons/flags/1x1/de.svg';
	import eu from 'flag-icons/flags/1x1/eu.svg';
	import fr from 'flag-icons/flags/1x1/fr.svg';
	import gb from 'flag-icons/flags/1x1/gb.svg';
	import hk from 'flag-icons/flags/1x1/hk.svg';
	import jp from 'flag-icons/flags/1x1/jp.svg';
	import us from 'flag-icons/flags/1x1/us.svg';
	import btc from 'cryptocurrency-icons/svg/color/btc.svg';
	import eth from 'cryptocurrency-icons/svg/color/eth.svg';

	let { symbol, size = 16 }: { symbol: string; size?: number } = $props();

	type Part = { flag: string } | { color: string };

	const CURRENCY: Record<string, Part> = {
		AUD: { flag: au },
		CAD: { flag: ca },
		EUR: { flag: eu },
		GBP: { flag: gb },
		JPY: { flag: jp },
		USD: { flag: us },
		XAU: { color: '#d4af37' }, // gold
		XAG: { color: '#c0c0c0' }, // silver
		XPT: { color: '#e5e4e2' }, // platinum
		XPD: { color: '#b4b8bc' }, // palladium
		XCU: { color: '#b87333' }, // copper
		XNG: { color: '#4f8fd6' }, // natural gas
		XAL: { color: '#b8bec6' }, // aluminium
		XNI: { color: '#8c9196' }, // nickel
		BTC: { flag: btc },
		ETH: { flag: eth }
	};

	const SINGLE: Record<string, Part> = {
		USTEC: { flag: us },
		US500: { flag: us },
		USOIL: { flag: us },
		UK100: { flag: gb },
		UKOIL: { flag: gb },
		DE30: { flag: de },
		DE40: { flag: de },
		GER30: { flag: de },
		FR40: { flag: fr },
		JP225: { flag: jp },
		HK50: { flag: hk },
		AUS200: { flag: au },
		STOXX50: { flag: eu },
		BTCUSD: { flag: btc },
		ETHUSD: { flag: eth }
	};

	const parts = $derived.by((): Part[] => {
		const s = symbol.toUpperCase();
		if (SINGLE[s]) return [SINGLE[s]];
		const base = CURRENCY[s.slice(0, 3)];
		const quote = CURRENCY[s.slice(3, 6)];
		if (s.length === 6 && base && quote) return [base, quote];
		return [{ color: '#555' }];
	});

	// Top-left and bottom-right triangles of the square, split on the diagonal.
	const CLIPS = ['polygon(0 0, 100% 0, 0 100%)', 'polygon(100% 0, 100% 100%, 0 100%)'];
</script>

<span class="relative block shrink-0 overflow-hidden rounded-full" style="width:{size}px; height:{size}px">
	{#each parts as part, i (i)}
		<span
			class="absolute inset-0 block bg-cover bg-center"
			style="{'flag' in part ? `background-image:url("${part.flag}")` : `background:${part.color}`};{parts.length > 1
				? `clip-path:${CLIPS[i]}`
				: ''}"
		></span>
	{/each}
	{#if parts.length > 1}
		<!-- The slash between the two halves. -->
		<span
			class="absolute inset-0 block"
			style="background:linear-gradient(to bottom right, transparent calc(50% - 0.6px), rgb(0 0 0 / 0.55) 50%, transparent calc(50% + 0.6px))"
		></span>
	{/if}
</span>
