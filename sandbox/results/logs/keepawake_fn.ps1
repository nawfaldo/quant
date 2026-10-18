# Keep Windows from sleeping while the FundedNext supervisor runs; exits with it.
Add-Type -Namespace W -Name P -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
while ($true) {
    [W.P]::SetThreadExecutionState([uint32]"0x80000001") | Out-Null   # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    $alive = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*super_fns.sh*' -and $_.CommandLine -notlike '*Get-CimInstance*' }).Count
    if ($alive -eq 0) { break }
    Start-Sleep 60
}
[W.P]::SetThreadExecutionState([uint32]"0x80000000") | Out-Null
