use serde_json::{Value, json};

const SIMULATIONS: usize = 1_000;
const PATH_STEPS: usize = 50;
const RENDERED_PATHS: usize = 50;
const RUIN_FRACTION: f64 = 0.5;
const SEED: u64 = 1;

pub fn run(pnls: &[f64], initial_balance: f64) -> Value {
    run_with_format(pnls, initial_balance, true, RuinRule::BalanceFraction)
}

/// Runs the standard simulation but defines ruin as a peak-to-trough dollar
/// drawdown strictly exceeding `drawdown_limit`.
pub fn run_with_drawdown_ruin(pnls: &[f64], initial_balance: f64, drawdown_limit: f64) -> Value {
    run_with_format(
        pnls,
        initial_balance,
        true,
        RuinRule::DrawdownDollars(drawdown_limit),
    )
}

pub(crate) fn run_for_storage(pnls: &[f64], initial_balance: f64) -> Value {
    run_with_format(pnls, initial_balance, false, RuinRule::BalanceFraction)
}

pub(crate) fn run_for_storage_with_drawdown_ruin(
    pnls: &[f64],
    initial_balance: f64,
    drawdown_limit: f64,
) -> Value {
    run_with_format(
        pnls,
        initial_balance,
        false,
        RuinRule::DrawdownDollars(drawdown_limit),
    )
}

#[derive(Clone, Copy)]
enum RuinRule {
    BalanceFraction,
    DrawdownDollars(f64),
}

fn run_with_format(
    pnls: &[f64],
    initial_balance: f64,
    formatted: bool,
    ruin_rule: RuinRule,
) -> Value {
    if pnls.is_empty() {
        return Value::Null;
    }

    let trade_count = pnls.len();
    let block_mean = (trade_count as f64).cbrt().max(2.0);
    let jump_probability = 1.0 / block_mean;
    let checkpoints = checkpoints(trade_count, PATH_STEPS.min(trade_count + 1));
    let mut random = Xoshiro256::new(SEED);
    let mut final_balances = Vec::with_capacity(SIMULATIONS);
    let mut max_drawdowns = Vec::with_capacity(SIMULATIONS);
    let mut paths = Vec::with_capacity(RENDERED_PATHS);
    let mut profitable = 0;
    let mut ruined = 0;

    for simulation in 0..SIMULATIONS {
        let outcome = simulate(
            pnls,
            initial_balance,
            jump_probability,
            &checkpoints,
            ruin_rule,
            &mut random,
        );
        profitable += usize::from(outcome.final_balance > initial_balance);
        ruined += usize::from(outcome.ruined);
        final_balances.push(outcome.final_balance);
        max_drawdowns.push(outcome.max_drawdown);
        if simulation < RENDERED_PATHS {
            paths.push(outcome.path);
        }
    }

    final_balances.sort_by(f64::total_cmp);
    max_drawdowns.sort_by(f64::total_cmp);

    json!({
        "initialBalance": display2(initial_balance, formatted),
        "sims": SIMULATIONS,
        "steps": checkpoints.len(),
        "numPaths": paths.len(),
        "p5": display2(quantile(&final_balances, 0.05), formatted),
        "p25": display2(quantile(&final_balances, 0.25), formatted),
        "p50": display2(quantile(&final_balances, 0.50), formatted),
        "p75": display2(quantile(&final_balances, 0.75), formatted),
        "p95": display2(quantile(&final_balances, 0.95), formatted),
        "pProfit": display4(profitable as f64 / SIMULATIONS as f64, formatted),
        "pRuin": display4(ruined as f64 / SIMULATIONS as f64, formatted),
        "ddP5": display4(quantile(&max_drawdowns, 0.05), formatted),
        "ddP25": display4(quantile(&max_drawdowns, 0.25), formatted),
        "ddP50": display4(quantile(&max_drawdowns, 0.50), formatted),
        "ddP75": display4(quantile(&max_drawdowns, 0.75), formatted),
        "ddP95": display4(quantile(&max_drawdowns, 0.95), formatted),
        "stepValues": checkpoints,
        "paths": paths.into_iter().map(|path| path.into_iter().map(|value| display2(value, formatted)).collect::<Vec<_>>()).collect::<Vec<_>>(),
    })
}

fn display2(value: f64, formatted: bool) -> f64 {
    if formatted {
        (value * 100.0).round() / 100.0
    } else {
        value
    }
}

fn display4(value: f64, formatted: bool) -> f64 {
    if formatted {
        (value * 10_000.0).round() / 10_000.0
    } else {
        value
    }
}

struct Simulation {
    final_balance: f64,
    max_drawdown: f64,
    ruined: bool,
    path: Vec<f64>,
}

