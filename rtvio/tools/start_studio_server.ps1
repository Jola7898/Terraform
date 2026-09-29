# Starts RTVIO Studio as a remote processing server on this (GPU) PC.
#
#   powershell -ExecutionPolicy Bypass -File tools\start_studio_server.ps1 `
#       [-AllowOrigin https://your-app.vercel.app] [-Funnel | -NoFunnel]
#
# Serves the web UI on every interface (LAN + Tailscale) behind a password,
# and to the Vercel-hosted UI through a public tunnel: a Cloudflare named
# tunnel (own domain), or with -Funnel, Tailscale Funnel (free, fixed
# https://<pc>.<tailnet>.ts.net address, no domain needed).
# The password comes from $env:RTVIO_STUDIO_PASSWORD; if that is unset you
# are asked for it once and it is saved for your Windows user (setx), so
# later runs - including one started by Task Scheduler at logon - need no
# prompt. -AllowOrigin and -Funnel are saved the same way
# (RTVIO_STUDIO_CORS_ORIGINS, RTVIO_STUDIO_FUNNEL); -NoFunnel undoes -Funnel.
# See docs/REMOTE_ACCESS.md.
param(
    [int]$Port = 8080,
    [int]$PhonePort = 5555,
    [string]$Python = "python",
    [string]$AllowOrigin = "",
    [switch]$Funnel,
    [switch]$NoFunnel
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if (-not $env:RTVIO_STUDIO_PASSWORD) {
    $sec = Read-Host "Choose a Studio password" -AsSecureString
    $env:RTVIO_STUDIO_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    if (-not $env:RTVIO_STUDIO_PASSWORD) { throw "empty password" }
    setx RTVIO_STUDIO_PASSWORD $env:RTVIO_STUDIO_PASSWORD | Out-Null
}

# Allow the web UI and the phone's stream through Windows Firewall, from this
# LAN and from Tailscale (100.64.0.0/10) only - the phone port has no
# password of its own. Needs an elevated shell the first time.
foreach ($rule in @(@{Name = "RTVIO Studio web"; Port = $Port}, @{Name = "RTVIO Studio phone"; Port = $PhonePort})) {
    if (-not (Get-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue)) {
        try {
            New-NetFirewallRule -DisplayName $rule.Name -Direction Inbound -Protocol TCP `
                -LocalPort $rule.Port -Action Allow -Profile Any `
                -RemoteAddress LocalSubnet, "100.64.0.0/10" | Out-Null
            Write-Host "firewall: opened TCP $($rule.Port) ($($rule.Name))"
        } catch {
            Write-Warning "could not add firewall rule '$($rule.Name)' - rerun once as Administrator"
        }
    }
}

if ($AllowOrigin) {
    $env:RTVIO_STUDIO_CORS_ORIGINS = $AllowOrigin.TrimEnd("/")
    setx RTVIO_STUDIO_CORS_ORIGINS $env:RTVIO_STUDIO_CORS_ORIGINS | Out-Null
}

# Remote access: the Vercel UI through a Cloudflare tunnel or Tailscale
# Funnel, and/or Tailscale for your own devices (phone app, drone).
$cf = Get-Service -Name cloudflared -ErrorAction SilentlyContinue
$ts = (Get-Command tailscale -ErrorAction SilentlyContinue).Source
if (-not $ts -and (Test-Path "$env:ProgramFiles\Tailscale\tailscale.exe")) { $ts = "$env:ProgramFiles\Tailscale\tailscale.exe" }

if ($Funnel -and $NoFunnel) { throw "pass -Funnel or -NoFunnel, not both" }
if ($Funnel) { $env:RTVIO_STUDIO_FUNNEL = "1"; setx RTVIO_STUDIO_FUNNEL 1 | Out-Null }
if ($NoFunnel) {
    $env:RTVIO_STUDIO_FUNNEL = ""
    Remove-ItemProperty -Path HKCU:\Environment -Name RTVIO_STUDIO_FUNNEL -ErrorAction SilentlyContinue
    if ($ts) { & $ts funnel reset; Write-Host "Tailscale Funnel: off" }
}
$useFunnel = $env:RTVIO_STUDIO_FUNNEL -eq "1"

if ($cf) {
    if ($cf.Status -ne "Running") {
        try { Start-Service cloudflared; Write-Host "Cloudflare tunnel: started" }
        catch { Write-Warning "Cloudflare tunnel service is installed but not running - start it as Administrator" }
    } else { Write-Host "Cloudflare tunnel: running" }
}
if ($useFunnel) {
    if (-not $ts) { throw "-Funnel needs Tailscale on this PC: install it from https://tailscale.com/download/windows and sign in" }
    # Publishes https://<pc>.<tailnet>.ts.net (port 443) -> http://127.0.0.1:$Port.
    # --bg keeps it configured across reboots; rerunning it is harmless. The
    # first time, it prints a link to enable HTTPS certificates and Funnel
    # for your tailnet and waits until you have.
    Write-Host "Tailscale Funnel: publishing this Studio to the internet..."
    & $ts funnel --bg $Port
    if ($LASTEXITCODE -ne 0) { throw "tailscale funnel failed (exit $LASTEXITCODE) - see its message above" }
    $dns = $null
    try { $dns = ((& $ts status --json | Out-String | ConvertFrom-Json).Self.DNSName).TrimEnd(".") } catch { }
    if ($dns) {
        Write-Host "Tailscale Funnel: https://$dns"
        Write-Host "  -> on Vercel, set RTVIO_API_URL = https://$dns (then redeploy)"
    }
}
if ($cf -or $useFunnel) {
    if ($env:RTVIO_STUDIO_CORS_ORIGINS) { Write-Host "Vercel UI allowed from: $env:RTVIO_STUDIO_CORS_ORIGINS" }
    else { Write-Warning "no -AllowOrigin set yet - the Vercel page will not be able to call this PC" }
}
if ($ts) {
    $tsip = $null
    try { $tsip = (& $ts ip -4 | Select-Object -First 1) } catch { }
    if ($tsip) { Write-Host "Tailscale: phone app / your devices -> ${tsip} (web :$Port, phone :$PhonePort)" }
}
if (-not $cf -and -not $ts) {
    Write-Warning "no Cloudflare tunnel or Tailscale found - only this LAN can reach the Studio (see docs/REMOTE_ACCESS.md)"
}

$env:PYTHONPATH = Join-Path $root "src"
Set-Location $root
& $Python -u -m rtvio.studio --web-host 0.0.0.0 --port $Port --phone-port $PhonePort
