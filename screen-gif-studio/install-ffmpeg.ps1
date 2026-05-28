$ErrorActionPreference = "Stop"

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Error "winget was not found. Install FFmpeg manually and add ffmpeg.exe to PATH."
}

winget install --id Gyan.FFmpeg --exact --source winget --accept-source-agreements --accept-package-agreements
