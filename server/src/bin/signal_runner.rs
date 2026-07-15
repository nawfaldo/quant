use backend_rust::backtest::{LiveBar, LiveNightDrift, iso_day};
use std::io::{self, BufRead, Write};

fn main() -> anyhow::Result<()> {
    let stdin = io::stdin();
    let stdout = io::stdout();
    run(stdin.lock(), stdout.lock())
}

fn run(reader: impl BufRead, mut writer: impl Write) -> anyhow::Result<()> {
    let mut strategy = None;

    for line in reader.lines() {
        let line = line?;
        let command = line.trim_end_matches('\r');
        if command.is_empty() {
            continue;
        }

        if strategy.is_none() {
            let Some(name) = command.strip_prefix("STRATEGY ") else {
                continue;
            };
            if name != "night_drift" {
                writeln!(writer, "ERROR unknown strategy: {name}")?;
                writer.flush()?;
                return Ok(());
            }
            strategy = Some(LiveNightDrift::default());
            writeln!(writer, "OK strategy={name}")?;
            writer.flush()?;
            continue;
        }

        if command.starts_with("CONFIG ") {
            writeln!(writer, "OK config")?;
        } else if command == "QUIT" {
            writeln!(writer, "OK quit")?;
            writer.flush()?;
            return Ok(());
        } else if let Some(raw_bar) = command.strip_prefix("BAR ") {
            match parse_bar(raw_bar) {
                Some(bar) => {
                    let signal = strategy.as_mut().expect("strategy selected").update(bar);
                    writeln!(writer, "{}", signal.as_str().to_ascii_uppercase())?;
                }
                None => writeln!(writer, "ERROR bad bar")?,
            }
        } else {
            writeln!(writer, "ERROR unknown command")?;
        }
        writer.flush()?;
    }
    Ok(())
}

fn parse_bar(value: &str) -> Option<LiveBar> {
    if value.len() < 17 || value.as_bytes().get(16) != Some(&b',') {
        return None;
    }
    let timestamp = parse_timestamp(&value[..16])?;
    let mut fields = value[17..].split(',');
    let open = fields.next()?.parse().ok()?;
    let high = fields.next()?.parse().ok()?;
    let low = fields.next()?.parse().ok()?;
    let close = fields.next()?.parse().ok()?;
    let volume = fields.next()?.parse::<i64>().ok()? as f64;
    if fields.next().is_some() {
        return None;
    }
    Some(LiveBar {
        timestamp,
        open,
        high,
        low,
        close,
        volume,
        vix: None,
    })
}

fn parse_timestamp(value: &str) -> Option<i64> {
    let day = iso_day(value.get(..10)?)?;
    if value.as_bytes().get(10) != Some(&b' ') || value.as_bytes().get(13) != Some(&b':') {
        return None;
    }
    let hour = value
        .get(11..13)?
        .parse::<i64>()
        .ok()
        .filter(|hour| *hour < 24)?;
    let minute = value
        .get(14..16)?
        .parse::<i64>()
        .ok()
        .filter(|minute| *minute < 60)?;
    Some(day * 86_400 + hour * 3_600 + minute * 60)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_protocol_bar() {
        let bar = parse_bar("2026-07-15 09:30,1,2,0.5,1.5,12").unwrap();
        assert_eq!(bar.close, 1.5);
        assert_eq!(bar.volume, 12.0);
    }

    #[test]
    fn responds_to_protocol() {
        let input = b"STRATEGY night_drift\nCONFIG contracts=0.1\nQUIT\n";
        let mut output = Vec::new();
        run(&input[..], &mut output).unwrap();
        assert_eq!(
            String::from_utf8(output).unwrap(),
            "OK strategy=night_drift\nOK config\nOK quit\n"
        );
    }
}
