param(
    [string]$TaskName = 'Hermes_UsagePlus_Prices',
    [string]$Python = (Get-Command python -ErrorAction Stop).Source,
    [string]$HermesHome,
    [string]$HermesSource,
    [string]$At = '06:15'
)
$ErrorActionPreference = 'Stop'
if (-not $HermesHome) {
    $HermesHome = & $Python -c 'from hermes_constants import get_hermes_home; print(get_hermes_home())'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve Hermes home.' }
}
if (-not $HermesSource) {
    $HermesSource = & $Python -c 'from pathlib import Path; import hermes_constants; print(Path(hermes_constants.__file__).resolve().parent)'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve Hermes source.' }
}
$Script = Join-Path $PSScriptRoot 'refresh-prices.ps1'
$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Existing) {
    $Actions = @($Existing.Actions)
    $ScriptPattern = '(?:^|\s)-File\s+"' + [regex]::Escape($Script) + '"(?=\s|$)'
    $HomePattern = '(?:^|\s)-HermesHome\s+"' + [regex]::Escape($HermesHome) + '"(?=\s|$)'
    if ($Actions.Count -ne 1 -or $Actions[0].Arguments -notmatch $ScriptPattern -or $Actions[0].Arguments -notmatch $HomePattern) {
        throw 'A different task already uses this name; refusing to overwrite it.'
    }
}
$Arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "' + $Script + '" -Python "' + $Python + '" -HermesHome "' + $HermesHome + '" -HermesSource "' + $HermesSource + '"'
$Action = New-ScheduledTaskAction -Execute (Join-Path $PSHOME 'powershell.exe') -Argument $Arguments
$Daily = New-ScheduledTaskTrigger -Daily -At $At
$Logon = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$Principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger @($Daily,$Logon) -Settings $Settings -Principal $Principal -Description 'Usage+: refresh official model prices daily and at logon; no LLM calls; last-good cache retained.' -Force | Select-Object TaskName,State
