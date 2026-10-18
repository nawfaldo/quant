# Keep Windows awake until sandbox/results/logs/ncf/ALL_DONE exists.
Add-Type -Namespace W -Name P -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
while (-not (Test-Path "$PSScriptRoot\ALL_DONE")) {
    [W.P]::SetThreadExecutionState([uint32]"0x80000001") | Out-Null
    Start-Sleep 60
}
[W.P]::SetThreadExecutionState([uint32]"0x80000000") | Out-Null
