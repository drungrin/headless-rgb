<#
.SYNOPSIS
Hex-dump whatever a client sends to the Mac agent stream port.

.DESCRIPTION
Windows-native twin of tools/stream_dump.py, for machines without Python.
Run it in place of the SSH tunnel: the SignalRGB plugin connects to
127.0.0.1:7532 either way, so this shows exactly which bytes the plugin puts on
the wire.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File tools\stream_dump.ps1

.EXAMPLE
powershell -ExecutionPolicy Bypass -File tools\stream_dump.ps1 -Port 7532 -MaxBytes 4096
#>

[CmdletBinding()]
param(
    [string] $ListenAddress = '127.0.0.1',
    [int] $Port = 7532,
    [int] $MaxBytes = 2048
)

$ErrorActionPreference = 'Stop'

function Write-HexDump {
    param(
        [byte[]] $Data,
        [int] $Count,
        [string] $Prefix
    )

    for ($offset = 0; $offset -lt $Count; $offset += 16) {
        $take = [Math]::Min(16, $Count - $offset)
        $hex = ''
        $text = ''
        for ($i = 0; $i -lt $take; $i++) {
            $byte = $Data[$offset + $i]
            $hex += '{0:x2} ' -f $byte
            if ($byte -ge 0x20 -and $byte -lt 0x7f) {
                $text += [char] $byte
            } else {
                $text += '.'
            }
        }
        '{0}{1:x6}  {2,-47}  {3}' -f $Prefix, $offset, $hex.TrimEnd(), $text | Write-Host
    }
}

$endpoint = [System.Net.IPEndPoint]::new([System.Net.IPAddress]::Parse($ListenAddress), $Port)
$listener = [System.Net.Sockets.TcpListener]::new($endpoint)
$listener.Start()
Write-Host "listening on ${ListenAddress}:${Port} (Ctrl+C to stop)"

try {
    while ($true) {
        $client = $listener.AcceptTcpClient()
        $label = $client.Client.RemoteEndPoint.ToString()
        Write-Host "[$label] connected"

        $stream = $client.GetStream()
        $buffer = [byte[]]::new(65536)
        $total = 0
        try {
            while ($true) {
                $read = $stream.Read($buffer, 0, $buffer.Length)
                if ($read -le 0) { break }
                $remaining = [Math]::Max(0, $MaxBytes - $total)
                $total += $read
                Write-Host "[$label] $read bytes (total $total)"
                if ($remaining -gt 0) {
                    Write-HexDump -Data $buffer -Count ([Math]::Min($read, $remaining)) -Prefix "[$label] "
                } elseif ($remaining -eq 0) {
                    Write-Host "[$label] ...dump limit reached, counting only"
                }
            }
        } catch {
            Write-Host "[$label] $($_.Exception.Message)"
        } finally {
            $client.Close()
            Write-Host "[$label] closed after $total bytes"
        }
    }
} finally {
    $listener.Stop()
}
