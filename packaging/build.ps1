# Builds dist\GazeAdaptiveStudy\GazeAdaptiveStudy.exe (Windows, one folder) and a release zip.
# Usage (project root):  powershell -ExecutionPolicy Bypass -File packaging\build.ps1 [-Version v2.1.0] [-Python path]
# Used locally and by .github/workflows/release.yml.
param(
    [string]$Version = "dev",
    [string]$Python = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not $Python) {
    $venv = Join-Path $root ".venv\Scripts\python.exe"
    $Python = if (Test-Path $venv) { $venv } else { "python" }
}

& $Python -m pip install -r requirements.txt -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

& $Python -m pytest -q                  # never ship a build with failing tests
if ($LASTEXITCODE -ne 0) { throw "tests failed - build aborted" }

& $Python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\gaze_app.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
$out = Join-Path $root "dist\GazeAdaptiveStudy"

# editable content next to the executable (sessions/, logs/, exports/ are created on first use)
Copy-Item config.yaml $out -Force
Copy-Item data $out -Recurse -Force
Copy-Item README.md $out -Force

# the exe is a windowed app: wait for it explicitly and check its exit code
$check = Start-Process -FilePath (Join-Path $out "GazeAdaptiveStudy.exe") -ArgumentList "--check" -Wait -PassThru -NoNewWindow
if ($check.ExitCode -ne 0) { throw "the built app failed its --check (exit $($check.ExitCode))" }
Remove-Item (Join-Path $out "logs") -Recurse -Force -ErrorAction SilentlyContinue   # log from the check

$zip = Join-Path $root "dist\GazeAdaptiveStudy-$Version-windows.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $out -DestinationPath $zip
Write-Host "Build ready: $out"
Write-Host "Release zip: $zip"
