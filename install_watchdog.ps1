# Registers the "Send to TV watchdog" scheduled task: every minute (and at logon) it runs watchdog.pyw,
# which restarts the server if its port is not listening. Run `install_watchdog.ps1 -Remove` to undo.
# Uses the pythonw.exe found on PATH; pass -Pythonw <path> to pick another one.
param([switch]$Remove, [string]$Pythonw)

$name = "Send to TV watchdog"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    return
}

if (-not $Pythonw) {
    $found = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if (-not $found) { throw "pythonw.exe not found on PATH. Pass -Pythonw <path to pythonw.exe>." }
    $Pythonw = $found.Source
}

$root = $PSScriptRoot
$action = New-ScheduledTaskAction -Execute $Pythonw -Argument "`"$root\watchdog.pyw`"" -WorkingDirectory $root
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $name -Action $action -Trigger @($logon, $repeat) -Settings $settings `
    -Description "Restarts the Send to TV server if it is not running." -Force | Out-Null
