# Build Xray_labs with PyInstaller (Windows)
# Usage: right-click -> Run with PowerShell, or .\build.ps1

$ErrorActionPreference = "Stop"

Write-Host "=== X-Ray-lab PyInstaller build ===" -ForegroundColor Cyan

python -m pip install -r requirements.txt --quiet

$constants = Get-Content "constants.py" -Raw
$versionMatch = [regex]::Match($constants, 'APP_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"')
if (-not $versionMatch.Success) {
    throw "APP_VERSION not found in constants.py"
}
$version = $versionMatch.Groups[1].Value

$dist = "dist"
if (Test-Path $dist) { Remove-Item -Recurse -Force $dist }

$icon = "icon_cat.ico"
if (-not (Test-Path $icon)) {
    Write-Host "Warning: no icon_cat.ico, building without custom icon" -ForegroundColor Yellow
}

Write-Host "Running PyInstaller using Xray_labs.spec..." -ForegroundColor Green
pyinstaller --clean --noconfirm Xray_labs.spec
Write-Host "PyInstaller finished." -ForegroundColor Green

if (Test-Path "dist/Xray_labs/Xray_labs.exe") {
    Write-Host "`n=== BUILD SUCCESS ===" -ForegroundColor Green
    Write-Host "Portable folder: dist/Xray_labs/" -ForegroundColor Green

    Copy-Item "README.md" "dist/Xray_labs/README.txt" -Force -ErrorAction SilentlyContinue

    $howTo = @(
        "X-Ray-lab - Portable version (v$version)",
        "",
        "1. Zapusti Xray_labs.exe ili Xray_labs.bat",
        "2. Nichego ustanavlivat ne nuzhno.",
        "",
        "Antivirus mozhet dat lozhnoe srabatyvanie na PyInstaller.",
        "Dobavte papku v isklyucheniya Windows Defender.",
        "",
        "Podrobnosti - v README.txt"
    ) -join "`r`n"
    $howTo | Out-File -Encoding UTF8 "dist/Xray_labs/HOW_TO_RUN.txt"

    @"
@echo off
cd /d "%~dp0"
start "" Xray_labs.exe
"@ | Out-File -Encoding ASCII "dist/Xray_labs/Xray_labs.bat"

    $zipName = "Xray_labs-v$version-portable.zip"
    $zipPath = "releases\$zipName"

    if (-not (Test-Path "releases")) { New-Item -ItemType Directory -Path "releases" | Out-Null }
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

    Compress-Archive -Path "dist/Xray_labs" -DestinationPath $zipPath -Force

    $stableZip = "releases\Xray_labs-portable.zip"
    Copy-Item $zipPath $stableZip -Force

    Write-Host "`n=== PORTABLE PACKAGES ===" -ForegroundColor Cyan
    Write-Host "Versioned zip: $zipPath" -ForegroundColor Green
    Write-Host "Stable zip (auto-updater): $stableZip" -ForegroundColor Green

    $isccCandidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 5\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 5\ISCC.exe"
    )
    $iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

    if ($iscc) {
        Write-Host "`nInno Setup found - building installer..." -ForegroundColor Green
        $issFile = "installer\Xray_labs.iss"
        if (Test-Path $issFile) {
            & $iscc "/DMyAppVersion=$version" $issFile
            $setupExe = "releases\Xray_labs-$version-setup.exe"
            if (Test-Path $setupExe) {
                Write-Host "Installer created: $setupExe" -ForegroundColor Green
            }
        }
    } else {
        Write-Host "`nInno Setup not found. Install from https://jrsoftware.org/isinfo.php and re-run build.ps1" -ForegroundColor Yellow
        Write-Host "Without Inno Setup you can still use the portable zip." -ForegroundColor Yellow
    }

    Write-Host "`n=== RELEASE FILES (upload to GitHub) ===" -ForegroundColor Yellow
    Write-Host "1. releases\Xray_labs-$version-setup.exe     (INSTALLER for Release)" -ForegroundColor Yellow
    Write-Host "2. releases\Xray_labs-v$version-portable.zip (versioned portable)" -ForegroundColor Yellow
    Write-Host "3. releases\Xray_labs-portable.zip           (for in-app updater)" -ForegroundColor Yellow
} else {
    Write-Host "Build may have failed. Check above output." -ForegroundColor Red
    exit 1
}
