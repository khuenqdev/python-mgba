# start.ps1 - PowerShell Launcher for mGBA Python Emulator
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$pythonExe = $null

# 1. Probe virtual environments
$venvCandidates = @(
    ".venv\Scripts\python.exe",
    "venv\Scripts\python.exe",
    "ENV\Scripts\python.exe",
    "env\Scripts\python.exe"
)

foreach ($candidate in $venvCandidates) {
    $fullPath = Join-Path $PSScriptRoot $candidate
    if (Test-Path $fullPath) {
        $pythonExe = $fullPath
        break
    }
}

# 2. Probe system PATH
if (-not $pythonExe) {
    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pyCmd) {
        $pythonExe = $pyCmd.Source
    } else {
        $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
        if ($pyLauncher) {
            $pythonExe = $pyLauncher.Source
        }
    }
}

if (-not $pythonExe) {
    Write-Host "[ERROR] Python was not found in your PATH or in any local virtual environment (.venv/venv)." -ForegroundColor Red
    Write-Host "Please install Python 3.10+ and run: pip install -r requirements.txt" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "Starting mGBA Python Emulator..." -ForegroundColor Green
Write-Host "Using interpreter: $pythonExe" -ForegroundColor DarkGray

try {
    & $pythonExe main.py $args
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`n[NOTICE] The application exited with code $LASTEXITCODE." -ForegroundColor Yellow
        Read-Host "Press Enter to exit"
    }
} catch {
    Write-Host "`n[CRASH DETECTED] An error occurred while executing the emulator:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
}