import json

data = json.load(open('sandbox/results/kendall_replacements_final.json'))['single_replacements']

print(f"{'Candidate':<35} | {'26 PnL':>7} {'PF26':>5} {'Tr26':>4} | {'25-26 Med':>10} {'d26':>6} {'p99_26':>7} | {'22-24 Med':>10} {'d24':>6} {'p99_24':>7}")
print("-" * 110)

# Filter candidates with positive 2026 PnL and sort by balanced score
# Score: 25-26 return + 22-24 return - penalty for excess DD
for x in sorted(data, key=lambda x: -x['pnl26']):
    c = x['candidate']
    print(f"{c:<35} | ${x['pnl26']:>6.1f} {x['pf26']:>5.2f} {x['trades26']:>4} | {x['med26']:>+9.0f}% {x['d_med26_canon']:>+5.0f} {x['dd99_26']:>6.2f}% | {x['med24']:>+9.0f}% {x['d_med24_canon']:>+5.0f} {x['dd99_24']:>6.2f}%")
