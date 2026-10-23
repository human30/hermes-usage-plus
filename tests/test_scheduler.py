"""Execute installer with scheduler cmdlets mocked; never access real tasks."""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('powershell.exe'), 'Windows PowerShell required')
class SchedulerTests(unittest.TestCase):
    def run_installer(self, existing):
        script = str(ROOT / 'scripts' / 'install-schedule.ps1').replace("'", "''")
        existing = existing.replace("'", "''")
        command = r'''
$ErrorActionPreference = 'Stop'
$global:Registered = $false
function Get-ScheduledTask { param($TaskName, $ErrorAction) [pscustomobject]@{Actions=@([pscustomobject]@{Arguments='EXISTING'})} }
function New-ScheduledTaskAction { param($Execute,$Argument) [pscustomobject]@{Arguments=$Argument} }
function New-ScheduledTaskTrigger { param([switch]$Daily,$At,[switch]$AtLogOn,$User) 'trigger' }
function New-ScheduledTaskSettingsSet { param([switch]$StartWhenAvailable,$MultipleInstances,$ExecutionTimeLimit,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries) 'settings' }
function New-ScheduledTaskPrincipal { param($UserId,$LogonType,$RunLevel) 'principal' }
function Register-ScheduledTask { param($TaskName,$Action,$Trigger,$Settings,$Principal,$Description,[switch]$Force) $global:Registered=$true; [pscustomobject]@{TaskName=$TaskName;State='mock'} }
try {
    & 'INSTALLER' -Python 'C:\fake\python.exe' -HermesHome 'C:\profiles\default' -HermesSource 'C:\fake\source' | Out-Null
    if (-not $global:Registered) { throw 'Mock registration not reached' }
    Write-Output 'REGISTERED'
} catch {
    if ($global:Registered) { throw 'Registered before rejecting conflict' }
    Write-Output ('REJECTED: ' + $_.Exception.Message)
}
'''.replace('EXISTING', existing).replace('INSTALLER', script)
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_existing_task_requires_exact_script_and_quoted_home(self):
        refresh = str(ROOT / 'scripts' / 'refresh-prices.ps1')
        for name, arguments, accepted in (
            ('same', f'-File "{refresh}" -HermesHome "C:\\profiles\\default"', True),
            ('other_profile', f'-File "{refresh}" -HermesHome "C:\\profiles\\other"', False),
            ('home_prefix', f'-File "{refresh}" -HermesHome "C:\\profiles\\default-extra"', False),
            ('missing_home', f'-File "{refresh}"', False),
            ('other_script', '-File "C:\\other.ps1" -HermesHome "C:\\profiles\\default"', False),
        ):
            with self.subTest(name=name):
                output = self.run_installer(arguments)
                self.assertEqual(output == 'REGISTERED', accepted, output)
                if not accepted:
                    self.assertIn('refusing to overwrite', output)


if __name__ == '__main__':
    unittest.main()
