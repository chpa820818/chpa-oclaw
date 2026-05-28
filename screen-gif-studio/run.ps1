$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$workspaceRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
$stderrDir = Join-Path $workspaceRoot "copilot-temp\screen-gif-studio"
New-Item -ItemType Directory -Force $stderrDir | Out-Null
$stderrLog = Join-Path $stderrDir ("python-stderr-{0}.log" -f $PID)

$previousErrorActionPreference = $ErrorActionPreference
$previousNativePreference = $null
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -Scope Global -ErrorAction SilentlyContinue) {
    $previousNativePreference = $PSNativeCommandUseErrorActionPreference
}

try {
    # PowerShell can promote native stderr output, such as harmless image-library
    # warnings, to NativeCommandError. Capture stderr and surface only real errors.
    $ErrorActionPreference = "Continue"
    if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -Scope Global -ErrorAction SilentlyContinue) {
        $PSNativeCommandUseErrorActionPreference = $false
    }

    & python .\main.py 2> $stderrLog
    $exitCode = $LASTEXITCODE

    $stderrLines = @()
    if (Test-Path $stderrLog) {
        $stderrLines = Get-Content $stderrLog | Where-Object {
            $_ -and ($_ -notmatch "^libpng warning: iCCP: known incorrect sRGB profile$")
        }
    }

    if ($exitCode -ne 0) {
        $stderrLines | ForEach-Object { Write-Error $_ -ErrorAction Continue }
        exit $exitCode
    }

    $stderrLines | ForEach-Object { Write-Warning $_ }
}
finally {
    if ($null -ne $previousNativePreference) {
        $PSNativeCommandUseErrorActionPreference = $previousNativePreference
    }
    $ErrorActionPreference = $previousErrorActionPreference
    if (Test-Path $stderrLog) {
        Remove-Item $stderrLog -Force
    }
}
