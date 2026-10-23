param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$HermesHome,
    [Parameter(Mandatory=$true)][string]$HermesSource
)
$ErrorActionPreference = 'Stop'
$PluginRoot = Split-Path -Parent $PSScriptRoot
$env:HERMES_HOME = $HermesHome
$env:PYTHONPATH = $HermesSource
$env:PYTHONIOENCODING = 'utf-8'
$Scratch = Join-Path $HermesHome 'cache\scratch'
New-Item -ItemType Directory -Force -Path $Scratch | Out-Null
$env:TMPDIR = $Scratch
$env:TEMP = $Scratch
$env:TMP = $Scratch
$OutputFile = Join-Path $Scratch ('usage-prices-' + [guid]::NewGuid().ToString('N') + '.json')
$ErrorFile = Join-Path $Scratch ('usage-prices-' + [guid]::NewGuid().ToString('N') + '.err')
try {
    $Arguments = '"' + (Join-Path $PluginRoot 'worker.py') + '" prices --home "' + $HermesHome + '"'
    $Process = Start-Process -FilePath $Python -ArgumentList $Arguments -PassThru -WindowStyle Hidden -RedirectStandardOutput $OutputFile -RedirectStandardError $ErrorFile
    if (-not $Process.WaitForExit(90000)) {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        throw 'Usage+ pricing refresh timed out (90 seconds).'
    }
    $Process.Refresh()
    $Result = Get-Content -LiteralPath $OutputFile -Raw | ConvertFrom-Json
    if ($Result.status -ne 'ok') { throw 'Usage+ pricing refresh failed. Last good rates retained.' }
    $ReportDir = Join-Path $HermesHome 'cache\usage-plus'
    New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
    $Result | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $ReportDir 'last-scheduled-refresh.json') -Encoding UTF8
} finally {
    Remove-Item -LiteralPath $OutputFile,$ErrorFile -Force -ErrorAction SilentlyContinue
}
