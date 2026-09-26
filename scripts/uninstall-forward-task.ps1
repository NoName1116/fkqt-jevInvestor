[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidatePattern('^Fkqt-JevInvestor-[A-Za-z0-9_-]+$')][string]$TaskName = 'Fkqt-JevInvestor-Forward'
)
$ErrorActionPreference = 'Stop'
Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
if ($PSCmdlet.ShouldProcess($TaskName, '卸载指定前向运行任务（保留数据库及所有快照）')) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
