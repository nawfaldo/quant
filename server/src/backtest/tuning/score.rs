pub fn score(sharpe: f64, profit_factor: f64, max_drawdown: f64) -> f64 {
    sharpe * 100.0 + profit_factor * 50.0 - max_drawdown * 2.0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_the_zig_weighting() {
        assert_eq!(score(2.0, 1.5, 10.0), 255.0);
    }
}
