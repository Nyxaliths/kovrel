# Kovrel uninstaller. Windows runs this from Settings > Apps > Kovrel > Uninstall.
# Removes only what the installer added; files people sent (Downloads\Kovrel) are kept.
param([switch]$Quiet)
$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Windows.Forms
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $Quiet) {
    $answer = [System.Windows.Forms.MessageBox]::Show(
        "Remove Kovrel from this PC?`n`nFiles you received from your phone (Downloads\Kovrel) are kept.`nYour settings, trusted phones and button layouts are deleted.",
        'Uninstall Kovrel', 'YesNo', 'Question')
    if ($answer -ne 'Yes') { exit }
}

Stop-Process -Name Kovrel -Force
Start-Sleep -Milliseconds 800

foreach ($folder in 'Desktop', 'Programs', 'Startup') {
    Remove-Item (Join-Path ([Environment]::GetFolderPath($folder)) 'Kovrel.lnk') -Force
}
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Kovrel' -Recurse -Force
Remove-Item (Join-Path $env:APPDATA 'Kovrel') -Recurse -Force   # settings, PIN, trusted phones, certificate, activity log

Set-Location $env:TEMP
Remove-Item (Join-Path $dir '_internal'), (Join-Path $dir 'Kovrel.exe'), (Join-Path $dir 'uninstall.ps1') -Recurse -Force
if (-not (Get-ChildItem $dir -Force)) { Remove-Item $dir -Force }   # only if nothing else is in there

if (-not $Quiet) {
    [System.Windows.Forms.MessageBox]::Show('Kovrel has been removed.', 'Uninstall Kovrel') | Out-Null
}
