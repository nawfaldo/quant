<script lang="ts">
 import type { Snippet } from 'svelte';
 let { children }: { children: Snippet } = $props();
 const id = $props.id();
 let viewport = $state<HTMLDivElement>();
 let content = $state<HTMLDivElement>();
 let scroll = $state({ top: 0, height: 1, view: 1 });
 function measure() {
  if (viewport) scroll = { top: viewport.scrollTop, height: viewport.scrollHeight, view: viewport.clientHeight };
 }
 $effect(() => {
  if (!viewport || !content) return;
  const observer = new ResizeObserver(measure);
  observer.observe(viewport);
  observer.observe(content);
  measure();
  return () => observer.disconnect();
 });
 const height = $derived(Math.min(scroll.view, Math.max(24, scroll.view * scroll.view / scroll.height)));
 const top = $derived(scroll.height > scroll.view ? scroll.top / (scroll.height - scroll.view) * (scroll.view - height) : 0);
 function drag(event: PointerEvent) {
  if (!viewport) return;
  const target = event.currentTarget as HTMLElement;
  target.setPointerCapture(event.pointerId);
  const start = event.clientY, initial = viewport.scrollTop;
  const ratio = (scroll.height - scroll.view) / (scroll.view - height);
  const move = (e: PointerEvent) => { if (viewport) viewport.scrollTop = initial + (e.clientY - start) * ratio; };
  const stop = () => {
   target.removeEventListener('pointermove', move);
   target.removeEventListener('lostpointercapture', stop);
  };
  target.addEventListener('pointermove', move);
  target.addEventListener('lostpointercapture', stop);
 }
</script>

<div class="relative mt-1 -mx-2 min-h-0 flex-1">
 <div {id} bind:this={viewport} onscroll={measure} class="h-full overflow-x-hidden overflow-y-auto overscroll-contain [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
  <div bind:this={content}>{@render children()}</div>
 </div>
 {#if scroll.height > scroll.view}
  <div class="absolute inset-y-0 right-[3px] w-[5px]">
   <div role="scrollbar" aria-controls={id} aria-label="Scroll list" aria-orientation="vertical" aria-valuemin={0} aria-valuemax={Math.max(0, scroll.height - scroll.view)} aria-valuenow={Math.round(scroll.top)} tabindex="-1" onpointerdown={drag} class="absolute w-full cursor-pointer touch-none rounded-full bg-white/45 hover:bg-white/70" style:height="{height}px" style:top="{top}px"></div>
  </div>
 {/if}
</div>
