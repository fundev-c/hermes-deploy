# Starts the Ubuntu-24.04 WSL distro at Windows logon, so the BearDrive hub and
# sync daemon (systemd user units, kept alive by linger + .wslconfig) come up
# after a reboot. Nothing else starts the distro on its own.
Register-ScheduledTask -TaskName "BearDrive - start WSL" `
  -Action (New-ScheduledTaskAction -Execute "C:\Windows\System32\wsl.exe" -Argument "-d Ubuntu-24.04 --exec /bin/true") `
  -Trigger (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME")
