<#
.SYNOPSIS
    Installs the reddit-scraper MCP connector into Claude Desktop on this machine.

.DESCRIPTION
    Merges a "reddit-scraper" entry into %APPDATA%\Claude\claude_desktop_config.json,
    using the mcp-remote bridge to reach the streamable-HTTP MCP server with Bearer auth.
    Existing connectors in the config are preserved.

.PARAMETER ServerUrl
    Full URL of the MCP endpoint, including /mcp. Use the reachable host of the machine
    running the server (e.g. http://10.0.0.5:8765/mcp). Defaults to localhost.

.PARAMETER Token
    The Bearer token. Must match MCP_AUTH_TOKEN the server was started with.
    If omitted, the script reads it from the repo .env, then falls back to prompting.

.PARAMETER ConfigPath
    Override the auto-detected claude_desktop_config.json path. By default the script
    finds the right location for both the Microsoft Store (MSIX) build and the standard
    standalone build automatically.

.EXAMPLE
    .\install.ps1 -ServerUrl http://10.0.0.5:8765/mcp -Token 0ae3712a...
#>
param(
    [string]$ServerUrl = "http://localhost:8765/mcp",
    [string]$Token,
    [string]$ConfigPath
)

$ErrorActionPreference = "Stop"

function Resolve-ClaudeConfigPath {
    param([string]$Override)
    if ($Override) { return $Override }
    # Microsoft Store / MSIX build uses a virtualized Roaming path under Packages\.
    $pkgRoot = Join-Path $env:LOCALAPPDATA "Packages"
    if (Test-Path $pkgRoot) {
        $pkgCfgDir = Get-ChildItem $pkgRoot -Directory -Filter "Claude_*" -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName "LocalCache\Roaming\Claude" } |
            Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($pkgCfgDir) { return (Join-Path $pkgCfgDir "claude_desktop_config.json") }
    }
    # Standard (non-Store) standalone install.
    return (Join-Path (Join-Path $env:APPDATA "Claude") "claude_desktop_config.json")
}

# --- Resolve the auth token -------------------------------------------------
if (-not $Token) {
    $envFile = Join-Path $PSScriptRoot "..\..\.env"
    if (Test-Path $envFile) {
        $match = Select-String -Path $envFile -Pattern '^\s*MCP_AUTH_TOKEN\s*=' | Select-Object -First 1
        if ($match) {
            $val = ($match.Line -split '=', 2)[1].Trim().Trim('"').Trim("'")
            if ($val) {
                $Token = $val
                Write-Host "Using MCP_AUTH_TOKEN from $envFile" -ForegroundColor DarkGray
            }
        }
    }
}
if (-not $Token) {
    $Token = Read-Host "Enter MCP_AUTH_TOKEN (must match the server)"
}
if (-not $Token) {
    Write-Error "No token provided. Aborting."
}

# --- Prerequisite check -----------------------------------------------------
if (-not (Get-Command npx -ErrorAction SilentlyContinue)) {
    Write-Warning "Node.js / npx was not found on PATH. Claude Desktop needs it to run mcp-remote."
    Write-Warning "Install Node.js LTS from https://nodejs.org and re-launch Claude Desktop."
}

# --- Locate / create the Claude Desktop config ------------------------------
$cfgPath = Resolve-ClaudeConfigPath -Override $ConfigPath
$cfgDir  = Split-Path $cfgPath -Parent
Write-Host "Target config: $cfgPath" -ForegroundColor DarkGray
if (-not (Test-Path $cfgDir)) {
    New-Item -ItemType Directory -Path $cfgDir -Force | Out-Null
}

if (Test-Path $cfgPath) {
    Copy-Item $cfgPath "$cfgPath.bak" -Force
    Write-Host "Backed up existing config to $cfgPath.bak" -ForegroundColor DarkGray
    $raw = Get-Content -Path $cfgPath -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($raw)) { $cfg = [pscustomobject]@{} }
    else { $cfg = $raw | ConvertFrom-Json }
}
else {
    $cfg = [pscustomobject]@{}
}

# --- Merge in the reddit-scraper entry (preserving others) ------------------
if (-not ($cfg.PSObject.Properties.Name -contains 'mcpServers')) {
    $cfg | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{})
}

# Windows: launch via `cmd /c npx` so npx resolves from PATH (no spaces). Letting
# Claude Desktop expand "npx" to "C:\Program Files\nodejs\npx.cmd" breaks because it
# passes that spaced path to cmd /C unquoted. Also keep the header's space inside the
# AUTH_HEADER env var so the arg itself has no space for cmd to split on.
$entry = [pscustomobject]@{
    command = "cmd"
    args    = @("/c", "npx", "-y", "mcp-remote", $ServerUrl, "--header", 'Authorization:${AUTH_HEADER}')
    env     = [pscustomobject]@{ AUTH_HEADER = "Bearer $Token" }
}

if ($cfg.mcpServers.PSObject.Properties.Name -contains 'reddit-scraper') {
    $cfg.mcpServers.'reddit-scraper' = $entry
}
else {
    $cfg.mcpServers | Add-Member -NotePropertyName 'reddit-scraper' -NotePropertyValue $entry
}

# --- Write back as UTF-8 (no BOM) -------------------------------------------
$json = $cfg | ConvertTo-Json -Depth 10
[System.IO.File]::WriteAllText($cfgPath, $json, (New-Object System.Text.UTF8Encoding($false)))

Write-Host ""
Write-Host "Connector installed." -ForegroundColor Green
Write-Host "  Config:   $cfgPath"
Write-Host "  Endpoint: $ServerUrl"
Write-Host ""
Write-Host "Next: fully quit Claude Desktop (tray -> Quit) and relaunch it." -ForegroundColor Yellow
Write-Host "Then check Settings -> Connectors for 'reddit-scraper'."