fn simulate(
    pnls: &[f64],
    initial_balance: f64,
    jump_probability: f64,
    checkpoints: &[u32],
    ruin_rule: RuinRule,
    random: &mut Xoshiro256,
) -> Simulation {
    let mut source_index = random.index(pnls.len());
    let mut equity = initial_balance;
    let mut peak = initial_balance;
    let mut max_drawdown = 0.0_f64;
    let mut ruined = false;
    let mut checkpoint_index = 0;
    let mut path = Vec::with_capacity(checkpoints.len());

    while checkpoints.get(checkpoint_index) == Some(&0) {
        path.push(equity);
        checkpoint_index += 1;
    }

    for applied in 1..=pnls.len() {
        equity += pnls[source_index];
        peak = peak.max(equity);
        let drawdown_dollars = peak - equity;
        if peak > 0.0 {
            max_drawdown = max_drawdown.max(drawdown_dollars / peak * 100.0);
        }
        ruined |= match ruin_rule {
            RuinRule::BalanceFraction => equity <= initial_balance * RUIN_FRACTION,
            RuinRule::DrawdownDollars(limit) => drawdown_dollars > limit,
        };

        while checkpoints.get(checkpoint_index) == Some(&(applied as u32)) {
            path.push(equity);
            checkpoint_index += 1;
        }

        source_index = if random.unit_f64() < jump_probability {
            random.index(pnls.len())
        } else {
            (source_index + 1) % pnls.len()
        };
    }

    while path.len() < checkpoints.len() {
        path.push(equity);
    }

    Simulation {
        final_balance: equity,
        max_drawdown,
        ruined,
        path,
    }
}

fn checkpoints(trade_count: usize, requested_steps: usize) -> Vec<u32> {
    if requested_steps == 1 {
        return vec![trade_count as u32];
    }

    let mut output = Vec::with_capacity(requested_steps);
    let mut previous = 0;
    for index in 0..requested_steps {
        let fraction = index as f64 / (requested_steps - 1) as f64;
        let mut count = (fraction * trade_count as f64).round() as u32;
        if index > 0 && count <= previous {
            count = previous + 1;
        }
        count = count.min(trade_count as u32);
        output.push(count);
        previous = count;
    }
    output
}

fn quantile(sorted: &[f64], probability: f64) -> f64 {
    let index = (probability * (sorted.len() - 1) as f64).round() as usize;
    sorted[index]
}

struct Xoshiro256 {
    state: [u64; 4],
}

impl Xoshiro256 {
    fn new(seed: u64) -> Self {
        let mut split_mix = SplitMix64(seed);
        Self {
            state: [
                split_mix.next(),
                split_mix.next(),
                split_mix.next(),
                split_mix.next(),
            ],
        }
    }

    fn next(&mut self) -> u64 {
        let result = self.state[0]
            .wrapping_add(self.state[3])
            .rotate_left(23)
            .wrapping_add(self.state[0]);
        let temporary = self.state[1] << 17;

        self.state[2] ^= self.state[0];
        self.state[3] ^= self.state[1];
        self.state[1] ^= self.state[2];
        self.state[0] ^= self.state[3];
        self.state[2] ^= temporary;
        self.state[3] = self.state[3].rotate_left(45);
        result
    }

    fn unit_f64(&mut self) -> f64 {
        let random = self.next();
        let mut leading_zeros = u64::from(random.leading_zeros());
        if leading_zeros >= 12 {
            leading_zeros = 12;
            loop {
                let additional = u64::from(self.next().leading_zeros());
                leading_zeros += additional;
                if additional != 64 {
                    break;
                }
                if leading_zeros >= 1_022 {
                    leading_zeros = 1_022;
                    break;
                }
            }
        }

        let mantissa = random & 0x000f_ffff_ffff_ffff;
        let exponent = (1_022 - leading_zeros) << 52;
        f64::from_bits(exponent | mantissa)
    }

    fn index(&mut self, upper_bound: usize) -> usize {
        let bound = upper_bound as u64;
        let mut product = self.next() as u128 * bound as u128;
        let mut low = product as u64;
        if low < bound {
            let mut threshold = bound.wrapping_neg();
            if threshold >= bound {
                threshold -= bound;
                if threshold >= bound {
                    threshold %= bound;
                }
            }
            while low < threshold {
                product = self.next() as u128 * bound as u128;
                low = product as u64;
            }
        }
        (product >> 64) as usize
    }
}

struct SplitMix64(u64);

impl SplitMix64 {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9e37_79b9_7f4a_7c15);
        let mut value = self.0;
        value = (value ^ (value >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        value = (value ^ (value >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        value ^ (value >> 31)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn checkpoints_include_start_and_finish() {
        let points = checkpoints(100, 50);
        assert_eq!(points.first(), Some(&0));
        assert_eq!(points.last(), Some(&100));
        assert!(points.windows(2).all(|pair| pair[0] < pair[1]));
    }

    #[test]
    fn result_is_deterministic_and_uses_half_balance_for_ruin() {
        let first = run(&[10.0, -20.0, 5.0, -2.0], 100.0);
        let second = run(&[10.0, -20.0, 5.0, -2.0], 100.0);
        assert_eq!(first, second);
        assert_eq!(first["steps"], 5);
    }

    #[test]
    fn drawdown_ruin_uses_the_requested_dollar_limit() {
        let result = run_with_drawdown_ruin(&[-20.0], 100.0, 10.0);

        assert_eq!(result["pRuin"], 1.0);
    }
}
