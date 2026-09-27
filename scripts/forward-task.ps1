param(
    [Parameter(Mandatory = $true)][string]$Config,
    [string]$Date = (Get-Date -Format 'yyyy-MM-dd')
)
$ErrorActionPreference = 'Stop'
try {
    $configPath = (Resolve-Path -LiteralPath $Config).Path
    $settings = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    if (-not [IO.Path]::IsPathRooted($settings.jev_cwd) -or -not [IO.Path]::IsPathRooted($settings.jev_python)) {
        throw 'FORWARD_ABSOLUTE_PATH_REQUIRED'
    }
    if (-not (Test-Path -LiteralPath $settings.jev_python -PathType Leaf)) { throw 'FORWARD_INTERPRETER_MISSING' }
    if (-not (Test-Path -LiteralPath $settings.jev_cwd -PathType Container)) { throw 'FORWARD_WORKSPACE_MISSING' }
    Set-Location -LiteralPath $settings.jev_cwd
    $env:PYTHONPATH = Join-Path $settings.jev_cwd 'src'
    & $settings.jev_python -m fkqt_jevinvestor forward run --config $configPath --date $Date
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine('FORWARD_TASK_FAILED')
    exit 2
}
