[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)][string]$Config,
    [ValidatePattern('^Fkqt-JevInvestor-[A-Za-z0-9_-]+$')][string]$TaskName = 'Fkqt-JevInvestor-Forward'
)
$ErrorActionPreference = 'Stop'
$configPath = (Resolve-Path -LiteralPath $Config).Path
$settings = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$runnerPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'forward-task.ps1')).Path
if (-not [IO.Path]::IsPathRooted($settings.jev_cwd) -or -not [IO.Path]::IsPathRooted($settings.jev_python)) {
    throw 'FORWARD_ABSOLUTE_PATH_REQUIRED'
}
if (-not (Test-Path -LiteralPath $settings.jev_python -PathType Leaf)) { throw 'FORWARD_INTERPRETER_MISSING' }
if (-not (Test-Path -LiteralPath $settings.jev_cwd -PathType Container)) { throw 'FORWARD_WORKSPACE_MISSING' }
$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Config "{1}"' -f $runnerPath, $configPath
$action = New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument $arguments -WorkingDirectory $settings.jev_cwd
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At '21:15'
$repeating = New-ScheduledTaskTrigger -Once -At '21:15' -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Minutes 105)
$trigger.Repetition = $repeating.Repetition
$taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 12)
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$definition = New-ScheduledTask -Action $action -Trigger $trigger -Settings $taskSettings -Principal $principal -Description '外部触发一次性 C 组模拟前向；日历、重试与告警由运行器处理，无券商连接。'
if ($PSCmdlet.ShouldProcess($TaskName, '注册前向运行计划任务')) {
    Register-ScheduledTask -TaskName $TaskName -InputObject $definition -ErrorAction Stop | Select-Object TaskName, State
}
