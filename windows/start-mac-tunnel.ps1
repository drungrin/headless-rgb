<#
.SYNOPSIS
Keep the complete SignalRGB-to-Mac transport alive.

.DESCRIPTION
SignalRGB 2.5 exposes UDP, but not TCP, to third-party device plugins. This
supervisor keeps both required processes running:

  SignalRGB --UDP 7532--> Python bridge --TCP 7532--> SSH --> Mac agent

TCP and UDP can share port 7532. Every endpoint binds to 127.0.0.1, so nothing
new is exposed on the LAN; SSH key authentication remains the trust boundary.
MacHost is whatever ~/.ssh/config calls the Mac.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File windows\start-mac-tunnel.ps1

.EXAMPLE
Register it to run at logon (adjust the repo path if you moved it):

    schtasks /create /tn "headless-lights Mac bridge" /sc onlogon /rl limited ^
      /tr "powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\Users\Michel\Projetos\headless-rgb\windows\start-mac-tunnel.ps1"
#>

[CmdletBinding()]
param(
    [string] $MacHost = 'mac',
    [int] $Port = 7532,
    [int] $RetrySeconds = 5
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$bridgeScript = Join-Path $scriptDir 'signalrgb-mac-bridge.py'
$logDir = Join-Path $env:LOCALAPPDATA 'headless-lights'
$supervisorLog = Join-Path $logDir 'transport-supervisor.log'
$bridgeLog = Join-Path $logDir 'signalrgb-bridge.log'
$bridgeErrorLog = Join-Path $logDir 'signalrgb-bridge.error.log'
$sshErrorLog = Join-Path $logDir 'ssh.error.log'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

function Write-Status([string] $Message) {
    $line = "$(Get-Date -Format s) $Message"
    Add-Content -Path $supervisorLog -Value $line -Encoding UTF8
    Write-Host $line
}

if (-not (Test-Path $bridgeScript)) {
    throw "missing UDP-to-TCP bridge: $bridgeScript"
}

# Do not use the Microsoft Store python.exe alias: it opens the Store instead of
# executing Python on machines where the alias is enabled.
$python = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe" `
    -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $python) {
    throw 'Python 3 is required; install it with winget install Python.Python.3.12'
}

$ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
$sshArguments = @(
    '-N'
    '-T'
    '-o', 'BatchMode=yes'
    '-o', 'ExitOnForwardFailure=yes'
    '-o', 'ServerAliveInterval=15'
    '-o', 'ServerAliveCountMax=3'
    '-L', "127.0.0.1:${Port}:127.0.0.1:${Port}"
    $MacHost
)
$bridgeArguments = @(
    $bridgeScript,
    '--listen-port', [string] $Port,
    '--upstream-port', [string] $Port
)

Write-Status "SignalRGB UDP 127.0.0.1:${Port} -> TCP/SSH -> ${MacHost}:127.0.0.1:${Port}"
Write-Status 'supervisor started; Ctrl+C stops both child processes'

$bridge = $null
$tunnel = $null
try {
    while ($true) {
        if ($null -eq $tunnel -or $tunnel.HasExited) {
            if ($null -ne $tunnel) {
                Write-Status "ssh exited with $($tunnel.ExitCode); restarting"
                $tunnel.Dispose()
            }
            $tunnel = Start-Process -FilePath $ssh -ArgumentList $sshArguments `
                -NoNewWindow -RedirectStandardError $sshErrorLog -PassThru
            Write-Status "ssh tunnel started (pid $($tunnel.Id))"
        }

        if ($null -eq $bridge -or $bridge.HasExited) {
            if ($null -ne $bridge) {
                Write-Status "UDP bridge exited with $($bridge.ExitCode); restarting"
                $bridge.Dispose()
            }
            $bridge = Start-Process -FilePath $python -ArgumentList $bridgeArguments `
                -NoNewWindow -RedirectStandardOutput $bridgeLog `
                -RedirectStandardError $bridgeErrorLog -PassThru
            Write-Status "UDP bridge started (pid $($bridge.Id))"
        }

        Start-Sleep -Seconds $RetrySeconds
    }
} finally {
    Write-Status 'supervisor stopping child processes'
    foreach ($process in @($bridge, $tunnel)) {
        if ($null -ne $process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
