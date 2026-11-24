$ErrorActionPreference = "Stop"
Set-Location "C:\Users\Administrator\AURALINK\backend"

function Write-File($path, $content) {
    $dir = Split-Path $path -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    if (Test-Path $path) { Copy-Item $path "$path.bak_$(Get-Date -Format yyyyMMdd_HHmmss)" }
    Set-Content -Path $path -Value $content -Encoding utf8
    Write-Host "  wrote -> $path"
}
