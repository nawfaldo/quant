<#
    Re-price the whole exness study on REAL in-session spreads.

    The 2026-08-16 run fell on a Sunday, so every non-crypto symbol was priced
    on its Friday-close quote -- 1.4x to 6.1x the tradeable spread. That did not
    merely shade the returns, it changed SELECTION: xngusd went from 0 of 2,592
    cells clearing the gates at its 15.6 bp weekend quote to 192 clearing at
    4 bp, and hk50:gap went from 4/7 positive years to 6/7.

    This waits for each market to open, records a real median spread per symbol,
    then re-runs the study end to end WITHOUT --stale-spreads, so the run fails
    loudly if any symbol is still on a weekend quote rather than quietly
    repeating the same mistake.

    Run it any time from Sunday evening UTC onward; `watch` blocks until the
    markets come to it. Expect roughly 3-4 hours after the last market opens.

        powershell -File sandbox\research\refresh_spreads_and_rerun.ps1

    The MT5 terminal must be running and logged in to 416209807.
#>
param(
    [double] $WatchHours   = 18.0,
    [int]    $MinSamples   = 30,
    [int]    $Workers      = 16,
    [string] $BarMinutes   = "30"
)

$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..\..")
$py = ".\.venv\Scripts\python.exe"
$mod = "sandbox.research.exness_families"
$log = "sandbox\results\refresh_$(Get-Date -Format yyyyMMdd_HHmm).log"

function Say($text) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $text"
    Write-Output $line
    Add-Content -Path $log -Value $line -Encoding utf8
}

Say "waiting for markets to open, up to $WatchHours h, $MinSamples ticks/symbol"
& $py -m $mod watch --symbols all --hours $WatchHours --min-samples $MinSamples 2>&1 |
    Tee-Object -FilePath $log -Append

# `coverage` prints the provenance tag per symbol. Anything still reading
# weekend_close did not open inside the window and will stop `select` below.
Say "spread provenance after watch:"
& $py -m $mod coverage --symbols all 2>&1 | Tee-Object -FilePath $log -Append

# No --stale-spreads anywhere below. `resolve` refuses a weekend quote, so a
# symbol whose market never opened halts rather than being re-priced wrongly.
Say "select (no --stale-spreads; a stale symbol will abort its own run)"
& $py -m $mod select --symbols all --bar-minutes $BarMinutes --workers $Workers 2>&1 |
    Tee-Object -FilePath $log -Append

Say "validate"
& $py -m $mod validate --symbols all --bar-minutes $BarMinutes 2>&1 |
    Tee-Object -FilePath $log -Append

Say "report"
& $py -m sandbox.research.exness_families_report --symbols all `
    --bar-minutes $BarMinutes `
    --out "sandbox\results\FAMILY_STUDY_insession.md" 2>&1 |
    Tee-Object -FilePath $log -Append

Say "DONE. Nulls are NOT run here -- `why` costs 3x a select and only the"
Say "families that survive re-selection need one. Run it per symbol after"
Say "reading FAMILY_STUDY_insession.md, then rebuild the book."
