<#
.SYNOPSIS
Runs PhantomShare over the Local Area Network without the internet.

.DESCRIPTION
This script configures PhantomShare to use a local relay server instead of the public one.
If run without arguments, it acts as the "Host": it starts the local relay server
and connects to it. It will also print out the IP address you need to use on other devices.
If run with an IP address as an argument, it acts as a "Client" and connects to the existing Host.

.EXAMPLE
.\lan_mode.ps1
Starts the server and client (Host Mode).

.EXAMPLE
.\lan_mode.ps1 192.168.1.50
Starts the client and connects to the Host at 192.168.1.50.
#>

param (
    [Parameter(Position=0, HelpMessage="The IP address of the Host to connect to (leave blank to be the Host)")]
    [string]$ConnectTo
)

$ErrorActionPreference = 'Stop'

if ($ConnectTo) {
    Write-Host "Starting PhantomShare in Client Mode..." -ForegroundColor Cyan
    Write-Host "Connecting to LAN Host at $ConnectTo" -ForegroundColor Green
    
    cmd.exe /c "set PHANTOMSHARE_RELAY_URL=ws://${ConnectTo}:8765& set PHANTOMSHARE_CERT_PINNING=false& python main.py"
} else {
    Write-Host "Starting PhantomShare in Host Mode..." -ForegroundColor Cyan
    
    # 1. Get the primary LAN IP Address
    $IpAddress = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias Ethernet*,Wi-Fi* -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notmatch "^127\.|^169\.254\." } | Select-Object -First 1).IPAddress
    
    if (-not $IpAddress) {
        Write-Host "Could not automatically determine your LAN IP address. Falling back to localhost (127.0.0.1)." -ForegroundColor Yellow
        $IpAddress = "127.0.0.1"
    } else {
        Write-Host "Detected your LAN IP: $IpAddress" -ForegroundColor Green
    }
    
    # Kill any existing relay server running locally to prevent port conflicts (OSError 10048)
    Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -match "relay_server\.py" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    
    # 2. Start the local relay server in a separate window
    Write-Host "Installing server requirements (if missing)..." -ForegroundColor Yellow
    $PythonExe = (Get-Command python).Source
    $ProjectDir = $PWD.Path
    Start-Process -FilePath $PythonExe -ArgumentList "-m pip install -q -r server\requirements.txt" -Wait -WindowStyle Hidden
    
    Write-Host "Starting local relay server on port 8765..." -ForegroundColor Cyan
    Start-Process cmd.exe -ArgumentList "/k cd /d `"$ProjectDir`" & `"$PythonExe`" server\relay_server.py"
    
    # Give the server a moment to start
    Start-Sleep -Seconds 2
    
    Write-Host ""
    Write-Host "==========================================================" -ForegroundColor Yellow
    Write-Host "To connect another device on your network, run this:" -ForegroundColor White
    Write-Host ".\lan_mode.ps1 $IpAddress" -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Yellow
    Write-Host ""
    
    # 4. Start the PhantomShare application
    Write-Host "Starting PhantomShare client..." -ForegroundColor Cyan
    cmd.exe /c "set PHANTOMSHARE_RELAY_URL=ws://127.0.0.1:8765& set PHANTOMSHARE_CERT_PINNING=false& python main.py"
}
