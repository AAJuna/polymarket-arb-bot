param(
    [string]$Server = "root@172.232.226.157",
    [string]$Password = "",
    [string]$RemoteBase = "/home/runcloud/webapps/app-maggio/polymarket",
    [string]$BackupRoot = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $BackupRoot) {
    $BackupRoot = Join-Path $repoRoot "backups\server-state"
}
if (-not $Password) {
    $Password = $env:POLYMARKET_SERVER_PASSWORD
}
if (-not $Password) {
    throw "Missing server password. Pass -Password or set POLYMARKET_SERVER_PASSWORD."
}

$pscp = "C:\Program Files\PuTTY\pscp.exe"
if (-not (Test-Path $pscp)) {
    throw "pscp.exe not found at $pscp"
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$destination = Join-Path $BackupRoot $timestamp
New-Item -ItemType Directory -Force -Path $destination | Out-Null

$files = @(
    "data/portfolio.json",
    "data/portfolio.json.bak",
    "data/trade_ledger.jsonl",
    "logs/trades.log"
)

$downloaded = @()
foreach ($relativePath in $files) {
    $localPath = Join-Path $destination ($relativePath -replace "/", "\")
    $localDir = Split-Path -Parent $localPath
    if ($localDir) {
        New-Item -ItemType Directory -Force -Path $localDir | Out-Null
    }

    & $pscp -batch -pw $Password "$Server`:$RemoteBase/$relativePath" $localPath | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $downloaded += $relativePath
        continue
    }

    Write-Warning "Failed to download $relativePath"
}

$manifest = [ordered]@{
    created_at = (Get-Date).ToString("o")
    server = $Server
    remote_base = $RemoteBase
    destination = $destination
    files = $downloaded
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 (Join-Path $destination "manifest.json")

Write-Host "Backup saved to $destination"
Write-Host "Files:"
$downloaded | ForEach-Object { Write-Host " - $_" }
